#!/usr/bin/env python3
"""
Official COCO Evaluation Script

This script performs ONLY official COCO evaluation.

Inputs
------
1. Ground Truth COCO JSON
2. Prediction COCO JSON

Output
------
Official COCO metrics:
    AP
    AP50
    AP75
    AP Small
    AP Medium
    AP Large
    AR

Author: 
"""

import argparse
import json
import sys
from pathlib import Path

from pycocotools.coco import COCO
from pycocotools.cocoeval import COCOeval


# ---------------------------------------------------------
# Utility
# ---------------------------------------------------------

def check_file(path, description):
    path = Path(path)

    if not path.exists():
        print(f"\n[ERROR] {description} not found:")
        print(path)
        sys.exit(1)

    print(f"[OK] {description}: {path}")


# ---------------------------------------------------------
# Main
# ---------------------------------------------------------

def main():

    parser = argparse.ArgumentParser(
        description="Official COCO Evaluation"
    )

    parser.add_argument(
        "--gt",
        required=True,
        help="Ground Truth COCO JSON"
    )

    parser.add_argument(
        "--pred",
        required=True,
        help="Prediction JSON"
    )

    parser.add_argument(
        "--output",
        default="coco_results",
        help="Directory to save evaluation summary"
    )

    args = parser.parse_args()

    gt_json = Path(args.gt)
    pred_json = Path(args.pred)
    output_dir = Path(args.output)

    output_dir.mkdir(
        parents=True,
        exist_ok=True
    )

    check_file(gt_json, "Ground Truth")
    check_file(pred_json, "Prediction JSON")

    print("\n" + "=" * 60)
    print("OFFICIAL COCO EVALUATION")
    print("=" * 60)

    # -----------------------------------------------------
    # Load Ground Truth
    # -----------------------------------------------------

    print("\nLoading Ground Truth...")

    coco_gt = COCO(str(gt_json))

    # -----------------------------------------------------
    # Load Predictions
    # -----------------------------------------------------

    print("Loading Predictions...")

    coco_dt = coco_gt.loadRes(str(pred_json))

    # -----------------------------------------------------
    # Dataset Information
    # -----------------------------------------------------

    print("\nDataset Summary")
    print("-" * 60)

    print(f"Images      : {len(coco_gt.dataset['images'])}")
    print(f"Annotations : {len(coco_gt.dataset['annotations'])}")
    print(f"Categories  : {len(coco_gt.dataset['categories'])}")

    with open(pred_json, "r") as f:
        preds = json.load(f)

    print(f"Predictions : {len(preds)}")

    # -----------------------------------------------------
    # Official COCO Evaluation
    # -----------------------------------------------------

    print("\nRunning COCO Evaluation...\n")

    coco_eval = COCOeval(
        coco_gt,
        coco_dt,
        iouType="bbox"
    )

    coco_eval.evaluate()
    coco_eval.accumulate()

    print("=" * 60)
    print("OFFICIAL COCO RESULTS")
    print("=" * 60)

    coco_eval.summarize()

    # -----------------------------------------------------
    # Save Results
    # -----------------------------------------------------

    metric_names = [
        "AP@[0.50:0.95]",
        "AP50",
        "AP75",
        "AP_small",
        "AP_medium",
        "AP_large",
        "AR@1",
        "AR@10",
        "AR@100",
        "AR_small",
        "AR_medium",
        "AR_large",
    ]

    summary_file = output_dir / "evaluation_summary.txt"

    with open(summary_file, "w") as f:

        f.write("Official COCO Evaluation\n")
        f.write("=" * 60 + "\n\n")

        f.write(f"Ground Truth : {gt_json}\n")
        f.write(f"Predictions  : {pred_json}\n\n")

        for name, value in zip(metric_names, coco_eval.stats):
            line = f"{name:<18}: {value:.4f}"
            print(line)
            f.write(line + "\n")

    print("\nSaved summary:")
    print(summary_file)

    print("\nEvaluation completed successfully.")


if __name__ == "__main__":
    main()