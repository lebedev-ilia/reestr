#!/usr/bin/env python3
"""Готовит то, что читается без единой запущенной команды.

Реестр должен одинаково работать везде: на другом компьютере, на сервере,
в наборе разработчика, в песочнице, в веб-нейросети. Там, где код запустить
можно, есть поиск. Там, где нельзя, — только чтение файлов, и для этого
здесь заранее собираются:

  ukazatel/karta.md          что вообще лежит в реестре, числами
  ukazatel/svodka.md         последнее состояние: решения, о чём говорили
  ukazatel/hronika/<год-месяц>.md   пересказ бесед по дням

Всё производное: строится заново из сырого, правится только здесь.
"""
import json
import re
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

KOREN = Path(__file__).resolve().parent.parent
UKAZ = KOREN / "ukazatel"
HRON = UKAZ / "hronika"

OBREZ_VOPROS = 400       # сколько знаков сообщения Ильи оставляем
OBREZ_OTVET = 300        # сколько от ответа


def hody():
    """Все ходы бесед, без повторов, по времени."""
    vidali = set()
    vse = []
    for f in sorted((KOREN / "syroe" / "besedy").rglob("*.jsonl")):
        for stroka in f.open(encoding="utf-8", errors="replace"):
            try:
                h = json.loads(stroka)
            except Exception:
                continue
            k = h.get("uuid")
            if k and k in vidali:
                continue
            if k:
                vidali.add(k)
            h["_fayl"] = str(f.relative_to(KOREN))
            vse.append(h)
    vse.sort(key=lambda h: h.get("kogda") or "")
    return vse


def pervaya_mysl(t, predel):
    """Первые осмысленные строки без служебного шума."""
    t = re.sub(r"\[инструмент [^\]]*\][^\n]*", "", t)
    t = re.sub(r"\[результат\][\s\S]*", "", t)
    t = re.sub(r"```[\s\S]*?```", " ", t)
    t = re.sub(r"[#*>`]", "", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t[:predel] + ("…" if len(t) > predel else "")


def hronika(vse):
    HRON.mkdir(parents=True, exist_ok=True)
    po_mesyacam = defaultdict(list)
    for h in vse:
        kogda = h.get("kogda") or ""
        if len(kogda) < 10:
            continue
        po_mesyacam[kogda[:7]].append(h)

    for mesyac, hs in sorted(po_mesyacam.items()):
        stroki = [
            f"# Беседы за {mesyac}",
            "",
            "Пересказ по дням: о чём спрашивал Илья и что отвечал агент.",
            "Дословно — в `syroe/besedy/`, поиск — `sluzhba/nayti.py`.",
            "",
        ]
        tekushchiy_den = None
        for h in hs:
            den = h["kogda"][:10]
            if den != tekushchiy_den:
                stroki += ["", f"## {den}", ""]
                tekushchiy_den = den
            t = h.get("tekst", "")
            if h.get("kto") == "user":
                if t.startswith("[результат]"):
                    continue          # это вывод команды, не речь Ильи
                m = pervaya_mysl(t, OBREZ_VOPROS)
                if m:
                    stroki.append(f"**Илья:** {m}")
            else:
                m = pervaya_mysl(t, OBREZ_OTVET)
                if m:
                    stroki.append(f"— {m}")
                    stroki.append("")
        (HRON / f"{mesyac}.md").write_text("\n".join(stroki) + "\n",
                                           encoding="utf-8")
    return sorted(po_mesyacam)


def karta(vse, mesyacy):
    razdely = defaultdict(int)
    for f in (KOREN / "zapisi").rglob("*.md"):
        razdely[f.relative_to(KOREN / "zapisi").parts[0]] += 1

    sistemy = {}
    for d in sorted((KOREN / "syroe" / "sistemy").glob("*")):
        if d.is_dir():
            sistemy[d.name] = sum(1 for _ in d.rglob("*") if _.is_file())

    faylov = 0
    opis = KOREN / "syroe" / "fayly" / "_opis.jsonl"
    if opis.exists():
        faylov = sum(1 for _ in opis.open(encoding="utf-8") if _.strip())

    dat = [h["kogda"][:10] for h in vse if h.get("kogda")]
    stroki = [
        "# Карта реестра",
        "",
        f"Собрано {datetime.now():%Y-%m-%d %H:%M}. Файл производный —"
        " не править руками.",
        "",
        "## Беседы",
        "",
        f"- ходов: **{len(vse)}**",
        f"- период: **{min(dat) if dat else '—'} — {max(dat) if dat else '—'}**",
        f"- месяцев: {len(mesyacy)} ({', '.join(mesyacy)})",
        "- пересказ по дням: `ukazatel/hronika/<год-месяц>.md`",
        "- дословно: `syroe/besedy/<год-месяц>/<дата>__<сеанс>.jsonl`",
        "",
        "## Файлы из бесед",
        "",
        f"- собрано: **{faylov}**, опись — `syroe/fayly/_opis.jsonl`",
        "",
        "## Память систем агентов",
        "",
    ]
    for imya, n in sistemy.items():
        stroki.append(f"- `{imya}`: {n} файлов — `syroe/sistemy/{imya}/`")
    if not sistemy:
        stroki.append("- пока нет")
    stroki += ["", "## Осмысленные записи", ""]
    for r, n in sorted(razdely.items()):
        stroki.append(f"- `{r}`: {n}")
    if not razdely:
        stroki.append("- пока нет")
    (UKAZ / "karta.md").write_text("\n".join(stroki) + "\n", encoding="utf-8")


def svodka(vse):
    """Последнее состояние: свежие записи и о чём шла речь недавно."""
    stroki = [
        "# Последнее состояние",
        "",
        f"Собрано {datetime.now():%Y-%m-%d %H:%M}. Файл производный.",
        "",
        "## Свежие осмысленные записи",
        "",
    ]
    zap = sorted((KOREN / "zapisi").rglob("*.md"),
                 key=lambda f: f.stat().st_mtime, reverse=True)[:15]
    if zap:
        for f in zap:
            soderzh = f.read_text(encoding="utf-8", errors="replace")
            m = re.search(r"^---[\s\S]*?^---\s*(.+)", soderzh, re.M)
            sut = pervaya_mysl(m.group(1) if m else soderzh, 220)
            stroki.append(f"- **{f.relative_to(KOREN / 'zapisi')}** — {sut}")
    else:
        stroki.append("- пока нет")

    stroki += ["", "## О чём говорили в последние дни", ""]
    posledniye = [h for h in vse[-400:] if h.get("kto") == "user"]
    for h in posledniye[-25:]:
        t = h.get("tekst", "")
        if t.startswith("[результат]"):
            continue
        m = pervaya_mysl(t, 220)
        if m:
            stroki.append(f"- `{h['kogda'][:10]}` {m}")

    stroki += [
        "",
        "## Как искать дальше",
        "",
        "Если можно запускать код:",
        "",
        "```bash",
        "python3 sluzhba/indeks.py          # один раз после клонирования",
        'python3 sluzhba/nayti.py "что ищем"',
        "```",
        "",
        "Если нельзя — читать `ukazatel/hronika/<год-месяц>.md`",
        "и `syroe/` напрямую.",
    ]
    (UKAZ / "svodka.md").write_text("\n".join(stroki) + "\n", encoding="utf-8")


def main():
    UKAZ.mkdir(parents=True, exist_ok=True)
    vse = hody()
    if not vse:
        print("бесед нет")
        return 1
    mesyacy = hronika(vse)
    karta(vse, mesyacy)
    svodka(vse)
    print(f"пересказ: {len(mesyacy)} месяцев · карта и сводка обновлены")
    return 0


if __name__ == "__main__":
    sys.exit(main())
