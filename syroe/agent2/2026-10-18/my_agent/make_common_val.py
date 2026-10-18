#!/usr/bin/env python3
"""make_common_val.py — привести валидационную выборку к общим классам двух моделей.

Зачем: у базовой модели Заказчика 8 классов, у нашей — 5, пересекаются четыре:
    car, truck, garbagetruck, van
Сравнивать «в лоб» нельзя. Скрипт строит из одной и той же выборки два набора разметки:
  * для НАШЕЙ модели — только 4 общих класса, индексы как в нашей модели;
  * для БАЗОВОЙ модели — те же объекты, но индексы как в её модели (8 классов).
Изображения берутся одни и те же (симлинки), поэтому выборка идентична.

Запуск (на сервере):
  python3 make_common_val.py
"""
import os
import shutil
from pathlib import Path

BASE = Path("/home/user2/baseline/waste_up")
OUT = Path("/home/user2/common_val")

# наши классы и их индексы
OUR_NAMES = ["car", "truck", "garbagetruck", "van", "waste"]
OUR_IDX = {n: i for i, n in enumerate(OUR_NAMES)}

# классы базовой модели (из data/waste_up.yaml Заказчика)
BASE_NAMES = ["garbagetruck", "truck", "van", "constrwaste", "tko", "dirt", "treewaste", "car"]
BASE_IDX = {n: i for i, n in enumerate(BASE_NAMES)}

# что сравниваем
COMMON = ["car", "truck", "garbagetruck", "van"]


def build(name, names, index_of, common):
    root = OUT / name
    for sub in ("images", "labels"):
        (root / sub).mkdir(parents=True, exist_ok=True)
    n_img = n_obj = n_skip = 0
    for split in ("val",):
        src_lbl = BASE / "labels" / split
        src_img = BASE / "images" / split
        for lbl in sorted(src_lbl.glob("*.txt")):
            lines_out = []
            for line in lbl.read_text().splitlines():
                p = line.split()
                if len(p) < 5:
                    continue
                cls = int(p[0])
                if cls >= len(OUR_NAMES):
                    continue
                cname = OUR_NAMES[cls]
                if cname not in common:
                    n_skip += 1
                    continue
                lines_out.append(" ".join([str(index_of[cname])] + p[1:]))
                n_obj += 1
            if not lines_out:
                continue
            # расширение изображения может быть .jpg ИЛИ .jpeg — иначе ссылка бьётся
            img = None
            for ext in (".jpg", ".jpeg", ".png", ".JPG"):
                cand = src_img / (lbl.stem + ext)
                if cand.exists():
                    img = cand
                    break
            if img is None:
                print(f"  ВНИМАНИЕ: не найдено изображение для {lbl.name}")
                continue
            (root / "labels" / lbl.name).write_text("\n".join(lines_out) + "\n")
            dst = root / "images" / img.name
            if dst.exists() or dst.is_symlink():
                dst.unlink()
            os.symlink(img, dst)
            n_img += 1
    (root / "data.yaml").write_text(
        f"path: {root}\ntrain: images\nval: images\n"
        f"nc: {len(names)}\nnames: {names}\n", encoding="utf-8")
    print(f"{name}: изображений {n_img}, объектов {n_obj}, пропущено объектов вне общих классов {n_skip}")
    print(f"  data.yaml: {root/'data.yaml'}")


if __name__ == "__main__":
    if OUT.exists():
        shutil.rmtree(OUT)
    build("ours", OUR_NAMES, OUR_IDX, COMMON)
    build("base", BASE_NAMES, BASE_IDX, COMMON)
    print("\nготово. Обе выборки указывают на одни и те же изображения.")
