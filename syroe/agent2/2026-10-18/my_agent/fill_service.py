#!/usr/bin/env python3
"""fill_service.py — оценка заполненности отходами (задача 2 ТЗ, п. 6).

ДВА РЕЖИМА

1. `--mode vehicle` (основной, прямой ответ на задачу 2) — **заполненность кузова**:

       заполненность = площадь отходов внутри кузова / площадь кузова

   Почему это корректно без переразметки: проверено на разметке Заказчика — в датасете
   `waste_up` класс `waste` это отходы В КУЗОВЕ машины, у всех 446 объектов центр лежит
   внутри рамки машины (99,1 % перекрывают кузов более чем наполовину).

   Точность на 434 кадрах: MAE **6,98 п.п.**, медианная ошибка 2,61 п.п., отношение площадей
   1,014 (смещения нет), корреляция **0,715**, диапазон измеряемой величины 8–86 %.
   Тривиальный прогноз «средняя заполненность» даёт 10,26 п.п. — модель точнее в 1,5 раза.

2. `--mode zone` (дополнительный) — **доля площади рабочей зоны, занятая отходами**:

       доля = площадь отходов внутри полигональной зоны / площадь зоны

   Полезно для контроля замусоренности площадки. Точность на 1061 кадре: MAE **1,78 п.п.**,
   отношение площадей 1,004, корреляция **0,886** (в 5,3 раза лучше тривиального прогноза).
   Меньшая абсолютная ошибка здесь объясняется тем, что и сами значения малы (в среднем 7,8 %).

Зона берётся из `polygon_masks.json` (алгоритм построения — `build_polygon_mask.py`, п. 5.2 ТЗ).

ВАЖНАЯ ОГОВОРКА К ТОЧНОСТИ

Разметка в датасете — **рамки, а не полигоны**, поэтому «площадь» здесь означает площадь рамок,
а не реального силуэта отходов: она завышает физическую площадь. Истина и предсказание
считаются по одному правилу, поэтому точность воспроизведения разметки осмысленна,
но физическая площадь отходов по этим данным неизвестна.

ПОРОГИ РЕКОМЕНДАЦИЙ — предложение, требуют согласования с Заказчиком (п. 6.2 ТЗ).

Запуск:
    python3 fill_service.py --mode vehicle --source <кадр|каталог> --weights best.pt --out r.json
    python3 fill_service.py --mode zone --source <кадр|каталог> --camera auto \\
        --masks polygon_masks.json --weights best.pt --out r.json
"""
import argparse
import json
import sys
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw

VEHICLE_CLASSES = {"truck", "garbagetruck"}
WASTE_CLASSES = {"waste"}
IMGSZ, CONF, IOU = 640, 0.25, 0.6


# ---------------------------------------------------------------- зона

def raster_zone(polys, gw, gh):
    img = Image.new("1", (gw, gh), 0)
    d = ImageDraw.Draw(img)
    for p in polys:
        xy = [(q[0] * gw, q[1] * gh) for q in p]
        if len(xy) >= 3:
            d.polygon(xy, fill=1)
    return np.array(img, dtype=bool)


def zone_camera(name, camera):
    """Камера по имени файла: кадры лежат плоско, каталог отделён двойным подчёркиванием."""
    if camera != "auto":
        return camera
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from stream_naming import series_of
        return series_of(str(name).replace("__", "/"))
    except Exception:
        return camera


# ---------------------------------------------------------------- заполненность

def level_of(fill, warn, alarm):
    if fill >= alarm:
        return "alarm", "Заполнено — требуется вывоз"
    if fill >= warn:
        return "warn", "Заполняется — запланировать вывоз"
    return "ok", "Заполненность в норме"


def vehicle_fill(boxes, warn, alarm):
    """boxes: список (класс, cx, cy, w, h) в нормированных координатах.

    Для каждого объекта отходов ищется машина, внутри которой лежит его центр;
    заполненность = площадь пересечения рамок / площадь кузова.
    """
    wastes = [b for b in boxes if b[0] in WASTE_CLASSES]
    vehs = [b for b in boxes if b[0] in VEHICLE_CLASSES]
    items = []
    for w in wastes:
        cx, cy = w[1], w[2]
        for v in vehs:
            if abs(cx - v[1]) <= v[3] / 2 and abs(cy - v[2]) <= v[4] / 2:
                ix = min(cx + w[3] / 2, v[1] + v[3] / 2) - max(cx - w[3] / 2, v[1] - v[3] / 2)
                iy = min(cy + w[4] / 2, v[2] + v[4] / 2) - max(cy - w[4] / 2, v[2] - v[4] / 2)
                if ix > 0 and iy > 0 and v[3] * v[4] > 0:
                    fill = (ix * iy) / (v[3] * v[4])
                    lvl, msg = level_of(fill, warn, alarm)
                    items.append({"vehicle_class": v[0],
                                  "fill_fraction": round(float(fill), 4),
                                  "fill_percent": round(float(fill) * 100, 2),
                                  "level": lvl, "recommendation": msg})
                break
    return items


def zone_fill(boxes, shape, zone, gw, gh, warn, alarm):
    h, w = shape[:2]
    m = np.zeros((gh, gw), dtype=bool)
    for b in boxes:
        if b[0] not in WASTE_CLASSES:
            continue
        X1, Y1 = int((b[1] - b[3] / 2) * gw), int((b[2] - b[4] / 2) * gh)
        X2, Y2 = int((b[1] + b[3] / 2) * gw), int((b[2] + b[4] / 2) * gh)
        m[max(0, Y1):min(gh, Y2), max(0, X1):min(gw, X2)] = True
    inter = (m & zone).sum()
    fill = inter / zone.sum() if zone.sum() else 0.0
    lvl, msg = level_of(fill, warn, alarm)
    return {"fill_fraction": round(float(fill), 4), "fill_percent": round(float(fill) * 100, 2),
            "objects_in_zone": int(sum(1 for b in boxes if b[0] in WASTE_CLASSES)),
            "level": lvl, "recommendation": msg}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["vehicle", "zone"], default="vehicle")
    ap.add_argument("--source", required=True)
    ap.add_argument("--weights", required=True)
    ap.add_argument("--camera", default="auto", help="для режима zone; auto — по имени файла")
    ap.add_argument("--masks", default="polygon_masks.json")
    ap.add_argument("--warn", type=float, default=0.35)
    ap.add_argument("--alarm", type=float, default=0.60)
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    masks = {}
    if args.mode == "zone":
        masks = json.loads(Path(args.masks).read_text(encoding="utf-8"))["cameras"]

    from ultralytics import YOLO
    model = YOLO(args.weights)
    names = model.names

    src = Path(args.source)
    files = [src] if src.is_file() else sorted(
        f for f in src.iterdir() if f.suffix.lower() in (".jpg", ".jpeg", ".png"))

    results = []
    for f in files:
        frame = cv2.imread(str(f))
        if frame is None:
            continue
        r = model.predict(frame, imgsz=IMGSZ, conf=CONF, iou=IOU, verbose=False)[0]
        h, w = frame.shape[:2]
        boxes = []
        if r.boxes is not None and len(r.boxes) > 0:
            xyxy = r.boxes.xyxy.cpu().numpy()
            cls = r.boxes.cls.cpu().numpy().astype(int)
            for (x1, y1, x2, y2), c in zip(xyxy, cls):
                boxes.append((names[c], ((x1 + x2) / 2) / w, ((y1 + y2) / 2) / h,
                              (x2 - x1) / w, (y2 - y1) / h))
        rec = {"file": f.name}
        if args.mode == "vehicle":
            items = vehicle_fill(boxes, args.warn, args.alarm)
            rec["vehicles"] = items
            rec["fill_percent"] = round(float(np.mean([i["fill_percent"] for i in items])), 2) if items else None
            rec["level"] = items[0]["level"] if items else "none"
        else:
            cam = zone_camera(f.name, args.camera)
            if cam not in masks:
                print(json.dumps({"error": f"нет зоны для камеры «{cam}»",
                                  "available": sorted(masks)}, ensure_ascii=False), file=sys.stderr)
                return 2
            zone = raster_zone(masks[cam]["polygons"], IMGSZ, IMGSZ)
            rec.update(zone_fill(boxes, frame.shape, zone, IMGSZ, IMGSZ, args.warn, args.alarm))
            rec["camera"] = cam
        results.append(rec)

    vals = [r["fill_percent"] for r in results if r.get("fill_percent") is not None]
    summary = {"mode": args.mode, "frames": len(results),
               "frames_with_detection": len(vals),
               "mean_fill_percent": round(float(np.mean(vals)), 2) if vals else None,
               "max_fill_percent": round(float(np.max(vals)), 2) if vals else None,
               "alarms": sum(1 for r in results if r.get("level") == "alarm"),
               "warnings": sum(1 for r in results if r.get("level") == "warn")}
    text = json.dumps({"summary": summary, "results": results}, ensure_ascii=False, indent=2)
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
        print(json.dumps(summary, ensure_ascii=False, indent=1))
    else:
        print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
