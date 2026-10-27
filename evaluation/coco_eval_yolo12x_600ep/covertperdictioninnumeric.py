#!/usr/bin/env python3
"""
Convert Ultralytics prediction image_ids (file names) to numeric COCO image_ids
using the ground-truth JSON.

FIXES vs original:
  1. Extension mismatch: GT file_name is "v07_031525.jpg" but Ultralytics image_id is
     "v07_031525" (no extension). We match by stripping ".jpg" on BOTH sides, so nothing
     is silently dropped.
  2. Unmatched predictions are now EXCLUDED from the output (previously their image_id
     stayed a string, corrupting the COCO eval).
  3. Robust: builds the lookup with and without extension.
"""

import json
import os

# ------------------------------------------------------------------ paths
# ------------------------------------------------------------------ paths
GT_JSON = "./gt_coco_official.json"

PRED_JSON = "./runs/official_detect/yolo12x_test_eval_verify/predictions.json"

OUTPUT_JSON = "./runs/official_detect/yolo12x_test_eval_verify/predictions_numeric.json"

# ------------------------------------------------------------------ load GT
with open(GT_JSON, "r") as f:
    gt = json.load(f)

# Build a lookup that matches regardless of extension.
# key forms stored: "v07_031525.jpg" AND "v07_031525"
name_to_id = {}
for img in gt["images"]:
    fn = img["file_name"]
    stem = os.path.splitext(fn)[0]
    name_to_id[fn] = img["id"]
    name_to_id[stem] = img["id"]

print(f"GT images : {len(gt['images'])}")

# ------------------------------------------------------------------ load preds
with open(PRED_JSON, "r") as f:
    preds = json.load(f)
print(f"Predictions (raw) : {len(preds)}")

# ------------------------------------------------------------------ convert
converted = []
missing = set()

def lookup_id(p):
    # try image_id, then file_name, with and without extension
    candidates = []
    if "image_id" in p:
        candidates.append(str(p["image_id"]))
        candidates.append(os.path.splitext(str(p["image_id"]))[0])
    if "file_name" in p:
        candidates.append(str(p["file_name"]))
        candidates.append(os.path.splitext(str(p["file_name"]))[0])
    for c in candidates:
        if c in name_to_id:
            return name_to_id[c]
    return None

for p in preds:
    nid = lookup_id(p)
    if nid is None:
        missing.add(str(p.get("image_id", p.get("file_name", "?"))))
        continue
    q = dict(p)
    q["image_id"] = nid
    # COCO eval only needs image_id, category_id, bbox, score. Drop file_name if present.
    q.pop("file_name", None)
    converted.append(q)

# ------------------------------------------------------------------ save
os.makedirs(os.path.dirname(OUTPUT_JSON), exist_ok=True)
with open(OUTPUT_JSON, "w") as f:
    json.dump(converted, f)

print("\n-------------------------------------")
print("Conversion complete")
print("-------------------------------------")
print(f"Converted predictions : {len(converted)}")
print(f"Dropped (unmatched)   : {len(preds) - len(converted)}")
print(f"Unique missing names  : {len(missing)}")
if missing:
    print("\nFirst missing names:")
    for x in sorted(missing)[:20]:
        print("  ", x)

print("\nSaved:", OUTPUT_JSON)
print("\nSanity: converted count should now match raw pred count (minus any genuinely")
print("out-of-GT predictions). If dropped=0, every prediction mapped correctly.")