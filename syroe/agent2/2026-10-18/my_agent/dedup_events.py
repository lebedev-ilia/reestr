#!/usr/bin/env python3
"""
dedup_events.py — учёт полигональной маски и дедупликация ТС (п. 5.1, 5.2, 103 ТЗ).

ТЗ:
  * «Полигональная маска — заранее определённая область кадра, в пределах которой
    выполняется детекция/оценка» → считаем только объекты, чей центр попал в зону;
  * «Дедупликация — механизм предотвращения повторной фиксации одного и того же ТС»;
  * «реализация/настройка дедупликации на основе доступного трекинга, признаков или
    пространственно-временных критериев»;
  * п. 103 — передать «описание алгоритма … и дедупликации».

Что делает:
  1. Берёт детекции (от модели ИЛИ из разметки — для отладки без GPU).
  2. Присваивает им идентификаторы трека.
     - В рабочем режиме трекинг даёт **ByteTrack** из ultralytics — документированный
       способ: `model.track(frame, persist=True, tracker="bytetrack.yaml")`, где
       `persist=True` допустим только для последовательных кадров одного потока
       (https://docs.ultralytics.com/modes/track/). Наши кадры одной камеры — как раз поток.
     - В режиме отладки (без сервера) используется простой IoU-трекер ниже: он повторяет
       ядро SORT/ByteTrack (сопоставление по IoU), но без каскада и ReID.
  3. Отсекает всё вне полигональной маски камеры.
  4. Дедуплицирует: ТС регистрируется **один раз** — при первом появлении в зоне.
     Дополнительно работает пространственно-временная склейка: если трек потерялся
     (перекрытие) и через несколько кадров рядом возник новый ID, он считается тем же ТС.
  5. Считает метрики качества дедупликации и пишет события в JSON.

Запуск (отладка на разметке, без модели):
  python3 dedup_events.py --mode cvat --zip <архив CVAT> --masks files/polygon_masks/polygon_masks.json --out files/dedup
Запуск (рабочий, с моделью, на сервере):
  python3 dedup_events.py --mode yolo --model <best.pt> --images <папка кадров> --camera <имя> \
      --masks polygon_masks.json --out <каталог>
"""
import argparse
import collections
import json
import math
import re
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path

SHAPE_TAGS = ("box", "polygon", "polyline", "points", "ellipse", "mask")

# Классы-транспортные средства из разметки Заказчика (см. «Описание классов cvat v1.2»).
# `waste` — отходы, не ТС: в дедупликацию ТС не входит (задача 2 ТЗ).
VEHICLE_CLASSES = {"car", "van", "truck", "garbagetruck", "waste_truck"}

# единое определение потока/кадра — берём из build_polygon_mask, чтобы не разъезжались
from stream_naming import series_of, frame_number  # noqa: E402


# ---------------------------------------------------------------- полигональная маска

class ROI:
    """Полигональная маска камеры: набор полигонов в нормализованных координатах."""

    def __init__(self, polygons):
        self.polygons = [[(float(x), float(y)) for x, y in p] for p in polygons]

    def contains(self, x, y) -> bool:
        """Точка (нормированные координаты) внутри хотя бы одного полигона."""
        for poly in self.polygons:
            n = len(poly)
            inside = False
            j = n - 1
            for i in range(n):
                xi, yi = poly[i]
                xj, yj = poly[j]
                if ((yi > y) != (yj > y)) and (x < (xj - xi) * (y - yi) / (yj - yi + 1e-12) + xi):
                    inside = not inside
                j = i
            if inside:
                return True
            # граница тоже считается попаданием (допуск)
            for i in range(n):
                x1, y1 = poly[i]
                x2, y2 = poly[(i + 1) % n]
                dx, dy = x2 - x1, y2 - y1
                L2 = dx * dx + dy * dy
                t = 0.0 if L2 == 0 else max(0.0, min(1.0, ((x - x1) * dx + (y - y1) * dy) / L2))
                if math.hypot(x - (x1 + t * dx), y - (y1 + t * dy)) < 0.004:
                    return True
        return False


def load_roi(masks_path: Path, camera: str):
    data = json.loads(Path(masks_path).read_text(encoding="utf-8"))
    cam = data["cameras"].get(camera)
    if cam is None:
        return None
    return ROI(cam["polygons"])


# ---------------------------------------------------------------- трекер (отладочный)

class IoUTracker:
    """Простое ядро SORT/ByteTrack: сопоставление по IoU + возраст трека.

    Используется только для отладки алгоритма дедупликации без GPU-сервера.
    В рабочем режиме треки даёт ByteTrack из ultralytics.
    """

    def __init__(self, iou_thr=0.3, max_age=12, min_hits=1):
        self.iou_thr = iou_thr
        self.max_age = max_age
        self.min_hits = min_hits
        self.tracks = {}          # id -> dict
        self.next_id = 1

    @staticmethod
    def _iou(a, b):
        ax1, ay1, ax2, ay2 = a
        bx1, by1, bx2, by2 = b
        ix1, iy1 = max(ax1, bx1), max(ay1, by1)
        ix2, iy2 = min(ax2, bx2), min(ay2, by2)
        iw, ih = max(0.0, ix2 - ix1), max(0.0, iy2 - iy1)
        inter = iw * ih
        if inter <= 0:
            return 0.0
        ua = (ax2 - ax1) * (ay2 - ay1) + (bx2 - bx1) * (by2 - by1) - inter
        return inter / ua if ua > 0 else 0.0

    def update(self, frame_idx, dets):
        """dets: [(bbox, cls, conf)]. Возвращает [(track_id, bbox, cls, conf)]."""
        for t in self.tracks.values():
            t["age"] += 1
        pairs = []
        for tid, t in self.tracks.items():
            if t["age"] > self.max_age:
                continue
            for di, d in enumerate(dets):
                iou = self._iou(t["bbox"], d[0])
                if iou >= self.iou_thr:
                    pairs.append((iou, tid, di))
        pairs.sort(reverse=True)
        used_t, used_d = set(), set()
        out = []
        for iou, tid, di in pairs:
            if tid in used_t or di in used_d:
                continue
            used_t.add(tid)
            used_d.add(di)
            t = self.tracks[tid]
            t["bbox"], t["age"], t["hits"] = dets[di][0], 0, t["hits"] + 1
            out.append((tid, dets[di][0], dets[di][1], dets[di][2]))
        for di, d in enumerate(dets):
            if di in used_d:
                continue
            tid = self.next_id
            self.next_id += 1
            self.tracks[tid] = {"bbox": d[0], "age": 0, "hits": 1, "first": frame_idx}
            if self.min_hits <= 1:
                out.append((tid, d[0], d[1], d[2]))
        return out


# ---------------------------------------------------------------- дедупликация

class Deduplicator:
    """Регистрирует ТС один раз + склеивает потерянные треки (пространственно-временной критерий)."""

    def __init__(self, camera, roi: ROI, reid_frames=25, reid_dist=0.06, cooldown=10,
                 vehicle_classes=None):
        self.camera = camera
        self.roi = roi
        # ТЗ: дедупликация — про ТС («повторной фиксации одного и того же транспортного
        # средства»). Классы, не являющиеся ТС (например `waste` — отходы), не дедуплицируются,
        # иначе статичные объекты дают сотни ложных «событий».
        self.vehicle_classes = set(vehicle_classes) if vehicle_classes else None
        self.reid_frames = reid_frames    # сколько кадров держим «потерянный» трек для склейки
        self.reid_dist = reid_dist        # максимальное расстояние между центрами (нормированное)
        self.cooldown = cooldown          # не регистрировать повторно раньше, чем через N кадров
        self.active = {}                  # track_id -> dict(bbox, cls, last_frame)
        self.vehicle_of_track = {}        # track_id -> vehicle_id
        self.vehicles = {}                # vehicle_id -> event
        self.next_vehicle = 1
        self.stats = collections.Counter()

    def _center(self, bbox):
        x1, y1, x2, y2 = bbox
        return (x1 + x2) / 2.0, (y1 + y2) / 2.0

    def _find_vehicle(self, cx, cy, frame_idx, cls, seen_now):
        """Ищем недавно потерянное ТС рядом с новым треком.

        Пространственно-временной критерий из ТЗ: то же ТС = тот же класс,
        последняя позиция близко (`reid_dist`), прошло не больше `reid_frames` кадров,
        и в текущем кадре это ТС ещё не видно (иначе склеили бы два разных объекта).
        """
        best, best_d = None, None
        for vid, ev in self.vehicles.items():
            if ev["class"] != cls:
                continue
            if vid in seen_now:
                continue
            age = frame_idx - ev["last_frame"]
            if age <= 0 or age > self.reid_frames:
                continue
            lx, ly = ev["last_center"]
            d = math.hypot(cx - lx, cy - ly)
            if d <= self.reid_dist and (best_d is None or d < best_d):
                best, best_d = vid, d
        return best

    def update(self, frame_idx, observations, frame_size=(1.0, 1.0)):
        """observations: [(track_id, bbox(x1,y1,x2,y2 норм.), cls, conf)]."""
        seen_now = set()
        # --- проход 1: уже известные треки продлевают своё ТС
        pending = []
        for tid, bbox, cls, conf in observations:
            cx, cy = self._center(bbox)
            in_zone = self.roi.contains(cx, cy) if self.roi else True
            self.active[tid] = {"bbox": bbox, "cls": cls, "last_frame": frame_idx,
                                "in_zone": in_zone}
            self.stats["detections"] += 1
            if in_zone:
                self.stats["detections_in_zone"] += 1
            else:
                continue
            if self.vehicle_classes is not None and cls not in self.vehicle_classes:
                self.stats["skipped_non_vehicle"] += 1
                continue
            self.stats["vehicle_detections_in_zone"] += 1
            vid = self.vehicle_of_track.get(tid)
            if vid is not None:
                ev = self.vehicles[vid]
                ev["last_frame"] = frame_idx
                ev["last_center"] = [round(cx, 5), round(cy, 5)]
                ev["frames_in_zone"] += 1
                seen_now.add(vid)
            else:
                pending.append((tid, bbox, cls, cx, cy))

        # --- проход 2: новые треки — склейка с потерянным ТС либо регистрация нового
        for tid, bbox, cls, cx, cy in pending:
            vid = self._find_vehicle(cx, cy, frame_idx, cls, seen_now)
            if vid is not None:
                self.vehicle_of_track[tid] = vid
                ev = self.vehicles[vid]
                ev["reid_merges"] += 1
                self.stats["reid_merges"] += 1
            else:
                vid = self.next_vehicle
                self.next_vehicle += 1
                self.vehicle_of_track[tid] = vid
                self.vehicles[vid] = {
                    "camera": self.camera, "vehicle_id": vid, "class": cls,
                    "first_frame": frame_idx, "last_frame": frame_idx,
                    "last_center": [round(cx, 5), round(cy, 5)],
                    "first_center": [round(cx, 5), round(cy, 5)],
                    "frames_in_zone": 0, "reid_merges": 0, "track_ids": [],
                }
                self.stats["events"] += 1
            ev = self.vehicles[vid]
            ev["last_frame"] = frame_idx
            ev["last_center"] = [round(cx, 5), round(cy, 5)]
            ev["frames_in_zone"] += 1
            if tid not in ev["track_ids"]:
                ev["track_ids"].append(tid)
            seen_now.add(vid)
        return list(self.vehicles.values())

    def report(self):
        s = self.stats
        # коэффициент дедупликации считаем от детекций ТС в зоне: именно они «повторные»
        raw = s["vehicle_detections_in_zone"] or 1
        return {
            "camera": self.camera,
            "detections_total": s["detections"],
            "detections_in_zone": s["detections_in_zone"],
            "vehicle_detections_in_zone": s["vehicle_detections_in_zone"],
            "raw_track_ids": len(self.vehicle_of_track) + sum(
                1 for t in self.active if t not in self.vehicle_of_track),
            "unique_vehicles": s["events"],
            "reid_merges": s["reid_merges"],
            "skipped_non_vehicle": s["skipped_non_vehicle"],
            "dedup_coefficient": round(1.0 - s["events"] / raw, 4),
            "vehicles": list(self.vehicles.values()),
        }


# ---------------------------------------------------------------- источники детекций

def frames_from_cvat(zip_path, camera_filter=None):
    """Кадры из разметки: рамки считаются «детекциями». Для отладки без GPU."""
    with zipfile.ZipFile(zip_path) as zf:
        name = [n for n in zf.namelist() if n.endswith(".xml")][0]
        root = ET.fromstring(zf.read(name))
    by_cam = collections.defaultdict(list)
    for im in root.findall("image"):
        nm = im.get("name")
        cam = series_of(nm)   # единое определение потока с build_polygon_mask
        if camera_filter and cam != camera_filter:
            continue
        W, H = float(im.get("width")), float(im.get("height"))
        dets, cls = [], []
        for el in list(im):
            if el.tag not in SHAPE_TAGS:
                continue
            if el.tag == "box":
                x1, y1 = float(el.get("xtl")), float(el.get("ytl"))
                x2, y2 = float(el.get("xbr")), float(el.get("ybr"))
            else:
                pts = el.get("points") or ""
                coords = [tuple(map(float, p.split(","))) for p in pts.split(";") if "," in p]
                if len(coords) < 3:
                    continue
                xs = [c[0] for c in coords]
                ys = [c[1] for c in coords]
                x1, y1, x2, y2 = min(xs), min(ys), max(xs), max(ys)
            dets.append((x1 / W, y1 / H, x2 / W, y2 / H))
            cls.append(el.get("label"))
        by_cam[cam].append({"name": nm, "frame": frame_number(nm),
                            "size": (W, H), "dets": dets, "classes": cls})
    for cam in by_cam:
        by_cam[cam].sort(key=lambda f: f["frame"])
    return by_cam


def frames_from_yolo(model_path, images_dir, manifest=None, camera=None, conf=0.25, device=0):
    """Кадры с детекциями модели + ByteTrack (рабочий режим, запускается на сервере).

    `manifest` — текстовый файл со списком кадров одной камеры в хронологическом порядке
    (нужен, когда кадры лежат плоско и разбиты на train/val: подавать надо ВСЕ кадры камеры,
    иначе трек рвётся на границе сплита).
    """
    from ultralytics import YOLO  # импорт здесь: локально ultralytics не нужен
    model = YOLO(model_path)
    if manifest:
        files = [l.strip() for l in Path(manifest).read_text().splitlines() if l.strip()]
        files = [Path(f) for f in files]
    else:
        files = sorted(Path(images_dir).glob("*.jpg")) + sorted(Path(images_dir).glob("*.png"))
    out = []
    skipped = 0
    for idx, f in enumerate(files):
        # persist=True — кадры одной камеры идут последовательно (см. docs/modes/track)
        try:
            res = model.track(str(f), persist=True, tracker="bytetrack.yaml",
                              conf=conf, device=device, verbose=False)
        except Exception as e:
            skipped += 1
            print(f"  кадр пропущен ({f.name}): {type(e).__name__}")
            continue
        if not res:      # нечитаемый кадр — ultralytics вернул пустой результат
            skipped += 1
            continue
        r0 = res[0]
        dets, cls, ids = [], [], []
        H, W = r0.orig_shape
        if r0.boxes is not None and len(r0.boxes):
            xyxy = r0.boxes.xyxy.cpu().numpy()
            cl = r0.boxes.cls.cpu().numpy()
            tid = r0.boxes.id
            tid = tid.cpu().numpy() if tid is not None else [-1] * len(cl)
            for b, c, t in zip(xyxy, cl, tid):
                # приводим к python-float: numpy-типы не сериализуются в JSON
                dets.append((float(b[0]) / W, float(b[1]) / H,
                             float(b[2]) / W, float(b[3]) / H))
                cls.append(r0.names[int(c)])
                ids.append(int(t))
        out.append({"name": f.name, "frame": idx, "size": (W, H), "dets": dets,
                    "classes": cls, "track_ids": ids,
                    "size_norm": (1.0, 1.0)})
    if skipped:
        print(f"  пропущено нечитаемых кадров: {skipped}")
    name = camera or (Path(images_dir).name if images_dir else "camera")
    return {name: out}


# ---------------------------------------------------------------- прогон

def run_camera(frames, camera, roi, args):
    tracker = IoUTracker(iou_thr=args.iou)
    dedup = Deduplicator(camera, roi, reid_frames=args.reid_frames,
                         reid_dist=args.reid_dist, cooldown=args.cooldown,
                         vehicle_classes=VEHICLE_CLASSES)
    for fr in frames:
        if "track_ids" in fr:  # детекции уже с треками (ByteTrack)
            obs = [(t, b, c, 1.0) for t, b, c in zip(fr["track_ids"], fr["dets"], fr["classes"])]
        else:
            dets = list(zip(fr["dets"], fr["classes"], [1.0] * len(fr["dets"])))
            obs = tracker.update(fr["frame"], dets)
        dedup.update(fr["frame"], obs, frame_size=fr.get("size_norm", fr["size"]))
    return dedup.report()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["cvat", "yolo"], required=True)
    ap.add_argument("--zip", help="архив CVAT (режим cvat)")
    ap.add_argument("--model", help="веса модели (режим yolo)")
    ap.add_argument("--images", help="папка кадров одной камеры (режим yolo)")
    ap.add_argument("--manifest", help="список кадров одной камеры по порядку (режим yolo)")
    ap.add_argument("--camera", help="имя камеры (режим yolo)")
    ap.add_argument("--masks", required=True, help="polygon_masks.json")
    ap.add_argument("--out", required=True)
    ap.add_argument("--iou", type=float, default=0.3, help="порог IoU для отладочного трекера")
    ap.add_argument("--reid-frames", type=int, default=25)
    ap.add_argument("--reid-dist", type=float, default=0.06)
    ap.add_argument("--cooldown", type=int, default=10)
    ap.add_argument("--conf", type=float, default=0.25)
    args = ap.parse_args()

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    if args.mode == "cvat":
        by_cam = frames_from_cvat(args.zip)
    else:
        by_cam = frames_from_yolo(args.model, args.images, manifest=args.manifest,
                                  camera=args.camera, conf=args.conf)

    result = {"mode": args.mode, "vehicle_classes": sorted(VEHICLE_CLASSES), "cameras": {}}
    # признак «это не видеопоток»: разные разрешения кадров => трекинг неприменим
    resolutions = {}
    for cam, frames in by_cam.items():
        resolutions[cam] = len({(round(f["size"][0]), round(f["size"][1])) for f in frames})
    print(f"{'камера':<18}{'кадров':>8}{'детекций':>10}{'в зоне':>9}{'ТС (событий)':>14}{'склеек':>8}{'коэф.дедуп':>12}")
    for camera, frames in sorted(by_cam.items()):
        roi = load_roi(Path(args.masks), camera)
        rep = run_camera(frames, camera, roi, args)
        rep["distinct_resolutions"] = resolutions.get(camera, 1)
        rep["tracking_applicable"] = resolutions.get(camera, 1) <= 2
        result["cameras"][camera] = rep
        print(f"{camera:<18}{len(frames):>8}{rep['detections_total']:>10}{rep['detections_in_zone']:>9}"
              f"{rep['unique_vehicles']:>14}{rep['reid_merges']:>8}{rep['dedup_coefficient']:>12.3f}")

    (out / "dedup_events.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=1, default=float), encoding="utf-8")
    print(f"\nзаписано: {out/'dedup_events.json'}")


if __name__ == "__main__":
    main()
