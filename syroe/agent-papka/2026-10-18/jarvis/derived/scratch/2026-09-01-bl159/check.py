"""Одноразово: проверка when_str из modules/inbox/read.py (bl-159)."""
import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[3]))
from modules.inbox.read import when_str  # noqa: E402

cases = [
    "2026-08-31T10:43:12+03:00",   # Москва -> +7 часов
    "2026-08-31T23:10:00+00:00",   # UTC -> уже 1 сентября
    "2026-09-01T09:00:00+10:00",   # уже местное
    "",                            # даты нет
    "мусор",                       # кривой заголовок
    "2026-08-31T10:43:12",         # без зоны
]
for t in cases:
    s = when_str(t)
    print(f"{t!r:32} -> {s!r}  len={len(s)}")
