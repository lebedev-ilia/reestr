#!/usr/bin/env python3
"""
cvat2yolo.py — конвертация разметки CVAT XML в формат YOLO (detection).

Зачем: baseline по ТЗ требует обучаемую выборку. Разметка у нас в CVAT XML
(полигоны, боксы, полилинии), а YOLO учится на нормализованных координатах:
    <class_id> <x_center> <y_center> <width> <height>

Что делает:
  1) читает annotations.xml (внутри zip-архива CVAT), собирает классы и объекты;
  2) полигоны/боксы превращает в ограничивающие рамки;
  3) делает стратифицированное разбиение train/val по изображениям (по умолчанию 85/15);
  4) пишет структуру YOLO: images/{train,val}, labels/{train,val}, data.yaml;
  5) может проверить, что все картинки есть на месте (`--check-images ROOT`).

Примеры:
  python3 cvat2yolo.py --zip "task_waste_up....zip" --out /home/user2/rvc/waste_yolo
  python3 cvat2yolo.py --zip "task_dumpster....zip" --out dumpster_yolo --check-images /home/cvat_share
"""
import argparse
import random
import xml.etree.ElementTree as ET
import zipfile
from collections import Counter, defaultdict
from pathlib import Path


def load_root(zip_path: Path):
    """Достаёт annotations.xml из архива CVAT и парсит его."""
    with zipfile.ZipFile(zip_path) as zf:
        names = [n for n in zf.namelist() if n.endswith(".xml")]
        if not names:
            raise SystemExit(f"в {zip_path} не найден xml")
        data = zf.read(names[0])
    return ET.fromstring(data)


def task_labels(root):
    for path in ("./meta/task/labels/label", "./meta/project/labels/label"):
        labels = [e.findtext("name") for e in root.findall(path)]
        if labels:
            return labels
    # если мета нет — собираем классы по факту
    found = []
    for el in root.iter():
        if el.tag in ("box", "polygon", "polyline", "points", "ellipse", "mask"):
            lbl = el.get("label")
            if lbl and lbl not in found:
                found.append(lbl)
    return found


def bbox_from_element(el):
    """Возвращает (x1, y1, x2, y2) для box/polygon/polyline в пикселях."""
    if el.tag == "box":
        x1 = float(el.get("xtl")); y1 = float(el.get("ytl"))
        x2 = float(el.get("xbr")); y2 = float(el.get("ybr"))
        return min(x1, x2), min(y1, y2), max(x1, x2), max(y1, y2)
    pts = el.get("points")
    if not pts:
        return None
    coords = []
    for pair in pts.split(";"):
        if not pair.strip():
            continue
        x, y = pair.split(",")[:2]
        coords.append((float(x), float(y)))
    if not coords:
        return None
    xs = [c[0] for c in coords]; ys = [c[1] for c in coords]
    return min(xs), min(ys), max(xs), max(ys)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--zip", required=True, help="архив CVAT с annotations.xml")
    ap.add_argument("--out", required=True, help="куда писать структуру YOLO")
    ap.add_argument("--val-ratio", type=float, default=0.15)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--check-images", help="корень датасета, чтобы проверить наличие картинок")
    ap.add_argument("--skip-empty", action="store_true", help="не включать изображения без объектов")
    ap.add_argument("--yaml-path", help="переопределить path в data.yaml (для деплоя на другой машине)")
    args = ap.parse_args()

    out = Path(args.out)
    root = load_root(Path(args.zip))
    labels = task_labels(root)
    if not labels:
        raise SystemExit("классы не найдены")
    cls_id = {name: i for i, name in enumerate(labels)}

    images = root.findall("image")
    per_image = []           # (имя, ширина, высота, [(cls, x1,y1,x2,y2)])
    stats = Counter()
    skipped_shapes = Counter()
    missing_names = []

    for im in images:
        name = im.get("name")
        w, h = float(im.get("width")), float(im.get("height"))
        objs = []
        for el in list(im):
            if el.tag not in ("box", "polygon", "polyline", "points", "ellipse", "mask"):
                continue
            lbl = el.get("label")
            if lbl not in cls_id:
                skipped_shapes[f"класс без описания:{lbl}"] += 1
                continue
            bb = bbox_from_element(el)
            if bb is None:
                skipped_shapes[el.tag] += 1
                continue
            x1, y1, x2, y2 = bb
            if x2 - x1 <= 1 or y2 - y1 <= 1:
                skipped_shapes[f"{el.tag}:нулевая площадь"] += 1
                continue
            objs.append((cls_id[lbl], x1, y1, x2, y2))
            stats[lbl] += 1
        if objs or not args.skip_empty:
            per_image.append((name, w, h, objs))

    if args.check_images:
        base = Path(args.check_images)
        for name, *_ in per_image:
            if not (base / name).exists():
                missing_names.append(name)

    rnd = random.Random(args.seed)
    names = [p[0] for p in per_image]
    rnd.shuffle(names)
    n_val = max(1, int(len(names) * args.val_ratio))
    val_set = set(names[:n_val])

    for split in ("train", "val"):
        (out / "images" / split).mkdir(parents=True, exist_ok=True)
        (out / "labels" / split).mkdir(parents=True, exist_ok=True)

    written = Counter()
    for name, w, h, objs in per_image:
        split = "val" if name in val_set else "train"
        # имя метки: плоское, с заменой разделителей пути
        stem = name.replace("/", "__")
        label_path = out / "labels" / split / (stem.rsplit(".", 1)[0] + ".txt")
        lines = []
        for cid, x1, y1, x2, y2 in objs:
            cx = (x1 + x2) / 2 / w
            cy = (y1 + y2) / 2 / h
            bw = (x2 - x1) / w
            bh = (y2 - y1) / h
            lines.append(f"{cid} {cx:.6f} {cy:.6f} {bw:.6f} {bh:.6f}")
        label_path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
        written[split] += 1

    (out / "classes.txt").write_text("\n".join(labels) + "\n", encoding="utf-8")
    yaml_path = args.yaml_path or str(out.resolve())
    (out / "data.yaml").write_text(
        f"path: {yaml_path}\ntrain: images/train\nval: images/val\n"
        f"nc: {len(labels)}\nnames: {labels}\n", encoding="utf-8")
    (out / "manifest.txt").write_text("\n".join(f"{n}\t{'val' if n in val_set else 'train'}"
                                               for n in names) + "\n", encoding="utf-8")

    print(f"классы ({len(labels)}): {labels}")
    print("объектов по классам:", dict(stats))
    print(f"изображений: {len(per_image)} | train: {written['train']} | val: {written['val']}")
    if skipped_shapes:
        print("пропущено фигур:", dict(skipped_shapes))
    if args.check_images:
        print(f"проверка картинок: отсутствует {len(missing_names)}"
              + (f" (пример: {missing_names[0]})" if missing_names else ""))
    print("записано в:", out.resolve())


if __name__ == "__main__":
    main()
