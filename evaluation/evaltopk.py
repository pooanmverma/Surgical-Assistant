# evaltopk.py
import json, sys
import numpy as np
from collections import defaultdict
from pycocotools.coco import COCO
from pycocotools.cocoeval import COCOeval

GT = "gt_coco_official.json"
GRASPER_ID = 1

def evaluate(gt, preds, label):
    json.dump(preds, open("_tmp_pred.json", "w"))
    ev = COCOeval(gt, gt.loadRes("_tmp_pred.json"), "bbox")
    ev.evaluate(); ev.accumulate(); ev.summarize()
    prec = ev.eval["precision"]                       # [T,R,K,A,M]
    names = [gt.cats[c]["name"] for c in ev.params.catIds]
    ap50, ap5095 = [], []
    for k in range(len(names)):
        p50 = prec[0, :, k, 0, 2]; p50 = p50[p50 > -1]
        pall = prec[:, :, k, 0, 2]; pall = pall[pall > -1]
        ap50.append(100 * p50.mean()); ap5095.append(100 * pall.mean())
    print(f"\n##### {label} | predictions: {len(preds)}")
    print("class      : " + " | ".join(names) + " | Overall")
    print("AP50       : " + " | ".join(f"{v:.1f}" for v in ap50) + f" | {100*ev.stats[1]:.1f}")
    print("AP50:95    : " + " | ".join(f"{v:.1f}" for v in ap5095) + f" | {100*ev.stats[0]:.1f}")

def main(pred_path):
    gt = COCO(GT)
    name2id = {i["file_name"].rsplit(".", 1)[0]: i["id"] for i in gt.dataset["images"]}
    preds = json.load(open(pred_path))
    for p in preds:
        if isinstance(p["image_id"], str):
            p["image_id"] = name2id[p["image_id"]]
        p.pop("file_name", None)

    evaluate(gt, preds, "RAW")

    by = defaultdict(list)
    for p in preds:
        by[(p["image_id"], p["category_id"])].append(p)
    kept = []
    for (img, cat), lst in by.items():
        lst.sort(key=lambda x: -x["score"])
        kept += lst[: 2 if cat == GRASPER_ID else 1]
    evaluate(gt, kept, "TOP-K")

if __name__ == "__main__":
    main(sys.argv[1])