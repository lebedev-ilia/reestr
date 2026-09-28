#!/usr/bin/env python3
"""Поиск по реестру. Отдаёт выдержки и указатели, а не документы целиком.

Сделано под агента с ограниченным контекстом: по умолчанию 10 попаданий
по ~200 знаков. Читать источник целиком — отдельным шагом, по указателю.

  nayti.py "прокси"
  nayti.py "цена Kwork" --razdel besedy --s 2026-09-01
  nayti.py "Олег" --skolko 20 --shire
  nayti.py --pokazat syroe/besedy/2026-09/2026-09-28__abc.jsonl.gz --metka <uuid>
"""
import argparse
import gzip
import json
import re
import sqlite3
import sys
from pathlib import Path

KOREN = Path(__file__).resolve().parent.parent
BAZA = KOREN / "ukazatel" / "reestr.db"


def zapros_fts(slova):
    """Простая фраза -> запрос FTS5: все слова обязательны, префиксный поиск."""
    if re.search(r'["*]|\bOR\b|\bAND\b|\bNOT\b', slova):
        return slova                      # пользователь знает, что делает
    kuski = [k for k in re.split(r"\W+", slova, flags=re.U) if len(k) > 1]
    if not kuski:
        return slova
    return " AND ".join(f'"{k}"*' for k in kuski)


def pokazat_istochnik(put, metka):
    f = KOREN / put
    if not f.exists():
        print(f"нет такого источника: {put}", file=sys.stderr)
        return 1
    if f.suffix in (".gz", ".jsonl"):
        fh = (gzip.open(f, "rt", encoding="utf-8") if f.suffix == ".gz"
              else f.open(encoding="utf-8", errors="replace"))
        with fh:
            for stroka in fh:
                h = json.loads(stroka)
                if not metka or h.get("uuid") == metka:
                    print(f"--- {h.get('kogda','')} · {h.get('kto','')}")
                    print(h.get("tekst", ""))
                    if metka:
                        return 0
    else:
        print(f.read_text(encoding="utf-8"))
    return 0


def main():
    p = argparse.ArgumentParser(add_help=True)
    p.add_argument("slova", nargs="?", default="", help="что ищем")
    p.add_argument("--razdel", default="", help="besedy | uroki | lyudi | ...")
    p.add_argument("--s", default="", help="с даты YYYY-MM-DD")
    p.add_argument("--po", default="", help="по дату YYYY-MM-DD")
    p.add_argument("--skolko", type=int, default=10)
    p.add_argument("--shire", action="store_true", help="выдержки длиннее")
    p.add_argument("--pokazat", default="", help="показать источник целиком")
    p.add_argument("--metka", default="", help="конкретный ход по uuid")
    a = p.parse_args()

    if a.pokazat:
        return pokazat_istochnik(a.pokazat, a.metka)

    if not a.slova:
        p.print_help()
        return 1
    if not BAZA.exists():
        print("указателя нет — сначала: python3 sluzhba/indeks.py", file=sys.stderr)
        return 1

    dlina = 48 if a.shire else 24        # в токенах snippet()
    db = sqlite3.connect(BAZA)
    usloviya, parametry = ["kuski MATCH ?"], [zapros_fts(a.slova)]
    if a.razdel:
        usloviya.append("razdel = ?")
        parametry.append(a.razdel)
    if a.s:
        usloviya.append("data >= ?")
        parametry.append(a.s)
    if a.po:
        usloviya.append("data <= ?")
        parametry.append(a.po)
    parametry.append(a.skolko)

    sql = f"""
        SELECT data, razdel, kto, istochnik, metka,
               snippet(kuski, 0, '«', '»', '…', {dlina})
        FROM kuski
        WHERE {' AND '.join(usloviya)}
        ORDER BY rank, data DESC
        LIMIT ?
    """
    try:
        stroki = db.execute(sql, parametry).fetchall()
    except sqlite3.OperationalError as e:
        print(f"не понял запрос: {e}", file=sys.stderr)
        return 1

    if not stroki:
        print("ничего не нашлось")
        return 0

    for data, razdel, kto, istochnik, metka, vyderzhka in stroki:
        kto = f" · {kto}" if kto else ""
        print(f"\n[{data or '—'}] {razdel}{kto}")
        print(f"  {vyderzhka}")
        print(f"  ← {istochnik}" + (f"  --metka {metka}" if razdel == "besedy" else ""))

    print(f"\nнайдено показано: {len(stroki)}. "
          f"Читать целиком: nayti.py --pokazat <источник> [--metka <uuid>]")
    db.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
