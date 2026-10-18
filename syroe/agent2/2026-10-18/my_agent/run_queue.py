#!/usr/bin/env python3
"""run_queue.py — очередь экспериментов: строго по одному, без перегрузки GPU.

Ждёт завершения текущих опытов A и B, затем запускает следующие по списку.
Одновременно обучается только одна модель — так не создаётся оверхед.

Опыт C: обучение при большем разрешении (imgsz 960). Обоснование: исходные кадры 2560x1920
        сжимаются до 640 в 4 раза, мелкие объекты теряют детали. Ранее проверено, что инференс
        на 1280 при обучении на 640 даёт провал (0,706) — значит правильный путь именно
        ОБУЧАТЬ на большем разрешении. Этот опыт отвечает на вопрос: даёт ли это прирост.

Опыт D: более тяжёлая модель (YOLO11s) при полноценном длинном обучении. Ранее при 30 эпохах
        YOLO11s дала всего +0,0015 — проверяем, меняется ли вывод при 200 эпохах.
"""
import json
import subprocess
import time
from pathlib import Path

LOG = "/home/user2/experiments.log"
SUMMARY = "/home/user2/experiments_summary.json"
DATA = "/home/user2/baseline/waste_up/data.yaml"
COMMON = "/home/user2/common_val/ours/data.yaml"
OUT = Path("/home/user2/baseline/runs")


def experiments_running():
    r = subprocess.run(["pgrep", "-f", "python run_experiments.py"], capture_output=True, text=True)
    return r.returncode == 0


print("ждём завершения опытов A и B...", flush=True)
deadline = time.time() + 6 * 3600
while experiments_running() and time.time() < deadline:
    time.sleep(120)
print("опыты A и B завершены, начинаю очередь", flush=True)

# накопим итоги предыдущих опытов, чтобы не потерять
summary = {}
if Path(SUMMARY).exists():
    try:
        summary = json.loads(Path(SUMMARY).read_text(encoding="utf-8"))
        print(f"подхвачено результатов: {list(summary.keys())}", flush=True)
    except Exception as e:
        print("не удалось прочитать прошлую сводку:", e, flush=True)

from ultralytics import YOLO  # импорт после ожидания, чтобы не держать память

QUEUE = [
    ("C960", "/home/user2/baseline/yolo11n.pt", 150, 960,
     dict(multi_scale=True, cos_lr=True, patience=60)),
    ("D_s200", "/home/user2/baseline/yolo11s.pt", 200, 640,
     dict(multi_scale=True, cos_lr=True, patience=60)),
]

for name, weights, epochs, imgsz, extra in QUEUE:
    if not Path(weights).exists():
        print(f"ПРОПУСК {name}: нет весов {weights}", flush=True)
        continue
    print(f"\n{'='*70}\nОПЫТ {name}: epochs={epochs}, imgsz={imgsz}, {extra}\n{'='*70}", flush=True)
    t0 = time.time()
    model = YOLO(weights)
    model.train(data=DATA, project=str(OUT), name=f"waste_up_{name}", exist_ok=True,
                epochs=epochs, imgsz=imgsz, batch=16, device=0, workers=8,
                plots=False, val=True, **extra)
    dt = time.time() - t0
    best = OUT / f"waste_up_{name}" / "weights" / "best.pt"
    print(f"{name}: обучение {dt/60:.1f} мин", flush=True)

    m = YOLO(str(best))
    full = m.val(data=DATA, imgsz=imgsz, batch=16, conf=0.001, iou=0.6,
                 device=0, workers=8, plots=False, verbose=False)
    com = m.val(data=COMMON, imgsz=imgsz, batch=16, conf=0.001, iou=0.6,
                device=0, workers=8, plots=False, verbose=False)
    names = m.names
    summary[name] = {
        "params": {"epochs": epochs, "imgsz": imgsz, "weights": weights,
                   **{k: str(v) for k, v in extra.items()}},
        "minutes": round(dt / 60, 1),
        "full_5class": {"map50": float(full.box.map50), "map50_95": float(full.box.map),
                        "per_class": {names[i]: float(v) for i, v in
                                      enumerate(full.box.maps) if i in names}},
        "common_4class": {"map50": float(com.box.map50), "map50_95": float(com.box.map),
                          "P": float(com.box.mp), "R": float(com.box.mr),
                          "per_class": {names[i]: float(v) for i, v in
                                        enumerate(com.box.maps) if i in names}},
    }
    Path(SUMMARY).write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary[name], ensure_ascii=False, indent=2), flush=True)

print("\nОЧЕРЕДЬ ЗАВЕРШЕНА")
print("QUEUE_DONE")
