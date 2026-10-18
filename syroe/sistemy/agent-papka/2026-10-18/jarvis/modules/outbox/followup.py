#!/usr/bin/env python3
"""Второе касание: кому пора написать вдогонку и каким текстом.

Зачем. Первое письмо ушло, ответа нет — это ещё не отказ. Второе короткое
письмо по уже собранному контакту стоит дешевле, чем поиск нового: работа по
сбору карточки и по зацепке уже оплачена. Здесь считается, КОМУ пора, и
собирается черновик — ровно до момента показа Илье.

Чего этот модуль не делает — и не будет делать без отдельного решения:
  * НЕ отправляет. Ни строчки SMTP тут нет. Отправка любого письма от имени
    Ильи идёт только после его «да» (правило ot-imeni-ili-cherez-chernovik);
  * НЕ считает молчание сам. Статус контакта берётся из funnel.rows(), а тот
    из history.collect(): третьего источника правды по переписке заводить
    нельзя, иначе досье, воронка и второе касание разъедутся в числах.

Порог. QUIET_DAYS суток с ПОСЛЕДНЕГО ушедшего письма — не с первого: если
контакту уже писали вдогонку, отсчёт начинается заново, и человек не получит
третье письмо через день после второго.

Про отправку, когда она будет разрешена. modules/outbox/send.py устроен так,
что адрес, уже лежащий в журнале, пропускается ВСЕГДА (третий предохранитель).
Второе касание через send.run() поэтому не уйдёт — и это правильно: тот
предохранитель защищает от случайного повторного залпа по всему списку.
Отправлять вдогонку нужно отдельным путём, который повтор разрешает явно и
поштучно. Такого пути ещё нет — он собирается тогда, когда Илья скажет «да»
на конкретные тексты, а не заранее.

Тред. Message-ID первого письма в журнал рассылки не пишется (в sent.jsonl
он есть только у ручных писем), поэтому вдогонку письмо в исходный тред по
In-Reply-To не встанет: у получателя оно ляжет отдельным письмом с темой
«Re: …». Чинится это в send.py при отправке первого письма, а не здесь.

Запуск (из корня агента):
    python3 -m modules.outbox.followup             # пересобрать черновики
    python3 -m modules.outbox.followup --print     # то же и напечатать
    python3 -m modules.outbox.followup --json      # машинный вывод
    python3 -m modules.outbox.followup --days 4    # другой порог, разово
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

if __package__ in (None, ""):  # запуск файлом, а не модулем
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    __package__ = "modules.outbox"

from . import funnel as F  # noqa: E402
from . import history as H  # noqa: E402

ROOT = H.ROOT
OUT = ROOT / "derived" / "logs" / "followup.md"
TEMPLATES = Path(__file__).resolve().parent / "templates.json"

# Сколько суток молчания, прежде чем писать вдогонку. Шесть — чтобы письмо не
# догнало человека внутри одной рабочей недели: ушло в понедельник, вдогонку
# уходит не раньше следующего понедельника. Порог живёт здесь и только здесь.
QUIET_DAYS = 6

# Каркас второго касания по каркасу первого письма. Нет пары — контакт в
# черновики не идёт: сочинять текст на ходу под незнакомый каркас нельзя.
PAIRS = {"T1": "F1", "T4": "F4"}

MONTHS = ["января", "февраля", "марта", "апреля", "мая", "июня",
          "июля", "августа", "сентября", "октября", "ноября", "декабря"]


def templates() -> dict:
    return json.loads(TEMPLATES.read_text(encoding="utf-8"))


def ru_date(ts: str) -> str:
    dt = H.when(ts)
    if dt == datetime.max:
        return "в прошлый раз"
    return f"{dt.day} {MONTHS[dt.month - 1]}"


def candidates(now: datetime | None = None, days: int = QUIET_DAYS) -> list[dict]:
    """Контакты, которым пора вдогонку: молчат дольше days суток."""
    now = now or datetime.now()
    out = []
    for r in F.rows(now):
        if r["status"] != "молчит":
            continue
        if not r["num"]:            # вне рассылки и продолжения переписки
            continue
        age = F.age_days(r["last_out"], now)
        if age is None or age < days:
            continue
        r = dict(r)
        r["quiet_days"] = round(age, 1)
        r["fu_template"] = PAIRS.get(r["template"], "")
        out.append(r)
    out.sort(key=lambda r: -r["quiet_days"])
    return out


def first_subjects() -> dict[str, str]:
    """адрес -> тема ПЕРВОГО ушедшего письма, из журнала отправки.

    Не из карточки: у волны 1 карточек в wave2/*.json нет вовсе, а темы в
    карточках вторых волн с тех пор могли переписаться. Тред собирается по
    теме, поэтому она должна совпасть с той, что человек видит у себя.
    """
    out: dict[str, str] = {}
    for addr, t in H.collect().items():
        subj = next((e["subject"] for e in t["events"]
                     if e["dir"] == "out" and e["ok"] and e["subject"]), "")
        if subj:
            out[addr] = subj
    return out


def draft(row: dict, cards: dict, tpl: dict, subjects: dict | None = None) -> dict:
    """Черновик догоняющего письма по контакту. Скобок в тексте остаться не должно."""
    fu = tpl.get(row["fu_template"]) or {}
    card = cards.get(row["num"]) or {}
    subjects = subjects or {}
    first = (subjects.get(row["addr"]) or card.get("subject") or "").strip()
    body = (fu.get("skeleton") or "").format(WHEN=ru_date(row["last_out"]))
    return {
        "num": row["num"],
        "name": row["name"] or card.get("name") or row["addr"],
        "addr": row["addr"],
        "party": row["party"],
        "template": row["template"],
        "fu_template": row["fu_template"],
        "quiet_days": row["quiet_days"],
        "first_subject": first,
        "subject": f"Re: {first}" if first else "Ещё раз про субподряд",
        "body": body,
        "ready": bool(fu) and "[" not in body and "{" not in body,
    }


def build(now: datetime | None = None, days: int = QUIET_DAYS) -> dict:
    now = now or datetime.now()
    tpl, cards, subj = templates(), H.wave2_cards(), first_subjects()
    rows = candidates(now, days)
    drafts = [draft(r, cards, tpl, subj) for r in rows if r["fu_template"]]
    skipped = [r for r in rows if not r["fu_template"]]
    return {"now": now.strftime("%Y-%m-%d %H:%M"), "days": days,
            "drafts": drafts, "skipped": skipped}


def report(data: dict) -> str:
    L = ["# Второе касание: черновики", "",
         f"Собрано: {data['now']} · `python3 -m modules.outbox.followup`", "",
         f"Порог молчания — {data['days']} суток с последнего ушедшего письма. "
         "Кандидаты и статусы берутся из воронки, та — из журналов отправки и "
         "входящих. Отправки здесь нет: тексты уходят Илье черновиком, и без "
         "его «да» ни одно письмо не уходит.", ""]
    if not data["drafts"] and not data["skipped"]:
        L += ["## Кандидатов нет", "",
              "Никто ещё не молчит дольше порога — писать вдогонку рано. "
              "Это не поломка: значит, первые письма ушли меньше "
              f"{data['days']} суток назад.", ""]
    for d in data["drafts"]:
        L += [f"## {d['name']} · {d['addr']}", "",
              f"карточка {d['num']}, партия {d['party']}, первое письмо по "
              f"каркасу {d['template']}, молчит {d['quiet_days']} сут., "
              f"вдогонку по каркасу {d['fu_template']}"
              + ("" if d["ready"] else " · ТЕКСТ НЕ ГОТОВ, остались метки"), "",
              f"Тема: {d['subject']}", "", "```", d["body"], "```", ""]
    if data["skipped"]:
        L += ["## Без пары каркасов", "",
              "Первое письмо ушло по каркасу, для которого текста второго "
              "касания ещё нет. Пока не написан — контакт ждёт.", ""]
        L += [f"- {r['name']} ({r['addr']}) · каркас {r['template']}, "
              f"молчит {r['quiet_days']} сут." for r in data["skipped"]]
        L += [""]
    L += ["---", "",
          "Файл производный: сносится и пересобирается одной командой, "
          "уникального в нём нет."]
    return "\n".join(L)


def main() -> None:
    ap = argparse.ArgumentParser(description="Второе касание: кому пора и каким текстом")
    ap.add_argument("--print", action="store_true", dest="show")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--days", type=int, default=QUIET_DAYS)
    a = ap.parse_args()
    data = build(days=a.days)
    if a.json:
        print(json.dumps(data, ensure_ascii=False, indent=2))
        return
    text = report(data)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(text, encoding="utf-8")
    print(f"{OUT}: кандидатов {len(data['drafts'])}, "
          f"без каркаса {len(data['skipped'])}")
    if a.show:
        print()
        print(text)


if __name__ == "__main__":
    main()
