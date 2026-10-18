#!/usr/bin/env python3
"""wbf_ensemble.py — объединение предсказаний двух моделей методом WBF.

Зачем: наша модель и модель Заказчика сильны на РАЗНЫХ классах, то есть их ошибки
не коррелируют — это условие, при котором ансамбль действительно даёт выигрыш.

    класс          базовая (YOLOv5x)   наша (YOLO11n)   кто лучше
    car                   0,905             0,971       наша
    van                   0,956             0,986       наша
    truck                 0,983             0,934       базовая
    garbagetruck          0,976             0,954       базовая

Алгоритм WBF (Weighted Boxes Fusion), по описанию авторов:
  1. все боксы обеих моделей сортируются по уверенности;
  2. бокс попадает в кластер, если его IoU с каким-либо боксом кластера выше порога;
  3. координаты усредняются с весами по уверенности, уверенность умножается на min(n, N)/N,
     где n — число боксов в кластере, N — число моделей (штраф за то, что объект нашли не все).

Пакет `ensemble-boxes` не используется сознательно: pip на этом сервере зависает, поэтому
алгоритм реализован здесь и проверен на синтетических случаях.

Запуск на сервере:
    python wbf_ensemble.py --ours <ours.pt> --base <base.pt> --data <data.yaml> --imgsz 640
"""
import argparse
import json
from pathlib import Path

import numpy as np


def iou_matrix(boxes, box):
    """IoU одного бокса со всеми боксами массива. Формат боксов: xyxy."""
    x1 = np.maximum(boxes[:, 0], box[0])
    y1 = np.maximum(boxes[:, 1], box[1])
    x2 = np.minimum(boxes[:, 2], box[2])
    y2 = np.minimum(boxes[:, 3], box[3])
    inter = np.clip(x2 - x1, 0, None) * np.clip(y2 - y1, 0, None)
    a1 = (boxes[:, 2] - boxes[:, 0]) * (boxes[:, 3] - boxes[:, 1])
    a2 = (box[2] - box[0]) * (box[3] - box[1])
    union = a1 + a2 - inter
    return np.where(union > 0, inter / np.maximum(union, 1e-9), 0.0)


def wbf_fuse(boxes, scores, labels, num_models, iou_thr=0.55, conf_type="avg"):
    """Слить боксы нескольких моделей. Возвращает (boxes, scores, labels).

    boxes/scores/labels — согласованные массивы от ВСЕХ моделей сразу
    (модель-источник восстанавливается по порядку: сначала все боксы первой модели и т. д.).
    """
    if len(boxes) == 0:
        return np.zeros((0, 4)), np.zeros((0,)), np.zeros((0,), dtype=int)

    order = np.argsort(-scores)
    boxes, scores, labels = boxes[order], scores[order], labels[order]

    clusters = []      # список списков индексов
    fused = []         # накопленные боксы кластеров
    for i in range(len(boxes)):
        best_j, best_iou = -1, 0.0
        for j, fbox in enumerate(fused):
            if labels[clusters[j][0]] != labels[i]:
                continue  # классы не смешиваем
            v = iou_matrix(fbox.reshape(1, 4), boxes[i])[0]
            if v > best_iou:
                best_iou, best_j = v, j
        if best_iou > iou_thr:
            clusters[best_j].append(i)
            cl = clusters[best_j]
            w = scores[cl]
            fused[best_j] = (boxes[cl] * w[:, None]).sum(0) / max(w.sum(), 1e-9)
        else:
            clusters.append([i])
            fused.append(boxes[i].copy())

    out_b, out_s, out_l = [], [], []
    for cl, fbox in zip(clusters, fused):
        confs = scores[cl]
        if conf_type == "avg":
            conf = confs.mean()
        else:  # max
            conf = confs.max()
        # штраф за неполноту: если объект нашли не все модели, уверенность снижается
        conf *= min(len(cl), num_models) / num_models
        out_b.append(fbox)
        out_s.append(conf)
        out_l.append(labels[cl[0]])
    return np.array(out_b), np.array(out_s), np.array(out_l, dtype=int)


def _self_test():
    """Проверка алгоритма на синтетике — без моделей и без GPU."""
    # 1. Совпадающие боксы двух моделей должны слиться в один, уверенность = среднее
    b = np.array([[10, 10, 50, 50], [11, 11, 51, 51]], dtype=float)
    s = np.array([0.9, 0.8])
    l = np.array([0, 0])
    fb, fs, fl = wbf_fuse(b, s, l, num_models=2)
    assert len(fb) == 1, f"ожидался 1 бокс, получено {len(fb)}"
    assert abs(fs[0] - 0.85) < 1e-6, f"уверенность {fs[0]}, ожидалось 0,85"
    assert fl[0] == 0
    # 2. Далеко разнесённые боксы не сливаются
    fb2, _, _ = wbf_fuse(np.array([[0, 0, 10, 10], [100, 100, 140, 140]], float),
                         np.array([0.9, 0.8]), np.array([0, 0]), 2)
    assert len(fb2) == 2, f"ожидалось 2 бокса, получено {len(fb2)}"
    # 3. Боксы разных классов не сливаются, даже если совпадают
    fb3, _, _ = wbf_fuse(np.array([[10, 10, 50, 50], [10, 10, 50, 50]], float),
                         np.array([0.9, 0.8]), np.array([0, 1]), 2)
    assert len(fb3) == 2, f"разные классы слились: {len(fb3)}"
    # 4. Найденный одной моделью объект получает штраф (уверенность вдвое ниже)
    fb4, fs4, _ = wbf_fuse(np.array([[10, 10, 50, 50]], float), np.array([0.9]),
                           np.array([0]), num_models=2)
    assert abs(fs4[0] - 0.45) < 1e-6, f"штраф не применён: {fs4[0]}"
    # 5. Пустой вход не ломает
    fb5, _, _ = wbf_fuse(np.zeros((0, 4)), np.zeros((0,)), np.zeros((0,), int), 2)
    assert len(fb5) == 0
    print("самопроверка WBF пройдена: слияние, разделение, классы, штраф, пустой вход")


def collect_predictions(model, images, imgsz, conf, iou, device=0):
    """Предсказания модели по списку изображений в абсолютных координатах."""
    all_b, all_s, all_l = [], [], []
    results = model.predict(images, imgsz=imgsz, conf=conf, iou=iou,
                            device=device, verbose=False, stream=True)
    for r in results:
        if r.boxes is None or len(r.boxes) == 0:
            all_b.append(np.zeros((0, 4)))
            all_s.append(np.zeros((0,)))
            all_l.append(np.zeros((0,), dtype=int))
            continue
        all_b.append(r.boxes.xyxy.cpu().numpy())
        all_s.append(r.boxes.conf.cpu().numpy())
        all_l.append(r.boxes.cls.cpu().numpy().astype(int))
    return all_b, all_s, all_l


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ours", required=True, help="веса нашей модели")
    ap.add_argument("--base", required=True, help="веса модели Заказчика")
    ap.add_argument("--images", required=True, help="каталог изображений для оценки")
    ap.add_argument("--imgsz", type=int, default=640)
    ap.add_argument("--conf", type=float, default=0.001)
    ap.add_argument("--iou", type=float, default=0.6)
    ap.add_argument("--iou-thr", type=float, default=0.55, help="порог IoU для кластеризации в WBF")
    ap.add_argument("--out", default="/home/user2/wbf_result.json")
    args = ap.parse_args()

    _self_test()

    from ultralytics import YOLO
    images = sorted(str(p) for p in Path(args.images).glob("*")
                    if p.suffix.lower() in (".jpg", ".jpeg", ".png"))

    ours = YOLO(args.ours)
    base = YOLO(args.base)
    print(f"изображений: {len(images)}; наша модель даёт классы {ours.names}")
    print(f"модель Заказчика даёт классы {base.names}")

    # ВАЖНО: у моделей разные наборы классов. Сопоставление по именам выполняется здесь,
    # иначе индексы классов будут означать разное и сливать боксы нельзя.
    ob, os_, ol = collect_predictions(ours, images, args.imgsz, args.conf, args.iou)
    bb, bs, bl = collect_predictions(base, images, args.imgsz, args.conf, args.iou)

    print("собраны предсказания обеих моделей")
    print("ПРЕДУПРЕЖДЕНИЕ: перед слиянием нужно привести индексы классов к общему пространству —")
    print("это делается по словарям имён, реализация сопоставления требует проверки на сервере.")
    print("Заготовка сохранена, слияние не выполнено.")


if __name__ == "__main__":
    main()
