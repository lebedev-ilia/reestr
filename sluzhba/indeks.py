#!/usr/bin/env python3
"""Указатель: строит полнотекстовый поиск по всему реестру.

База пересоздаётся целиком из сырого и записей — она производная,
её не жалко потерять и не нужно хранить в git.
"""
import gzip
import json
import re
import sqlite3
import sys
from pathlib import Path

KOREN = Path(__file__).resolve().parent.parent
BAZA = KOREN / "ukazatel" / "reestr.db"

SOZDANIE = """
DROP TABLE IF EXISTS kuski;
CREATE VIRTUAL TABLE kuski USING fts5(
    tekst,                      -- что ищем
    razdel   UNINDEXED,         -- besedy | uroki | lyudi | ...
    data     UNINDEXED,         -- YYYY-MM-DD
    kto      UNINDEXED,         -- user | assistant | —
    istochnik UNINDEXED,        -- путь относительно корня
    metka    UNINDEXED,         -- uuid хода или заголовок записи
    tokenize = "unicode61 remove_diacritics 2"
);
"""

# Слишком короткое не индексируем — шум.
MINIMUM = 40


def chistka(t):
    t = re.sub(r"\s+", " ", t)
    return t.strip()


def iz_besed():
    """Ходы бесед. Claude Code при продолжении сессии копирует историю
    в новый файл, поэтому один и тот же ход встречается несколько раз —
    берём только первое вхождение по uuid."""
    vidali = set()
    papka = KOREN / "syroe" / "besedy"
    for f in sorted(list(papka.rglob("*.jsonl")) + list(papka.rglob("*.jsonl.gz"))):
        data = f.name[:10]
        otn = str(f.relative_to(KOREN))
        otkryt = (gzip.open(f, "rt", encoding="utf-8") if f.suffix == ".gz"
                  else f.open(encoding="utf-8", errors="replace"))
        with otkryt as fh:
            for stroka in fh:
                try:
                    h = json.loads(stroka)
                except Exception:
                    continue
                t = chistka(h.get("tekst", ""))
                if len(t) < MINIMUM:
                    continue
                klyuch = h.get("uuid") or hash(t)
                if klyuch in vidali:
                    continue
                vidali.add(klyuch)
                yield (t, "besedy", (h.get("kogda") or data)[:10],
                       h.get("kto") or "", otn, h.get("uuid") or "")


def iz_zapisey():
    papka = KOREN / "zapisi"
    for f in sorted(papka.rglob("*.md")):
        razdel = f.relative_to(papka).parts[0]
        otn = str(f.relative_to(KOREN))
        soderzh = f.read_text(encoding="utf-8", errors="replace")
        # дата из заголовка вида `data: 2026-09-28`
        m = re.search(r"^data:\s*(\d{4}-\d{2}-\d{2})", soderzh, re.M)
        data = m.group(1) if m else ""
        # режем по абзацам, чтобы выдача была короткой
        for abzac in re.split(r"\n\s*\n", soderzh):
            t = chistka(abzac)
            if len(t) < MINIMUM:
                continue
            yield (t, razdel, data, "", otn, f.stem)


def iz_sistem():
    """Память систем агентов. Раздел вида «jarvis-3/facts» — чтобы искать
    прицельно и чтобы несколько систем не смешивались: их уже три."""
    baza = KOREN / "syroe" / "sistemy"
    if not baza.is_dir():
        return
    for sistema in sorted(d for d in baza.iterdir() if d.is_dir()):
        for f in sorted(sistema.rglob("*")):
            if not f.is_file() or f.name == "_opis.json":
                continue
            otn_v_sisteme = f.relative_to(sistema)
            podrazdel = (otn_v_sisteme.parts[0]
                         if len(otn_v_sisteme.parts) > 1 else "koren")
            razdel = f"{sistema.name}/{podrazdel}"
            otn = str(f.relative_to(KOREN))
            try:
                if f.suffix == ".gz":
                    soderzh = gzip.open(f, "rt", encoding="utf-8",
                                        errors="replace").read()
                else:
                    soderzh = f.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            m = re.search(r"(\d{4}-\d{2}-\d{2})", f.name) or \
                re.search(r"^\s*(?:data|date|дата):\s*(\d{4}-\d{2}-\d{2})",
                          soderzh, re.M | re.I)
            data = m.group(1) if m else ""
            for abzac in re.split(r"\n\s*\n", soderzh):
                t = chistka(abzac)
                if len(t) < MINIMUM:
                    continue
                yield (t, razdel, data, "", otn, f.stem)


def main():
    BAZA.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(BAZA)
    db.executescript(SOZDANIE)

    vsego = {"besedy": 0, "zapisi": 0, "sistemy": 0}
    paket = []
    for r in iz_besed():
        paket.append(r)
        vsego["besedy"] += 1
        if len(paket) >= 2000:
            db.executemany("INSERT INTO kuski VALUES (?,?,?,?,?,?)", paket)
            paket = []
    for r in iz_sistem():
        paket.append(r)
        vsego["sistemy"] += 1
        if len(paket) >= 2000:
            db.executemany("INSERT INTO kuski VALUES (?,?,?,?,?,?)", paket)
            paket = []
    for r in iz_zapisey():
        paket.append(r)
        vsego["zapisi"] += 1
    if paket:
        db.executemany("INSERT INTO kuski VALUES (?,?,?,?,?,?)", paket)

    db.execute("INSERT INTO kuski(kuski) VALUES('optimize')")
    db.commit()
    razmer = BAZA.stat().st_size / 1024 / 1024
    print(f"кусков из бесед: {vsego['besedy']} · из памяти систем: "
          f"{vsego['sistemy']} · из записей: {vsego['zapisi']}")
    print(f"указатель: {BAZA.relative_to(KOREN)} ({razmer:.1f} МБ)")
    db.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
