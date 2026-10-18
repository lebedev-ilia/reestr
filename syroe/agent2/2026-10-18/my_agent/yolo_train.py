#!/usr/bin/env python3
"""
yolo_train.py — обучение baseline-модели детекции ТС по ТЗ «Автоматика» и снятие метрик.

Запуск НА СЕРВЕРЕ (отсоединённо, чтобы пережил разрыв SSH):
  nohup setsid python3 yolo_train.py --data /home/user2/baseline/data.yaml \
      --out /home/user2/baseline/runs >> /home/user2/baseline/train.log 2>&1 &

Что делает:
  1) обучает YOLO на подготовленном датасете (CVAT → YOLO);
  2) считает метрики на val-сплите (mAP@0.5, mAP@0.5:0.95, precision, recall, F1);
  3) сохраняет metrics.json и results.csv (для отчёта по ТЗ);
  4) при падении/обрыве — поддерживает resume последнего чекпоинта.

Почему так: по ТЗ (п. 6.4) метрики — precision, recall, F1, mAP; пороги фиксируются после
проверки данных и базовой модели. Этот скрипт даёт числовой baseline.
"""
import argparse
import json
import shutil
from pathlib import Path


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", required=True, help="путь к data.yaml")
    ap.add_argument("--out", required=True, help="каталог для runs и метрик")
    ap.add_argument("--model", default="yolo11n.pt", help="стартовые веса (n/s/m)")
    ap.add_argument("--epochs", type=int, default=30)
    ap.add_argument("--imgsz", type=int, default=640)
    ap.add_argument("--batch", type=int, default=16)
    ap.add_argument("--device", default="0", help="GPU id или cpu")
    ap.add_argument("--name", default="baseline")
    ap.add_argument("--resume", action="store_true", help="продолжить с последнего чекпоинта")
    args = ap.parse_args()

    from ultralytics import YOLO

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    last = out / args.name / "weights" / "last.pt"

    if args.resume and last.exists():
        print(f"[resume] продолжаю с {last}")
        model = YOLO(str(last))
        model.train(resume=True)
    else:
        model = YOLO(args.model)
        model.train(
            data=args.data,
            epochs=args.epochs,
            imgsz=args.imgsz,
            batch=args.batch,
            device=args.device,
            project=str(out),
            name=args.name,
            exist_ok=True,
            # чекпоинт каждую эпоху — защита от обрывов и падений
            save=True,
            save_period=1,
            plots=True,
            deterministic=False,
        )

    # ---- метрики на валидации ----
    best = out / args.name / "weights" / "best.pt"
    weights = best if best.exists() else last
    print(f"[metrics] считаю по {weights}")
    metrics = model.val(data=args.data, imgsz=args.imgsz, device=args.device,
                        project=str(out), name=args.name + "_val", exist_ok=True)

    names = getattr(metrics, "names", {}) or {}
    result = {
        "weights": str(weights),
        "data": args.data,
        "imgsz": args.imgsz,
        "epochs": args.epochs,
        "task": getattr(model, "task", None),
    }
    # У сегментации две группы метрик: маски (seg) и рамки (box).
    # По ТЗ задача 2 — «полигональная маска», поэтому главные метрики — seg.
    for group in ("box", "seg"):
        m = getattr(metrics, group, None)
        if m is None:
            continue
        g = {
            "map50": float(getattr(m, "map50", 0.0)),
            "map50_95": float(getattr(m, "map", 0.0)),
            "precision": float(getattr(m, "mp", 0.0)),
            "recall": float(getattr(m, "mr", 0.0)),
        }
        p, r = g["precision"], g["recall"]
        g["f1"] = 2 * p * r / (p + r) if (p + r) else 0.0
        maps = getattr(m, "maps", None)
        if maps is not None:
            g["per_class"] = {names.get(i, str(i)): float(v) for i, v in enumerate(list(maps))}
        result[group] = g

    # обратная совместимость: главные поля берём из seg, если он есть, иначе из box
    main = result.get("seg") or result.get("box")
    if main:
        for k in ("map50", "map50_95", "precision", "recall", "f1", "per_class"):
            result[k] = main[k]

    (out / "metrics.json").write_text(json.dumps(result, ensure_ascii=False, indent=2),
                                      encoding="utf-8")
    # копия результатов по эпохам — для отчёта
    csv = out / args.name / "results.csv"
    if csv.exists():
        shutil.copy(csv, out / "results.csv")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    print("ГОТОВО")


if __name__ == "__main__":
    main()
