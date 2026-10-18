#!/usr/bin/env python3
"""cvat2yolo_types.py — CVAT → YOLO-seg с разбиением по АТРИБУТАМ (типы отходов).

Зачем: ТЗ (задача 2) требует «оценку заполненности и морфологии отходов (определение типов
отходов, включая ТКО и КГО)». В разметке Заказчика типы отходов лежат в АТРИБУТЕ `type`
класса `tko`, а заполненность контейнера — в атрибутах `заполнен` и `переполнен`.

**Ultralytics не умеет работать с атрибутами.** Официальный ответ разработчиков
(issue #23784): «Ultralytics doesn't use attribute information. You can create different
classes for them. As long as they can be distinguished visually, it shouldn't be confusing».
Поэтому атрибут `type` разворачивается в ОТДЕЛЬНЫЕ КЛАССЫ — это и делает скрипт.

Атрибуты заполненности отдельными классами делать нельзя: это свойство объекта, а не его тип,
и признаки визуально пересекаются. Они сохраняются отдельным файлом
(`fill_labels.json`) для задачи оценки заполненности.

Классы на выходе:
    0 dumpster      — контейнер
    1 placedump     — площадка накопления
    2 waste_tko     — ТКО          (tko + type=tko_dump)
    3 waste_kgm     — КГО          (tko + type=kgm_dump)
    4 waste_carton  — картон       (tko + type=carton_dump)
    5 waste_bag     — мешки        (tko + type=bag_dump)
    6 waste_plastic — пластик      (tko + type=plastic_dump)
    7 waste_constr  — строительные (constrwaste)
    8 waste_tires   — шины         (tires)
    9 waste_tree    — растительные (treewaste)

Сплит train/val НЕ пересчитывается: он берётся из уже существующего набора
(передаётся списками имён), чтобы результаты были сравнимы с прежними.
"""
import argparse
import collections
import json
import re
import xml.etree.ElementTree as ET
import zipfile

CLASS_MAP = {
    # состояния контейнера разворачиваются из атрибутов заполнен/переполнен (см. --with-fill)
    ("dumpster", None): 0,
    ("placedump", None): 1,
    ("tko", "tko_dump"): 2,
    ("tko", "kgm_dump"): 3,
    ("tko", "carton_dump"): 4,
    ("tko", "bag_dump"): 5,
    ("tko", "plastic_dump"): 6,
    ("constrwaste", None): 7,
    ("tires", None): 8,
    ("treewaste", None): 9,
}
NAMES = ["dumpster", "placedump", "waste_tko", "waste_kgm", "waste_carton",
         "waste_bag", "waste_plastic", "waste_constr", "waste_tires", "waste_tree"]

# Разбиение контейнера по состоянию (атрибуты заполнен / переполнен).
# Состояние ВИЗУАЛЬНО различимо (полный и пустой контейнер выглядят по-разному),
# поэтому по официальной рекомендации Ultralytics его можно вынести в отдельные классы.
NAMES_WITH_FILL = ["dumpster_empty", "dumpster_filled", "dumpster_overflow", "placedump",
                   "waste_tko", "waste_kgm", "waste_carton", "waste_bag", "waste_plastic",
                   "waste_constr", "waste_tires", "waste_tree"]


# индексы классов в режиме --with-fill: контейнер развёрнут в три состояния,
# остальные классы сдвигаются. Соответствие задаётся ЯВНО, чтобы не было съезда индексов.
WITH_FILL_MAP = {
    ("placedump", None): 3,
    ("tko", "tko_dump"): 4,
    ("tko", "kgm_dump"): 5,
    ("tko", "carton_dump"): 6,
    ("tko", "bag_dump"): 7,
    ("tko", "plastic_dump"): 8,
    ("constrwaste", None): 9,
    ("tires", None): 10,
    ("treewaste", None): 11,
}


def container_state(at):
    """Состояние контейнера по атрибутам. Возвращает 'empty' | 'filled' | 'overflow' | None."""
    f, o = (at.get("заполнен") or "").lower(), (at.get("переполнен") or "").lower()
    if o == "да":
        return "overflow"
    if f == "да":
        return "filled"
    if f == "нет":
        return "empty"
    return None


def label_name(image_name):
    """Имя метки из имени изображения: каталоги через __, расширение → .txt."""
    stem = re.sub(r"\.(jpg|jpeg|png|bmp)$", "", image_name, flags=re.I)
    return stem.replace("/", "__") + ".txt"


def shape_points(el):
    if el.tag == "box":
        x1, y1 = float(el.get("xtl")), float(el.get("ytl"))
        x2, y2 = float(el.get("xbr")), float(el.get("ybr"))
        return [(x1, y1), (x2, y1), (x2, y2), (x1, y2)]
    pts = el.get("points")
    if not pts:
        return []
    out = []
    for pair in pts.split(";"):
        p = pair.split(",")
        if len(p) >= 2:
            out.append((float(p[0]), float(p[1])))
    return out


def attrs_of(el):
    d = {}
    for a in el.findall("attribute"):
        d[a.get("name")] = (a.text or "").strip()
    return d


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--zip", required=True, help="внешний zip с архивом CVAT")
    ap.add_argument("--inner", required=True, help="имя внутреннего архива")
    ap.add_argument("--out", required=True, help="каталог для меток")
    ap.add_argument("--split", required=True, help="JSON: {'train': [имена изображений], 'val': [...]}")
    ap.add_argument("--with-fill", action="store_true",
                    help="разбить контейнер по состоянию (пустой/заполнен/переполнен)")
    args = ap.parse_args()

    z = zipfile.ZipFile(args.zip)
    z2 = zipfile.ZipFile(__import__("io").BytesIO(z.read(args.inner)))
    xml_name = [n for n in z2.namelist() if n.endswith(".xml")][0]
    root = ET.fromstring(z2.read(xml_name))

    split = json.load(open(args.split))
    known = {label_name(i): part for part, items in split.items() for i in items}

    stats = collections.Counter()
    unknown_attr = collections.Counter()
    fill_labels = {}
    written = 0

    from pathlib import Path
    for part in ("train", "val"):
        (Path(args.out) / part).mkdir(parents=True, exist_ok=True)

    for im in root.findall("image"):
        img_name = im.get("name")
        W, H = float(im.get("width")), float(im.get("height"))
        lname = label_name(img_name)
        part = known.get(lname)
        if part is None:
            stats["изображений вне сплита"] += 1
            continue

        lines, fills = [], []
        for el in list(im):
            if el.tag not in ("box", "polygon", "polyline", "points"):
                continue
            lab = el.get("label")
            at = attrs_of(el)
            typ = at.get("type")
            if args.with_fill:
                if lab == "dumpster":
                    st = container_state(at)
                    cls = {"empty": 0, "filled": 1, "overflow": 2}.get(st) if st else None
                else:
                    cls = WITH_FILL_MAP.get((lab, typ))
                    if cls is None:
                        cls = WITH_FILL_MAP.get((lab, None))
            else:
                cls = CLASS_MAP.get((lab, typ))
            if cls is None:
                if typ is not None or lab == "tko":
                    unknown_attr[f"{lab}/{typ}"] += 1
                cls = CLASS_MAP.get((lab, None))
                if cls is None:
                    stats[f"пропущен класс {lab}"] += 1
                    continue
            pts = shape_points(el)
            if len(pts) < 3:
                continue
            norm = []
            for x, y in pts:
                norm.append(min(max(x / W, 0.0), 1.0))
                norm.append(min(max(y / H, 0.0), 1.0))
            lines.append(f"{cls} " + " ".join(f"{v:.6f}" for v in norm))
            stats[(NAMES_WITH_FILL if args.with_fill else NAMES)[cls]] += 1

            if lab == "dumpster":
                fills.append({"заполнен": at.get("заполнен"), "переполнен": at.get("переполнен"),
                              "type": typ, "вне_площадки": at.get("вне_площадки")})

        (Path(args.out) / part / lname).write_text("\n".join(lines) + ("\n" if lines else ""),
                                                  encoding="utf-8")
        written += 1
        if fills:
            fill_labels[lname] = fills

    (Path(args.out) / "fill_labels.json").write_text(
        json.dumps(fill_labels, ensure_ascii=False, indent=1), encoding="utf-8")
    (Path(args.out) / "classes.txt").write_text(
        "\n".join(NAMES_WITH_FILL if args.with_fill else NAMES) + "\n", encoding="utf-8")

    print(f"записано меток: {written}")
    print("\n=== объектов по классам ===")
    total = 0
    for n in (NAMES_WITH_FILL if args.with_fill else NAMES):
        if stats[n]:
            print(f"  {n}: {stats[n]}")
            total += stats[n]
    print(f"  ВСЕГО: {total}")
    if unknown_attr:
        print("\nнеизвестные сочетания класс/тип:", dict(unknown_attr.most_common(8)))
    for k in stats:
        if k not in (NAMES_WITH_FILL if args.with_fill else NAMES):
            print(f"  {k}: {stats[k]}")
    print(f"\nконтейнеров с метками заполненности: {sum(len(v) for v in fill_labels.values())}")
    print("CONVERT_DONE")


if __name__ == "__main__":
    main()
