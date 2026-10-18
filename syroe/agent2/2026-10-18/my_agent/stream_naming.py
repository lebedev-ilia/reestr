#!/usr/bin/env python3
"""stream_naming.py — определение потока (камеры) и номера кадра по имени файла.

Вынесено отдельно намеренно: этот модуль используют и `build_polygon_mask.py`
(там нужны skimage/scipy), и `dedup_events.py` (работает на сервере в YOLO-окружении,
где этих пакетов нет). Тяжёлых зависимостей здесь быть не должно.

Установлено по разметке Заказчика (7101 кадр, `task_waste_up`):
  * ПАПКА сама по себе поток не задаёт: в папке `truck` лежат три разных префикса —
    `truck` (1820 кадров), `garbagetruck` (1776), `van` (306), причём номера кадров
    в них совпадают (0,1,2…), из-за чего сортировка по номеру перемешивала три
    независимые последовательности;
  * потоков всего 8: truck/{truck,garbagetruck,van}, truck_03/truck, van_02/van,
    van_03/van, garbagetruck_03/garbage, waste_01/waste_truck;
  * нумерация внутри потока непрерывна, у `garbage_` шаг 2 (635 кадров из диапазона 650).
"""
import re

__all__ = ["series_of", "frame_number"]


def series_of(name: str) -> str:
    """Поток = ПАПКА + ПРЕФИКС имени файла (без цифр и расширения).

    >>> series_of("truck/garbagetruck00000.jpg")
    'truck/garbagetruck'
    >>> series_of("garbagetruck_03/garbage03_00002.jpg")
    'garbagetruck_03/garbage'
    """
    folder = name.rsplit("/", 1)[0] if "/" in name else ""
    base = name.rsplit("/", 1)[-1]
    prefix = re.sub(r"\d+", "", base)
    prefix = re.sub(r"\.(jpg|jpeg|png|bmp)$", "", prefix, flags=re.I).rstrip("_")
    return f"{folder}/{prefix}" if folder else prefix


def frame_number(name: str) -> int:
    """Номер кадра — ПОСЛЕДНЕЕ число в имени.

    У `garbage03_00002.jpg` это 2, а не 3: первое число — часть имени серии.

    >>> frame_number("garbage03_00002.jpg")
    2
    """
    nums = re.findall(r"(\d+)", name.rsplit("/", 1)[-1])
    return int(nums[-1]) if nums else 0
