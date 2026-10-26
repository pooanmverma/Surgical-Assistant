import torch
from ultralytics import YOLO
import os

def main():
    # 1 Hardware Check
    if not torch.cuda.is_available():
        print("WARNING: CUDA is not available. Training YOLOv12 on CPU will be not possible.")
        return
    
    print(f"✅ GPU Detected: {torch.cuda.get_device_name(0)}")
    print(f"✅ VRAM Available: {torch.cuda.get_device_properties(0).total_memory/ 1e9 :.2f} GB")

    # 2 Initialize the YOLOv12 Model

    print("⬇️ Loading YOLOv12x (Attentation-Centric) model .... ")
    # model = YOLO('yolov12x.pt')
    # model = YOLO("yolo12x.pt")
    # print(model.model)

    CKPT = "yolo12x.pt"
    # load the checkpoint
    model = YOLO(CKPT)

    # 3 Define Training Configuration
    # Project Configuration

    PROJECT_NAME = "runs/official_detect"
    RUN_NAME ="yolo12x_official_split_baseline"

    print(f"🚀 Starting training for {RUN_NAME}")

    # Start Training

    results = model.train(
        data='official_split.yaml',
        epochs=200,
        patience=30,
        imgsz=640,
        batch=8,
        workers=32,
        freeze=0,
        device=0,
        amp=True,
        optimize=True,
        lr0=5e-5,
        augment=True,
        hsv_h=0.015,
        hsv_s=0.7,
        hsv_v=0.4,
        degrees=0.0,
        flipud=0.0,
        fliplr=0.5,
        mosaic=0.5,
        resume=False,

        project=PROJECT_NAME,
        name=RUN_NAME,
        exist_ok=True,
        verbose=True
    )
    trainer = model.trainer
    print("Train samples:", len(trainer.train_loader.dataset))
    print("Val samples:", len(trainer.val_loader.dataset))

    # 5 Export the best model for inference later
    print("Exporting best model to ONNX for deployment tests ....")
    success=model.export(format='onnx',dynamic=True)
    print(f"Export status: {success}")

if __name__ == '__main__':
    main()
