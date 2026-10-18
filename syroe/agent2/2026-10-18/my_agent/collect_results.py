#!/usr/bin/env python3
"""collect_results.py — собрать итоги всех экспериментов в одну сравнительную таблицу.

Запускается на сервере: сводит результаты опытов A, B, C, D и известные базовые значения
в один отчёт, чтобы не собирать цифры вручную.
"""
import json
from pathlib import Path

SUMMARY = Path("/home/user2/experiments_summary.json")

# известные значения: наша модель 30 эпох и базовая модель Заказчика
KNOWN = {
    "наша n, 30 эпох (текущая)": {
        "full_5class": {"map50": 0.9877, "map50_95": 0.9200,
                        "per_class": {"waste": 0.750}},
        "common_4class": {"map50": 0.9943, "map50_95": 0.9614,
                          "per_class": {"car": 0.971, "truck": 0.934,
                                        "garbagetruck": 0.954, "van": 0.986}},
    },
    "базовая Заказчика (YOLOv5x)": {
        "full_5class": None,
        "common_4class": {"map50": 0.965, "map50_95": 0.955,
                          "per_class": {"car": 0.905, "truck": 0.983,
                                        "garbagetruck": 0.976, "van": 0.956}},
    },
}

data = dict(KNOWN)
if SUMMARY.exists():
    data.update(json.loads(SUMMARY.read_text(encoding="utf-8")))

print("=" * 96)
print("СРАВНЕНИЕ ВСЕХ ЭКСПЕРИМЕНТОВ")
print("=" * 96)
print(f"\n{'вариант':<34}{'полный набор':>22}{'общие 4 класса':>24}{'waste':>9}")
print(f"{'':34}{'mAP50':>8}{'mAP50-95':>12}{'mAP50':>10}{'mAP50-95':>12}{'mAP50-95':>9}")
print("-" * 96)
for name, v in data.items():
    f = v.get("full_5class") or {}
    c = v.get("common_4class") or {}
    w = (f.get("per_class") or {}).get("waste", "—")
    print(f"{name:<34}"
          f"{f.get('map50', '—'):>8}{f.get('map50_95', '—'):>12}"
          f"{c.get('map50', '—'):>10}{c.get('map50_95', '—'):>12}"
          f"{w if isinstance(w, str) else round(w, 3):>9}")

print("\nПО КЛАССАМ (общие 4 класса, mAP@0,5:0,95):")
classes = ["car", "truck", "garbagetruck", "van"]
print(f"{'вариант':<34}" + "".join(f"{c:>16}" for c in classes))
for name, v in data.items():
    c = v.get("common_4class") or {}
    pc = c.get("per_class") or {}
    print(f"{name:<34}" + "".join(
        f"{pc.get(cl, '—') if not isinstance(pc.get(cl), float) else round(pc[cl], 4):>16}"
        for cl in classes))
print("\nCOLLECT_DONE")
