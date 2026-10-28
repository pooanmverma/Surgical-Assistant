from ultralytics import YOLO
import os

# Optional: ensure GPU memory fragmentation is minimized
os.environ["PYTORCH_CUDA_ALLOC_CONF"] = "expandable_segments:True"

def main():
    # Load the RT-DETR-x (large) pretrained model.
    # This is the key change: loading a Transformer-based model.
    run_name = 'rtdetr_x_surgical_tools_final3'
    last_checkpoint = f'runs/detect/{run_name}/weights/last.pt'
    if os.path.exists(last_checkpoint):
        print(f"Resuming training from checkpoint: {last_checkpoint}")
        model = YOLO(last_checkpoint)
        resume_flag = True
    else:
        print("Starting new training run...")
        model = YOLO('rtdetr-x.pt')
        resume_flag = False
    # model = YOLO('rtdetr-x.pt')

    print("--- Starting RT-DETR Training ---")
    print("Model: RT-DETR-x (Transformer-based)")
    print("Dataset: surgical_tools.yaml")

    # Use the same optimized training parameters as your YOLOv10 run
    # to ensure a fair comparison.
    results = model.train(
        data='surgical_tools.yaml',
        epochs=200,          # Use the same number of epochs
        imgsz=640,           # Use the same image size
        batch=8,            # Use the same batch size
        patience=80,
        
        # --- Augmentation (Same as your YOLOv10 run) ---
        augment=True,
        fliplr=0.5,
        degrees=15.0,
        scale=0.1,
        hsv_s=0.6,
        hsv_v=0.4,
        mosaic=0.5,
        
        # --- Performance Settings ---
        workers=32,
        amp=True,
        cache=False,
        
        name=run_name,# New save directory
        resume=resume_flag
    )
    
    print("RT-DETR training complete.")

if __name__ == '__main__':
    main()
