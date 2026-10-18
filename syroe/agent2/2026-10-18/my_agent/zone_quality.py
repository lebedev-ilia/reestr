#!/usr/bin/env python3
"""zone_quality.py — проверка КАЧЕСТВА построенной зоны (проверка алгоритма маски).

Документированная практика требует проверять зону измеримым критерием, а не «на глаз».
Считаем: какая доля размеченных объектов попадает ВНУТРЬ зоны своей камеры.
Если зона построена правильно — подавляющая часть объектов должна быть внутри,
иначе зона отсекает полезные детекции.
"""
import json
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

sys.path.insert(0, "/home/user2")
from stream_naming import series_of as _series_of

ROOT = Path("/home/user2/baseline/waste_up")
MASKS = json.loads(Path("/home/user2/polygon_masks.json").read_text(encoding="utf-8"))["cameras"]
GRID = 640


def series_of(name):
    return _series_of(str(name).replace("__", "/"))


def raster(polys, gw, gh):
    img = Image.new("1", (gw, gh), 0)
    d = ImageDraw.Draw(img)
    for p in polys:
        xy = [(q[0] * gw, q[1] * gh) for q in p]
        if len(xy) >= 3:
            d.polygon(xy, fill=1)
    return np.array(img, dtype=bool)


def main():
    val = ROOT / "images" / "val"
    lbl = ROOT / "labels" / "val"
    images = sorted(p for p in val.iterdir() if p.suffix.lower() in (".jpg", ".jpeg", ".png"))
    zone_cache = {}
    total, inside, by_class = 0, 0, {}
    for img in images:
        key = series_of(img.name)
        cam = MASKS.get(key)
        lp = lbl / f"{img.stem}.txt"
        if cam is None or not lp.exists():
            continue
        if key not in zone_cache:
            zone_cache[key] = raster(cam["polygons"], GRID, GRID)
        zone = zone_cache[key]
        for line in lp.read_text().splitlines():
            p = line.split()
            if len(p) < 5:
                continue
            cx, cy = float(p[1]), float(p[2])
            gx, gy = int(cx * GRID), int(cy * GRID)
            gx, gy = min(max(gx, 0), GRID - 1), min(max(gy, 0), GRID - 1)
            hit = bool(zone[gy, gx])
            total += 1
            inside += hit

    print(f"проверено объектов: {total}")
    print(f"внутри зоны: {inside} ({inside/max(total,1)*100:.1f}%)")
    print(f"вне зоны:    {total-inside} ({(total-inside)/max(total,1)*100:.1f}%)")
    Path("/home/user2/zone_quality.json").write_text(json.dumps(
        {"objects_total": total, "objects_inside": inside,
         "inside_share": inside / max(total, 1)}, ensure_ascii=False, indent=2), encoding="utf-8")
    print("ZONE_QUALITY_DONE")


if __name__ == "__main__":
    main()
