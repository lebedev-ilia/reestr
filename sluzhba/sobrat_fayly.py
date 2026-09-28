#!/usr/bin/env python3
"""Сбор файлов, упомянутых в беседах, в хранилище реестра.

Расшифровка сохраняет только имя файла. Сам файл живёт в Загрузках или
на рабочем столе и однажды будет удалён. Здесь он остаётся навсегда.

Берём только то, что действительно упомянуто в беседах, — не папки целиком.
Складываем по отпечатку содержимого: один файл хранится один раз, сколько бы
раз его ни присылали.
"""
import argparse
import hashlib
import json
import re
import shutil
import sys
from datetime import datetime
from pathlib import Path

KOREN = Path(__file__).resolve().parent.parent
FAYLY = KOREN / "syroe" / "fayly"
OPIS = FAYLY / "_opis.jsonl"
BESEDY = Path.home() / ".claude" / "projects"

PREDEL = 40 * 1024 * 1024          # крупнее 40 МБ не берём
NE_BRAT_PAPKI = {"__pycache__", "node_modules", ".git", ".venv", "site-packages",
                 ".ssh", ".aws", ".gnupg", ".docker", "gh"}
NE_BRAT_IMENA = re.compile(
    r"(\.lock$|\.pyc$|\.session$|\.session-journal$|^\.env|\.pem$|\.key$"
    r"|^id_(rsa|dsa|ecdsa|ed25519)|\.ppk$|^known_hosts$|^authorized_keys$"
    r"|token|secret|credential|password)", re.I
)
# Производное реестра само себя не собирает.
# Расшифровки бесед тоже: их принимает prinyat.py, копия здесь была бы лишней.
NE_BRAT_PUTI = (str(KOREN),)
NE_BRAT_TOCHNO = re.compile(r"/\.claude/projects/[^/]+\.jsonl$")

PUT = re.compile(r"/Users/[A-Za-z0-9_.\-]+/[^\s\"'`,;:)\]}<>|]+")

SEKRETY_TEKST = re.compile(
    r"(sk-ant-[A-Za-z0-9_\-]{20,}|ghp_[A-Za-z0-9]{36}"
    r"|github_pat_[A-Za-z0-9_]{50,}|AIza[A-Za-z0-9_\-]{30,}"
    r"|-----BEGIN [A-Z ]*PRIVATE KEY-----"
    r"|ssh-(?:rsa|ed25519) AAAA"
    r"|hf_[A-Za-z0-9]{30,}"
    r"|sk-[A-Za-z0-9]{32,}"
    r"|AKIA[0-9A-Z]{16}"
    r"|xox[baprs]-[A-Za-z0-9\-]{10,}"
    r"|\"cookies\"\s*:\s*\[)"
)


def puti_iz_besed():
    """Все пути к файлам, упомянутые в расшифровках."""
    nayden = {}
    for f in sorted(BESEDY.glob("*/*.jsonl")):
        for stroka in f.open(encoding="utf-8", errors="replace"):
            try:
                d = json.loads(stroka)
            except Exception:
                continue
            kogda = (d.get("timestamp") or "")[:10]
            kuski = []

            soob = d.get("message") or {}
            sod = soob.get("content")
            if isinstance(sod, str):
                kuski.append(sod)
            elif isinstance(sod, list):
                for c in sod:
                    if not isinstance(c, dict):
                        continue
                    if c.get("type") == "text":
                        kuski.append(c.get("text", ""))
                    elif c.get("type") == "tool_use":
                        kuski.append(json.dumps(c.get("input", {}),
                                                ensure_ascii=False))

            a = d.get("attachment") or {}
            for pole in ("filePath", "path", "displayPath", "filename"):
                if isinstance(a.get(pole), str):
                    kuski.append(a[pole])

            for k in kuski:
                for m in PUT.finditer(k):
                    p = m.group(0).rstrip(".,;:")
                    nayden.setdefault(p, kogda)
    return nayden


def goditsya(p: Path) -> bool:
    if any(ch in NE_BRAT_PAPKI for ch in p.parts):
        return False
    if NE_BRAT_IMENA.search(p.name):
        return False
    if any(str(p).startswith(x) for x in NE_BRAT_PUTI):
        return False
    if NE_BRAT_TOCHNO.search(str(p)):
        return False
    return True


def uzhe_sobrano():
    if not OPIS.exists():
        return {}
    bylo = {}
    for s in OPIS.read_text(encoding="utf-8").splitlines():
        try:
            z = json.loads(s)
            bylo[z["otpechatok"]] = z
        except Exception:
            pass
    return bylo


def main():
    a = argparse.ArgumentParser()
    a.add_argument("--proverka", action="store_true", help="только показать")
    n = a.parse_args()

    FAYLY.mkdir(parents=True, exist_ok=True)
    bylo = uzhe_sobrano()
    naydeno = puti_iz_besed()

    novyh = propushcheno = otkazano = 0
    novye_zapisi = []
    obyom = 0

    for put, kogda in sorted(naydeno.items()):
        p = Path(put)
        if not p.is_file() or not goditsya(p):
            continue
        try:
            razmer = p.stat().st_size
        except OSError:
            continue
        if razmer == 0 or razmer > PREDEL:
            continue

        baytы = p.read_bytes()
        otpechatok = hashlib.sha256(baytы).hexdigest()[:32]
        if otpechatok in bylo:
            propushcheno += 1
            continue

        # Проверяем СОДЕРЖИМОЕ всего, что вообще читается как текст, —
        # независимо от расширения. Урок 28.09, повторённый здесь же:
        # список запрещённых имён всегда отстаёт на один файл. Именно так
        # в сбор попал приватный ключ без расширения.
        try:
            kak_tekst = baytы.decode("utf-8")
        except UnicodeDecodeError:
            kak_tekst = None          # двоичное: картинка, pdf, docx
        if kak_tekst and SEKRETY_TEKST.search(kak_tekst):
            otkazano += 1
            print(f"  отказано (секрет внутри): {p.name}", file=sys.stderr)
            continue

        zapis = {
            "otpechatok": otpechatok,
            "imya": p.name,
            "otkuda": str(p),
            "vpervye": kogda or datetime.now().strftime("%Y-%m-%d"),
            "bayt": razmer,
        }
        novye_zapisi.append(zapis)
        novyh += 1
        obyom += razmer

        if not n.proverka:
            cel = FAYLY / otpechatok[:2] / f"{otpechatok}{p.suffix.lower()}"
            cel.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(p, cel)

    if novye_zapisi and not n.proverka:
        with OPIS.open("a", encoding="utf-8") as f:
            for z in novye_zapisi:
                f.write(json.dumps(z, ensure_ascii=False) + "\n")

    print(f"путей в беседах: {len(naydeno)} · собрано новых: {novyh} · "
          f"уже было: {propushcheno} · отказано по секретам: {otkazano}")
    print(f"объём новых: {obyom/1024/1024:.1f} МБ")
    if n.proverka and novye_zapisi:
        for z in novye_zapisi[:15]:
            print(f"  {z['imya']}  ({z['bayt']//1024} КБ)  {z['otkuda']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
