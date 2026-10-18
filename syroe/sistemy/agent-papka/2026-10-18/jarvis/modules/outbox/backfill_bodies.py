"""Восстановление тел ушедших писем в журнал отправки (bl-091).

Зачем: до 25.08.2026 modules/outbox/send.py клал в memory/outbox/sent.jsonl
только тему письма, без текста. modules/outbox/history.py подставлял в досье
ТЕКУЩИЙ шаблон, а шаблоны с тех пор переписывались (stil-holodnyh-pisem,
поправки Ильи). Досье показывало не то, что человек получил, — а по досье
строится второе касание.

Что делает: для каждой записи журнала без тела находит источник текста в
ИСТОРИИ ГИТА на момент отправки — последний коммит, сделанный не позже ts
записи, — и кладёт тело в журнал вместе с происхождением:

    "body"        — текст письма (без подписи: подпись не восстанавливаем)
    "body_exact"  — false: это реконструкция, а не то, что записал send.py
    "body_source" — "<файл>@<коммит>" — откуда взято

Записи, для которых источник не нашёлся, помечаются "body_lost": true —
history.py покажет «текст утерян» вместо подстановки сегодняшнего шаблона.

Запуск:  python3 -m modules.outbox.backfill_bodies [--dry]
Идемпотентен: записи с телом не трогает. Перед правкой кладёт копию журнала
в derived/scratch/.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from . import send as S

ROOT = Path(__file__).resolve().parents[2]
JOURNAL = ROOT / "memory" / "outbox" / "sent.jsonl"
SCRATCH = ROOT / "derived" / "scratch"

LETTERS = "modules/outbox/letters.json"
BRAND = "modules/outbox/brand.json"


def git(*args: str) -> str:
    out = subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True)
    return out.stdout if out.returncode == 0 else ""


def commit_at(path: str, ts: str) -> str:
    """Последний коммит файла, сделанный не позже ts. Пусто — файла тогда не было."""
    line = git("log", "-1", "--format=%h", f"--until={ts}", "--", path).strip()
    return line


def blob(path: str, commit: str) -> dict | list | None:
    if not commit:
        return None
    raw = git("show", f"{commit}:{path}")
    if not raw.strip():
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return None


def wave2_files() -> list[str]:
    """Партии второй волны — все, что видел гит когда-либо, а не только живые."""
    names = git("log", "--pretty=format:", "--name-only", "--",
                "modules/outbox/wave2/*.json").splitlines()
    return sorted({n.strip() for n in names if n.strip().endswith(".json")})


def find_body(rec: dict, w2: list[str], cache: dict) -> tuple[str, str]:
    """(тело, источник) для записи журнала. Пустое тело — источник не найден."""
    ts = rec.get("ts", "")
    num = str(rec.get("num") or "")
    addr = (rec.get("to") or "").strip().lower()

    # 1. Первая волна: тела лежали в letters.json, ключ — номер карточки.
    ci = commit_at(LETTERS, ts)
    letters = cache.setdefault(("L", ci), blob(LETTERS, ci)) if ci else None
    if isinstance(letters, dict) and num in letters:
        cb = commit_at(BRAND, ts)
        b = cache.setdefault(("B", cb), blob(BRAND, cb)) or {}
        body = S.render(letters[num].get("body", ""), b if isinstance(b, dict) else {})
        if body.strip():
            return body.strip(), f"{LETTERS}@{ci}"

    # 2. Вторая волна: тело лежит в карточке партии, ключ — адрес.
    if addr:
        for path in w2:
            ci = commit_at(path, ts)
            if not ci:
                continue
            cards = cache.setdefault((path, ci), blob(path, ci))
            if not isinstance(cards, list):
                continue
            for c in cards:
                if not isinstance(c, dict):
                    continue
                if (c.get("email") or "").strip().lower() != addr:
                    continue
                body = (c.get("body") or c.get("letter") or "").strip()
                if body:
                    return body, f"{path}@{ci}"
    return "", ""


def main() -> int:
    dry = "--dry" in sys.argv
    if not JOURNAL.exists():
        print("журнала нет — нечего восстанавливать")
        return 1

    recs = [json.loads(l) for l in JOURNAL.read_text(encoding="utf-8").splitlines()
            if l.strip()]
    w2 = wave2_files()
    cache: dict = {}
    done = lost = kept = 0

    for rec in recs:
        if rec.get("body"):
            kept += 1
            continue
        body, src = find_body(rec, w2, cache)
        if body:
            rec["body"] = body
            rec["body_exact"] = False
            rec["body_source"] = src
            rec.pop("body_lost", None)
            done += 1
            print(f"[восстановлено] {rec.get('ts')} {rec.get('num'):>5} "
                  f"{rec.get('to') or rec.get('real_to')}  <- {src}")
        else:
            rec["body_lost"] = True
            lost += 1
            print(f"[утеряно]       {rec.get('ts')} {rec.get('num'):>5} "
                  f"{rec.get('to') or rec.get('real_to')}")

    print(f"\nитого: {kept} с телом, {done} восстановлено из гита, {lost} утеряно")
    if dry:
        print("(сухой прогон, журнал не тронут)")
        return 0

    SCRATCH.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y-%m-%d-%H%M")
    copy = SCRATCH / f"{stamp}-sent.jsonl.bak"
    shutil.copy2(JOURNAL, copy)
    tmp = JOURNAL.with_suffix(".jsonl.tmp")
    tmp.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in recs),
                   encoding="utf-8")
    tmp.replace(JOURNAL)
    print(f"журнал переписан, копия прежнего: {copy.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
