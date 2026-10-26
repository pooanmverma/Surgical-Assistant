#!/usr/bin/env python3
"""Run detector -> tracker -> class-level window -> online monitor worker.

Per frame:
  1. DeepSORTLogicAdapter.process_frame() returns tracks with a Boolean
     'reported' flag.
  2. A class is active in a frame if any of its tracks is reported.
  3. Each class keeps a 0.5 s rolling buffer; the class is signalled as
     present when its active fraction reaches 0.6 (0.9 for Irrigator).
     These thresholds and the adapter settings are the same as in
     main_c_integration.py.
  4. The seven Boolean signals go to rtamt_stream_worker.py, which evaluates
     the mined rules online and returns per-rule verdicts.

The detector checkpoint is a command-line option.  The default is the same
YOLOv12-X checkpoint that main_c_integration.py uses
(yolo12x_official_split_baseline_600ep).  The C integration forwards only
Bipolar (T2) and Irrigator (T7) to its monitor; this script forwards all seven
classes, so the two monitors check different rule sets and their verdicts are
not directly comparable.

Timing: total_elapsed measures the total runtime from video read to last frame 
processed, including detection, tracking, temporal voting and runtime monitoring.
Model loading, worker start-up, the first --warmup-frames frames, CSV writing 
and console output are excluded for accurate FPS measurement.
"""

import argparse
import csv
import json
import pathlib
import subprocess
import sys
import time
from collections import deque

import cv2
import numpy as np

PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[2]
RTAMT_HOME = pathlib.Path(__file__).resolve().parent

# Adapter settings: identical to main_c_integration.py.
CONF_THRESHOLD = 0.4
EMA_ALPHA = 0.4
PRESENT_THRESHOLD = 0.5
ABSENT_THRESHOLD = 0.35

# Model class ids: 0 Grasper, 1 Bipolar, 2 Hook, 3 Scissors, 4 Clipper, 
# 5 Irrigator, 6 Bag.
TOOL_NAMES = {0: "Grasper", 1: "Bipolar", 2: "Hook", 3: "Scissors",
              4: "Clipper", 5: "Irrigator", 6: "Bag"}
CLASS_IDS = list(range(7))
WINDOW_SECONDS = 0.5
DEFAULT_ON_FRACTION = 0.6
ON_FRACTION_OVERRIDE = {5: 0.9}  # Irrigator: stricter, as in the C integration


def parse_args():
    p = argparse.ArgumentParser(description="Surgical-tool pipeline with online rule monitoring")
    p.add_argument("--project-root", type=pathlib.Path, default=PROJECT_ROOT)
    p.add_argument("--model", type=pathlib.Path, default=None,
                   help="detector checkpoint (default: YOLOv12-X official-split best.pt)")
    p.add_argument("--video", type=pathlib.Path, default=None)
    p.add_argument("--output", type=pathlib.Path,
                   default=PROJECT_ROOT / "rtamt_results_video20.csv")
    p.add_argument("--rtamt-python", type=pathlib.Path,
                   default=pathlib.Path(sys.executable))
    p.add_argument("--worker", type=pathlib.Path, 
                   default=RTAMT_HOME / "rtamt_stream_worker.py")
    p.add_argument("--rules", type=pathlib.Path, default=None,
                   help="mined constraints JSON (default: the worker's default)")
    p.add_argument("--no-rtamt", action="store_true", help="native MutEx evaluation in the worker")
    p.add_argument("--dedupe", choices=["max", "all", "min", "interval"], default="max",
                   help="repeated Response rules: max = [0, loosest deadline] (default), min, all, or interval [min, max]")
    p.add_argument("--device", default="cuda") 
    p.add_argument("--max-frames", type=int, default=0, help="0 = whole video")
    p.add_argument("--warmup-frames", type=int, default=20)
    args = p.parse_args()
    root = args.project_root
    if args.model is None:
        args.model = root / "runs/official_detect/yolo12x_official_split_baseline_600ep/weights/best.pt"
    if args.video is None:  
        args.video = root / "m2cai16-tool/test_dataset/tool_video_11.mp4"
    return args


# ------------------------------------------------------------------ worker
def start_worker(args):
    for path, label in ((args.rtamt_python, "RTAMT Python"), (args.worker, "Worker script")):
        if not path.is_file():
            raise FileNotFoundError(f"{label} not found: {path}")
    cmd = [str(args.rtamt_python), str(args.worker)]
    if args.rules is not None:
        cmd += ["--rules", str(args.rules)]
    if args.no_rtamt:
        cmd.append("--no-rtamt") 
    cmd += ["--dedupe", args.dedupe]
    return subprocess.Popen(
        cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=None,
        text=True, bufsize=1, cwd=str(args.worker.parent),
    )


def send_frame(worker, payload):
    """Send one frame, read exactly one JSON response, and validate it."""
    if worker.poll() is not None:
        raise RuntimeError(f"Worker exited with code {worker.returncode}.")
    worker.stdin.write(json.dumps(payload) + "\n") 
    worker.stdin.flush()
    line = worker.stdout.readline()
    if line == "":
        raise RuntimeError("Worker closed stdout without a response.")
    try:
        response = json.loads(line)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Worker returned non-JSON output: {line[:300]!r}") from exc
    if not isinstance(response, dict):
        raise RuntimeError("Worker response must be a JSON object.")
    if "error" in response:
        raise RuntimeError(f"Worker error on frame {payload['frame']}: {response['error']}")
    if response.get("frame") != payload["frame"]:
        raise RuntimeError(f"Frame mismatch: sent {payload['frame']}, got {response.get('frame')}.")
    if not isinstance(response.get("rules"), list) or not response["rules"]:
        raise RuntimeError("Worker response contains no per-rule results.")
    return response


def stop_worker(worker):
    if worker is None:
        return
    try:
        if worker.stdin is not None and not worker.stdin.closed:
            worker.stdin.close() 
    except (BrokenPipeError, OSError):
        pass
    try:
        worker.wait(timeout=10)
    except subprocess.TimeoutExpired:
        worker.terminate()
        try:
            worker.wait(timeout=5) 
        except subprocess.TimeoutExpired:
            worker.kill()
            worker.wait()


# --------------------------------------------------------------------- csv
def is_violation(status):
    return str(status).strip().upper() == "VIOLATION"


def open_csv(path, rule_names):
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = ["frame", "time_sec", *[f"{TOOL_NAMES[c]}_present" for c in CLASS_IDS],
              "frame_status", "overall_status"]
    for name in rule_names:
        fields += [f"{name}_robustness", f"{name}_frame_status",
                   f"{name}_cumulative_robustness", f"{name}_overall_rule_status"]
    handle = path.open("w", newline="", encoding="utf-8")
    writer = csv.DictWriter(handle, fieldnames=fields)
    writer.writeheader()
    return handle, writer
    

def csv_row(frame, time_sec, tool_values, response, rule_names):
    by_name = {r["name"]: r for r in response["rules"]}
    missing = set(rule_names) - set(by_name)
    if missing:
        raise RuntimeError(f"Response is missing rule results: {sorted(missing)}")
    row = {"frame": frame, "time_sec": f"{time_sec:.3f}",
           "frame_status": response["frame_status"], 
           "overall_status": response["overall_status"]}
    for cls, value in zip(CLASS_IDS, tool_values):
        row[f"{TOOL_NAMES[cls]}_present"] = value
    for name in rule_names:
        r = by_name[name]
        row[f"{name}_robustness"] = r.get("robustness", "")
        row[f"{name}_frame_status"] = r.get("frame_status", "")
        row[f"{name}_cumulative_robustness"] = r.get("cumulative_robustness", "")
        row[f"{name}_overall_rule_status"] = r.get("overall_rule_status", "")
    return row


# ------------------------------------------------------------------ signals 
def class_signals(tracks, buffers):
    """Class-level activity -> rolling-window majority vote -> binary signal."""
    active = set()
    for track in tracks:
        if not track.get("reported", False):
            continue
        try:
            cls = int(track["class_id"])
        except (KeyError, TypeError, ValueError):
            continue
        if cls in buffers:
            active.add(cls)
    values = []
    for cls in CLASS_IDS:
        buf = buffers[cls] 
        buf.append(1 if cls in active else 0)
        fraction = sum(buf) / len(buf)
        values.append(int(fraction >= ON_FRACTION_OVERRIDE.get(cls, DEFAULT_ON_FRACTION)))
    return values


def latency_stats(samples_ms):
    if not samples_ms:
        return {}
    a = np.asarray(samples_ms, dtype=float)
    return {"n": int(a.size), "mean_ms": float(a.mean()), "p50_ms": float(np.percentile(a, 50)),
            "p99_ms": float(np.percentile(a, 99)), "max_ms": float(a.max())}


# --------------------------------------------------------------------- main 
def main():
    args = parse_args()
    sys.path.insert(0, str(args.project_root))
    from src.integration.adapter import DeepSORTLogicAdapter  # noqa: E402
    
    print("=" * 72)
    print("ONLINE RULE MONITORING")  
    print("=" * 72)
    print(f"Model : {args.model}\nVideo : {args.video}\nOutput: {args.output}")
    for path, label in ((args.model, "Model checkpoint"), (args.video, "Input video")):
        if not path.exists():
            raise FileNotFoundError(f"{label} not found: {path}")
            
    cap = cv2.VideoCapture(str(args.video))
    if not cap.isOpened():
        raise RuntimeError(f"Could not open video: {args.video}")
    fps = cap.get(cv2.CAP_PROP_FPS)
    if not fps or fps <= 0:
        fps = 25.0
    budget_ms = 1000.0 / fps
    window = int(round(fps * WINDOW_SECONDS))
    if window < 1:
        cap.release()
        raise ValueError(f"Invalid window length for FPS={fps}")
    print(f"Video FPS: {fps:.3f} | window: {window} frames | budget: {budget_ms:.1f} ms/frame")
        
    worker = csv_file = writer = None  
    rule_names = None
    frame_number = violation_frames = 0
    rule_viol_frames, rule_first_time = {}, {}
    core_ms, worker_ms = [], []

    total_start = time.perf_counter()
    processed_frames = 0
        
    try:
        adapter = DeepSORTLogicAdapter(
            model_path=str(args.model), device=args.device, conf_thresh=CONF_THRESHOLD,
            smoother_cfg={"fps": fps, "ema_alpha": EMA_ALPHA, 
                          "present_th": PRESENT_THRESHOLD, "absent_th": ABSENT_THRESHOLD},
        )
        buffers = {cls: deque(maxlen=window) for cls in CLASS_IDS}
        worker = start_worker(args)
        
        while True:
            if args.max_frames and frame_number >= args.max_frames:
                break
            t0 = time.perf_counter()
            ret, frame = cap.read()
            if not ret:
                break
            frame_number += 1
            processed_frames += 1
                
            tracks = adapter.process_frame(frame, frame_idx=frame_number) 
            tool_values = class_signals(tracks, buffers)
            time_sec = (frame_number - 1) / fps
            
            t1 = time.perf_counter()
            response = send_frame(worker, {"frame": frame_number, "time_sec": time_sec, 
                                           "tools": tool_values})
            t2 = time.perf_counter()
            if frame_number > args.warmup_frames:
                core_ms.append((t2 - t0) * 1000.0)
                worker_ms.append((t2 - t1) * 1000.0)
                
            current = [r["name"] for r in response["rules"]]
            if len(current) != len(set(current)):
                raise RuntimeError("Worker returned duplicate rule names.")
            if writer is None:
                rule_names = current
                csv_file, writer = open_csv(args.output, rule_names)
                rule_viol_frames = {n: 0 for n in rule_names}
                rule_first_time = {n: None for n in rule_names}
                print(f"Monitoring {len(rule_names)} rules.")
            elif set(current) != set(rule_names):
                raise RuntimeError("Rule set changed during processing.")

            writer.writerow(csv_row(frame_number, time_sec, tool_values, response, rule_names))
            if is_violation(response["frame_status"]):
                violation_frames += 1
            for r in response["rules"]:
                if is_violation(r.get("frame_status")):
                    rule_viol_frames[r["name"]] += 1
                    if rule_first_time[r["name"]] is None:
                        rule_first_time[r["name"]] = time_sec
                          
            if frame_number == 1 or frame_number % 30 == 0:
                active = [TOOL_NAMES[c] for c, v in zip(CLASS_IDS, tool_values) if v]
                print(f"Frame {frame_number:6d} | t={time_sec:8.3f}s | "
                      f"{response['frame_status']:9s} | overall {response['overall_status']:9s} | "
                      f"active {active}", flush=True)
                csv_file.flush()
    finally:
        cap.release()
        if csv_file is not None:
            csv_file.close()
        stop_worker(worker)

    total_elapsed = time.perf_counter() - total_start  
    total_elapsed = time.perf_counter() - total_start
    end_to_end_fps = processed_frames / total_elapsed
    core, work = latency_stats(core_ms), latency_stats(worker_ms)
    over = int(np.sum(np.asarray(core_ms) > budget_ms)) if core_ms else 0

    print("\n" + "=" * 72 + "\nSUMMARY\n" + "=" * 72)
    print(f"Frames processed        : {frame_number}")
    print(f"Total elapsed time      : {total_elapsed:.2f} s")
    print(f"End-to-end FPS          : {end_to_end_fps:.2f}")
    print(f"Frames with violations  : {violation_frames}")
    if core:
        print(f"Core latency (ms)       : mean {core['mean_ms']:.2f} | p50 {core['p50_ms']:.2f} | "
              f"p99 {core['p99_ms']:.2f} | max {core['max_ms']:.2f}  (n={core['n']})")
        print(f"Worker round trip (ms)  : mean {work['mean_ms']:.2f} | p99 {work['p99_ms']:.2f} | "
              f"max {work['max_ms']:.2f}")
        print(f"Frames over {budget_ms:.1f} ms budget: {over} of {core['n']}")
    print("\nPer-rule violations (frames, first time):")
    for name in rule_names or []:
        first = rule_first_time[name]
        print(f"  {name:<32s} {rule_viol_frames[name]:6d}   "
              f"{'-' if first is None else f'{first:.2f}s'}")

    summary = {
        "model": str(args.model), "video": str(args.video), "fps": fps, "window_frames": window,
        "frames": frame_number, "violation_frames": violation_frames,
        "total_elapsed": total_elapsed, "end_to_end_fps": end_to_end_fps,
        "core_latency": core, "worker_latency": work,
        "frames_over_budget": over, "budget_ms": budget_ms,
        "rules": {n: {"violation_frames": rule_viol_frames[n],
                      "first_violation_time": rule_first_time[n]} for n in (rule_names or [])},
    }
    if rule_names:
        out = args.output.with_suffix(".summary.json")
        out.write_text(json.dumps(summary, indent=2), encoding="utf-8")
        print(f"\nCSV     : {args.output}\nSummary : {out}")
    else:
        print("No CSV written: the video contained no readable frames.")


if __name__ == "__main__":
    main()
