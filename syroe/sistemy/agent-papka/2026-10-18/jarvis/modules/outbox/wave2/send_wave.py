"""Отправка писем второй волны: карточки лежат в wave2/*.json, а не в T-835.

Зачем отдельный файл. send.py умеет всё нужное — предохранители, журнал,
паузы, подпись, — но берёт карточки из markdown-списка первой волны и темы с
телами из subjects.json/letters.json. Вторая волна живёт партиями в json,
где тема и тело лежат прямо в карточке. Здесь только переходник: карточки
приводятся к формату send.run и подставляются вместо файловых источников.
Сама отправка, журнал и все три предохранителя остаются в send.py — второго
пути наружу быть не должно.

Запуск (из корня агента):
    python3 -m modules.outbox.wave2.send_wave seg4-partiya-AF          # черновики
    python3 -m modules.outbox.wave2.send_wave seg4-partiya-AF --send --limit 15
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .. import send as S

HERE = Path(__file__).resolve().parent


def load_batch(names: list[str]) -> list[dict]:
    """Карточки партий в порядке файлов и в порядке внутри файла."""
    cards: list[dict] = []
    for n in names:
        p = HERE / (n if n.endswith(".json") else n + ".json")
        if not p.exists():
            raise SystemExit(f"нет партии: {p}")
        for c in json.loads(p.read_text(encoding="utf-8")):
            c["_batch"] = p.stem
            cards.append(c)
    return cards


def to_send_format(cards: list[dict]) -> tuple[list[dict], dict, dict]:
    """(карточки для send.run, темы, письма) — ключ везде id карточки."""
    out, subj, letts = [], {}, {}
    for c in cards:
        num = c["id"]
        out.append({"num": num, "name": c["name"],
                    "channels": {"email": [c["email"]] if c.get("email") else []},
                    "letter": c.get("body", "")})
        if c.get("subject"):
            subj[num] = c["subject"]
        if c.get("body"):
            letts[num] = {"body": c["body"], "template": c.get("template", "T?")}
    return out, subj, letts


def main() -> None:
    ap = argparse.ArgumentParser(description="Отправка партий второй волны")
    ap.add_argument("batch", nargs="+", help="имена партий: seg4-partiya-AF ...")
    ap.add_argument("--send", action="store_true",
                    help="действительно отправить (без флага — только черновики)")
    ap.add_argument("--limit", type=int, default=3, help="потолок писем за запуск")
    ap.add_argument("--only", help="id карточек через запятую: 4.81,4.82")
    ap.add_argument("--pilot", metavar="ADDR", help="слать на этот адрес вместо контакта")
    args = ap.parse_args()

    cards, subj, letts = to_send_format(load_batch(args.batch))
    # Подмена источников: send.run зовёт их как функции модуля. Файлы
    # subjects.json/letters.json при этом не трогаются вовсе и не должны:
    # дом текста второй волны — сама карточка партии, и он один. Сводить
    # партии в letters.json намеренно НЕ будем (bl-044 снята 26.08): это
    # второй источник правды, который разъедется с правками Ильи в партиях.
    S.subjects = lambda: subj
    S.letters = lambda: letts

    only = [s.strip() for s in args.only.split(",")] if args.only else None
    sys.exit(S.run(cards, args.limit, not args.send, only, args.pilot))


if __name__ == "__main__":
    main()
