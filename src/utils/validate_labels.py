#!/usr/bin/env python3
"""
validate_labels.py

Validates YOLO-format labels in a directory (image + .txt pairs).
Checks:
 - every image has a .txt label file
 - label file is not empty
 - each label line has 5+ tokens (class, x_center, y_center, w, h)
 - class index is in [0, nc-1]
 - coordinates are floats in [0,1]

Usage:
  python3 validate_labels.py --dir ./keras_frcnn/dataset/JPEGImages_train --nc 7
"""
import argparse
from pathlib import Path

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff"}

def is_image(p: Path):
    return p.suffix.lower() in IMAGE_EXTS

def check_folder(folder: Path, nc: int):
    imgs = sorted([p for p in folder.iterdir() if p.is_file() and is_image(p)])
    print(f"Checking {len(imgs)} images in {folder}")
    bad = 0
    for img in imgs:
        lbl = folder / (img.stem + ".txt")
        if not lbl.exists():
            print(f"[MISSING] {lbl} for image {img.name}")
            bad += 1
            continue
        lines = lbl.read_text().strip().splitlines()
        if len(lines) == 0:
            print(f"[EMPTY] {lbl} is empty")
            bad += 1
            continue
        for i, line in enumerate(lines, start=1):
            parts = line.strip().split()
            if len(parts) < 5:
                print(f"[BAD_FORMAT] {lbl} line {i}: less than 5 tokens -> {line}")
                bad += 1
                continue
            try:
                cls = int(parts[0])
                if cls < 0 or cls >= nc:
                    print(f"[BAD_CLASS] {lbl} line {i}: class {cls} out of range [0,{nc-1}]")
                    bad += 1
                coords = list(map(float, parts[1:5]))
                for c in coords:
                    if c < 0.0 or c > 1.0:
                        print(f"[BAD_COORD] {lbl} line {i}: coord {c} out of [0,1]")
                        bad += 1
            except Exception as e:
                print(f"[EXC] {lbl} line {i}: {e}")
                bad += 1
    print(f"Completed checks. Issues found: {bad}")
    return bad

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dir", "-d", required=True, help="folder to check (images + .txt)")
    parser.add_argument("--nc", type=int, required=True, help="number of classes (nc)")
    args = parser.parse_args()
    folder = Path(args.dir)
    if not folder.exists():
        print("Folder not found:", folder)
        return
    check_folder(folder, args.nc)

if __name__ == "__main__":
    main()
