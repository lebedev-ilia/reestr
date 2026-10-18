#!/usr/bin/env python3
"""Приёмка ВСЕХ партий второй волны одной командой: брак, доля своего, отправлено.

Зачем. Раньше приёмка шла циклом руками: `validate` на файл, потом `dupcheck`
на весь пул, потом глазами найти в его выводе строку своей партии, потом
`taken`, потом снова validate после правки. За 25.08 этот круг повторён
больше полутора сотен раз (bl-080). Здесь он собран в один прогон и одну
таблицу: строка на партию, вердикт словом, а не тремя выводами подряд.

Что считается:
  * ошибки и предупреждения — `validate.check_file`, тем же порядком файлов и
    с общими словарями seen_*, что и `validate --all` (иначе дубли между
    партиями не ловятся);
  * доля своего текста — `dupcheck.own_shares` ПО ВСЕМУ ПУЛУ, а не по партии
    (правило merilo-schitaetsya-po-vsemu-polyu: фильтр ограничивает показ);
  * отправлено — журнал `memory/outbox/sent.jsonl` по адресу карточки.

Вердикт партии:
    БРАК        — есть ошибки валидатора, в отправку нельзя;
    ДОРАБОТАТЬ  — ошибок нет, но своего текста меньше порога (по умолчанию
                  38%, столько же требует planner.MIN_SHARE);
    ГОТОВА      — порог взят, есть неотправленные карточки;
    ОТПРАВЛЕНА  — порог взят, неотправленных не осталось.

Запуск (из корня агента):
    python3 -m modules.outbox.wave2.status              # таблица по всем
    python3 -m modules.outbox.wave2.status --details    # + что именно чинить
    python3 -m modules.outbox.wave2.status AH AG        # показать эти партии
    python3 -m modules.outbox.wave2.status --min-share 0.35
    python3 -m modules.outbox.wave2.status --json

Буквы партий — фильтр ПОКАЗА. Разбор всегда идёт по всему пулу: доля своего
текста у партии не должна зависеть от того, с какими буквами её запустили.

Код возврата: 1, если среди показанных партий есть БРАК, иначе 0.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import dupcheck as D
from . import validate as V
from .. import send as S

MIN_SHARE = 0.38   # тот же порог, что у planner.MIN_SHARE
MIN_CARD_SHARE = 0.30  # пол на ОДНО письмо, тот же у planner.MIN_CARD_SHARE
MIN_HITS = 4       # порог штампа у dupcheck


def tag_of(path: Path) -> str:
    """Буква партии по имени файла — та же формула, что в dupcheck.load."""
    return path.stem.split("partiya-")[-1]


def collect(min_share: float = MIN_SHARE, min_hits: int = MIN_HITS,
            root: Path | None = None, sent: set[str] | None = None,
            min_card_share: float = MIN_CARD_SHARE) -> list[dict]:
    """Строка на партию: карточки, брак, доля своего, отправленное, вердикт.

    Доля партии — среднее, и среднее прикрывает брак: 26.08.2026 партия H
    брала порог (41%), а пять писем из восьми внутри неё были 22–28% своего
    текста, то есть почти шаблон (bl-105). Поэтому у партии есть ВТОРОЕ
    число: `weak` — письма ниже пола на одну карточку. Отдельные слабые
    письма не делают партию непроходной (остальные её письма годны), их
    выбрасывает поштучно planner.pool_cards.

    Чистая функция от каталога и журнала: `root` и `sent` подменяются в
    самотесте, поэтому проверять её можно на фикстурах, не трогая живой пул
    и не читая memory/.
    """
    root = Path(root) if root else V.HERE
    files = sorted(root.glob("*.json"))
    if sent is None:
        sent = set(S.journal_load().keys())

    old_doms, old_names = V.known_contacts()
    seen_ids: dict = {}
    seen_doms: dict = {}
    seen_names: dict = {}
    rows: dict[str, dict] = {}
    for f in files:
        errs, warns = V.check_file(f, seen_ids, seen_doms, seen_names, old_doms, old_names)
        try:
            cards = json.loads(f.read_text(encoding="utf-8"))
            cards = cards if isinstance(cards, list) else list(cards.values())[0]
        except Exception:
            cards = []
        tag = tag_of(f)
        row = rows.setdefault(tag, {
            "tag": tag, "files": [], "cards": 0, "sent": 0,
            "errors": [], "warns": [], "shares": [],
        })
        row["files"].append(f.name)
        row["cards"] += len(cards)
        row["sent"] += sum(1 for c in cards
                           if str(c.get("email", "")).strip().lower() in sent)
        row["errors"] += errs
        row["warns"] += warns

    # Доли — по всему пулу разом, одним расчётом (штамп виден только так).
    for share, _own, _total, tag, cid, name in D.own_shares(
            D.load(root=str(root)), min_hits=min_hits):
        if tag in rows:
            rows[tag]["shares"].append((share, cid, name))

    out = []
    for tag in sorted(rows):
        row = rows[tag]
        shares = sorted(row["shares"])
        row["avg"] = sum(s[0] for s in shares) / len(shares) if shares else 0.0
        row["low"] = sum(1 for s in shares if s[0] < min_share)
        row["weak"] = [s for s in shares if s[0] < min_card_share]
        row["worst"] = shares[0] if shares else None
        row["shares"] = shares
        if row["errors"]:
            row["verdict"] = "БРАК"
        elif row["avg"] < min_share:
            row["verdict"] = "ДОРАБОТАТЬ"
        elif row["sent"] >= row["cards"] and row["cards"]:
            row["verdict"] = "ОТПРАВЛЕНА"
        else:
            row["verdict"] = "ГОТОВА"
        out.append(row)
    return out


def print_table(rows: list[dict], min_share: float, details: bool = False) -> None:
    print(f"партий {len(rows)}, карточек {sum(r['cards'] for r in rows)}, "
          f"порог доведённости {min_share:.0%}\n")
    print(f"{'парт':<5}{'карт':>5}{'отпр':>6}{'ошиб':>6}{'предупр':>9}"
          f"{'своё':>7}{'ниже':>6}{'слаб':>6}  вердикт")
    for r in rows:
        print(f"{r['tag']:<5}{r['cards']:>5}{r['sent']:>6}{len(r['errors']):>6}"
              f"{len(r['warns']):>9}{r['avg']:>7.0%}{r['low']:>6}"
              f"{len(r.get('weak', [])):>6}  {r['verdict']}")

    weak_total = sum(len(r.get("weak", [])) for r in rows)
    if weak_total:
        print(f"\nписем ниже пола на карточку ({MIN_CARD_SHARE:.0%}) — {weak_total}; "
              "в отправку они не идут поштучно, даже из готовой партии:")
        for r in rows:
            for share, cid, name in r.get("weak", []):
                print(f"  ↓ {share:>4.0%} своего — {r['tag']} {cid} {name}")

    ready = [r for r in rows if r["verdict"] == "ГОТОВА"]
    left = sum(r["cards"] - r["sent"] for r in ready)
    print(f"\nготовых партий {len(ready)} ({', '.join(r['tag'] for r in ready) or '—'}), "
          f"в них неотправленных писем {left}")
    bad = [r for r in rows if r["verdict"] == "БРАК"]
    work = [r for r in rows if r["verdict"] == "ДОРАБОТАТЬ"]
    if bad:
        print(f"брак в партиях: {', '.join(r['tag'] for r in bad)}")
    if work:
        print("дорабатывать: " + ", ".join(
            f"{r['tag']} (+{(min_share - r['avg']) * 100:.0f} п.п., "
            f"{r['low']} писем ниже порога)" for r in work))

    if not details:
        return
    for r in rows:
        if r["verdict"] in ("ГОТОВА", "ОТПРАВЛЕНА"):
            continue
        print(f"\n--- {r['tag']} ({', '.join(r['files'])}) ---")
        for e in r["errors"][:20]:
            print("  ✗", e)
        if len(r["errors"]) > 20:
            print(f"  … и ещё {len(r['errors']) - 20} ошибок")
        for w in r["warns"][:10]:
            print("  ~", w)
        if len(r["warns"]) > 10:
            print(f"  … и ещё {len(r['warns']) - 10} предупреждений")
        for share, cid, name in r["shares"][:10]:
            if share < min_share:
                print(f"  ↓ {share:>4.0%} своего — {cid} {name}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Приёмка всех партий второй волны")
    ap.add_argument("tags", nargs="*", help="показать только эти партии (разбор всё равно по всему пулу)")
    ap.add_argument("--details", action="store_true", help="что именно чинить в непроходных партиях")
    ap.add_argument("--min-share", type=float, default=MIN_SHARE, help="порог доли своего текста (среднее по партии)")
    ap.add_argument("--min-card-share", type=float, default=MIN_CARD_SHARE,
                    help="пол на одно письмо: ниже — письмо не уйдёт даже из готовой партии")
    ap.add_argument("--min-hits", type=int, default=MIN_HITS, help="со скольких писем n-грамма считается штампом")
    ap.add_argument("--json", action="store_true", help="машинный вывод")
    args = ap.parse_args(argv)

    rows = collect(args.min_share, args.min_hits, min_card_share=args.min_card_share)
    want = {t.upper() for t in args.tags}
    unknown = sorted(want - {r["tag"] for r in rows})
    shown = [r for r in rows if not want or r["tag"] in want]

    if args.json:
        print(json.dumps([{k: v for k, v in r.items() if k != "shares"} for r in shown],
                         ensure_ascii=False, indent=1))
    else:
        if unknown:
            print(f"ВНИМАНИЕ: таких партий нет: {', '.join(unknown)}")
        print_table(shown, args.min_share, args.details)
    return 1 if any(r["verdict"] == "БРАК" for r in shown) else 0


if __name__ == "__main__":
    sys.exit(main())
