"""Самотест планировщика ежедневной рассылки (planner.py) — без единого
настоящего письма и без прикосновения к живой памяти. Запуск:

    cd "…/Agent" && jarvis/.venv/bin/python -m jarvis.modules.outbox.wave2.planner_selftest

Проверяется календарная механика (по образцу core/night_selftest.py: сравнивается
не "сейчас ровно 7:00", а "какой момент уже наступил и не пропущен слишком
сильно"), выбор партий и адресатов, суточная квота по факту, а не по плану, и
что реальная отправка (send_one) вызывается ТОЛЬКО там, где это ожидаемо —
здесь она подменена заглушкой, ни одного SMTP-соединения тест не открывает.

Живые файлы не трогает: derived/outbox_plan.json (STATE) и memory/outbox/
sent.jsonl (S.JOURNAL) подменяются на временные пути на время прогона и
возвращаются на место в finally — по правилу samotest-ne-pishet-v-zhivuyu-pamyat.
"""
from __future__ import annotations

import json
import shutil
import tempfile
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from jarvis.core import config as C
from jarvis.modules.outbox import send as S
from jarvis.modules.outbox.wave2 import dupcheck as D
from jarvis.modules.outbox.wave2 import geo as G
from jarvis.modules.outbox.wave2 import planner as P

fails: list[str] = []


def check(name: str, ok: bool) -> None:
    print(("  ок   " if ok else "  ПЛОХО") + f"  {name}")
    if not ok:
        fails.append(name)


def at(s: str) -> datetime:
    return datetime.fromisoformat(s).replace(tzinfo=ZoneInfo(C.TZ))


def card(cid: str, city: str, email: str = "", name: str = "") -> dict:
    return {"id": cid, "name": name or cid, "city": city,
            "email": email or f"{cid.replace('.', '_')}@example.test",
            "subject": f"тема {cid}", "body": "х" * 150, "template": "T1"}


def write_journal(path: Path, recs: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for r in recs:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


# --------------------------------------------------------------------------
# Часть 1. Календарь и часовые пояса — чистые функции, без файлов вовсе.
# --------------------------------------------------------------------------

def test_pure() -> None:
    # Смещения по образцу — Москва, Калининград, Владивосток лежат вне пула,
    # но проверяют формулу так же, как реальные записи CITY_TZ.
    off, ok = G.offset_for("Москва")
    check("Москва: +3, опознан", (off, ok) == (3, True))
    off, ok = G.offset_for("Калининград")
    check("Калининград: +2, опознан", (off, ok) == (2, True))
    off, ok = G.offset_for("Якутск")
    check("Якутск: +9, опознан", (off, ok) == (9, True))
    off, ok = G.offset_for("Тридевятое Царство")
    check("незнакомый город: Москва +3, resolved=False", (off, ok) == (3, False))
    off, ok = G.offset_for("")
    check("пустая строка: Москва +3, resolved=False", (off, ok) == (3, False))

    # Фоллбэк по базовому имени: уточнение через запятую/скобку, которого нет
    # в точном списке CITY_TZ, но базовый город есть в BASE_TZ.
    off, ok = G.offset_for("Екатеринбург, Свердловская область — новый адрес")
    check("фоллбэк по базовому имени срабатывает", (off, ok) == (5, True))

    due, offset, resolved = P.target_dt("Красноярск", date(2026, 8, 26))
    check("target_dt: 07:00 местного времени, смещение +7",
          due.hour == 7 and due.minute == 0 and offset == 7 and resolved)
    check("target_dt переводится в 00:00 UTC (07:00 - 7)",
          due.astimezone(ZoneInfo("UTC")).hour == 0)


def test_pool_ordering() -> None:
    """batch_order/candidates: партии — по убыванию доли своего текста,
    внутри партии — по порядку карточек, уже отправленные пропускаются."""
    pool = [
        ("LOW", card("LOW.1", "Москва")),      # ниже порога, не должна попасть
        ("HI", card("HI.1", "Омск", "a@x.test")),
        ("HI", card("HI.2", "Томск", "b@x.test")),
        ("MID", card("MID.1", "Тула", "c@x.test")),
    ]
    # own_shares считается по body — соберём тела так, чтобы доля была предсказуема:
    # у HI и MID тело всё "своё" (без повторов), у LOW тело совпадает с HI почти
    # целиком, значит у LOW доля своего текста ниже порога.
    stampword = " ".join(f"с{i}" for i in range(12))
    for tag, c in pool:
        c["body"] = f"{stampword} " + " ".join(f"{c['id']}сл{i}" for i in range(3))
    pool[0] = ("LOW", {**pool[0][1], "body": stampword})  # LOW — почти весь штамп

    # batch_order сам вызывает D.load() по реальным файлам (см.
    # test_live_pool_geo_coverage ниже) — здесь проверяем его логику
    # сортировки на синтетике напрямую через own_shares, минуя load().
    scores = D.own_shares(pool, min_hits=2, skel=set())
    by_tag: dict[str, list[float]] = {}
    for share, _o, _t, tag, _cid, _n in scores:
        by_tag.setdefault(tag, []).append(share)
    avg = {t: sum(v) / len(v) for t, v in by_tag.items()}
    check("LOW ниже HI/MID по доле своего текста", avg["LOW"] < avg["HI"])

    cands = P.candidates(limit=10, pool=[(t, c) for t, c in pool if t != "LOW"],
                         sent=set())
    check("candidates отдаёт все неотправленные карточки не-LOW партий",
          [c["id"] for _t, c in cands] == ["HI.1", "HI.2", "MID.1"])

    cands2 = P.candidates(limit=10, pool=[(t, c) for t, c in pool if t != "LOW"],
                          sent={"a@x.test"})
    check("уже отправленный адрес выпадает из кандидатов",
          [c["id"] for _t, c in cands2] == ["HI.2", "MID.1"])

    cands3 = P.candidates(limit=1, pool=[(t, c) for t, c in pool if t != "LOW"],
                          sent=set())
    check("candidates уважает limit", len(cands3) == 1 and cands3[0][1]["id"] == "HI.1")


def test_build_plan_and_due_now() -> None:
    pool = [("B", card("B.1", "Омск", "o@x.test")),      # +6
           ("B", card("B.2", "Москва", "m@x.test")),     # +3
           ("B", card("B.3", "Якутск", "y@x.test"))]     # +9
    day = date(2026, 8, 26)
    state = P.build_plan(day, pool=pool, sent=set(), limit=10,
                         built_at=at("2026-08-25T20:00"))
    check("build_plan: три карточки, три разных due", len(state["items"]) == 3)
    dues = {i["num"]: i["due"] for i in state["items"]}
    check("Омск (+6): 07:00 в поясе +06:00", dues["B.1"].endswith("07:00:00+06:00"))
    check("Москва (+3): 07:00 в поясе +03:00", dues["B.2"].endswith("07:00:00+03:00"))
    check("Якутск (+9): 07:00 в поясе +09:00", dues["B.3"].endswith("07:00:00+09:00"))

    # Момент "Омска" (UTC+6) в абсолютном времени — 01:00 UTC 26.08.
    # На который час Владивостока (UTC+10) это приходится?
    omsk_abs = datetime.fromisoformat(dues["B.1"])
    vlad_local = omsk_abs.astimezone(ZoneInfo(C.TZ))
    check("07:00 Омска приходится на утро/день Владивостока, тот же календарный день",
          vlad_local.date() == day and 8 <= vlad_local.hour <= 15)

    # due_now: до момента — не due; в окне допуска (<=3ч) — due; дальше — уже нет.
    # Проверяется на плане с ОДНОЙ карточкой: три письма из B выше нарочно в
    # разных поясах, и их окна допуска пересекаются по абсолютному времени
    # (Якутск и Омск разнесены ровно на 3 часа) — граничная проверка на них
    # была бы проверкой суммы окон, а не одного.
    one = P.build_plan(day, pool=[("B", card("B.1", "Омск", "o@x.test"))],
                       sent=set(), limit=10, built_at=at("2026-08-25T20:00"))
    before = omsk_abs - timedelta(minutes=1)
    inside = omsk_abs + timedelta(hours=2, minutes=59)
    after = omsk_abs + timedelta(hours=3, minutes=1)
    check("до момента — не due", P.due_now(one, before, sent=set()) == [])
    got = P.due_now(one, inside, sent=set())
    check("в окне допуска (<=3ч после) — due, ровно письмо Омска",
          len(got) == 1 and got[0]["num"] == "B.1")
    check("больше 3ч после момента — уже не due (не досылаем вдогонку)",
          P.due_now(one, after, sent=set()) == [])
    check("уже отправленный адрес не due, даже если момент наступил",
          P.due_now(one, inside, sent={"o@x.test"}) == [])


# --------------------------------------------------------------------------
# Часть 2. Состояние в derived/ и суточная квота по журналу — на подменённых
# путях, живые файлы не трогаются.
# --------------------------------------------------------------------------

def test_state_and_quota() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="planner-selftest-"))
    live_state, live_journal = P.STATE, S.JOURNAL
    P.STATE = tmp / "outbox_plan.json"
    S.JOURNAL = tmp / "sent.jsonl"
    try:
        # ensure_plan до START_DAY — план не строится вовсе.
        check("до 26.08 план не строится",
              P.ensure_plan(at("2026-08-25T12:00")) is None
              and not P.STATE.exists())

        pool = [("Q", card("Q.1", "Москва", "q1@x.test")),
               ("Q", card("Q.2", "Пермь", "q2@x.test"))]
        # Подменяем pool_cards на время построения плана — ensure_plan сам
        # вызывает candidates()/pool_cards() без параметров, поэтому здесь
        # подменяется candidates целиком.
        real_candidates = P.candidates
        P.candidates = lambda limit=P.QUOTA, pool=None, sent=None: real_candidates(
            limit, pool if pool is not None else [(t, c) for t, c in pool_ref], sent)
        pool_ref = pool
        try:
            state = P.ensure_plan(at("2026-08-26T06:00"))
        finally:
            P.candidates = real_candidates
        check("план на 26.08 построен и записан на диск",
              state is not None and state["day"] == "2026-08-26"
              and P.STATE.exists())
        check("живой derived/outbox_plan.json не тронут (свой временный путь)",
              live_state != P.STATE)

        # Повторный вызов в тот же день не пересобирает план (сравнение по
        # built_at — второй вызов должен вернуть то же состояние).
        again = P.ensure_plan(at("2026-08-26T06:30"))
        check("в тот же день план не пересобирается повторно",
              again["built_at"] == state["built_at"])

        forced = P.ensure_plan(at("2026-08-26T06:31"), force=True)
        check("--rebuild пересобирает план даже в тот же день",
              forced["built_at"] != state["built_at"])

        # Суточная квота считается по журналу, а не по плану.
        write_journal(S.JOURNAL, [
            {"ts": "2026-08-26T07:05:00", "to": "q1@x.test", "ok": True},
            {"ts": "2026-08-26T09:00:00", "to": "x@other.test", "ok": True},
            {"ts": "2026-08-25T23:00:00", "to": "y@other.test", "ok": True},  # вчера
            {"ts": "2026-08-26T07:06:00", "to": "z@other.test", "ok": False},  # ошибка
        ])
        n = P.count_sent_today(at("2026-08-26T10:00"))
        check("суточный счёт — только ok=true за сегодняшний календарный день (2, не 4)",
              n == 2)
    finally:
        P.STATE, S.JOURNAL = live_state, live_journal
        shutil.rmtree(tmp, ignore_errors=True)


# --------------------------------------------------------------------------
# Часть 3. tick(): оркестрация. send_one/rebuild_history подменены заглушками
# — ни одного реального письма, ни одной пересборки истории по-настоящему.
# --------------------------------------------------------------------------

def test_tick_orchestration() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="planner-selftest-tick-"))
    live_state, live_journal = P.STATE, S.JOURNAL
    live_send_one, live_rebuild = P.send_one, P.rebuild_history
    P.STATE = tmp / "outbox_plan.json"
    S.JOURNAL = tmp / "sent.jsonl"

    sent_log: list[str] = []
    rebuild_calls: list[int] = []

    def fake_send_one(card_dict: dict) -> bool:
        # Симулируем реальную отправку: пишем во ПОДМЕНЁННЫЙ журнал (не
        # настоящий — S.JOURNAL уже указывает на tmp), как это сделал бы
        # send.py, но без SMTP.
        sent_log.append(card_dict["id"])
        rec = {"ts": datetime.now().isoformat(timespec="seconds"),
              "to": card_dict["email"], "ok": True}
        with S.JOURNAL.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        return True

    def fake_rebuild() -> None:
        rebuild_calls.append(1)

    P.send_one = fake_send_one
    P.rebuild_history = fake_rebuild
    try:
        day = date(2026, 8, 26)
        pool = [("T", card(f"T.{i}", "Москва", f"t{i}@x.test")) for i in range(1, 4)]
        state = P.build_plan(day, pool=pool, sent=set(), limit=10,
                             built_at=at("2026-08-25T20:00"))
        P.write_state(state)

        # Все карточки — "Москва", due одинаковый (07:00+03:00): момент берём
        # относительно него, а не вручную угадываем час Владивостока, чтобы
        # не пересчитывать смещения в самом тесте.
        due_dt = datetime.fromisoformat(state["items"][0]["due"])

        real_find_card = P.find_card
        P.find_card = lambda batch, num, all_cards=None: next(
            (c for t, c in pool if t == batch and c["id"] == num), None)
        try:
            n = P.tick(due_dt + timedelta(minutes=1))  # в окне допуска для всех трёх
        finally:
            P.find_card = real_find_card

        check("tick отправил все три письма, у которых момент наступил",
              n == 3 and sorted(sent_log) == ["T.1", "T.2", "T.3"])
        check("после каждой отправки вызвана пересборка истории",
              len(rebuild_calls) == 3)

        # Второй тик минутой позже — отправлять уже нечего (все в журнале).
        sent_log.clear()
        rebuild_calls.clear()
        n2 = P.tick(due_dt + timedelta(minutes=2))
        check("повторный тик ничего не шлёт: все уже в журнале",
              n2 == 0 and sent_log == [] and rebuild_calls == [])
    finally:
        P.send_one, P.rebuild_history = live_send_one, live_rebuild
        P.STATE, S.JOURNAL = live_state, live_journal
        shutil.rmtree(tmp, ignore_errors=True)


def test_tick_quota_stops_at_15() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="planner-selftest-quota-"))
    live_state, live_journal = P.STATE, S.JOURNAL
    live_send_one, live_rebuild = P.send_one, P.rebuild_history
    P.STATE = tmp / "outbox_plan.json"
    S.JOURNAL = tmp / "sent.jsonl"

    sent_log: list[str] = []

    def fake_send_one(card_dict: dict) -> bool:
        sent_log.append(card_dict["id"])
        rec = {"ts": datetime.now().isoformat(timespec="seconds"),
              "to": card_dict["email"], "ok": True}
        with S.JOURNAL.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        return True

    P.send_one = fake_send_one
    P.rebuild_history = lambda: None
    try:
        day = date(2026, 8, 26)
        # 14 писем уже "ушло" сегодня по журналу, в плане ещё три штуки в due-окне.
        already = [{"ts": f"2026-08-26T0{i}:00:00", "to": f"prev{i}@x.test", "ok": True}
                  for i in range(1, 8)] + \
                 [{"ts": f"2026-08-26T1{i}:00:00", "to": f"prev1{i}@x.test", "ok": True}
                  for i in range(0, 7)]
        check("подготовка: ровно 14 писем уже в журнале за сегодня", len(already) == 14)
        write_journal(S.JOURNAL, already)

        pool = [("T", card(f"Q.{i}", "Москва", f"q{i}@x.test")) for i in range(1, 4)]
        state = P.build_plan(day, pool=pool, sent=set(), limit=10,
                             built_at=at("2026-08-25T20:00"))
        P.write_state(state)
        due_dt = datetime.fromisoformat(state["items"][0]["due"])
        real_find_card = P.find_card
        P.find_card = lambda batch, num, all_cards=None: next(
            (c for t, c in pool if t == batch and c["id"] == num), None)
        try:
            n = P.tick(due_dt + timedelta(minutes=1))
        finally:
            P.find_card = real_find_card
        check("квота 15/сутки соблюдена: из трёх готовых ушло только одно",
              n == 1 and sent_log == ["Q.1"])
    finally:
        P.send_one, P.rebuild_history = live_send_one, live_rebuild
        P.STATE, S.JOURNAL = live_state, live_journal
        shutil.rmtree(tmp, ignore_errors=True)


# --------------------------------------------------------------------------
# Часть 4. Живой пул wave2 — только чтение, ничего не отправляется и не пишется.
# --------------------------------------------------------------------------

def test_batch_order_rejects_broken() -> None:
    """Партия с ошибкой валидатора не попадает в очередь отправки, даже если
    порог доли своего текста взят (bl-103, 26.08.2026: партия H — 41% своего
    и две карточки без строки окупаемости).

    Фикстуры в своём каталоге, журнал не читается: тест не должен зависеть
    от того, что сегодня в живом пуле и в memory/outbox.
    """
    import tempfile

    from jarvis.modules.outbox.selftest import fixture_card
    from jarvis.modules.outbox.wave2 import status as ST

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)

        def dump(tag: str, cards: list[dict]) -> None:
            (root / f"seg9-partiya-{tag}.json").write_text(
                json.dumps(cards, ensure_ascii=False), encoding="utf-8")

        clean = [fixture_card(f"9.{i}", f"Компания{i}", f"clean{i}.ru",
                              " ".join(f"своё{i}слово{j}" for j in range(40)))
                 for i in range(4)]
        dump("CL", clean)
        # та же доведённость, но у одной карточки выброшена окупаемость
        broken = [fixture_card(f"9.1{i}", f"Контора{i}", f"broke{i}.ru",
                               " ".join(f"иное{i}слово{j}" for j in range(40)))
                  for i in range(4)]
        # Довод убирается целиком, а не заменой слова: с 27.08 приёмка ищет
        # смысл (validate.BENEFIT_RE), и «помогут первой же сэкономленной
        # неделей» она бы засчитала за довод по корню «сэконом».
        broken[0]["body"] = broken[0]["body"].replace(
            " — эти минуты окупятся первой же сэкономленной неделей", "")
        dump("BR", broken)

        rows = {r["tag"]: r for r in ST.collect(root=root, sent=set())}
        order = P.batch_order(rows=list(rows.values()))
        blocked = {b["tag"]: b["why"] for b in P.batches_blocked(rows=list(rows.values()))}

    check(f"обе фикстуры выше порога доли (CL {rows['CL']['avg']:.0%}, "
          f"BR {rows['BR']['avg']:.0%}) — различие только в браке",
          rows["CL"]["avg"] >= P.MIN_SHARE and rows["BR"]["avg"] >= P.MIN_SHARE)
    check(f"валидатор видит брак только в BR ({len(rows['BR']['errors'])} ошибок)",
          bool(rows["BR"]["errors"]) and not rows["CL"]["errors"])
    check(f"в очередь отправки попала только чистая партия ({order})",
          order == ["CL"])
    check(f"отсев назван вслух, а не молчит ({blocked.get('BR')})",
          "брак" in blocked.get("BR", ""))

    # Порядок среди годных — по убыванию доли своего текста, как и был.
    synth = [{"tag": "A", "avg": 0.50, "errors": []},
             {"tag": "B", "avg": 0.90, "errors": []},
             {"tag": "C", "avg": 0.99, "errors": ["брак"]},
             {"tag": "D", "avg": 0.20, "errors": []}]
    check("годные идут по убыванию доли, бракованная не первая",
          P.batch_order(rows=synth) == ["B", "A"])


def test_weak_card_dropped() -> None:
    """Отдельное письмо-близнец не уходит, даже если его партия годна.

    bl-105, 26.08.2026: партия H шла как ГОТОВА (41% своего текста в среднем),
    а пять писем из восьми внутри неё были 22–28% — почти шаблон. Среднее
    прикрывало брак. Здесь фикстура того же вида: партия из четырёх писем,
    три богатых и одно пустое; партия обязана остаться годной, а пустое
    письмо — выпасть из пула поштучно.
    """
    import tempfile

    from jarvis.modules.outbox.selftest import fixture_card
    from jarvis.modules.outbox.wave2 import status as ST

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        cards = [fixture_card(f"9.{i}", f"Компания{i}", f"rich{i}.ru",
                              " ".join(f"своё{i}слово{j}" for j in range(40)))
                 for i in range(3)]
        cards.append(fixture_card("9.9", "Близнец", "twin.ru", "почти ничего своего"))
        (root / "seg9-partiya-WK.json").write_text(
            json.dumps(cards, ensure_ascii=False), encoding="utf-8")

        rows = ST.collect(root=root, sent=set())
        row = {r["tag"]: r for r in rows}["WK"]
        weak = P.weak_cards(rows)
        all_cards = [("WK", c) for c in cards]
        pool = P.pool_cards(rows=rows, all_cards=all_cards)

    check(f"партия годна по среднему ({row['avg']:.0%}) и без брака",
          row["avg"] >= P.MIN_SHARE and not row["errors"])
    check(f"письмо-близнец опознано как слабое ({[s[1] for s in row['weak']]})",
          [s[1] for s in row["weak"]] == ["9.9"])
    check("weak_cards отдаёт (партия, id) с долей", ("WK", "9.9") in weak)
    ids = [c["id"] for _t, c in pool]
    check(f"в пул отправки попали только богатые письма ({ids})",
          ids == ["9.0", "9.1", "9.2"])

    # Пол на карточку не должен выкидывать партию целиком — иначе три годных
    # письма теряются из-за одного слабого.
    check("партия при этом осталась в очереди отправки",
          "WK" in P.batch_order(rows=rows))


def test_mailrisk_verdicts() -> None:
    """Вердикт по адресу — чистая функция от карточки и кеша проб (bl-055).

    Повод: 25.08 письмо на client@ellyme.ru отлупилось «550 No such user» при
    живом MX домена. Проверяем ровно то, ради чего сито заведено: твёрдые
    признаки выкидывают карточку из плана, мягкие — только сдвигают её в хвост,
    а нормальный ящик компании (info@) не трогается вовсе.
    """
    from jarvis.modules.outbox.selftest import fixture_card
    from jarvis.modules.outbox.wave2 import mailrisk as M

    живой = {"mx": True, "a": True, "site": "ok", "ts": "2026-08-26"}

    def c(email: str, **extra: object) -> dict:
        card = fixture_card("9.1", "Компания", "example.ru", "свой текст")
        card["email"] = email
        card.update(extra)
        return card

    lvl, why = M.verdict(c("hi@live.ru", dead="2026-08-25: отлуп 550 No such user"),
                         {"live.ru": живой})
    check(f"реальный отлуп делает карточку мёртвой ({lvl})",
          lvl == M.DEAD and any("отлуп" in w for w in why))
    check("нормальный ящик компании не помечается",
          M.verdict(c("info@live.ru"), {"live.ru": живой})[0] == M.OK)
    check("client@ — подозрительный, но не мёртвый",
          M.verdict(c("client@live.ru"), {"live.ru": живой})[0] == M.SUSPECT)
    check("no-reply@ — мёртвый: такой ящик человек не читает",
          M.verdict(c("no-reply@live.ru"), {"live.ru": живой})[0] == M.DEAD)
    check("пометка сборщика risk даёт подозрительного",
          M.verdict(c("hi@live.ru", risk="адрес с агрегатора"),
                    {"live.ru": живой})[0] == M.SUSPECT)

    нет_dns = {"mx": False, "a": False, "site": "нет: нет A-записи", "ts": "2026-08-26"}
    check("домен без MX и без A — мёртвый",
          M.verdict(c("hi@dead.ru"), {"dead.ru": нет_dns})[0] == M.DEAD)
    check("домен с A, но без MX — подозрительный",
          M.verdict(c("hi@half.ru"),
                    {"half.ru": {"mx": False, "a": True, "site": "ok"}})[0] == M.SUSPECT)
    check("вместо сайта заглушка регистратора — мёртвый",
          M.verdict(c("hi@park.ru"),
                    {"park.ru": {"mx": True, "a": True, "site": "парковка"}})[0] == M.DEAD)
    check("сайт отвечает ошибкой — только подозрительный",
          M.verdict(c("hi@err.ru"),
                    {"err.ru": {"mx": True, "a": True, "site": "нет: HTTP 500"}})[0] == M.SUSPECT)
    check("домена нет в кеше — вердикт по одним пометкам, не выдумываем",
          M.verdict(c("hi@unknown.ru"), {})[0] == M.OK)

    # Дубли доменов: два ИП на yandex.ru — норма, два адреса на своём домене — нет.
    pub = [("A", c("one@yandex.ru")), ("A", c("two@yandex.ru"))]
    check("публичный почтовик дублем домена не считается",
          M.domain_counts(pub) == {})
    own = [("A", c("one@studio.ru")), ("A", c("two@studio.ru"))]
    check("свой домен дважды в пуле — подозрительно",
          M.verdict(own[0][1], {}, M.domain_counts(own))[0] == M.SUSPECT)

    # Свежесть пробы: раз в две недели домен перепроверяется.
    check("проба старше двух недель считается протухшей",
          M.stale({"ts": "2026-08-01"}, today=date(2026, 8, 26))
          and not M.stale({"ts": "2026-08-20"}, today=date(2026, 8, 26))
          and M.stale({}, today=date(2026, 8, 26)))


def test_mailrisk_site_reading() -> None:
    """Толкование ответа сайта — без единого сетевого запроса: urlopen подменён.

    Отдельно проверяется 403: защита от ботов на живом сайте не должна читаться
    как мёртвый домен, иначе сито выкинет нормальные компании.
    """
    import urllib.error
    import urllib.request

    from jarvis.modules.outbox.wave2 import mailrisk as M

    class Fake:
        def __init__(self, body: bytes) -> None:
            self.body = body

        def read(self, n: int = 0) -> bytes:
            return self.body

        def __enter__(self):        # noqa: ANN204
            return self

        def __exit__(self, *a: object) -> bool:
            return False

    orig = urllib.request.urlopen
    try:
        urllib.request.urlopen = lambda *a, **k: Fake(
            "<title>Студия</title> делаем сайты".encode())
        check("живая главная страница — ok", M.probe_site("live.ru") == "ok")

        urllib.request.urlopen = lambda *a, **k: Fake(
            "<h1>Домен продаётся</h1> напишите владельцу".encode())
        check("страница-заглушка опознана как парковка",
              M.probe_site("park.ru") == "парковка")

        def raise403(*a: object, **k: object):    # noqa: ANN202
            raise urllib.error.HTTPError("u", 403, "F", None, None)

        urllib.request.urlopen = raise403
        check("403 (антибот) — не приговор домену", M.probe_site("guard.ru") == "ok")

        def raise500(*a: object, **k: object):    # noqa: ANN202
            raise urllib.error.HTTPError("u", 500, "E", None, None)

        urllib.request.urlopen = raise500
        check("500 — сайт не отвечает", M.probe_site("bad.ru") == "нет: HTTP 500")

        def refuse(*a: object, **k: object):      # noqa: ANN202
            raise OSError("connection refused")

        urllib.request.urlopen = refuse
        check("соединения нет вовсе — сайт не отвечает",
              M.probe_site("gone.ru") == "нет: не отвечает")

        # Кириллический домен: без punycode urllib падает UnicodeEncodeError и
        # живой сайт читается как мёртвый (нашлось первой же прогонкой 26.08).
        видел: list[str] = []

        def запомни(req: object, *a: object, **k: object):    # noqa: ANN202
            видел.append(getattr(req, "full_url", str(req)))
            return Fake("<title>Автошкола</title>".encode())

        urllib.request.urlopen = запомни
        check("кириллический домен идёт в сеть в punycode",
              M.probe_site("получи-права.рф") == "ok"
              and видел and видел[0].startswith("https://xn--"))

        # A-запись только на www — не повод хоронить домен.
        видел.clear()

        def только_www(req: object, *a: object, **k: object):  # noqa: ANN202
            url = getattr(req, "full_url", str(req))
            видел.append(url)
            if "//www." not in url:
                raise OSError("no route")
            return Fake("<title>Компания</title>".encode())

        urllib.request.urlopen = только_www
        check(f"сайт найден на www, когда голый домен молчит ({len(видел)} попыток)",
              M.probe_site("onlywww.ru") == "ok"
              and any("//www.onlywww.ru" in u for u in видел))
    finally:
        urllib.request.urlopen = orig


def test_risky_card_dropped_from_pool() -> None:
    """Пул отправки: мёртвый адрес выкинут, подозрительный — в хвосте партии.

    Порядок партий при этом не ломается: сдвиг идёт ВНУТРИ партии, иначе
    подозрительное письмо уехало бы в конец очереди на неделю вперёд.
    """
    import tempfile

    from jarvis.modules.outbox.selftest import fixture_card
    from jarvis.modules.outbox.wave2 import mailrisk as M
    from jarvis.modules.outbox.wave2 import status as ST

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        cards = [fixture_card(f"9.{i}", f"Компания{i}", f"rich{i}.ru",
                              " ".join(f"своё{i}слово{j}" for j in range(40)))
                 for i in range(4)]
        cards[1]["dead"] = "2026-08-25: отлуп 550 No such user"
        cards[2]["email"] = "client@rich2.ru"
        (root / "seg9-partiya-WR.json").write_text(
            json.dumps(cards, ensure_ascii=False), encoding="utf-8")
        rows = ST.collect(root=root, sent=set())
        all_cards = [("WR", c) for c in cards]
        risk = M.levels(all_cards, cache={})
        pool = P.pool_cards(rows=rows, all_cards=all_cards, risk=risk)

    check(f"мёртвая карточка опознана ({risk.get(('WR', '9.1'))})",
          risk.get(("WR", "9.1")) == M.DEAD)
    ids = [c["id"] for _t, c in pool]
    check(f"отлупившийся адрес в пул не попал ({ids})", "9.1" not in ids)
    check("подозрительный client@ остался, но последним в партии",
          ids == ["9.0", "9.3", "9.2"])


def test_live_pool_geo_coverage() -> None:
    """Все города живого пула wave2 либо опознаны, либо явно ожидаемо не
    опознаны (сборщик не нашёл город — записал "Россия"). Если появится
    новый неопознанный город, тест покраснеет и укажет на него по имени —
    это и есть отчёт для ручного разбора, а не тихий сбой."""
    try:
        cards = D.load()
    except Exception as e:                                   # noqa: BLE001
        check(f"живой пул wave2 читается ({type(e).__name__}: {e})", False)
        return
    cities = sorted({c.get("city", "") for _t, c in cards})
    bad = G.unresolved(cities)
    expected_unresolved = {"Россия"}
    check(f"неопознанные города пула — ровно ожидаемые ({sorted(bad)})",
          set(bad) <= expected_unresolved)

    order = P.batch_order()
    check("хотя бы одна партия проходит порог 38% (иначе план дня всегда пуст)",
          len(order) > 0)


def run() -> int:
    print("\n--- ПЛАНИРОВЩИК РАССЫЛКИ: КАЛЕНДАРЬ И ЧАСОВЫЕ ПОЯСА ---")
    test_pure()
    print("\n--- ПУЛ И ПОРЯДОК ПАРТИЙ ---")
    test_pool_ordering()
    print("\n--- ПЛАН ДНЯ И DUE-ОКНО ---")
    test_build_plan_and_due_now()
    print("\n--- СОСТОЯНИЕ И СУТОЧНАЯ КВОТА ---")
    test_state_and_quota()
    print("\n--- TICK: ОРКЕСТРАЦИЯ (send_one/rebuild_history — заглушки) ---")
    test_tick_orchestration()
    test_tick_quota_stops_at_15()
    print("\n--- ОТСЕВ БРАКОВАННЫХ ПАРТИЙ (фикстуры) ---")
    test_batch_order_rejects_broken()
    print("\n--- ПОШТУЧНЫЙ ОТСЕВ ПИСЕМ-БЛИЗНЕЦОВ (фикстуры) ---")
    test_weak_card_dropped()
    print("\n--- ОТСЕВ ПО АДРЕСУ: МЁРТВЫЕ И ПОДОЗРИТЕЛЬНЫЕ (bl-055) ---")
    test_mailrisk_verdicts()
    test_mailrisk_site_reading()
    test_risky_card_dropped_from_pool()
    print("\n--- ЖИВОЙ ПУЛ WAVE2 (только чтение) ---")
    test_live_pool_geo_coverage()

    print()
    if fails:
        print(f"ПЛОХО: {len(fails)} — " + "; ".join(fails))
        return 1
    print("всё зелёное")
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
