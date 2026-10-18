"""Счётчик повторов по телам и темам писем волны 2.

Зачем: validate.py ловит формальный брак (пустые поля, чужие плейсхолдеры,
битые адреса), но НЕ ловит однотипность — когда сборщик написал 12 писем
по одному лекалу и вставил в них разные названия. Такая партия проходит
валидатор и при этом читается получателями как рассылка.

Что делает: режет body на пословные n-граммы (по умолчанию 9 слов),
выкидывает подстановки (имена, города, суммы тут роли не играют — они
внутри n-граммы и сами её ломают) и показывает, какие куски текста
встречаются в нескольких письмах. Отдельно — скелеты тем.

Запуск:
    .venv/bin/python -m modules.outbox.wave2.dupcheck            # весь пул
    .venv/bin/python -m modules.outbox.wave2.dupcheck AH AG      # показать партии
    .venv/bin/python -m modules.outbox.wave2.dupcheck --min 3    # порог повторов

Буква — это фильтр ПОКАЗА, а не разбора: штампы всегда ищутся по всему пулу.
Иначе счётчик врёт в пользу партии — фраза, повторённая в 22 письмах пула,
внутри одной партии встречается дважды, штампом не считается и уходит в
«свой текст». Замер 25.08 на живом пуле: партия H при фильтре давала 37%
своего текста, при полном разборе — 26%; порог приёмки 35% она проходила,
не будучи переписанной. Считать по подмножеству — это мерить лекало самим
лекалом.

Читать вывод так: n-грамма в 2-3 письмах — норма (общие обороты вежливости
и подпись). В 5+ письмах — лекало, партию надо переписывать под адресатов.
Подпись и приветствие отсекаются по списку HAM ниже.
"""
from __future__ import annotations

import collections
import glob
import json
import os
import re
import sys

WAVE = os.path.dirname(os.path.abspath(__file__))
N = 9  # длина n-граммы в словах

# Куски, которые ОБЯЗАНЫ повторяться: подпись, приветствие, тело каркаса.
# Их повтор — не брак, поэтому из отчёта они убираются.
HAM = (
    "илья лебедев",
    "alverra",
    "с уважением",
    "добрый день",
)

TEMPLATES = os.path.join(os.path.dirname(WAVE), "templates.json")


def skeleton_grams() -> set[str]:
    """n-граммы неизменяемой части каркасов T1-T4.

    Всё, что в skeleton заключено в квадратные скобки, — это места под
    индивидуальные вставки: зацепка, окупаемость, перечень процессов.
    Именно их повтор и есть брак, поэтому скобочные куски в белый список
    НЕ попадают — они вырезаются вместе со своим содержимым.
    """
    out: set[str] = set()
    tpl = json.load(open(TEMPLATES, encoding="utf-8"))
    for key, val in tpl.items():
        if not isinstance(val, dict) or "skeleton" not in val:
            continue
        body = re.sub(r"\[[^\]]*\]", " \x00 ", val["skeleton"])
        for chunk in body.split("\x00"):
            out.update(ngrams(chunk))
    return out


def load(*, root: str | None = None) -> list[tuple[str, dict]]:
    """Весь пул целиком. Фильтр здесь НЕ применяется намеренно: штампы

    считаются по всему пулу, иначе счётчик врёт в пользу партии (см. шапку).

    `root` — только подмена каталога (нужна самотестам на фикстурах), и он
    keyword-only: позиционным аргументом сюда никогда не должен приезжать
    фильтр партий.
    """
    out = []
    for path in sorted(glob.glob(os.path.join(root or WAVE, "*.json"))):
        tag = os.path.basename(path).replace(".json", "").split("partiya-")[-1]
        raw = json.load(open(path, encoding="utf-8"))
        items = raw if isinstance(raw, list) else list(raw.values())[0]
        for card in items:
            out.append((tag, card))
    return out


def words(text: str) -> list[str]:
    return re.findall(r"[а-яёa-z0-9]+", text.lower())


def ngrams(text: str, n: int = N):
    ws = words(text)
    for i in range(len(ws) - n + 1):
        yield " ".join(ws[i : i + n])


def is_ham(gram: str, skel: set[str]) -> bool:
    return gram in skel or any(h in gram for h in HAM)


def glue(grams: list[str]) -> list[str]:
    """Склеить перекрывающиеся n-граммы одного набора писем в длинные фразы."""
    parts = [g.split() for g in grams]
    out: list[list[str]] = []
    for p in sorted(parts):
        for i, done in enumerate(out):
            for shift in range(1, N):
                if done[shift:] == p[: N - shift]:
                    out[i] = done + p[N - shift :]
                    break
                if p[shift:] == done[: N - shift]:
                    out[i] = p + done[N - shift :]
                    break
            else:
                continue
            break
        else:
            out.append(p)
    return [" ".join(p) for p in out]


def report_bodies(cards, min_hits: int, show_tags: set[str] | None = None) -> int:
    skel = skeleton_grams()
    seen = collections.defaultdict(set)  # n-грамма -> {id писем}
    for tag, card in cards:
        for gram in set(ngrams(card.get("body", ""))):
            if not is_ham(gram, skel):
                seen[gram].add(f"{tag}:{card['id']}")

    # сгруппировать по одинаковому набору писем и склеить в фразы
    by_ids = collections.defaultdict(list)
    for gram, ids in seen.items():
        if len(ids) < min_hits:
            continue
        # Счёт по всему пулу, показ — только там, где задета выбранная партия.
        if show_tags and not any(i.split(":")[0] in show_tags for i in ids):
            continue
        by_ids[frozenset(ids)].append(gram)

    print(f"\n=== ПОВТОРЫ ВНЕ КАРКАСА (n={N} слов, порог {min_hits} писем) ===")
    if not by_ids:
        print("чисто: ни одного лекала поверх каркаса")
    rows = sorted(by_ids.items(), key=lambda kv: (-len(kv[0]), sorted(kv[0])))
    for ids, grams in rows[:25]:
        tags = collections.Counter(i.split(":")[0] for i in ids)
        # Не most_common: при равном счёте он отдаёт порядок множества, а тот
        # меняется от запуска к запуску (хеши строк). Отчёт, который нельзя
        # сравнить с прошлым прогоном, не показывает, стало лучше или хуже.
        where = ", ".join(f"{t}×{c}" for t, c in sorted(tags.items(), key=lambda kv: (-kv[1], kv[0])))
        print(f"\n{len(ids)} писем ({where})")
        for phrase in sorted(glue(grams), key=len, reverse=True)[:3]:
            print(f"  «{phrase}»")
    return len(rows)


def report_subjects(cards, show_tags: set[str] | None = None) -> None:
    print("\n=== СКЕЛЕТЫ ТЕМ ===")
    skel = collections.defaultdict(list)
    for tag, card in cards:
        subj = card.get("subject", "")
        # скелет: тема без слов с заглавной буквы (имена, бренды, города)
        bones = " ".join(w for w in re.findall(r"[А-Яа-яЁёA-Za-z0-9\-]+", subj) if not w[:1].isupper())
        skel[bones].append(f"{tag}:{card['id']}")
    for bones, ids in sorted(skel.items(), key=lambda kv: -len(kv[1])):
        if show_tags and not any(i.split(":")[0] in show_tags for i in ids):
            continue
        if len(ids) >= 3:
            tags = collections.Counter(i.split(":")[0] for i in ids)
            where = ", ".join(f"{t}×{c}" for t, c in tags.most_common())
            print(f"{len(ids):>3} тем ({where}): «{bones}»")


def own_shares(cards, min_hits: int, skel: set[str] | None = None):
    """Доля своего текста по каждому письму: список кортежей, отсортированный.

    Отдельно от печати, чтобы это можно было проверять самотестом на
    фикстурах: (доля, своих слов, всего слов, партия, id, имя).
    Считать нужно ПО ВСЕМУ ПУЛУ — фильтр партий сюда не передаётся вовсе.
    """
    skel = skeleton_grams() if skel is None else skel
    freq = collections.Counter()
    for _, card in cards:
        freq.update(set(ngrams(card.get("body", ""))))

    scores = []
    for tag, card in cards:
        ws = words(card.get("body", ""))
        stamped = [False] * len(ws)
        for i in range(len(ws) - N + 1):
            gram = " ".join(ws[i : i + N])
            if gram in skel or freq[gram] >= min_hits:
                for j in range(i, i + N):
                    stamped[j] = True
        own = sum(1 for s in stamped if not s)
        scores.append((own / max(len(ws), 1), own, len(ws), tag, card["id"], card.get("name", "")))
    scores.sort()
    return scores


def report_own(cards, min_hits: int, show: int = 15,
               show_tags: set[str] | None = None) -> None:
    """Доля своего текста в каждом письме.

    Своё — слова, не покрытые ни каркасом T1-T4, ни штампом (n-граммой,
    которая встречается ещё в min_hits письмах). Письмо, где своего мало,
    получатель прочтёт как рассылку, а почтовый фильтр — как bulk.

    Частота штампов (freq) СЧИТАЕТСЯ ПО ВСЕМУ ПУЛУ, даже когда показывается
    одна партия: доля своего у партии не должна зависеть от того, с какими
    буквами её запустили.
    """
    scores = own_shares(cards, min_hits)
    avg = sum(s[0] for s in scores) / max(len(scores), 1)
    print(f"\n=== СВОЙ ТЕКСТ В ПИСЬМЕ (среднее по пулу {avg:.0%}) ===")
    if show_tags:
        scores = [s for s in scores if s[3] in show_tags]
    print(f"худшие {show}:")
    for share, own, total, tag, cid, name in scores[:show]:
        print(f"  {share:>4.0%}  {own:>3}/{total:<3} слов  {tag} {cid}  {name}")
    print("по партиям (среднее):")
    tag_avg = collections.defaultdict(list)
    for s in scores:
        tag_avg[s[3]].append(s[0])
    for tag, vals in sorted(tag_avg.items(), key=lambda kv: sum(kv[1]) / len(kv[1])):
        print(f"  {tag:>3}: {sum(vals)/len(vals):>4.0%}  ({len(vals)} писем)")


def main() -> None:
    args = [a for a in sys.argv[1:]]
    min_hits = 4
    if "--min" in args:
        i = args.index("--min")
        min_hits = int(args[i + 1])
        del args[i : i + 2]
    cards = load()
    show_tags = set(args) or None
    known = {tag for tag, _ in cards}
    unknown = sorted((show_tags or set()) - known)
    if unknown:
        print(f"ВНИМАНИЕ: таких партий нет: {', '.join(unknown)}")
    shown = sum(1 for tag, _ in cards if not show_tags or tag in show_tags)
    print(f"карточек в разборе: {len(cards)}"
          + (f", показываю {shown} ({', '.join(sorted(show_tags))})" if show_tags else ""))
    report_bodies(cards, min_hits, show_tags)
    report_own(cards, min_hits, show_tags=show_tags)
    report_subjects(cards, show_tags)


if __name__ == "__main__":
    main()
