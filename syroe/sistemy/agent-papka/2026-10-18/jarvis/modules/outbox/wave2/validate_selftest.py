"""Самотест приёмки партий писем (validate.py). Живую память не трогает:
карточки — фикстуры во временном каталоге, живой пул читается только на чтение.

    cd "…/Agent" && jarvis/.venv/bin/python -m jarvis.modules.outbox.wave2.validate_selftest

Зачем он появился (bl-133, 27.08.2026). Приёмка требовала в теле каждого
письма ДОСЛОВНЫХ «15 минут» и корня «окуп» — и этим сама держала пул в виде
одной рассылки: пять агентов-сборщиков из двенадцати независимо получили
ошибку на «дайте двадцать минут» и «поговорим полчаса». Требование заменено
смысловым (ASK_RE / BENEFIT_RE), а чтобы замена не превратилась в «проверка
пропускает вообще всё», здесь стоят обе стороны: живые формулировки проходят,
письмо без просьбы или без довода — валится.
"""
from __future__ import annotations

import json
import re
import tempfile
from pathlib import Path

from jarvis.modules.outbox.wave2 import validate as V

fails: list[str] = []


def check(name: str, ok: bool) -> None:
    print(("  ок   " if ok else "  ПЛОХО") + f"  {name}")
    if not ok:
        fails.append(name)


ASK = re.compile(V.ASK_RE, re.I)
BEN = re.compile(V.BENEFIT_RE, re.I)

# Формулировки, на которых спотыкалась старая приёмка (взяты из отчётов
# агентов-сборщиков 27.08) — все обязаны проходить.
ASK_OK = [
    "Дайте двадцать минут, посмотрим на вашем процессе.",
    "Поговорим полчаса — и станет видно, что снимать первым.",
    "Готов на короткий созвон, времени займёт немного.",
    "Хватит четверти часа, чтобы понять, есть ли тут смысл.",
    "Если тема живая, давайте обсудим на созвоне.",
    "Уделите 15 минут, посмотрим на вашем проекте.",
]
BEN_OK = [
    "Часы людей вернутся в оплачиваемые проекты.",
    "Эта работа перестанет требовать рук совсем.",
    "Сэкономит менеджеру день в неделю.",
    "Разговор окупится одной задачей.",
    "Вы высвободите разработчика с рутины.",
]
# Письмо, где ни просьбы, ни довода нет вовсе.
NOTHING = ("Добрый день. Наша компания занимается автоматизацией с 2019 года, "
           "работаем по всей стране и делаем проекты под ключ.")


def test_semantic_patterns() -> None:
    for s in ASK_OK:
        check(f"просьба засчитана: {s[:48]}…", bool(ASK.search(s)))
    for s in BEN_OK:
        check(f"довод засчитан: {s[:48]}…", bool(BEN.search(s)))
    check("письмо без просьбы не притворяется просьбой", not ASK.search(NOTHING))
    check("письмо без довода не притворяется доводом", not BEN.search(NOTHING))


def _card(cid: str, dom: str, body: str) -> dict:
    return {"id": cid, "segment": "seg9", "template": "T1", "name": f"Ф{cid}",
            "city": "Владивосток", "what": "проверка", "email": f"hi@{dom}",
            "source_url": "https://example.ru/", "checked": "2026-08-26",
            "why": "фикстура", "hook": "зацепка из четырёх слов ровно",
            "subject": f"Автоматизация под задачи {cid}", "body": body}


def _errors(body: str, **over: object) -> list[str]:
    """Полный прогон check_file на одной карточке во временном каталоге."""
    card = _card("9.1", "zz-fixture.test", body)
    card.update(over)
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp) / "seg9-partiya-ZZ.json"
        p.write_text(json.dumps([card], ensure_ascii=False), encoding="utf-8")
        errs, _warns = V.check_file(p, {}, {}, {}, set(), set())
    return errs


HEAD = ("Добрый день.\n\n" + " ".join(f"своё{i}слово" for i in range(60)) +
        "\n\nМы берём один ручной процесс и сдаём вместо него работающую систему.\n\n")
TAIL = "\n\nС уважением, Илья Лебедев"


def test_check_file_both_sides() -> None:
    good = HEAD + ("Дайте полчаса, разберём на вашем процессе. Время людей "
                   "вернётся в оплачиваемые проекты.") + TAIL
    check("письмо без слов «15 минут» и «окуп» проходит приёмку целиком",
          not _errors(good))

    no_ask = HEAD + "Время людей вернётся в оплачиваемые проекты." + TAIL
    errs = _errors(no_ask)
    check(f"письмо без просьбы забраковано ({len(errs)})",
          any("просьб" in e for e in errs))

    no_ben = HEAD + "Дайте полчаса, разберём на вашем процессе." + TAIL
    errs = _errors(no_ben)
    check(f"письмо без довода забраковано ({len(errs)})",
          any("довод" in e for e in errs))

    errs = _errors(HEAD + NOTHING + TAIL)
    check(f"письмо без просьбы и без довода даёт две ошибки ({len(errs)})",
          len(errs) >= 2)


def test_city_resolves() -> None:
    """bl-058: карточка без города или со страной вместо города уедет по
    Москве молча и сломает обещание «письмо в 07:00 по адресату»."""
    good = HEAD + ("Дайте полчаса, разберём на вашем процессе. Время людей "
                   "вернётся в оплачиваемые проекты.") + TAIL
    check("город из пула (Владивосток) приёмку проходит", not _errors(good))
    for bad in ("Россия", "", "   ", "РФ, удалённо"):
        errs = _errors(good, city=bad)
        check(f"город {bad!r} забракован ({len(errs)})",
              any("город" in e for e in errs))


def test_live_pool() -> None:
    """Живой пул — только чтение: ни одно уже собранное письмо не должно
    выпасть из-за смены требований (иначе правка приёмки молча блокирует
    отправку целых партий)."""
    files = sorted(V.HERE.glob("seg*-partiya-*.json"))
    bad_ask, bad_ben, bad_city, total = [], [], [], 0
    for f in files:
        raw = json.loads(f.read_text(encoding="utf-8"))
        cards = raw.get("cards", raw) if isinstance(raw, dict) else raw
        for c in cards if isinstance(cards, list) else []:
            city = str(c.get("city", "")).strip()
            if not city or not V.GEO.offset_for(city)[1]:
                bad_city.append(f"{f.name}:{c.get('id')} «{city}»")
            body = str(c.get("body", ""))
            if not body:
                continue
            total += 1
            if not ASK.search(body):
                bad_ask.append(f"{f.name}:{c.get('id')}")
            if not BEN.search(body):
                bad_ben.append(f"{f.name}:{c.get('id')}")
    check(f"в пуле есть письма ({total} в {len(files)} партиях)", total > 0)
    check(f"у всех писем пула видна просьба (без просьбы: {bad_ask[:3]})",
          not bad_ask)
    check(f"у всех писем пула виден довод (без довода: {bad_ben[:3]})",
          not bad_ben)
    check(f"у всех карточек пула город резолвится в пояс "
          f"(не резолвится: {bad_city[:3]})", not bad_city)


def run() -> int:
    print("\n--- СМЫСЛОВЫЕ ПРОВЕРКИ: ПРОСЬБА И ДОВОД ---")
    test_semantic_patterns()
    print("\n--- ПРИЁМКА ЦЕЛИКОМ НА ФИКСТУРАХ ---")
    test_check_file_both_sides()
    print("\n--- ГОРОД РЕЗОЛВИТСЯ В ПОЯС ---")
    test_city_resolves()
    print("\n--- ЖИВОЙ ПУЛ WAVE2 (только чтение) ---")
    test_live_pool()

    print()
    if fails:
        print(f"ПЛОХО: {len(fails)} — " + "; ".join(fails))
        return 1
    print("всё зелёное")
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
