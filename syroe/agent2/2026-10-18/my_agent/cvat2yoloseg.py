#!/usr/bin/env python3
"""
cvat2yoloseg.py — конвертация разметки CVAT в формат YOLO **segmentation**.

Зачем отдельный конвертер: для задачи 2 ТЗ («оценка заполненности/морфологии отходов
с полигональной маской») нужны именно маски, а cvat2yolo.py сводит полигоны к рамкам
и теряет эту информацию. Здесь полигоны сохраняются как есть.

Формат метки YOLO-seg (одна строка на объект):
    <class_id> <x1> <y1> <x2> <y2> ... <xn> <yn>
Координаты нормированы 0..1. Боксы из разметки превращаются в 4-точечный полигон.

Запуск:
  python3 cvat2yoloseg.py --zip <архив CVAT> --out <каталог> [--val-ratio 0.15]
      [--block-split] [--yaml-path <путь для data.yaml>]
"""
import argparse
import collections
import random
import re
import shutil
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

SHAPE_TAGS = ("box", "polygon", "polyline", "points", "ellipse", "mask")


def load_root(zip_path: Path):
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
    found = []
    for el in root.iter():
        if el.tag in SHAPE_TAGS:
            lbl = el.get("label")
            if lbl and lbl not in found:
                found.append(lbl)
    return found


def polygon_points(el):
    """Возвращает список (x, y) для фигуры. Бокс превращается в 4 точки."""
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
        parts = pair.split(",")
        if len(parts) < 2:
            continue
        out.append((float(parts[0]), float(parts[1])))
    return out


def key_of(name: str):
    """(серия, номер) для блочного сплита по видеопоследовательностям."""
    b = name.rsplit("/", 1)[-1].rsplit(".", 1)[0]
    m = re.match(r"^(.*?)(\d+)$", b)
    return (m.group(1), int(m.group(2))) if m else (b, -1)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--zip", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--val-ratio", type=float, default=0.15)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--block-split", action="store_true",
                    help="делить по сериям непрерывными блоками (без утечки соседних кадров)")
    ap.add_argument("--check-images", help="корень датасета для проверки наличия картинок")
    ap.add_argument("--yaml-path", help="переопределить path в data.yaml")
    args = ap.parse_args()

    out = Path(args.out)
    root = load_root(Path(args.zip))
    labels = task_labels(root)
    if not labels:
        raise SystemExit("классы не найдены")
    cls_id = {n: i for i, n in enumerate(labels)}

    per_image = []          # (имя, ширина, высота, [(cls, [(x,y)...])])
    stats = collections.Counter()
    skipped = collections.Counter()
    for im in root.findall("image"):
        name = im.get("name")
        w, h = float(im.get("width")), float(im.get("height"))
        objs = []
        for el in list(im):
            if el.tag not in SHAPE_TAGS:
                continue
            lbl = el.get("label")
            if lbl not in cls_id:
                skipped[f"класс без описания:{lbl}"] += 1
                continue
            pts = polygon_points(el)
            if len(pts) < 3:
                skipped[f"{el.tag}:точек<3"] += 1
                continue
            # отбрасываем вырожденные
            xs = [p[0] for p in pts]; ys = [p[1] for p in pts]
            if max(xs) - min(xs) <= 1 or max(ys) - min(ys) <= 1:
                skipped[f"{el.tag}:нулевая площадь"] += 1
                continue
            objs.append((cls_id[lbl], pts))
            stats[lbl] += 1
        per_image.append((name, w, h, objs))

    missing = []
    if args.check_images:
        base = Path(args.check_images)
        for name, *_ in per_image:
            if not (base / name).exists():
                missing.append(name)

    # ---- сплит ----
    if args.block_split:
        series = collections.defaultdict(list)
        for name, *_ in per_image:
            p, i = key_of(name)
            series[p].append((i, name))
        val_names = set()
        for p, items in series.items():
            items.sort()
            n_val = max(1, int(len(items) * args.val_ratio))
            for _, nm in items[len(items) - n_val:]:
                val_names.add(nm)
        split_of = {name: ("val" if name in val_names else "train") for name, *_ in per_image}
    else:
        names = [p[0] for p in per_image]
        random.Random(args.seed).shuffle(names)
        n_val = max(1, int(len(names) * args.val_ratio))
        val_names = set(names[:n_val])
        split_of = {name: ("val" if name in val_names else "train") for name, *_ in per_image}

    for sub in ("train", "val"):
        (out / "labels" / sub).mkdir(parents=True, exist_ok=True)
        (out / "images" / sub).mkdir(parents=True, exist_ok=True)

    written = collections.Counter()
    for name, w, h, objs in per_image:
        split = split_of[name]
        flat = name.replace("/", "__")
        stem = flat.rsplit(".", 1)[0]
        lines = []
        for cid, pts in objs:
            coords = []
            for x, y in pts:
                coords.append(f"{min(max(x / w, 0.0), 1.0):.6f}")
                coords.append(f"{min(max(y / h, 0.0), 1.0):.6f}")
            lines.append(f"{cid} " + " ".join(coords))
        (out / "labels" / split / (stem + ".txt")).write_text(
            "\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
        written[split] += 1

    (out / "classes.txt").write_text("\n".join(labels) + "\n", encoding="utf-8")
    yaml_path = args.yaml_path or str(out.resolve())
    (out / "data.yaml").write_text(
        f"path: {yaml_path}\ntrain: images/train\nval: images/val\n"
        f"nc: {len(labels)}\nnames: {labels}\n", encoding="utf-8")
    (out / "manifest.txt").write_text(
        "\n".join(f"{n}\t{split_of[n]}" for n, *_ in per_image) + "\n", encoding="utf-8")

    print(f"классы ({len(labels)}): {labels}")
    print("объектов по классам:", dict(stats))
    print(f"изображений: {len(per_image)} | train: {written['train']} | val: {written['val']}")
    if skipped:
        print("пропущено фигур:", dict(skipped))
    if args.check_images:
        print(f"проверка картинок: отсутствует {len(missing)}")
    print("записано в:", out.resolve())


if __name__ == "__main__":
    main()
