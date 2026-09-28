#!/usr/bin/env python3
"""Приём: расшифровки Claude Code -> сырое хранилище реестра.

Идемпотентен: повторный запуск добавляет только новое.
Ничего не удаляет и не правит — сырое только дописывается.
"""
import gzip
import hashlib
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

KOREN = Path(__file__).resolve().parent.parent
SYROE = KOREN / "syroe" / "besedy"

# Предохранитель. Урок 28.09: список запрещённых имён всегда отстаёт
# на один файл, защищает проверка содержимого.
SEKRETY = re.compile(
    r"(sk-ant-[A-Za-z0-9_\-]{20,}"
    r"|ghp_[A-Za-z0-9]{36}"
    r"|github_pat_[A-Za-z0-9_]{50,}"
    r"|AIza[A-Za-z0-9_\-]{30,}"
    r"|-----BEGIN (?:RSA |OPENSSH |EC )?PRIVATE KEY-----"
    r"|\"cookies\"\s*:\s*\["
    r"|xox[baprs]-[A-Za-z0-9\-]{10,})"
)


def istochniki():
    """Где Claude Code хранит расшифровки."""
    baza = Path.home() / ".claude" / "projects"
    if not baza.is_dir():
        return []
    return sorted(baza.glob("*/*.jsonl"))


def tekst_soobshcheniya(soderzhimoe):
    """Достаёт читаемый текст из message.content любой формы."""
    if isinstance(soderzhimoe, str):
        return soderzhimoe
    if not isinstance(soderzhimoe, list):
        return ""
    kuski = []
    for c in soderzhimoe:
        if not isinstance(c, dict):
            continue
        t = c.get("type")
        if t == "text":
            kuski.append(c.get("text", ""))
        elif t == "thinking":
            continue  # размышления в реестр не кладём
        elif t == "tool_use":
            imya = c.get("name", "?")
            vvod = json.dumps(c.get("input", {}), ensure_ascii=False)
            kuski.append(f"[инструмент {imya}] {vvod}")
        elif t == "tool_result":
            r = c.get("content")
            if isinstance(r, list):
                r = " ".join(
                    x.get("text", "") for x in r if isinstance(x, dict)
                )
            kuski.append(f"[результат] {r}")
        elif t == "image":
            # само изображение остаётся в расшифровке Claude Code;
            # здесь помечаем место, чтобы ход не терялся в поиске
            kuski.append("[изображение]")
    return "\n".join(k for k in kuski if k)


def razobrat(put):
    """JSONL Claude Code -> список ходов беседы."""
    hody = []
    seans = put.stem
    for stroka in put.open(encoding="utf-8", errors="replace"):
        try:
            d = json.loads(stroka)
        except Exception:
            continue
        if d.get("type") not in ("user", "assistant"):
            continue
        if d.get("isMeta") or d.get("isSidechain"):
            continue
        soob = d.get("message") or {}
        tekst = tekst_soobshcheniya(soob.get("content"))
        if not tekst.strip():
            continue
        hody.append(
            {
                "kto": soob.get("role") or d.get("type"),
                "kogda": d.get("timestamp"),
                "uuid": d.get("uuid"),
                "tekst": tekst,
            }
        )
    return seans, hody


def data_besedy(hody):
    for h in hody:
        if h.get("kogda"):
            try:
                return h["kogda"][:10]
            except Exception:
                pass
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def prinyat_odnu(put, tolko_proverka=False):
    seans, hody = razobrat(put)
    if not hody:
        return None
    data = data_besedy(hody)
    papka = SYROE / data[:7]           # по месяцам
    papka.mkdir(parents=True, exist_ok=True)
    cel = papka / f"{data}__{seans}.jsonl.gz"

    telo = "\n".join(json.dumps(h, ensure_ascii=False) for h in hody)

    # Секреты не выбрасывают беседу — они затираются. Терять историю нельзя,
    # выпускать ключ наружу тоже. Затёртое помечается, чтобы было видно.
    zatyorto = []

    def zamena(m):
        kusok = m.group(0)
        zatyorto.append(kusok[:12])
        vid = hashlib.sha256(kusok.encode()).hexdigest()[:8]
        return f"[СЕКРЕТ ЗАТЁРТ {vid}]"

    telo, skolko = SEKRETY.subn(zamena, telo)

    # уже принято и не изменилось — пропускаем
    otpechatok = hashlib.sha256(telo.encode()).hexdigest()[:16]
    metka = cel.with_suffix(".otpechatok")
    if cel.exists() and metka.exists() and metka.read_text().strip() == otpechatok:
        return {"seans": seans, "bez_izmeneniy": True, "hodov": len(hody)}

    if tolko_proverka:
        return {"seans": seans, "novoe": True, "hodov": len(hody)}

    with gzip.open(cel, "wt", encoding="utf-8") as f:
        f.write(telo)
    metka.write_text(otpechatok)
    return {"seans": seans, "zapisano": True, "hodov": len(hody),
            "data": data, "zatyorto": skolko, "obraztsy": zatyorto[:3]}


def main():
    tolko_proverka = "--proverka" in sys.argv
    puti = istochniki()
    if not puti:
        print("расшифровок не найдено")
        return 1

    novyh = bez_izm = sekretov = 0
    vsego_hodov = 0
    for p in puti:
        r = prinyat_odnu(p, tolko_proverka)
        if not r:
            continue
        vsego_hodov += r.get("hodov", 0)
        if r.get("zatyorto"):
            sekretov += r["zatyorto"]
            obr = ", ".join(r.get("obraztsy") or [])
            print(f"  затёрто секретов: {r['zatyorto']} в {r['seans']} ({obr}…)",
                  file=sys.stderr)
        if r.get("bez_izmeneniy"):
            bez_izm += 1
        else:
            novyh += 1

    print(f"источников: {len(puti)} · новых или обновлённых: {novyh} · "
          f"без изменений: {bez_izm} · затёрто секретов: {sekretov}")
    print(f"ходов всего: {vsego_hodov}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
