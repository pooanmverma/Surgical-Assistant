#!/usr/bin/env python3
"""Cross-check the RTAMT Bounded-Response backend against the native one.

It reads a runner CSV (the <Tool>_present columns hold the exact binary
signals the monitor saw), then feeds those signals to BOTH the native
obligation-queue monitor and the new RTAMT pastify monitor, and compares
them rule by rule.

The two use different reporting conventions, so they are NOT expected to
flag the identical frame: the RTAMT verdict for a trigger at frame t is
returned at t + N (N = deadline in frames), while the native monitor
reports at the first frame after the deadline, t + N + 1.  What should
match is (a) the TOTAL number of violation frames per rule (up to a small
boundary/warm-up difference) and (b) the set of flagged frames once the
native stream is shifted back by one.  Large disagreement means the RTAMT
spec or the frame conversion is wrong.

Run with the RTAMT interpreter (no GPU, no OpenCV needed):

  python3 \
      xcheck_response_backend.py RUNNER.csv \
      --rules merged_constraints_mutex_only.json --dedupe max

rtamt_stream_worker.py must sit next to this file.
"""

import argparse
import csv
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import Rtamtstreamworker as W  # noqa: E402

TOOLS = ["Grasper", "Bipolar", "Hook", "Scissors", "Clipper", "Irrigator", "Bag"]


def load_signals(path):
    with open(path, newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    if not rows:
        raise SystemExit("CSV has no rows.")
    return [[int(float(r[f"{t}_present"])) for t in TOOLS] for r in rows]


def violations(monitor, signals, fps):
    out = []
    for i, sig in enumerate(signals):
        v = [float(x) for x in sig]
        if monitor.step(i / fps, v)["frame_status"] == "VIOLATION":
            out.append(i)
    return out


def main():
    ap = argparse.ArgumentParser(description="native vs rtamt Bounded-Response cross-check")
    ap.add_argument("csv")
    ap.add_argument("--rules", required=True)
    ap.add_argument("--dedupe", default="max", choices=["max", "all", "min", "interval"])
    ap.add_argument("--fps", type=float, default=25.0)
    args = ap.parse_args()

    signals = load_signals(args.csv)
    n = len(signals)
    rules = json.load(open(args.rules, encoding="utf-8"))
    prepared = W.prepare_rules(rules, args.dedupe)

    print(f"{n} frames, fps={args.fps}, dedupe={args.dedupe}\n")
    print(f"{'rule':24s}{'deadline':>9s}{'native':>8s}{'rtamt':>8s}"
          f"{'shift-match':>12s}{'extra':>7s}{'missing':>8s}")

    worst = 0
    for display, rule, lower, upper in prepared:
        name = rule.get("name", "")
        if not W.RESPONSE_RE.match(name):
            continue
        a, b = map(int, W.RESPONSE_RE.match(name).groups())

        nat = W.BoundedResponseMonitor(display, "", a, b, upper, lower)
        rt = W.RTAMTBoundedResponseMonitor(display, "", a, b, upper, lower, args.fps)
        vn = violations(nat, signals, args.fps)
        vr = violations(rt, signals, args.fps)

        # native reports one frame after rtamt for the same obligation, so
        # shift the rtamt set forward by one and compare.
        sr = {i + 1 for i in vr}
        sn = set(vn)
        extra = len(sr - sn)       # rtamt flagged, native did not
        missing = len(sn - sr)     # native flagged, rtamt did not
        matched = len(sr & sn)
        worst = max(worst, extra + missing)
        print(f"{display:24s}{upper:9g}{len(vn):8d}{len(vr):8d}"
              f"{matched:12d}{extra:7d}{missing:8d}")

    print()
    if worst <= 3:
        print(f"RESULT: backends AGREE (max off-by-one mismatch per rule = {worst}, "
              "boundary only).")
    else:
        print(f"RESULT: backends DIFFER (up to {worst} frames on some rule) - inspect the spec.")


if __name__ == "__main__":
    main()