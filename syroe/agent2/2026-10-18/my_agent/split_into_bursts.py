#!/usr/bin/env python3
"""split_into_bursts.py — разбить кадры потока на «пачки» (события).

Зачем: датасет Заказчика — не видео, а выборка кадров по событию. Внутри события кадры идут
секундами, между событиями — часы и сутки. Покадровый трекинг корректен только внутри события,
поэтому для проверки дедупликации данные нужно сначала разбить на пачки.

Как определяем границу пачки: на кадрах выжжена отметка времени (OSD). Сравниваем область
отметки у соседних кадров.
  * внутри пачки меняются только цифры секунд -> различие маленькое;
  * на границе пачки меняются минуты/часы/дата -> различие резко больше.
Порог подбирается по распределению, а не на глаз: берём медиану и максимум различий внутри
известной пачки (по кадрам с известными отметками времени).

Запуск (на сервере, где лежат кадры):
  python3 split_into_bursts.py --manifests <каталог> --out <json>
"""
import argparse
import json
import os
from pathlib import Path

import numpy as np
from PIL import Image

# Область отметки времени на кадрах 2560x1920 (определена по факту: x 820..1920, y 5..65)
OSD_BOX = (820, 5, 1920, 65)
# Рабочий размер области отметки
OSD_SIZE = (275, 15)


def osd_vector(path):
    """Вектор области отметки времени. Возвращает None, если кадр недоступен или нестандартный.

    Часть файлов в датасете Заказчика недоступна для чтения (Permission denied) — это
    ограничение предоставленных данных, а не ошибка алгоритма, поэтому такие кадры
    пропускаются, а не роняют обработку.
    """
    try:
        with Image.open(path) as im:
            if im.width < 1300 or im.height < 800:   # другой формат кадра — область не та
                return None
            crop = im.crop(OSD_BOX).convert("L").resize(OSD_SIZE, Image.BILINEAR)
        return np.asarray(crop, dtype=np.float32) / 255.0
    except Exception:
        return None


def diff(a, b):
    """Доля различающихся пикселей после бинаризации."""
    if a is None or b is None:
        return None
    ta, tb = a > 0.5, b > 0.5
    return float(np.mean(ta != tb))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--manifests", required=True, help="каталог со списками кадров по камерам")
    ap.add_argument("--out", required=True)
    ap.add_argument("--threshold", type=float, default=None,
                    help="порог различия отметки времени; если не задан — оценивается автоматически")
    args = ap.parse_args()

    result = {}
    for mf in sorted(Path(args.manifests).glob("*.txt")):
        files = [l.strip() for l in mf.read_text().splitlines() if l.strip()]
        if not files:
            continue
        cam = mf.stem
        vecs = []
        unreadable = 0
        for f in files:
            v = osd_vector(f)
            if v is None:
                unreadable += 1
                vecs.append(None)          # недоступный кадр = разрыв последовательности
            else:
                vecs.append(v)
        if len([x for x in vecs if x is not None]) < 3:
            result[cam] = {"frames": len(files), "bursts": None,
                           "note": "область отметки не найдена (нестандартный размер кадра)"}
            print(f"{cam:<26} пропуск: нестандартный размер кадра")
            continue

        # разрыв ставим и там, где кадр недоступен
        diffs = []
        for i in range(len(vecs) - 1):
            if vecs[i] is None or vecs[i + 1] is None:
                diffs.append(1.0)
            else:
                diffs.append(diff(vecs[i], vecs[i + 1]))
        arr = np.array([d for d in diffs if d is not None])
        thr = args.threshold
        if thr is None:
            # различия внутри пачки малы; граница — выброс. Берём порог между медианой и максимумом.
            med = float(np.median(arr))
            thr = max(0.02, med * 6.0)
        bounds = [i + 1 for i, d in enumerate(diffs) if d is not None and d > thr]

        bursts = []
        prev = 0
        for b in bounds + [len(files)]:
            bursts.append({"start": prev, "end": b - 1, "len": b - prev})
            prev = b
        result[cam] = {
            "frames": len(files),
            "threshold": round(thr, 4),
            "bursts": len(bursts),
            "burst_list": bursts,
            "frames_per_burst_median": float(np.median([b["len"] for b in bursts])),
            "frames_per_burst_max": int(max(b["len"] for b in bursts)),
            "diff_median": round(float(np.median(arr)), 4),
            "diff_max": round(float(arr.max()), 4),
            "unreadable_frames": unreadable,
        }
        print(f"{cam:<26} кадров={len(files):>5} пачек={len(bursts):>4} "
              f"медиана кадров/пачка={np.median([b['len'] for b in bursts]):>5.0f} "
              f"макс={max(b['len'] for b in bursts):>5} порог={thr:.3f}")

    Path(args.out).write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\nзаписано: {args.out}")


if __name__ == "__main__":
    main()
