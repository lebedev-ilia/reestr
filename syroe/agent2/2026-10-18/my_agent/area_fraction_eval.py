#!/usr/bin/env python3
"""area_fraction_eval.py — проверка: можно ли надёжно оценить ДОЛЮ ПЛОЩАДИ отходов.

Зачем: класс `waste` в детекции даёт mAP@0,5:0,95 = 0,725 и никакими приёмами не вытягивается
(проверены 200 эпох, copy_paste, mixup, cls_pw, тяжёлая модель). ТЗ (п. 6) при этом требует
не детекцию отходов, а «сегментацию контейнера и содержимого, вычисление доли заполнения».

Ключевая мысль: для ОЦЕНКИ ПЛОЩАДИ метрика IoU/mAP не подходит — она измеряет совпадение
каждого объекта, а нам нужна суммарная площадь. Ошибки на отдельных объектах частично
взаимно компенсируются, поэтому доля площади может считаться хорошо даже при посредственном mAP.

Метрики взяты из практики оценки сегментации площадей (не IoU):
  - абсолютная ошибка доли (MAE), в процентных пунктах;
  - отношение предсказанной площади к истинной (area ratio), в идеале 1,0;
  - доля изображений, где ошибка доли меньше 5 и 10 п.п.;
  - сравнение с тривиальным прогнозом «средняя доля по выборке» — чтобы показать,
    что модель действительно что-то даёт, а не просто угадывает среднее.

Классы отходов (аморфные): tko, treewaste, constrwaste, tires.
"""
import json
from pathlib import Path

import numpy as np
from ultralytics import YOLO

WEIGHTS = "/home/user2/baseline_seg/runs/dumpster_seg/weights/best.pt"
ROOT = Path("/home/user2/baseline_seg/dumpster")
IMGSZ = 640
WASTE = {"tko", "treewaste", "constrwaste", "tires"}


def polygon_area_norm(coords):
    """Площадь полигона в нормированных координатах (формула шнуровки)."""
    pts = np.array(coords, dtype=float).reshape(-1, 2)
    x, y = pts[:, 0], pts[:, 1]
    return 0.5 * abs(np.dot(x, np.roll(y, -1)) - np.dot(y, np.roll(x, -1)))


def gt_areas(label_path, names):
    """Истинные площади по классам из метки YOLO-seg (доли площади изображения)."""
    out = {}
    if not label_path.exists():
        return out
    for line in label_path.read_text().splitlines():
        p = line.split()
        if len(p) < 7:
            continue
        cls = names[int(p[0])]
        out[cls] = out.get(cls, 0.0) + polygon_area_norm([float(v) for v in p[1:]])
    return out


def main():
    model = YOLO(WEIGHTS)
    names = model.names
    val_dir = ROOT / "images" / "val"
    lbl_dir = ROOT / "labels" / "val"
    images = sorted(p for p in val_dir.iterdir()
                    if p.suffix.lower() in (".jpg", ".jpeg", ".png"))
    print(f"изображений: {len(images)}; классы: {names}", flush=True)

    rows = []
    for i, img in enumerate(images):
        gt = gt_areas(lbl_dir / f"{img.stem}.txt", names)
        gt_frac = sum(v for k, v in gt.items() if k in WASTE)

        r = model.predict(str(img), imgsz=IMGSZ, conf=0.25, iou=0.6,
                          retina_masks=True, verbose=False, device=0)[0]
        pr_frac = 0.0
        if r.masks is not None and len(r.masks) > 0:
            masks = r.masks.data.cpu().numpy()
            cls = r.boxes.cls.cpu().numpy().astype(int)
            h, w = masks.shape[1], masks.shape[2]
            for m, c in zip(masks, cls):
                if names[c] in WASTE:
                    # объединяем маски одного класса без двойного счёта
                    pass
            union = np.zeros((h, w), dtype=bool)
            for m, c in zip(masks, cls):
                if names[c] in WASTE:
                    union |= (m > 0.5)
            pr_frac = union.sum() / (h * w)

        rows.append({"image": img.name, "gt": float(gt_frac), "pred": float(pr_frac)})
        if (i + 1) % 100 == 0:
            print(f"  обработано {i+1}/{len(images)}", flush=True)

    gt = np.array([r["gt"] for r in rows])
    pr = np.array([r["pred"] for r in rows])
    err = np.abs(pr - gt)
    mean_gt = gt.mean()

    res = {
        "images": len(rows),
        "waste_classes": sorted(WASTE),
        "mean_gt_fraction": float(mean_gt),
        "mean_pred_fraction": float(pr.mean()),
        "MAE_pp": float(err.mean() * 100),
        "median_AE_pp": float(np.median(err) * 100),
        "area_ratio": float(pr.sum() / max(gt.sum(), 1e-9)),
        "within_5pp": float((err <= 0.05).mean()),
        "within_10pp": float((err <= 0.10).mean()),
        "baseline_mean_MAE_pp": float(np.abs(gt - mean_gt).mean() * 100),
        "correlation": float(np.corrcoef(gt, pr)[0, 1]) if pr.std() > 0 else None,
    }
    # по подвыборкам: где отходы вообще есть
    nz = gt > 0.01
    if nz.sum() > 0:
        res["MAE_pp_where_waste"] = float(np.abs(pr[nz] - gt[nz]).mean() * 100)
        res["area_ratio_where_waste"] = float(pr[nz].sum() / gt[nz].sum())
        res["images_with_waste"] = int(nz.sum())

    print("\n" + "=" * 70)
    print("ОЦЕНКА ДОЛИ ПЛОЩАДИ ОТХОДОВ")
    print("=" * 70)
    for k, v in res.items():
        print(f"  {k}: {v}")
    Path("/home/user2/area_fraction_result.json").write_text(
        json.dumps({"summary": res, "per_image": rows}, ensure_ascii=False, indent=2),
        encoding="utf-8")
    print("\nсохранено: /home/user2/area_fraction_result.json")
    print("AREA_EVAL_DONE")


if __name__ == "__main__":
    main()
