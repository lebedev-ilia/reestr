#!/usr/bin/env python3
"""Итоговый отбор и скоринг заказов FL.ru под связку «агент + пользователь».

Вход: my_agent/fl_candidates.json (собран fl_parser.py + fl_details.py).
Выход: my_agent/fl_final.json + таблица для чтения.

Факторы (кроме релевантности из fl_rank.py):
  + конкретика ТЗ (объём описания, цифры/сроки) — меньше риск «резинового» проекта;
  + бюджет (наш порог 10 000 ₽, желательно 30 000+);
  + свежесть (актуальность: смотрим даты публикации в датасете);
  + заказчик без отзывов/молодой аккаунт — выше шанс, что возьмут новичка;
  - партнёрские схемы, «ищу в команду на постоянку» (это не заказ, а найм);
  - размытое ТЗ («сделайте как у конкурента»).
"""
import argparse
import json
import re
from datetime import datetime
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).parent))
from fl_rank import score as relevance  # noqa: E402

NEG_HARD = [
    "партнёрство по привлечению", "партнерство по привлечению", "% с каждого чека",
    "% с заказа", "ищу человека, который будет находить клиентов", "агент по поиску клиентов",
    "менеджер по привлечению клиентов", "продюсер", "набор текста", "обзвон",
]
NEG_HIRING = [
    "в команду", "на долгосрочный проект", "долгосрочное сотрудничество", "парт-тайм",
    "part-time", "в штат", "ищем разработчика в команду", "резюме", "портфолио пришлите",
    "оклад", "испытательный срок",
]


def parse_date(s):
    if not s:
        return None
    try:
        return datetime.strptime(s, "%d.%m.%Y %H:%M")
    except Exception:  # noqa: BLE001
        return None


def spec_points(o):
    """Конкретика ТЗ: чем подробнее и конкретнее, тем выше."""
    d = (o.get("full_desc") or o.get("desc") or "")
    p = 0
    n = len(d)
    if n > 400:
        p += 3
    if n > 900:
        p += 3
    if n > 1800:
        p += 2
    if len(re.findall(r'\d', d)) > 25:
        p += 3
    if re.search(r'https?://', d):
        p += 1
    if re.search(r'тз|техническое задание|требовани', d, re.I):
        p += 2
    if re.search(r'апи|api|webhook|интеграц', d, re.I):
        p += 2
    if re.search(r'этап|приёмк|приемк|тестов', d, re.I):
        p += 2
    if n < 150:
        p -= 6  # совсем без деталей — риск бесконечных уточнений
    return p


def client_points(o):
    p = 0
    reg = o.get("client_reg") or ""
    rev = o.get("client_reviews")
    years = 0
    m = re.search(r'(\d+)\s*(год|года|лет)', reg)
    if m:
        years = int(m.group(1))
    months = 1 if "месяц" in reg else 0
    if reg:
        if years >= 2:
            p += 2          # заказчик с опытом: понятные процессы
        elif years == 0 and months:
            p += 1          # новичок: ниже конкуренция, выше шанс взять без отзывов
    if rev is not None:
        if rev >= 5:
            p += 2          # платит и не пропадает
        if rev == 0:
            p -= 1
    else:
        p += 1              # отзывов не видно — часто новый заказчик
    return p


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="inp", default="my_agent/fl_candidates.json")
    ap.add_argument("--out", default="my_agent/fl_final.json")
    ap.add_argument("--table", default="my_agent/fl_final.txt")
    ap.add_argument("--top", type=int, default=60)
    args = ap.parse_args()

    rows = json.load(open(args.inp, encoding="utf-8"))["orders"]
    scored = []
    for o in rows:
        text = ((o.get("title") or "") + " " + (o.get("full_desc") or o.get("desc") or "")).lower()
        r, hits, neg = relevance(o)
        s = r * 1.0 + spec_points(o) + client_points(o)
        flags = []
        if o.get("budget") and o["budget"] >= 30000:
            s += 3
        if o.get("budget") and o["budget"] >= 100000:
            s += 2
        if o.get("budget") is None:
            s -= 2
            flags.append("бюджет не указан")
        if any(k in text for k in NEG_HARD):
            s -= 40
            flags.append("не заказ (партнёрка/найм)")
        if any(k in text for k in NEG_HIRING):
            s -= 12
            flags.append("похоже на найм/постоянку")
        d = parse_date(o.get("published"))
        o = dict(o, final=s, rel=r, hits=hits, neg=neg, flags=flags,
                 published_dt=d.isoformat() if d else None)
        scored.append(o)

    scored.sort(key=lambda x: (-x["final"], x.get("published_dt") or ""))
    top = scored[:args.top]

    Path(args.out).write_text(json.dumps(top, ensure_ascii=False, indent=1), encoding="utf-8")
    lines = [f"кандидатов: {len(scored)}; в таблице: {len(top)}"]
    for i, o in enumerate(top, 1):
        b = f"{o['budget']:,}".replace(",", " ") if o.get("budget") else "—"
        rev = o.get("client_reviews")
        lines.append(
            f"\n#{i:02d} [{o['final']:.0f}] {b} ₽ | опубл. {o.get('published')} | "
            f"заказчик: {o.get('client_reg')} | отз.: {rev if rev is not None else '—'}"
            f" | флаги: {', '.join(o['flags']) or '—'}")
        lines.append(f"     {o['title']}")
        lines.append(f"     {o['url']}")
        d = (o.get("full_desc") or o.get("desc") or "")[:600]
        lines.append(f"     {d}")
    Path(args.table).write_text("\n".join(lines), encoding="utf-8")
    print("\n".join(lines[:6]))
    print(f"\n-> {args.out}, {args.table}")


if __name__ == "__main__":
    main()
