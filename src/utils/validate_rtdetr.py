from ultralytics import YOLO
from pathlib import Path

# --- Configuration ---
# This path MUST match the 'name' you set in train_rtdetr.py
RUN_NAME = 'rtdetr_x_surgical_tools_final3'
MODEL_PATH = Path(f"runs/detect/{RUN_NAME}/weights/best.pt")
# --- End Configuration ---
# ./runs/detect/rtdetr_x_surgical_tools_final3

def main():
    if not MODEL_PATH.exists():
        print(f"Error: Model weights not found at {MODEL_PATH}")
        print("Please check the 'RUN_NAME' variable and ensure training is complete.")
        return

    print(f"Loading trained RT-DETR model from: {MODEL_PATH}")
    model = YOLO(MODEL_PATH)

    print("--- Starting RT-DETR Validation ---")
    # Run validation on the 'val' split defined in surgical_tools.yaml
    metrics = model.val()
    
    print("\n--- RT-DETR Validation Results ---")
    print(f"mAP@50-95: {metrics.box.map:.4f}")
    print(f"mAP@50:   {metrics.box.map50:.4f}")
    print(f"Precision: {metrics.box.p[0]:.4f}")
    print(f"Recall:    {metrics.box.r[0]:.4f}")
    print("---------------------------------")

if __name__ == '__main__':
    main()
