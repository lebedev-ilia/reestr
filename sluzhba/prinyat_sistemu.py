#!/usr/bin/env python3
"""Приём памяти системы агентов в сырое хранилище реестра.

Системы пересоздаются, реестр один. Память любой системы кладётся сюда
как свидетельство: дословно, с сохранением путей, только дописыванием.

  prinyat_sistemu.py <путь к memory> [--imya jarvis-3]
"""
import argparse
import gzip
import hashlib
import json
import re
import shutil
import sys
from datetime import datetime
from pathlib import Path

KOREN = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))
from prinyat import SEKRETY  # тот же предохранитель, что и для бесед

TEKSTOVYE = {".md", ".txt", ".json", ".jsonl", ".yaml", ".yml", ".csv", ".log"}
PROPUSK = {".lock", ".pyc", ".session", ".session-journal", ".pem", ".key"}
PROPUSK_PAPKI = {"__pycache__", ".git", "node_modules", ".venv"}
SZHAT_OT = 200 * 1024          # крупное храним сжатым


def nado_propustit(p: Path) -> bool:
    if any(ch in PROPUSK_PAPKI for ch in p.parts):
        return True
    if p.suffix.lower() in PROPUSK:
        return True
    if p.name.startswith("."):
        return True
    return False


def prochitat(p: Path):
    """Текст файла или None, если это не текст."""
    if p.suffix.lower() not in TEKSTOVYE:
        return None
    try:
        return p.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        return None


def main():
    a = argparse.ArgumentParser()
    a.add_argument("put", help="путь к папке memory системы")
    a.add_argument("--imya", default="", help="имя системы, напр. jarvis-3")
    a.add_argument("--proverka", action="store_true")
    n = a.parse_args()

    istok = Path(n.put).expanduser().resolve()
    if not istok.is_dir():
        print(f"нет такой папки: {istok}", file=sys.stderr)
        return 1

    imya = n.imya or f"sistema-{datetime.now():%Y-%m-%d}"
    cel_koren = KOREN / "syroe" / "sistemy" / imya

    vzyato = propushcheno = ne_tekst = zatyorto_vsego = 0
    opis = []

    for p in sorted(istok.rglob("*")):
        if not p.is_file() or nado_propustit(p):
            propushcheno += 1
            continue
        otn = p.relative_to(istok)
        tekst = prochitat(p)
        if tekst is None:
            ne_tekst += 1
            continue

        tekst, skolko = SEKRETY.subn(
            lambda m: "[СЕКРЕТ ЗАТЁРТ "
                      f"{hashlib.sha256(m.group(0).encode()).hexdigest()[:8]}]",
            tekst,
        )
        zatyorto_vsego += skolko
        vzyato += 1

        if n.proverka:
            continue

        cel = cel_koren / otn
        cel.parent.mkdir(parents=True, exist_ok=True)
        if len(tekst.encode()) >= SZHAT_OT:
            cel = cel.with_suffix(cel.suffix + ".gz")
            with gzip.open(cel, "wt", encoding="utf-8") as f:
                f.write(tekst)
        else:
            cel.write_text(tekst, encoding="utf-8")

        opis.append(
            {
                "put": str(otn),
                "razdel": otn.parts[0] if len(otn.parts) > 1 else "koren",
                "znakov": len(tekst),
                "zatyorto": skolko,
            }
        )

    if not n.proverka:
        cel_koren.mkdir(parents=True, exist_ok=True)
        (cel_koren / "_opis.json").write_text(
            json.dumps(
                {
                    "imya": imya,
                    "istok": str(istok),
                    "prinyato": datetime.now().strftime("%Y-%m-%d %H:%M"),
                    "faylov": vzyato,
                    "zatyorto_sekretov": zatyorto_vsego,
                    "fayly": opis,
                },
                ensure_ascii=False,
                indent=1,
            ),
            encoding="utf-8",
        )

    print(f"система: {imya}")
    print(f"взято текстовых файлов: {vzyato} · пропущено служебных: "
          f"{propushcheno} · не текст: {ne_tekst}")
    if zatyorto_vsego:
        print(f"затёрто секретов: {zatyorto_vsego}", file=sys.stderr)
    razdely = {}
    for o in opis:
        razdely[o["razdel"]] = razdely.get(o["razdel"], 0) + 1
    if razdely:
        print("разделы: " + ", ".join(
            f"{k} {v}" for k, v in sorted(razdely.items(), key=lambda x: -x[1])))
    return 0


if __name__ == "__main__":
    sys.exit(main())
