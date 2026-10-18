#!/usr/bin/env python3
"""zone_fill_eval.py — вариант 3: доля заполнения как доля площади ЗОНЫ, занятая отходами.

Переразметки нет и не будет, поэтому связь «содержимое ↔ контейнер» недоступна.
Вариант 3 заменяет её фиксированной зоной: полигональная маска (уже построена, п. 5.2/6.3 ТЗ)
и есть контейнер. Тогда:

    доля заполнения = площадь объектов класса waste ВНУТРИ зоны / площадь зоны

Метрики (как принято для оценки площадей, а не IoU):
    MAE доли в процентных пунктах, отношение площадей, корреляция,
    сравнение с тривиальным прогнозом «средняя доля».
"""
import json
from pathlib import Path

import numpy as np
import torch
from ultralytics import YOLO

WEIGHTS = "/home/user2/baseline/runs/waste_up_blocksplit/weights/best.pt"
ROOT = Path("/home/user2/baseline/waste_up")
MASKS = Path("/home/user2/polygon_masks.json")
WASTE_CLASSES = {"waste"}
GRID = 640  # рабочее разрешение растеризации


def raster_polygon(points_norm, w, h, gw, gh):
    """Растеризация полигона (нормированные координаты) в булеву маску сетки."""
    from PIL import Image, ImageDraw
    img = Image.new("1", (gw, gh), 0)
    d = ImageDraw.Draw(img)
    xy = [(p[0] * gw, p[1] * gh) for p in points_norm]
    if len(xy) >= 3:
        d.polygon(xy, fill=1)
    return np.array(img, dtype=bool)


# используем ШТАТНУЮ функцию проекта, а не свою копию — иначе ключи камер не совпадут с масками
import sys
sys.path.insert(0, "/home/user2")
from stream_naming import series_of as _series_of


def series_of(name):
    """Камера по имени файла. Кадры лежат плоско, каталог отделён двойным подчёркиванием."""
    return _series_of(str(name).replace("__", "/"))


def main():
    masks = json.loads(MASKS.read_text(encoding="utf-8"))["cameras"]
    model = YOLO(WEIGHTS)
    names = model.names
    val_dir = ROOT / "images" / "val"
    lbl_dir = ROOT / "labels" / "val"
    images = sorted(p for p in val_dir.rglob("*")
                    if p.suffix.lower() in (".jpg", ".jpeg", ".png") and p.is_file())
    print(f"изображений: {len(images)}; камер в масках: {len(masks)}", flush=True)

    rows, skipped, unreadable = [], 0, []
    for i, img in enumerate(images):
        key = series_of(img.name)
        cam = masks.get(key)
        if cam is None:
            skipped += 1
            continue
        gw = gh = GRID
        zone = np.zeros((gh, gw), dtype=bool)
        for poly in cam["polygons"]:
            zone |= raster_polygon(poly, cam["frame"]["width"], cam["frame"]["height"], gw, gh)
        zone_area = zone.sum()
        if zone_area == 0:
            skipped += 1
            continue

        # истина: боксы waste из метки, пересечённые с зоной
        gt_mask = np.zeros((gh, gw), dtype=bool)
        lp = lbl_dir / f"{img.stem}.txt"
        if lp.exists():
            for line in lp.read_text().splitlines():
                p = line.split()
                if len(p) < 5 or names[int(p[0])] not in WASTE_CLASSES:
                    continue
                cx, cy, bw, bh = (float(v) for v in p[1:5])
                x1, y1 = int((cx - bw / 2) * gw), int((cy - bh / 2) * gh)
                x2, y2 = int((cx + bw / 2) * gw), int((cy + bh / 2) * gh)
                gt_mask[max(0, y1):min(gh, y2), max(0, x1):min(gw, x2)] = True
        gt_fill = (gt_mask & zone).sum() / zone_area

        # предсказание
        try:
            rr = model.predict(str(img), imgsz=640, conf=0.25, iou=0.6,
                               verbose=False, device=0)
            if not rr:
                raise ValueError("нет результата")
            r = rr[0]
        except Exception as e:
            # известно: 11 файлов Заказчика недоступны (Permission denied) — пропускаем, а не падаем
            unreadable.append(f"{img.name}: {type(e).__name__}")
            continue
        pr_mask = np.zeros((gh, gw), dtype=bool)
        if r.boxes is not None and len(r.boxes) > 0:
            xyxy = r.boxes.xyxy.cpu().numpy()
            cls = r.boxes.cls.cpu().numpy().astype(int)
            W, H = r.orig_shape[1], r.orig_shape[0]
            for (x1, y1, x2, y2), c in zip(xyxy, cls):
                if names[c] not in WASTE_CLASSES:
                    continue
                X1, Y1 = int(x1 / W * gw), int(y1 / H * gh)
                X2, Y2 = int(x2 / W * gw), int(y2 / H * gh)
                pr_mask[max(0, Y1):min(gh, Y2), max(0, X1):min(gw, X2)] = True
        pr_fill = (pr_mask & zone).sum() / zone_area

        rows.append({"image": str(img.relative_to(val_dir)), "camera": key,
                     "zone_area_frac": float(zone_area / (gw * gh)),
                     "gt_fill": float(gt_fill), "pred_fill": float(pr_fill)})
        if (i + 1) % 200 == 0:
            print(f"  обработано {i+1}/{len(images)}", flush=True)

    gt = np.array([r["gt_fill"] for r in rows])
    pr = np.array([r["pred_fill"] for r in rows])
    err = np.abs(pr - gt)
    mean_gt = gt.mean()
    res = {
        "images_used": len(rows), "images_skipped_no_mask": skipped,
        "images_unreadable": len(unreadable), "unreadable_examples": unreadable[:5],
        "mean_gt_fill": float(mean_gt), "mean_pred_fill": float(pr.mean()),
        "MAE_pp": float(err.mean() * 100), "median_AE_pp": float(np.median(err) * 100),
        "area_ratio": float(pr.sum() / max(gt.sum(), 1e-9)),
        "within_2pp": float((err <= 0.02).mean()),
        "within_5pp": float((err <= 0.05).mean()),
        "baseline_mean_MAE_pp": float(np.abs(gt - mean_gt).mean() * 100),
        "correlation": float(np.corrcoef(gt, pr)[0, 1]) if pr.std() > 0 else None,
    }
    nz = gt > 0.005
    if nz.sum():
        res["images_with_waste_in_zone"] = int(nz.sum())
        res["MAE_pp_where_waste"] = float(np.abs(pr[nz] - gt[nz]).mean() * 100)
        res["area_ratio_where_waste"] = float(pr[nz].sum() / gt[nz].sum())

    print("\n" + "=" * 70)
    print("ВАРИАНТ 3: ДОЛЯ ЗАПОЛНЕНИЯ ЗОНЫ ОТХОДАМИ")
    print("=" * 70)
    for k, v in res.items():
        print(f"  {k}: {v}")
    Path("/home/user2/zone_fill_result.json").write_text(
        json.dumps({"summary": res, "per_image": rows}, ensure_ascii=False, indent=2),
        encoding="utf-8")
    print("\nсохранено: /home/user2/zone_fill_result.json")
    print("ZONE_FILL_DONE")


if __name__ == "__main__":
    main()
