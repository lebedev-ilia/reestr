#!/usr/bin/env python3
"""
resplit_yolo.py — пересобирает train/val так, чтобы убрать утечку соседних кадров.

Проблема: исходный сплит был случайным по кадрам. Датасет — это видеопоследовательности
(серии одного ТС/камеры), и соседние кадры почти идентичны. Замер показал, что у 99,9 %
val-кадров есть сосед (±1–2 кадра) в train, то есть модель фактически видела валидацию.
Отсюда «идеальные» метрики mAP50≈0,99 — они измеряют запоминание, а не обобщение.

Решение: внутри КАЖДОЙ серии сортируем кадры по номеру и отдаём в val последние 15 %
непрерывным блоком. Так соседние кадры не разрываются между выборками, а распределение
камер/серий между train и val сохраняется.

Запуск: python3 resplit_yolo.py <каталог_датасета> [доля_val]
"""
import collections
import os
import re
import shutil
import sys


def key(name):
    b = name.rsplit("/", 1)[-1].rsplit(".", 1)[0]
    m = re.match(r"^(.*?)(\d+)$", b)
    return (m.group(1), int(m.group(2))) if m else (b, -1)


def main():
    base = sys.argv[1].rstrip("/")
    ratio = float(sys.argv[2]) if len(sys.argv) > 2 else 0.15
    manifest_path = f"{base}/manifest.txt"

    rows = []
    for line in open(manifest_path, encoding="utf-8"):
        line = line.strip()
        if not line:
            continue
        parts = line.split("\t")
        if len(parts) >= 2:
            rows.append([parts[0], parts[1]])

    series = collections.defaultdict(list)
    for name, _ in rows:
        p, i = key(name)
        series[p].append((i, name))

    new_split = {}
    for p, items in series.items():
        items.sort()
        n_val = max(1, int(len(items) * ratio))
        for idx, (_, name) in enumerate(items):
            new_split[name] = "val" if idx >= len(items) - n_val else "train"

    moved = 0
    for row in rows:
        name, old = row[0], row[1]
        new = new_split[name]
        if new == old:
            continue
        flat = name.replace("/", "__")
        stem = flat.rsplit(".", 1)[0]
        for kind, fname in (("images", flat), ("labels", stem + ".txt")):
            src = f"{base}/{kind}/{old}/{fname}"
            dst_dir = f"{base}/{kind}/{new}"
            if os.path.lexists(src):
                os.makedirs(dst_dir, exist_ok=True)
                shutil.move(src, f"{dst_dir}/{fname}")
                moved += 1
        row[1] = new

    with open(manifest_path, "w", encoding="utf-8") as f:
        f.write("\n".join(f"{n}\t{s}" for n, s in rows) + "\n")

    # кэши ultralytics устарели после смены сплита
    for c in ("train.cache", "val.cache"):
        p = f"{base}/labels/{c}"
        if os.path.exists(p):
            os.remove(p)

    tr = sum(1 for _, s in rows if s == "train")
    va = sum(1 for _, s in rows if s == "val")
    print(f"серий: {len(series)} | перемещено файлов: {moved}")
    print(f"train: {tr} | val: {va}")

    # контроль утечки после пересборки
    tkeys = collections.defaultdict(set)
    for n, s in rows:
        if s != "train":
            continue
        p, i = key(n)
        tkeys[p].add(i)
    leak = 0
    tot = 0
    for n, s in rows:
        if s != "val":
            continue
        p, i = key(n)
        tot += 1
        if i - 1 in tkeys[p] or i + 1 in tkeys[p] or i - 2 in tkeys[p] or i + 2 in tkeys[p]:
            leak += 1
    print(f"утечка после пересборки: {leak}/{tot} ({100*leak/max(tot,1):.1f}%)")

    for p, items in sorted(series.items()):
        nv = sum(1 for _, n in items if new_split[n] == "val")
        print(f"   {p}: всего {len(items)}, в val {nv}")


if __name__ == "__main__":
    main()
