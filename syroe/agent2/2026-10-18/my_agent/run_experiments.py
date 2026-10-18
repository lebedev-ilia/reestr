#!/usr/bin/env python3
"""run_experiments.py — опыты A и B: длинное обучение и работа с редким классом.

Сравниваем с текущей моделью (30 эпох):
    полный набор (5 классов): mAP@0,5 = 0,988; mAP@0,5:0,95 = 0,920; класс waste = 0,725
    общие 4 класса:           mAP@0,5 = 0,994; mAP@0,5:0,95 = 0,961

Опыт A — документированный рецепт разработчиков: 200 эпох, multi_scale, косинусное расписание,
ранняя остановка. Цель — проверить, не были ли наши 30 эпох недотренированностью.

Опыт B — то же плюс целевое воздействие на слабый класс `waste`:
copy_paste (вставка объектов в другие кадры), mixup и cls_pw (вес редких классов).
"""
import json
import time
from pathlib import Path

from ultralytics import YOLO

DATA = "/home/user2/baseline/waste_up/data.yaml"        # полный набор, 5 классов
COMMON = "/home/user2/common_val/ours/data.yaml"        # общие 4 класса
OUT = Path("/home/user2/baseline/runs")
MODEL = "/home/user2/baseline/yolo11n.pt"

EXPERIMENTS = [
    ("A200", dict(epochs=200, multi_scale=True, cos_lr=True, patience=60)),
    ("B200", dict(epochs=200, multi_scale=True, cos_lr=True, patience=60,
                  copy_paste=0.3, mixup=0.1, cls_pw=1.0)),
]

COMMON_PARAMS = dict(imgsz=640, batch=16, device=0, workers=8, plots=False, val=True)


def main():
    summary = {}
    for name, extra in EXPERIMENTS:
        print(f"\n{'='*70}\nОПЫТ {name}: {extra}\n{'='*70}", flush=True)
        t0 = time.time()
        model = YOLO(MODEL)
        model.train(data=DATA, project=str(OUT), name=f"waste_up_{name}",
                    exist_ok=True, **COMMON_PARAMS, **extra)
        dt = time.time() - t0
        best = OUT / f"waste_up_{name}" / "weights" / "best.pt"
        print(f"обучение {name} заняло {dt/60:.1f} мин, веса: {best}", flush=True)

        m = YOLO(str(best))
        full = m.val(data=DATA, imgsz=640, batch=16, conf=0.001, iou=0.6,
                     device=0, workers=8, plots=False, verbose=False)
        com = m.val(data=COMMON, imgsz=640, batch=16, conf=0.001, iou=0.6,
                    device=0, workers=8, plots=False, verbose=False)
        names = m.names
        summary[name] = {
            "params": {k: str(v) for k, v in extra.items()},
            "minutes": round(dt / 60, 1),
            "full_5class": {"map50": float(full.box.map50), "map50_95": float(full.box.map),
                            "per_class": {names[i]: float(v) for i, v in
                                          enumerate(full.box.maps) if i in names}},
            "common_4class": {"map50": float(com.box.map50), "map50_95": float(com.box.map),
                              "P": float(com.box.mp), "R": float(com.box.mr),
                              "per_class": {names[i]: float(v) for i, v in
                                            enumerate(com.box.maps) if i in names}},
        }
        print(json.dumps(summary[name], ensure_ascii=False, indent=2), flush=True)

    Path("/home/user2/experiments_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print("\nИТОГ СОХРАНЁН: /home/user2/experiments_summary.json")
    print("EXPERIMENTS_DONE")


if __name__ == "__main__":
    main()
