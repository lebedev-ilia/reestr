"""Одноразово: закрыть bl-159 в очереди вызовом, а не правкой файла."""
import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[4]))
from jarvis.core import memory  # noqa: E402

memory.backlog_update(
    "bl-159",
    status="done",
    checkpoint=(
        "Сделано: в modules/inbox/read.py дата письма печатается в каждой строке "
        "всех разделов, включая 'мимо рассылки', в местном времени Ильи "
        "(when_str, ширина 16 знаков, заглушка '????-??-?? ??:??'); строки "
        "отсортированы свежими вверх. Живой прогон --days 4 --full даты показал, "
        "одноразовый скрипт для дат больше не нужен. Коммиты f9fb417, d31d31e."
    ),
    bump_cycle=True,
)
print("ok")
