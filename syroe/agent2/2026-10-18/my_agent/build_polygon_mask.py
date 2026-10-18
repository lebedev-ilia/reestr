#!/usr/bin/env python3
"""
build_polygon_mask.py — построение полигональных масок (ROI) по камерам.

По ТЗ (п. 5.2) «полигональная маска — заранее определённая область кадра, в пределах
которой выполняется детекция/оценка», и мы должны **разработать алгоритм её построения**
(п. 5.2, а также передать «описание алгоритма построения полигональных масок» — п. 103).

Алгоритм (по данным разметки, воспроизводимый, без ручного рисования):

  1. Разметка группируется по камерам (сериям) — у каждой камеры своя зона.
  2. Для каждой серии все размеченные объекты (полигоны/рамки) растрируются в общий
     аккумулятор: считаем, сколько кадров покрывают каждый пиксель.
  3. Нормализуем на число кадров → «карта активности»: где объекты появляются устойчиво.
  4. Порог по доле кадров: пиксель входит в зону, если объекты покрывали его
     не реже, чем в `--coverage` доле кадров (отсекает случайные выбросы разметки).
  5. Морфологическая чистка: закрытие дыр, удаление мелких островов, заполнение разрывов.
  6. Извлекаем контур (marching squares) и упрощаем его алгоритмом Дугласа-Пекера
     до полигона с приемлемым числом точек (`--epsilon`).
  7. Результат — полигон(ы) в нормализованных координатах (0..1) на кадр камеры,
     плюс картинка-оверлей для согласования с Заказчиком (ТЗ требует «согласование/уточнение»).

Применение маски (отсечение детекций вне зоны) реализовано функцией point_in_polygon.

Запуск:
  python3 build_polygon_mask.py --zip <архив CVAT> --out <каталог>
      [--coverage 0.02] [--grid 512] [--epsilon 0.008] [--min-area 0.01]
"""
import argparse
import collections
import json
import math
import re
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

import numpy as np
from scipy import ndimage
from skimage import draw, measure

SHAPE_TAGS = ("box", "polygon", "polyline", "points", "ellipse", "mask")

# определение потока/кадра — в отдельном модуле без тяжёлых зависимостей
from stream_naming import series_of, frame_number  # noqa: E402


def load_images(zip_path: Path):
    """Возвращает список (имя, ширина, высота, [(метка, [(x,y)...])])."""
    with zipfile.ZipFile(zip_path) as zf:
        names = [n for n in zf.namelist() if n.endswith(".xml")]
        if not names:
            raise SystemExit(f"в {zip_path} не найден xml")
        root = ET.fromstring(zf.read(names[0]))

    def poly_of(el):
        if el.tag == "box":
            x1, y1 = float(el.get("xtl")), float(el.get("ytl"))
            x2, y2 = float(el.get("xbr")), float(el.get("ybr"))
            if x1 > x2:
                x1, x2 = x2, x1
            if y1 > y2:
                y1, y2 = y2, y1
            return [(x1, y1), (x2, y1), (x2, y2), (x1, y2)]
        pts = el.get("points")
        if not pts:
            return []
        out = []
        for pair in pts.split(";"):
            if not pair.strip():
                continue
            p = pair.split(",")
            if len(p) >= 2:
                out.append((float(p[0]), float(p[1])))
        return out

    images = []
    for im in root.findall("image"):
        objs = []
        for el in list(im):
            if el.tag not in SHAPE_TAGS:
                continue
            pts = poly_of(el)
            if len(pts) >= 3:
                objs.append((el.get("label"), pts))
        images.append((im.get("name"), float(im.get("width")), float(im.get("height")), objs))
    return images


def points_in_polygon(pts, poly):
    """Векторный тест «точка внутри полигона» (ray casting)."""
    pts = np.asarray(pts, dtype=float)
    poly = np.asarray(poly, dtype=float)
    x, y = pts[:, 0], pts[:, 1]
    inside = np.zeros(len(pts), dtype=bool)
    n = len(poly)
    j = n - 1
    for i in range(n):
        xi, yi = poly[i]
        xj, yj = poly[j]
        cond = ((yi > y) != (yj > y)) & (x < (xj - xi) * (y - yi) / (yj - yi + 1e-12) + xi)
        inside ^= cond
        j = i
    return inside


def build_for_series(images, grid, coverage, epsilon, min_area):
    """Строит маску для одной камеры. images — список кадров этой камеры."""
    # 1. Размер кадра — самый частый
    sizes = collections.Counter((round(w), round(h)) for _, w, h, _ in images)
    W, H = sizes.most_common(1)[0][0]
    scale = grid / max(W, H)
    gw, gh = max(2, int(round(W * scale))), max(2, int(round(H * scale)))

    # 2. Аккумулятор покрытия
    acc = np.zeros((gh, gw), dtype=np.float32)
    n_frames = 0
    for _, w, h, objs in images:
        if not objs:
            continue
        n_frames += 1
        sx, sy = gw / w, gh / h
        frame_mask = np.zeros((gh, gw), dtype=bool)
        for _, pts in objs:
            xs = np.clip(np.array([p[0] for p in pts]) * sx, 0, gw - 1)
            ys = np.clip(np.array([p[1] for p in pts]) * sy, 0, gh - 1)
            rr, cc = draw.polygon(ys, xs, shape=(gh, gw))
            frame_mask[rr, cc] = True
        acc += frame_mask
    if n_frames == 0:
        return None
    coverage_map = acc / float(n_frames)

    # 3. Порог по устойчивости + морфология
    zone = coverage_map >= coverage
    zone = ndimage.binary_closing(zone, structure=np.ones((5, 5)), iterations=2)
    zone = ndimage.binary_fill_holes(zone)
    zone = ndimage.binary_opening(zone, structure=np.ones((3, 3)))
    lbl, n = ndimage.label(zone)
    if n > 1:  # оставляем компоненты не меньше min_area от общей площади зоны
        sizes_lbl = ndimage.sum(zone, lbl, range(1, n + 1))
        keep = {i + 1 for i, s in enumerate(sizes_lbl) if s >= min_area * zone.sum()}
        zone = np.isin(lbl, list(keep)) if keep else zone
        zone = ndimage.binary_fill_holes(zone)

    # 4. Контуры + упрощение Дугласа-Пекера
    polys = []
    if zone.any():
        for cont in measure.find_contours(zone.astype(float), 0.5):
            if len(cont) < 8:
                continue
            # tolerance в пикселях сетки: epsilon от длинной стороны
            tol = max(1.0, epsilon * max(gw, gh))
            approx = measure.approximate_polygon(cont, tolerance=tol)
            if len(approx) < 4:
                continue
            # контур в (row, col) -> (x, y) нормализованные
            poly = [(float(c) / gw, float(r) / gh) for r, c in approx]
            area = abs(sum(poly[i][0] * poly[(i + 1) % len(poly)][1]
                           - poly[(i + 1) % len(poly)][0] * poly[i][1]
                           for i in range(len(poly)))) / 2.0
            if area < min_area:
                continue
            polys.append({"points": poly, "area": area})
    polys.sort(key=lambda p: -p["area"])

    return {
        "frame": {"width": W, "height": H},
        "grid": {"w": gw, "h": gh},
        "frames_used": n_frames,
        "coverage_map": coverage_map,
        "polygons": polys,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--zip", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--coverage", type=float, default=0.02,
                    help="минимальная доля кадров, покрывающих пиксель (устойчивость)")
    ap.add_argument("--grid", type=int, default=512, help="длинная сторона рабочей сетки")
    ap.add_argument("--epsilon", type=float, default=0.008, help="допуск упрощения контура")
    ap.add_argument("--min-area", type=float, default=0.01, help="минимальная площадь полигона")
    args = ap.parse_args()

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    images = load_images(Path(args.zip))

    by_series = collections.defaultdict(list)
    for rec in images:
        by_series[series_of(rec[0])].append(rec)

    result = {"source": str(args.zip), "coverage_threshold": args.coverage,
              "epsilon": args.epsilon, "cameras": {}}

    print(f"камер (серий): {len(by_series)}")
    print(f"{'камера':<18}{'кадров':>8}{'полигонов':>11}{'точек':>8}{'площадь зоны':>14}")
    for series, recs in sorted(by_series.items(), key=lambda kv: -len(kv[1])):
        r = build_for_series(recs, args.grid, args.coverage, args.epsilon, args.min_area)
        if r is None:
            continue
        polys = r.pop("polygons")
        cov = r.pop("coverage_map")
        area = sum(p["area"] for p in polys)
        npts = sum(len(p["points"]) for p in polys)
        result["cameras"][series] = {
            "frame": r["frame"], "frames_used": r["frames_used"],
            "polygons": [p["points"] for p in polys],
            "area_fraction": area,
        }
        print(f"{series:<18}{r['frames_used']:>8}{len(polys):>11}{npts:>8}{area:>13.1%}")

        # картинка-оверлей: карта активности + контур зоны (для согласования)
        try:
            import matplotlib
            matplotlib.use("Agg")
            import matplotlib.pyplot as plt
            fig, ax = plt.subplots(figsize=(7, 7 * r["grid"]["h"] / r["grid"]["w"]))
            ax.imshow(cov, cmap="magma", vmin=0, vmax=max(0.05, float(np.percentile(cov, 99.5))))
            for p in polys:
                xs = [pt[0] * r["grid"]["w"] for pt in p["points"]]
                ys = [pt[1] * r["grid"]["h"] for pt in p["points"]]
                ax.plot(xs + [xs[0]], ys + [ys[0]], "-", color="#38bdf8", linewidth=2)
            ax.set_title(f"{series}: зона ({len(polys)} полигон(ов), {r['frames_used']} кадров)")
            ax.axis("off")
            fig.tight_layout()
            fig.savefig(out / f"mask_{series.replace('/', '__')}.png", dpi=110)
            plt.close(fig)
        except Exception as e:
            print(f"  (оверлей не сохранён: {e})")

    (out / "polygon_masks.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\nзаписано: {out/'polygon_masks.json'}")
    print(f"оверлеи: {out}/mask_<камера>.png")


if __name__ == "__main__":
    main()
