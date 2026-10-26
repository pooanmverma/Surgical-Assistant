"""
FILE: trace_logger.py

Generates temporal traces from the M2CAI16 training videos.

Output:
training_traces/
    tool_video_01_trace.csv
    ...
    tool_video_10_trace.csv
"""

import cv2
import csv
import os
import sys
import time
import logging

sys.path.append(os.getcwd())

try:
    from src.integration.adapter import DeepSORTLogicAdapter, TOOL_MAPPING
except ImportError as e:
    print("Failed to import DeepSORT adapter.")
    print(e)
    sys.exit(1)

# ----------------------------------------------------
# Configuration
# ----------------------------------------------------

MODEL_PATH = "./runs/official_detect/yolo12x_official_split_baseline_600ep/weights/best.pt"

VIDEO_DIR = "./m2cai16-tool/train_dataset"

OUTPUT_DIR = "training_traces12"

os.makedirs(OUTPUT_DIR, exist_ok=True)

# ----------------------------------------------------
# Logging
# ----------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    datefmt="%H:%M:%S",
)

logger = logging.getLogger("TraceLogger")


def main():

    logger.info("Starting Trace Generation")

    logger.info(f"Model : {MODEL_PATH}")
    logger.info(f"Videos: {VIDEO_DIR}")
    logger.info(f"Output: {OUTPUT_DIR}")

    if not os.path.exists(MODEL_PATH):
        logger.error("Model not found.")
        return

    if not os.path.isdir(VIDEO_DIR):
        logger.error("Training video directory not found.")
        return

    # ------------------------------------------------
    # Initialize detector only once
    # ------------------------------------------------

    logger.info("Loading YOLO + DeepSORT...")

    adapter = DeepSORTLogicAdapter(
        model_path=MODEL_PATH,
        tool_mapping=TOOL_MAPPING,
        conf_thresh=0.4,
        device="cuda"
    )

    overall_start = time.time()
    total_frames_processed = 0

    # ------------------------------------------------
    # Loop over all training videos
    # ------------------------------------------------

    for video_num in range(1, 11):

        video_name = f"tool_video_{video_num:02d}"

        video_path = os.path.join(
            VIDEO_DIR,
            video_name + ".mp4"
        )

        if not os.path.exists(video_path):
            logger.warning(f"{video_name} not found. Skipping.")
            continue

        output_csv = os.path.join(
            OUTPUT_DIR,
            video_name + "_trace.csv"
        )

        logger.info("=" * 70)
        logger.info(f"Processing {video_name}")
        logger.info("=" * 70)

        cap = cv2.VideoCapture(video_path)

        if not cap.isOpened():
            logger.warning(f"Cannot open {video_name}")
            continue

        total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        fps = cap.get(cv2.CAP_PROP_FPS) or 25.0

        logger.info(f"Frames : {total_frames}")
        logger.info(f"FPS    : {fps}")

        frame_idx = 0

        with open(output_csv, "w", newline="") as csv_file:

            writer = csv.writer(csv_file)
            writer.writerow(["frame", "active_tools"])

            while True:

                ret, frame = cap.read()

                if not ret:
                    break

                frame_idx += 1

                tracks = adapter.process_frame(
                    frame,
                    frame_idx=frame_idx
                )

                active_tools = set()

                for t in tracks:

                    if t.get("reported", False):

                        if t["class_id"] <= 6:
                            active_tools.add(t["class_id"])

                writer.writerow([
                    frame_idx,
                    "|".join(map(str, sorted(active_tools)))
                ])

                if frame_idx % 500 == 0:

                    names = [
                        TOOL_MAPPING.get(i, str(i))
                        for i in sorted(active_tools)
                    ]

                    logger.info(
                        f"{video_name} | "
                        f"{frame_idx}/{total_frames} | "
                        f"{names}"
                    )

        cap.release()

        total_frames_processed += frame_idx

        logger.info(f"Saved : {output_csv}")

    # ------------------------------------------------
    # Summary
    # ------------------------------------------------

    elapsed = time.time() - overall_start

    logger.info("=" * 70)
    logger.info("TRACE GENERATION FINISHED")
    logger.info("=" * 70)

    logger.info(f"Videos processed : 10")
    logger.info(f"Frames processed : {total_frames_processed}")
    logger.info(f"Elapsed time     : {elapsed:.2f} sec")

    if elapsed > 0:
        logger.info(
            f"Processing Speed : {total_frames_processed / elapsed:.2f} FPS"
        )

    logger.info(f"CSV Directory    : {OUTPUT_DIR}")

    logger.info("Next Step:")
    logger.info("Run run_mining_pipeline.py on the generated trace files.")


if __name__ == "__main__":
    main()