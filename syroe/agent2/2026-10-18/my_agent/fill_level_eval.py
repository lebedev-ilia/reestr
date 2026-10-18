#!/usr/bin/env python3
"""fill_level_eval.py — оценка ЗАПОЛНЕННОСТИ контейнера (п. 6 ТЗ).

ТЗ формулирует задачу 2 как «сегментация контейнера и содержимого, вычисление доли заполнения».
Здесь считаем:
    доля заполнения = площадь содержимого (tko, treewaste, constrwaste, tires)
                      / площадь контейнера (dumpster)
для кадров, где контейнер найден.

Отдельно считаем ошибки в АБСОЛЮТНЫХ и ОТНОСИТЕЛЬНЫХ величинах: при малых долях абсолютная
ошибка выглядит хорошо, но относительная может быть большой — это надо показывать честно.
"""
import json
from pathlib import Path

import numpy as np
from ultralytics import YOLO

WEIGHTS = "/home/user2/baseline_seg/runs/dumpster_seg/weights/best.pt"
ROOT = Path("/home/user2/baseline_seg/dumpster")
IMGSZ = 640
CONTENT = {"tko", "treewaste", "constrwaste", "tires"}
CONTAINER = {"dumpster"}
CLS_ALL = ["tko", "treewaste", "tires", "constrwaste", "dumpster", "placedump"]


def poly_area(coords):
    pts = np.array(coords, dtype=float).reshape(-1, 2)
    x, y = pts[:, 0], pts[:, 1]
    return 0.5 * abs(np.dot(x, np.roll(y, -1)) - np.dot(y, np.roll(x, -1)))


def gt_by_class(label_path, names):
    out = {}
    if not label_path.exists():
        return out
    for line in label_path.read_text().splitlines():
        p = line.split()
        if len(p) < 7:
            continue
        c = names[int(p[0])]
        out[c] = out.get(c, 0.0) + poly_area([float(v) for v in p[1:]])
    return out


def main():
    model = YOLO(WEIGHTS)
    names = model.names
    val_dir, lbl_dir = ROOT / "images" / "val", ROOT / "labels" / "val"
    images = sorted(p for p in val_dir.iterdir()
                    if p.suffix.lower() in (".jpg", ".jpeg", ".png"))
    print(f"изображений: {len(images)}", flush=True)

    rows = []
    for i, img in enumerate(images):
        gt = gt_by_class(lbl_dir / f"{img.stem}.txt", names)
        r = model.predict(str(img), imgsz=IMGSZ, conf=0.25, iou=0.6,
                          retina_masks=True, verbose=False, device=0)[0]
        pr = {}
        if r.masks is not None and len(r.masks) > 0:
            masks = r.masks.data.cpu().numpy()
            cls = r.boxes.cls.cpu().numpy().astype(int)
            h, w = masks.shape[1], masks.shape[2]
            for c in set(cls):
                sel = cls == c
                u = np.zeros((h, w), dtype=bool)
                for m in masks[sel]:
                    u |= (m > 0.5)
                pr[names[c]] = u.sum() / (h * w)

        gt_c = sum(v for k, v in gt.items() if k in CONTAINER)
        pr_c = sum(v for k, v in pr.items() if k in CONTAINER)
        gt_w = sum(v for k, v in gt.items() if k in CONTENT)
        pr_w = sum(v for k, v in pr.items() if k in CONTENT)
        rows.append({
            "image": img.name,
            "gt_container": gt_c, "pred_container": pr_c,
            "gt_content": gt_w, "pred_content": pr_w,
            "gt_fill": (gt_w / gt_c) if gt_c > 1e-6 else None,
            "pred_fill": (pr_w / pr_c) if pr_c > 1e-6 else None,
            "gt_classes": {k: round(v, 5) for k, v in gt.items()},
            "pred_classes": {k: round(v, 5) for k, v in pr.items()},
        })
        if (i + 1) % 200 == 0:
            print(f"  обработано {i+1}/{len(images)}", flush=True)

    res = {"images": len(rows)}

    # 1. площадь контейнера
    gc = np.array([r["gt_container"] for r in rows])
    pc = np.array([r["pred_container"] for r in rows])
    has_c = gc > 0.005
    res["images_with_container_gt"] = int(has_c.sum())
    if has_c.sum():
        res["container_MAE_pp"] = float(np.abs(pc[has_c] - gc[has_c]).mean() * 100)
        res["container_area_ratio"] = float(pc[has_c].sum() / gc[has_c].sum())

    # 2. площадь содержимого
    gw = np.array([r["gt_content"] for r in rows])
    pw = np.array([r["pred_content"] for r in rows])
    has_w = gw > 0.005
    res["images_with_content_gt"] = int(has_w.sum())
    if has_w.sum():
        res["content_MAE_pp"] = float(np.abs(pw[has_w] - gw[has_w]).mean() * 100)
        res["content_area_ratio"] = float(pw[has_w].sum() / gw[has_w].sum())

    # 3. доля заполнения — только там, где есть и контейнер, и содержимое в истине
    both = has_c & has_w
    res["images_for_fill"] = int(both.sum())
    if both.sum():
        gf = np.array([rows[i]["gt_fill"] for i in np.where(both)[0]], dtype=float)
        pf = np.array([rows[i]["pred_fill"] if rows[i]["pred_fill"] is not None else 0.0
                       for i in np.where(both)[0]], dtype=float)
        err = pf - gf
        res["fill_MAE_pp"] = float(np.abs(err).mean() * 100)
        res["fill_bias_pp"] = float(err.mean() * 100)
        res["fill_within_10pp"] = float((np.abs(err) <= 0.10).mean())
        res["fill_within_20pp"] = float((np.abs(err) <= 0.20).mean())
        res["fill_correlation"] = float(np.corrcoef(gf, pf)[0, 1]) if pf.std() > 0 else None
        res["fill_mean_gt"] = float(gf.mean())
        res["fill_mean_pred"] = float(pf.mean())
        # относительная ошибка — честная картина при малых долях
        nz = gf > 0.05
        if nz.sum():
            rel = np.abs(err[nz]) / gf[nz]
            res["fill_median_relative_error"] = float(np.median(rel))
            res["fill_relative_within_25pct"] = float((rel <= 0.25).mean())

    print("\n" + "=" * 70)
    print("ЗАПОЛНЕННОСТЬ КОНТЕЙНЕРА (п. 6 ТЗ)")
    print("=" * 70)
    for k, v in res.items():
        print(f"  {k}: {v}")
    Path("/home/user2/fill_level_result.json").write_text(
        json.dumps({"summary": res, "per_image": rows}, ensure_ascii=False, indent=2),
        encoding="utf-8")
    print("\nсохранено: /home/user2/fill_level_result.json")
    print("FILL_EVAL_DONE")


if __name__ == "__main__":
    main()
