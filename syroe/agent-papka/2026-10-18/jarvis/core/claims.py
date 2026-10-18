"""Реестр занятой работы: одна работа отдаётся один раз.

Проблема, ради которой модуль существует (bl-072, самоаудит 25.08.2026).
Отдать работу можно двумя разными путями: инструментом Agent — подагент внутри
моего процесса, и mcp__memory__delegate — отдельное задание своим процессом.
Пути ничего не знают друг о друге. 25.08 восемь заданий из шестнадцати были
поставлены на работу, отданную подагенту за минуты до этого: два исполнителя
делали одно и то же, а один из них не находил на диске файл, который в этот
момент писал другой. Деньги потрачены дважды, результаты разошлись.

Как лечится. Перед раздачей работа заявляется в общий реестр. Реестр видит
обоих: живые задания читаются прямо из memory/jobs (там правда о своих
процессах), подагенты внутри хода — из derived/claims.json. Совпало с уже
занятым — второй запуск отбивается с указанием, кто это уже делает.

Совпадение считается по смыслу, а не по строке: бриф второй раз слово в слово
не пишется никогда. Берутся основы слов (core/morph) и считается доля общих —
мера Жаккара. Порог намеренно высокий: пропустить дубль дешевле, чем отбить
законную работу, поэтому спорное считается разной работой.

Заявка подагента живёт CLAIM_TTL и истекает сама. Освобождать её некому:
подагент из Agent умирает вместе с ходом, а между ходами меня нет
(факт agent-sobytiyniy). Заявка задания снимается статусом самого задания.

Осознанный повтор разрешён явным словом в тексте: «повтор осознан».
Предохранитель не должен становиться стеной — Илья вправе велеть переделать.
"""

from __future__ import annotations

import fcntl
import json
import os
import time
from pathlib import Path

from . import config as C
from . import morph

# Сколько живёт заявка подагента, поднятого инструментом Agent. Снять её
# некому — процесс хода умирает целиком, — поэтому она истекает по времени.
# Час с небольшим: дольше подагента внутри хода не бывает (потолок шагов).
CLAIM_TTL = 75 * 60

# Доля общих основ, при которой работа считается той же самой.
THRESHOLD = 0.55

# Второй признак: короткий текст целиком лежит внутри длинного. Промпт
# подагента («собери контакты студий Владивостока») и подробный бриф задания о
# том же — одна работа, но Жаккар на разной длине занижен вдвое. Порог тут выше
# и требует, чтобы короткий текст был не огрызком.
CONTAINED = 0.75
CONTAINED_MIN = 8

# Тексты короче — сравниваются целиком: на трёх словах Жаккар шумит.
MIN_STEMS = 5

# Тот же шум, но выше по длине (31.08.2026). Плановый заход постоянной роли
# заявляется формульной строкой «<роль> <режим> по расписанию <окно>»
# (modules/agents/schedule.py). В ней шесть основ, из которых четыре — общий
# шаблон, и Жаккар двух РАЗНЫХ ролей даёт 0.571 при пороге 0.55: заход критика
# глушился заходом тимлида, за 31 час пропало четыре захода в обе стороны.
# Тот же шаблон роняет и «critic work ... 490000» против «critic work ... 490001»
# (0.667) — то есть следующее окно той же роли.
# Лечится не порогом, а признанием очевидного: в формульном ключе значима
# КАЖДАЯ основа — имя роли, режим, окно, — и одна разошедшаяся означает другую
# работу. Поэтому две коротких строки сравниваются целиком, как и тексты короче
# MIN_STEMS. Граница нарочно стоит вплотную под CONTAINED_MIN: короткий промпт
# подагента, который ловится признаком «текст внутри текста», начинается с 8
# основ — его это правило не задевает, а живые брифы редко бывают короче
# десяти основ.
EXACT_MAX = CONTAINED_MIN - 1

# Явное разрешение повторить уже занятую работу.
OVERRIDE = "повтор осознан"

# Веер однотипной работы по разным объектам (27.08.2026). Семь подагентов
# доводили СЕМЬ РАЗНЫХ партий писем по одному лекалу брифа: тексты сходились
# на 93-96%, и шесть законных запусков были отбиты как дубль. Работа при этом
# разная — различает её один объект, тонущий в общем тексте.
# Лечится не ослаблением порога, а явным словом: строка «объект работы: X»
# в брифе. Разные X — разная работа независимо от схожести слов; одинаковые X
# — дубль, даже если слова разошлись (предохранитель тут строже Жаккара).
# Это не лазейка для обхода: соврать объектом можно ровно так же, как написать
# «повтор осознан». Предохранитель ловит невнимательность, а не умысел.
OBJECT_MARK = "объект работы:"


def store() -> Path:
    """Файл заявок. В derived: восстановим из memory/jobs и лога, уникального
    в нём нет — правило v-derived-tolko-vosstanovimoe."""
    return C.DERIVED / "claims.json"


def _key(text: str) -> set[str]:
    return morph.stems(text or "")


def _similar(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    if len(a) < MIN_STEMS or len(b) < MIN_STEMS:
        return 1.0 if a == b else 0.0
    if max(len(a), len(b)) <= EXACT_MAX:
        # Обе строки короткие: это формульный ключ, а не бриф. Значима каждая
        # основа — сравниваем целиком, приблизительное сходство тут шум.
        return 1.0 if a == b else 0.0
    common = len(a & b)
    jaccard = common / len(a | b)
    short = min(len(a), len(b))
    contained = common / short
    if short >= CONTAINED_MIN and contained >= CONTAINED:
        return max(jaccard, contained)
    return jaccard


def _target(text: str) -> str:
    """Явно названный объект работы: остаток строки после OBJECT_MARK.

    Пусто, если маркера нет — тогда всё решает сравнение по смыслу, как раньше.
    """
    low = (text or "").lower()
    i = low.find(OBJECT_MARK)
    if i < 0:
        return ""
    tail = low[i + len(OBJECT_MARK):].splitlines()[0]
    return " ".join(tail.split()).strip(" .,;:—-")[:200]


def _read() -> list[dict]:
    try:
        data = json.loads(store().read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except Exception:
        return []


def _write(rows: list[dict]) -> None:
    path = store()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, path)


def _fresh(rows: list[dict], now: float | None = None) -> list[dict]:
    now = time.time() if now is None else now
    return [r for r in rows if now - float(r.get("ts") or 0) < CLAIM_TTL]


def _job_claims() -> list[dict]:
    """Живые задания — правда о них лежит в их собственном журнале."""
    from . import jobs
    out = []
    for meta in jobs.by_status(*jobs.LIVE):
        # claim.txt — суть работы, если задание её выделило (роли из
        # modules/agents так и делают: их брифы почти целиком совпадают
        # описанием роли, и сравнение по всему тексту считало бы дублем
        # любые два вопроса одному специалисту). Нет файла — берём бриф.
        try:
            path = jobs.claim_path(meta["id"])
            if not path.exists():
                path = jobs.brief_path(meta["id"])
            text = path.read_text(encoding="utf-8")
        except OSError:
            text = meta.get("title", "")
        out.append({"ref": meta["id"], "kind": "job",
                    "title": meta.get("title", ""), "text": text,
                    "ts": 0.0})
    return out


def active() -> list[dict]:
    """Вся занятая прямо сейчас работа: задания и подагенты в ходах."""
    return _job_claims() + _fresh(_read())


def find(text: str) -> tuple[dict | None, float]:
    """Кто уже делает эту работу. Возвращает (заявка, схожесть)."""
    if OVERRIDE in (text or "").lower():
        return None, 0.0
    key = _key(text)
    mine = _target(text)
    best, score = None, 0.0
    for row in active():
        theirs = _target(row.get("text", ""))
        if mine and theirs:
            if mine != theirs:
                continue          # веер: объекты разные — работа разная
            s = 1.0               # тот же объект — дубль, как бы ни разошлись слова
        else:
            s = _similar(key, _key(row.get("text", "")))
        if s > score:
            best, score = row, s
    return (best, score) if score >= THRESHOLD else (None, score)


def claim(text: str, title: str = "", kind: str = "agent",
          ref: str | None = None) -> str:
    """Занять работу. Зовётся ПОСЛЕ того, как find() ничего не нашёл."""
    ref = ref or f"sub-{int(time.time())}"
    path = store()
    path.parent.mkdir(parents=True, exist_ok=True)
    lock = path.with_suffix(".lock")
    with open(lock, "a+", encoding="utf-8") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        try:
            rows = _fresh(_read())
            rows.append({"ref": ref, "kind": kind, "ts": time.time(),
                         "title": (title or (text or "").strip().splitlines()[0]
                                   if text else "")[:120],
                         "text": (text or "")[:4000]})
            _write(rows)
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)
    return ref


def release(ref: str) -> None:
    """Снять заявку досрочно (задание не встало, ход оборвался)."""
    path = store()
    if not path.exists():
        return
    lock = path.with_suffix(".lock")
    with open(lock, "a+", encoding="utf-8") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        try:
            _write([r for r in _fresh(_read()) if r.get("ref") != ref])
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)


def busy_message(row: dict, score: float) -> str:
    """Текст отбоя. Говорит, кто занят и как повторить осознанно."""
    who = ("задание " + row["ref"]) if row.get("kind") == "job" else "подагент этого хода"
    return (f"Эту работу уже делает {who}: «{row.get('title', '')[:90]}» "
            f"(совпадение {int(score * 100)}%). Второй исполнитель — двойная "
            f"трата и разъезд результатов (bl-072). Дождись результата "
            f"(mcp__memory__jobs_status) или возьми другую задачу. "
            f"Если это ВЕЕР однотипной работы по разным объектам (свой файл, "
            f"своя партия, свой клиент на каждого исполнителя) — начни каждый "
            f"бриф строкой «{OBJECT_MARK} <что именно>»: разные объекты "
            f"считаются разной работой, как бы ни совпадали слова. "
            f"Если нужен именно повтор — слова «{OVERRIDE}» в брифе.")
