#!/usr/bin/env python3
"""Копилка вопросов разрешения, заданных в глухое время.

Зачем. Механизм ask (вопрос в ВК + ожидание «да») работает, только когда Илья
у экрана. Фоновый ход по определению идёт в его тишине, а ночью канал молчит
по правилу nochnaya-tishina. 25.08.2026 шесть вопросов из шести ушли в таймаут:
18 минут ожидания впустую и три остановленных дела (bl-083, след —
derived/audit/2026-08-25.md).

Что делает. В глухое время вопрос не задаётся вслепую: он падает строкой в
memory/questions.md, ход сразу получает отказ с объяснением «отложено, продолжай
без этого действия» и работает дальше. Копилка едет в системный промпт строкой
на вопрос — значит утром я вижу её сам и задаю всё пачкой (правило про внимание
Ильи: копить вопросы и спрашивать разом, §7 конституции).

Формат строки — markdown-чекбокс, чтобы файл читался глазами:

    - [ ] 2026-08-25 21:03 · Bash · rm derived/tmp · отложено: ночная тишина

Заданный вопрос помечается `- [x]` (mark_asked), строки не удаляются: история
того, что я хотел спросить, сама по себе полезна.
"""
from __future__ import annotations

import re
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

if __package__ in (None, ""):                       # запуск файлом, а не модулем
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from jarvis.core import config as C
    from jarvis.core import heartbeat as HB
else:
    from . import config as C
    from . import heartbeat as HB

# Тот же интервал, что в modules/vk/notify.py и core/heartbeat.py: ночью канал
# молчит целиком, значит ответа на вопрос не будет ни при каких условиях.
QUIET_FROM, QUIET_TO = 23, 8

HEAD = ("# Копилка вопросов\n\n"
        "Вопросы разрешения, заданные в глухое время: ночью или в фоновом ходе, "
        "когда Илья не у экрана. Задать пачкой, когда он на связи, и пометить "
        "заданные: `.venv/bin/python -m core.questions --asked`.\n")


def _now() -> datetime:
    return datetime.now(ZoneInfo(C.TZ))


def quiet_hours(now: datetime | None = None) -> bool:
    h = (now or _now()).hour
    return h >= QUIET_FROM or h < QUIET_TO


def deaf_reason(channel: str | None = None, now: datetime | None = None) -> str | None:
    """Почему Илья заведомо не ответит на вопрос прямо сейчас (или None).

    Канал берётся из пульса: HB.start(channel) пишет его на каждый ход. "vk" —
    ход начат сообщением самого Ильи, он ждёт ответа и вопрос уместен. Всё
    остальное ("self" — простой, "system" — ночной цикл и задания) идёт в его
    тишине: фоновый ход вообще запускается только после 10 минут молчания.
    """
    if quiet_hours(now):
        return "ночная тишина"
    if channel is None:
        # Внутрипроцессный канал точнее файлового: файл пишется на старте хода
        # и до следующего показывает прошлый.
        channel = HB.turn_channel() or HB.current_channel() or ""
    if channel != "vk":
        return "фоновый ход, Илья не у экрана"
    return None


# --------------------------------------------------------------------------
# Проверочный вызов
# --------------------------------------------------------------------------
# Живая проверка предохранителей шлюза (процедура perezapusk-demona-vk, шаг 6)
# обязана дёрнуть НАСТОЯЩИЙ опасный вызов через живой шлюз — иначе проверено
# ничего не будет. Побочный след 26.08.2026: такая проверка положила в копилку
# мусорный вопрос про POST на несуществующий домен, и его пришлось вычищать
# руками, иначе он уехал бы Илье в утренней пачке (bl-115).
#
# Признак — либо явный маркер в тексте вызова, либо адрес в зоне .invalid
# (RFC 2606: домен заведомо не существует, процедура берёт именно её).
PROBE_MARKER = "JARVIS_GATE_PROBE"
_PROBE_HOST = re.compile(r"[\w.-]+\.invalid\b", re.I)

PROBE_REPLY = ("Проверочный вызов: шлюз довёл дело до вопроса (решение ask). "
               "Вопрос Илье не задан и в копилку не положен — это и проверялось. "
               "Действие считай невыполненным и продолжай работу.")


def is_probe(preview: str) -> bool:
    """Проверочный ли это вызов — тот, чей ответ мне не нужен.

    Решение шлюза от маркера не меняется ни на йоту: он влияет только на то,
    услышит ли вопрос Илья — сейчас в чате или утром из копилки. Поэтому
    пометить им опасное действие бесполезно как обход: действие всё равно не
    выполнится, изменится только текст отказа.
    """
    text = str(preview or "")
    return PROBE_MARKER in text or bool(_PROBE_HOST.search(text))


def _lines() -> list[str]:
    if not C.QUESTIONS.exists():
        return []
    return C.QUESTIONS.read_text(encoding="utf-8").splitlines()


def add(tool: str, preview: str, why: str, context: str = "") -> str:
    """Отложить вопрос. Возвращает записанную строку."""
    stamp = _now().strftime("%Y-%m-%d %H:%M")
    text = " ".join(str(preview).split())[:300]
    row = f"- [ ] {stamp} · {tool} · {text} · отложено: {why}"
    if context:
        row += f" · {context}"
    C.QUESTIONS.parent.mkdir(parents=True, exist_ok=True)
    if not C.QUESTIONS.exists():
        C.QUESTIONS.write_text(HEAD, encoding="utf-8")
    with C.QUESTIONS.open("a", encoding="utf-8") as f:
        f.write(row + "\n")
    return row


def pending() -> list[str]:
    return [ln for ln in _lines() if ln.startswith("- [ ] ")]


def digest(limit: int = 12) -> str:
    """Блок для системного промпта. Пусто — блока не будет вовсе."""
    rows = pending()
    if not rows:
        return ""
    tail = rows[-limit:]
    out = [ln[6:] for ln in tail]
    if len(rows) > len(tail):
        out.insert(0, f"(показаны последние {len(tail)} из {len(rows)})")
    return "\n".join(f"- {ln}" for ln in out)


def mark_asked() -> int:
    """Пометить все накопленные вопросы заданными. Возвращает их число."""
    lines = _lines()
    n = 0
    for i, ln in enumerate(lines):
        if ln.startswith("- [ ] "):
            lines[i] = "- [x] " + ln[6:]
            n += 1
    if n:
        C.QUESTIONS.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return n


if __name__ == "__main__":
    if "--asked" in sys.argv:
        print(f"помечено заданными: {mark_asked()}")
    else:
        rows = pending()
        print(f"ждут Ильи: {len(rows)}")
        for r in rows:
            print(" ", r[6:])
