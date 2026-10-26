#!/usr/bin/env python3
"""
prepare_official_split.py

Creates an official benchmark split for YOLO training.

Official protocol:
    Train : 1405 original + flipped images
    Val   : 843 original images
    Test  : 563 original images

Unlike split_labeled_dataset.py, this script DOES NOT randomly shuffle
the dataset. Instead, it uses the official ImageSets/Main split.

Author: Project Contributor
"""

import shutil
from pathlib import Path
import yaml
from datetime import datetime

# ------------------------------------------------------------------
# CONFIGURATION
# ------------------------------------------------------------------

BASE_DIR = Path("./keras_frcnn/dataset")

IMG_DIR = BASE_DIR / "JPEGImages"

IMAGESET_DIR = BASE_DIR / "ImageSets" / "Main"

LABEL_EXT = ".txt"

OUT_TRAIN = BASE_DIR / "JPEGImages_train_official"
OUT_VAL = BASE_DIR / "JPEGImages_val_official"
OUT_TEST = BASE_DIR / "JPEGImages_test_official"

YAML_FILE = "surgical_tools.yaml"

NEW_YAML = "official_split.yaml"

IMAGE_EXT = ".jpg"

# ------------------------------------------------------------------


def read_split(filename):
    with open(IMAGESET_DIR / filename, "r") as f:
        return [x.strip() for x in f if x.strip()]


def safe_copy(src, dst):
    if src.exists():
        shutil.copy2(src, dst)


def prepare_output_folder(folder):
    if folder.exists():
        shutil.rmtree(folder)
    folder.mkdir(parents=True, exist_ok=True)


def copy_image_and_label(image_stem, destination, include_flip=False):

    # -----------------------
    # original image
    # -----------------------
    img = IMG_DIR / f"{image_stem}{IMAGE_EXT}"
    lbl = IMG_DIR / f"{image_stem}{LABEL_EXT}"

    safe_copy(img, destination / img.name)
    safe_copy(lbl, destination / lbl.name)

    # -----------------------
    # flipped image
    # -----------------------
    if include_flip:

        flip_img = IMG_DIR / f"{image_stem}_flip{IMAGE_EXT}"
        flip_lbl = IMG_DIR / f"{image_stem}_flip{LABEL_EXT}"

        safe_copy(flip_img, destination / flip_img.name)
        safe_copy(flip_lbl, destination / flip_lbl.name)


def main():

    print("=" * 70)
    print("Preparing OFFICIAL benchmark split")
    print("=" * 70)

    prepare_output_folder(OUT_TRAIN)
    prepare_output_folder(OUT_VAL)
    prepare_output_folder(OUT_TEST)

    train_ids = read_split("train.txt")
    val_ids = read_split("val.txt")
    test_ids = read_split("test.txt")

    print(f"Official Train : {len(train_ids)}")
    print(f"Official Val   : {len(val_ids)}")
    print(f"Official Test  : {len(test_ids)}")
    print()

    # -------------------------------------------------------
    # TRAIN
    # -------------------------------------------------------

    train_flip_count = 0

    for img_id in train_ids:

        copy_image_and_label(
            img_id,
            OUT_TRAIN,
            include_flip=True
        )

        if (IMG_DIR / f"{img_id}_flip.jpg").exists():
            train_flip_count += 1

    # -------------------------------------------------------
    # VAL
    # -------------------------------------------------------

    for img_id in val_ids:

        copy_image_and_label(
            img_id,
            OUT_VAL,
            include_flip=False
        )

    # -------------------------------------------------------
    # TEST
    # -------------------------------------------------------

    for img_id in test_ids:

        copy_image_and_label(
            img_id,
            OUT_TEST,
            include_flip=False
        )

    # -------------------------------------------------------
    # YAML
    # -------------------------------------------------------

    with open(YAML_FILE, "r") as f:
        old_yaml = yaml.safe_load(f)

    new_yaml = {
        "path": str(BASE_DIR),
        "train": "JPEGImages_train_official",
        "val": "JPEGImages_val_official",
        "test": "JPEGImages_test_official",
        "nc": old_yaml["nc"],
        "names": old_yaml["names"],
    }

    with open(NEW_YAML, "w") as f:
        yaml.dump(new_yaml, f, sort_keys=False)

    # -------------------------------------------------------
    # SUMMARY
    # -------------------------------------------------------

    train_imgs = len(list(OUT_TRAIN.glob("*.jpg")))
    val_imgs = len(list(OUT_VAL.glob("*.jpg")))
    test_imgs = len(list(OUT_TEST.glob("*.jpg")))

    print("=" * 70)
    print("SUMMARY")
    print("=" * 70)

    print(f"Original train images : {len(train_ids)}")
    print(f"Training flips        : {train_flip_count}")
    print(f"Final train images    : {train_imgs}")
    print()

    print(f"Validation images     : {val_imgs}")
    print(f"Test images           : {test_imgs}")
    print()

    print(f"YAML written to : {NEW_YAML}")

    print("=" * 70)
    print("Done.")
    print("=" * 70)


if __name__ == "__main__":
    main()