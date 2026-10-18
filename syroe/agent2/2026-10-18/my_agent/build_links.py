#!/usr/bin/env python3
"""
build_links.py — создаёт симлинки images/{split}/<плоское_имя> → /home/cvat_share/<оригинальный_путь>.

Зачем: YOLO хочет плоскую структуру images/train, images/val, а картинки лежат
в /home/cvat_share с вложенными путями. Копировать 26 ГБ нельзя, поэтому симлинки.

manifest.txt: строки "<относительный путь картинки>\t<split>".
Плоское имя = путь с заменой "/" на "__" (так же именует метки cvat2yolo.py).

Запуск на сервере:
  python3 build_links.py --manifest <файл> --root /home/cvat_share --out <каталог>
"""
import argparse
from pathlib import Path


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--manifest", required=True)
    ap.add_argument("--root", default="/home/cvat_share")
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    root = Path(args.root)
    out = Path(args.out)
    missing = []
    made = 0
    for line in Path(args.manifest).read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        parts = line.split("\t")
        if len(parts) < 2:
            continue
        orig, split = parts[0], parts[1]
        src = root / orig
        flat = orig.replace("/", "__")
        dst_dir = out / "images" / split
        dst_dir.mkdir(parents=True, exist_ok=True)
        dst = dst_dir / flat
        if dst.exists() or dst.is_symlink():
            continue
        if not src.exists():
            missing.append(orig)
            continue
        try:
            dst.symlink_to(src)
            made += 1
        except OSError as e:
            missing.append(f"{orig} ({e})")

    print(f"создано симлинков: {made}")
    print(f"пропущено/отсутствует: {len(missing)}")
    if missing:
        print("первые 10:", missing[:10])


if __name__ == "__main__":
    main()
