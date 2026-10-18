#!/usr/bin/env python3
"""Что уже занято: домены, названия и id — по первой волне и всем сданным партиям.

Нужно сборщику ПЕРЕД работой: список дедупа устаревает после каждой партии, и
переписывать его в задание руками — верный способ однажды прислать вчерашний.

    .venv/bin/python -m modules.outbox.wave2.taken           # для человека
    .venv/bin/python -m modules.outbox.wave2.taken --ids     # ещё и занятые id
"""
import json
import re
import sys

from .validate import EMAIL_RE, HERE, PUBLIC_MAIL, known_contacts


def main(argv: list[str]) -> int:
    doms, names = known_contacts()
    ids: dict[str, str] = {}
    wave2_doms: set[str] = set()
    wave2_names: set[str] = set()
    for f in sorted(HERE.glob("*.json")):
        try:
            cards = json.loads(f.read_text(encoding="utf-8"))
        except Exception as e:
            print(f"! {f.name} не читается: {e}", file=sys.stderr)
            continue
        for c in cards:
            em = str(c.get("email", ""))
            if EMAIL_RE.fullmatch(em):
                d = em.split("@")[-1].lower()
                if d not in PUBLIC_MAIL:
                    wave2_doms.add(d)
            if c.get("name"):
                wave2_names.add(str(c["name"]))
            if c.get("id"):
                ids[str(c["id"])] = f.name

    all_doms = sorted(doms | wave2_doms)
    print("# ЗАНЯТЫЕ ДОМЕНЫ (%d) — не брать ни один:" % len(all_doms))
    print(", ".join(all_doms))
    print()
    print("# ЗАНЯТЫЕ НАЗВАНИЯ второй волны (%d):" % len(wave2_names))
    print("; ".join(sorted(wave2_names)))
    print()
    print("# Названия первой волны (%d) — в T-835_60_KONTAKTOV.md, дубли по ним валидатор ловит сам." % len(names))
    if "--ids" in argv:
        print()
        print("# ЗАНЯТЫЕ id (%d):" % len(ids))
        def key(s: str):
            a, _, b = s.partition(".")
            return (int(a) if a.isdigit() else 99, int(b) if b.isdigit() else 0)
        print(", ".join(sorted(ids, key=key)))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
