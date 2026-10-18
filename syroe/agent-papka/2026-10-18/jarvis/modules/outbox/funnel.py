#!/usr/bin/env python3
"""Воронка холодной рассылки: что дала каждая партия и каждый каркас.

Зачем. К 26.08 ушло больше двадцати писем, ответов два — но какой сегмент и
какой каркас их дал, из журналов глазами не видно. Без этого выбор следующей
партии для ежедневных 15 писем — гадание. Здесь связка двух журналов сводится
в одну таблицу: строка на партию и строка на каркас.

Откуда числа (только журналы, ничего из памяти):
  * memory/outbox/sent.jsonl    — что ушло (пишет modules/outbox/send.py);
  * memory/outbox/replies.jsonl — что ответили (пишет modules/inbox/read.py);
  * modules/outbox/wave2/*.json — к какой партии относится карточка.

Связка «письмо ↔ ответ» тут своя НЕ строится: её уже делает history.collect()
(адрес карточки, пилоты отброшены, статус треда посчитан). Дублировать её
здесь значило бы завести второй источник правды, который разъедется с досье.

Единица счёта — КОНТАКТ, а не письмо. Отклик даёт человек, а не отправка:
если одному контакту ушло два письма, а ответ один, воронка «2 письма → 1
ответ» соврала бы в обе стороны. Писем показывается отдельным числом.

Статусы контакта (приоритет сверху вниз, как в history.collect):
    ответил   — живой ответ человека;
    отлуп     — письмо не дошло (bounce);
    автоответ — только автоответ, живого нет;
    не ушло   — ни одной успешной отправки (ошибка SMTP);
    молчит    — дошло, ответа нет.
Молчание делится по возрасту последнего письма: моложе FRESH_DAYS суток —
«рано» (ещё не молчание, просто не успели), старше — настоящее молчание.
Порог живёт здесь и только здесь.

Доля отклика считается от ДОШЕДШИХ (отправлено минус отлупы и «не ушло»):
процент от общего числа занижал бы каркас, которому достался пул с мёртвыми
адресами, — а виноват там сбор контактов, не текст письма.

Запуск (из корня агента):
    python3 -m modules.outbox.funnel              # пересобрать derived/logs/funnel.md
    python3 -m modules.outbox.funnel --print      # то же и напечатать
    python3 -m modules.outbox.funnel --json       # машинный вывод
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

from . import history as H  # noqa: E402

ROOT = H.ROOT
OUT = ROOT / "derived" / "logs" / "funnel.md"
WAVE2 = H.WAVE2

# Сколько суток письмо имеет право молчать, прежде чем это станет молчанием.
# Живые ответы 24-25.08 приходили в тот же день и на следующий; трёх суток
# хватает, чтобы отделить «не ответили» от «ещё не успели».
FRESH_DAYS = 3

SEGMENT_RU = {
    "1": "1 — веб-студии и агентства",
    "2": "2 — производство и опт",
    "3": "3 — vc.ru (не почта)",
    "4": "4 — интернет-магазины",
}

STATUSES = ["ответил", "автоответ", "отлуп", "не ушло", "молчит", "рано"]


def parties() -> dict[str, str]:
    """номер карточки -> имя партии («AF», «A»). Первая волна сюда не входит:
    её карточки живут в T-835 и партий не имеют."""
    out: dict[str, str] = {}
    if not WAVE2.is_dir():
        return out
    for path in sorted(WAVE2.glob("*.json")):
        name = path.stem  # seg4-partiya-AF
        party = name.split("partiya-")[-1] if "partiya-" in name else name
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        if not isinstance(data, list):
            continue
        for card in data:
            if isinstance(card, dict) and card.get("id"):
                out[str(card["id"])] = party
    return out


# Живой ответ ≠ интерес: DD Planet 26.08 ответила человеком и отказала одной
# строкой. Слова ниже — ПОДСКАЗКА глазам, а не классификация: колонку
# «ответили» они не меняют, счёт остаётся честным, решает всё равно Илья.
REFUSAL_WORDS = ("не актуально", "неактуально", "не интересно", "не нуждаемся",
                 "не планируем", "не требуется", "не ищем", "спасибо, нет",
                 "к сожалению", "не рассматриваем", "нет потребности")


def hint(text: str) -> str:
    low = (text or "").lower()
    return " · похоже на отказ" if any(w in low for w in REFUSAL_WORDS) else ""


def age_days(ts: str, now: datetime) -> float | None:
    dt = H.when(ts)
    if dt == datetime.max:
        return None
    return (now - dt).total_seconds() / 86400.0


def classify(thread: dict, now: datetime) -> str:
    """Статус контакта для воронки: history.collect + деление молчания по возрасту."""
    status = thread.get("status", "молчит")
    if status != "молчит":
        return status
    outs = [e for e in thread["events"] if e["dir"] == "out" and e["ok"]]
    last = outs[-1]["ts"] if outs else thread.get("last", "")
    age = age_days(last, now)
    if age is not None and age < FRESH_DAYS:
        return "рано"
    return "молчит"


def rows(now: datetime | None = None) -> list[dict]:
    """По контакту: партия, сегмент, каркас, сколько писем, статус."""
    now = now or datetime.now()
    party_of = parties()
    threads = H.collect()
    # Домен -> карточка: письмо человеку лично (n.a.kochubeev@nologostudio.ru)
    # ушло руками, номера карточки у него нет — но компания та же, что у
    # info@nologostudio.ru. Без этой сшивки один клиент даёт два «ответил»,
    # и воронка удваивает единственный живой интерес.
    by_domain = {addr.split("@")[-1]: t.get("num")
                 for addr, t in threads.items() if t.get("num")}
    out: list[dict] = []
    for addr, t in threads.items():
        num = str(t.get("num") or "")
        same = by_domain.get(addr.split("@")[-1], "")
        if num:
            party = party_of.get(num) or "волна 1"
            segment = num.split(".")[0]
        elif same:
            party, segment = "продолжение", ""
        else:
            party, segment = "вне рассылки", ""
        out.append({
            "follow_up": (not num) and str(same or ""),
            "addr": addr,
            "name": t.get("name") or "",
            "num": num,
            "party": party,
            "segment": segment,
            "template": t.get("template") or "—",
            "sent": t.get("n_ok", 0),
            "letters": t.get("n_out", 0),
            "status": classify(t, now),
            "last_out": next((e["ts"] for e in reversed(t["events"])
                              if e["dir"] == "out"), ""),
            "replied_at": next((e["ts"] for e in t["events"]
                                if e["dir"] == "in" and e["kind"] == "reply"), ""),
            "reply_text": next((e["body"] for e in t["events"]
                                if e["dir"] == "in" and e["kind"] == "reply"), ""),
        })
    # Ответ, пришедший на письмо-продолжение, принадлежит карточке: иначе
    # каркас, который этот интерес породил, останется с нулём в колонке
    # «ответили», а заслуга уедет в строку без партии.
    by_num = {r["num"]: r for r in out if r["num"]}
    for r in out:
        parent = by_num.get(r.get("follow_up") or "")
        if parent and r["status"] == "ответил" and parent["status"] != "ответил":
            parent["status"] = "ответил"
            parent["replied_at"] = parent["replied_at"] or r["replied_at"]
    out.sort(key=lambda r: (r["party"], r["num"]))
    return out


def group(items: list[dict], key: str) -> dict[str, dict]:
    agg: dict[str, dict] = {}
    for r in items:
        cell = agg.setdefault(r[key] or "—", {"контактов": 0, "писем": 0,
                                              **{s: 0 for s in STATUSES}})
        cell["контактов"] += 1
        cell["писем"] += r["letters"]
        cell[r["status"]] += 1
    for cell in agg.values():
        # Дошедшие = все, кроме отлупов и неотправленного.
        cell["дошло"] = cell["контактов"] - cell["отлуп"] - cell["не ушло"]
        cell["отклик"] = (cell["ответил"] / cell["дошло"] * 100) if cell["дошло"] else 0.0
    return agg


def table(agg: dict[str, dict], head: str) -> list[str]:
    lines = [f"| {head} | контактов | писем | дошло | ответили | автоответ | отлуп | не ушло | молчат | рано | отклик |",
             "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|"]
    for name in sorted(agg, key=lambda n: (-agg[n]["ответил"], -agg[n]["контактов"], n)):
        c = agg[name]
        lines.append(
            f"| {name} | {c['контактов']} | {c['писем']} | {c['дошло']} | {c['ответил']} | "
            f"{c['автоответ']} | {c['отлуп']} | {c['не ушло']} | {c['молчит']} | {c['рано']} | "
            f"{c['отклик']:.0f}% |")
    return lines


def report(now: datetime | None = None) -> str:
    now = now or datetime.now()
    all_items = rows(now)
    # В воронку идут только карточки рассылки. Ручные письма и продолжения
    # переписки — не новое касание: их вклад уже учтён на карточке.
    items = [r for r in all_items if r["num"]]
    extra = [r for r in all_items if not r["num"]]
    total = group(items, "party")
    whole = {"контактов": sum(c["контактов"] for c in total.values()),
             "писем": sum(c["писем"] for c in total.values())}
    for s in STATUSES:
        whole[s] = sum(c[s] for c in total.values())
    whole["дошло"] = whole["контактов"] - whole["отлуп"] - whole["не ушло"]
    отклик = (whole["ответил"] / whole["дошло"] * 100) if whole["дошло"] else 0.0

    L = [f"# Воронка холодной рассылки", "",
         f"Собрано: {now.strftime('%Y-%m-%d %H:%M')} · "
         f"`python3 -m modules.outbox.funnel`", "",
         "Числа только из журналов (`sent.jsonl` + `replies.jsonl`), единица счёта — "
         "контакт, а не письмо. Пилотные письма на ящики Ильи не в счёт. "
         f"«Рано» — письмо ушло меньше {FRESH_DAYS} суток назад, молчанием это ещё не считается. "
         "Отклик = живые ответы / дошло (без отлупов).", "",
         "## Итог", "",
         f"- контактов: {whole['контактов']}, писем отправлено: {whole['писем']}",
         f"- дошло: {whole['дошло']}, живых ответов: {whole['ответил']} ({отклик:.0f}%)",
         f"- отлупы: {whole['отлуп']}, автоответы: {whole['автоответ']}, "
         f"не ушло: {whole['не ушло']}",
         f"- молчат: {whole['молчит']}, ещё рано: {whole['рано']}", "",
         "## По партиям", ""]
    L += table(total, "партия")
    L += ["", "## По каркасам", ""]
    L += table(group(items, "template"), "каркас")

    seg = group(items, "segment")
    seg_named = {SEGMENT_RU.get(k, k or "—"): v for k, v in seg.items()}
    L += ["", "## По сегментам", ""]
    L += table(seg_named, "сегмент")

    answered = [r for r in items if r["status"] == "ответил"]
    L += ["", "## Кто ответил", ""]
    if answered:
        for r in sorted(answered, key=lambda r: r["replied_at"]):
            L.append(f"- {H.human(r['replied_at'])} · {r['name'] or r['addr']} "
                     f"({r['addr']}) · карточка {r['num'] or '—'}, каркас {r['template']}, "
                     f"партия {r['party']}{hint(r['reply_text'])}")
    else:
        L.append("- пока никто")

    bounced = [r for r in items if r["status"] in ("отлуп", "не ушло")]
    L += ["", "## Куда не дошло", ""]
    if bounced:
        for r in bounced:
            L.append(f"- {r['name'] or r['addr']} ({r['addr']}) · {r['status']} · "
                     f"карточка {r['num'] or '—'}, каркас {r['template']}, партия {r['party']}")
    else:
        L.append("- всё дошло")

    if extra:
        L += ["", "## Вне воронки (ручные письма и продолжения переписки)", ""]
        for r in extra:
            tail = (f"продолжение карточки {r['follow_up']}" if r["follow_up"]
                    else "вне рассылки")
            L.append(f"- {r['name'] or r['addr']} ({r['addr']}) · {r['status']} · {tail}")

    L += ["", "---", "",
          "Файл производный: сносится и пересобирается одной командой, "
          "уникального в нём нет.", ""]
    return "\n".join(L)


def main() -> None:
    ap = argparse.ArgumentParser(description="Воронка рассылки по партиям и каркасам")
    ap.add_argument("--print", action="store_true", dest="show",
                    help="напечатать отчёт в консоль")
    ap.add_argument("--json", action="store_true", help="машинный вывод, файл не пишется")
    args = ap.parse_args()

    if args.json:
        items = rows()
        print(json.dumps({"contacts": items,
                          "by_party": group(items, "party"),
                          "by_template": group(items, "template"),
                          "by_segment": group(items, "segment")},
                         ensure_ascii=False, indent=2))
        return

    text = report()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(text, encoding="utf-8")
    if args.show:
        print(text)
    else:
        print(f"воронка собрана: {OUT.relative_to(ROOT)} ({len(text)} знаков)")


if __name__ == "__main__":
    main()
