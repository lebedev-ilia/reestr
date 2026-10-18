"""Планировщик ежедневной рассылки: 15 писем в сутки, каждое — в 07:00 по
МЕСТНОМУ времени города адресата, а не по Владивостоку и не по Москве.

Повод (25.08.2026): Илья велел слать по 15 писем в день из пула второй волны,
сначала сказал "в 7 утра по Москве", потом поправил голосовым — "в семь утра
по времени города, а не по московскому". Машина стоит во Владивостоке
(Asia/Vladivostok, C.TZ), поэтому 07:00 Калининграда и 07:00 Якутска — это
два разных момента машинного времени, и без планировщика прямая отправка
"в 7 по Владивостоку" разослала бы половину писем адресатам в их два часа ночи.

Устроено по образцу core/night.py: расписания-крона нет, планировщик тикает
вместе с демоном ВК (см. modules/vk/daemon.py), сравнивается не "сейчас
ровно 7:00", а "какой момент уже наступил и ещё не пропущен слишком сильно".

Понятия:
  * "план дня" — до 15 карточек из очередной ГОДНОЙ партии (38%+ своего
    текста по wave2/dupcheck.py И ни одной ошибки wave2/validate.py — оба
    условия читаются из wave2/status.py), ещё не отправленных, каждой назначен
    момент = ближайшие локальные 07:00 её города. Хранится в
    derived/outbox_plan.json — производное: пересобирается из wave2/*.json +
    dupcheck + журнала sent.jsonl в любой момент, ничего уникального внутри
    нет (правило v-derived-tolko-vosstanovimoe).
  * "тик" (tick) — просмотр плана: что из него уже наступило (с допуском
    GRACE на выключенную машину) и не пропущено, то уходит через
    send_wave.to_send_format + send.run — единственную дорогу наружу.
    Пропущенное больше чем на GRACE не досылается вдогонку: карточка
    остаётся неотправленной и снова попадёт в план, когда до неё дойдёт
    очередь в пуле (сегодня или позже).
  * суточная квота (15) считается ФАКТОМ — числом писем с ok=true в
    sent.jsonl за календарный день машины (Владивосток), а не числом строк
    в плане: план может недобрать 15 (пул кончился) или недоотправить
    (часть пропущена по GRACE), и квота в это не упирается искусственно.

Планировщик стартует с 26.08.2026 (START_DAY): на 25.08 квота выбрана вручную
(партия AF, 15 писем в 15:10) — досылать вдогонку в это число не нужно.

Модуль трогает core/config.py (часовой пояс, путь derived/), поэтому запуск —
как у остальных модулей, которым нужно ядро (core/night_selftest и т.п.):
из каталога НАД jarvis, полным именем пакета.

Запуск вручную (из каталога над jarvis, т.е. из "…/Agent"):
    jarvis/.venv/bin/python -m jarvis.modules.outbox.wave2.planner plan
    jarvis/.venv/bin/python -m jarvis.modules.outbox.wave2.planner plan --rebuild
    jarvis/.venv/bin/python -m jarvis.modules.outbox.wave2.planner tick
    jarvis/.venv/bin/python -m jarvis.modules.outbox.wave2.planner tick --send

`tick --send` — не для повседневного вызова руками: это то же самое, что
делает демон на каждом опросе long-poll (см. planner.tick() и вызов в
modules/vk/daemon.py). Ручной запуск нужен для разбора, если демон стоит.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from jarvis.core import config as C
from jarvis.core import memory as M
from jarvis.modules.outbox import send as S
from jarvis.modules.outbox import history as H
from jarvis.modules.outbox.wave2 import dupcheck as D
from jarvis.modules.outbox.wave2 import geo as G
from jarvis.modules.outbox.wave2 import mailrisk as MR
from jarvis.modules.outbox.wave2 import send_wave as SW
from jarvis.modules.outbox.wave2 import status as ST

STATE = C.DERIVED / "outbox_plan.json"   # производное: план дня, восстановим

START_DAY = date(2026, 8, 26)   # раньше не планируем — квота 25.08 выбрана вручную
QUOTA = 15                      # писем в сутки, по факту отправленного
LOCAL_HOUR = 7                  # час адресата, на который целимся
MIN_SHARE = 0.38                # доведённая партия — СРЕДНЯЯ доля своего текста
MIN_CARD_SHARE = 0.30           # пол на одно письмо: ниже — не уходит вовсе
GRACE = timedelta(hours=3)      # пропущенный момент досылаем, если опоздание меньше


def now_local() -> datetime:
    return datetime.now(ZoneInfo(C.TZ))


# ---------------------------------------------------------------------------
# Пул: какие карточки годятся и в каком порядке партий
# ---------------------------------------------------------------------------

def batch_rows(min_share: float = MIN_SHARE) -> list[dict]:
    """Приёмка всех партий одним прогоном — то же, что печатает
    `python3 -m modules.outbox.wave2.status`. Журнал отправленного здесь не
    нужен (sent=set()): кто уже получил письмо, решает candidates по адресу."""
    return ST.collect(min_share, sent=set())


def batch_order(min_share: float = MIN_SHARE, rows: list[dict] | None = None) -> list[str]:
    """Партии, годные к отправке, от самой доведённой к менее.

    Годная — это ДВА условия, а не одно: доля своего текста >= min_share И
    ни одной ошибки валидатора. 26.08.2026: партия H брала порог
    доведённости (41%), но валидатор находил в ней две карточки без строки
    окупаемости — по старому коду, смотревшему только на долю, письмо с
    формальным браком ушло бы живому человеку (bl-103). Порог доведённости
    и брак — разные вещи, отправка обязана смотреть на оба.

    Не отдельная метрика: и доля, и ошибки читаются из status.collect —
    того же прогона, что показывает приёмка (правило
    merilo-schitaetsya-po-vsemu-polyu). Порог приёмки задаёт процедура
    ezhednevnaya-rassylka-15, не этот файл.
    """
    rows = batch_rows(min_share) if rows is None else rows
    good = [r for r in rows if not r["errors"] and r["avg"] >= min_share]
    return [r["tag"] for r in sorted(good, key=lambda r: -r["avg"])]


def batches_blocked(min_share: float = MIN_SHARE,
                    rows: list[dict] | None = None) -> list[dict]:
    """Партии, которых отправка не возьмёт, и почему — для вывода `plan`.
    Молча выкинутая из очереди партия выглядит как пустой пул."""
    rows = batch_rows(min_share) if rows is None else rows
    out = []
    for r in rows:
        if r["errors"]:
            out.append({"tag": r["tag"], "why": f"брак валидатора ({len(r['errors'])})"})
        elif r["avg"] < min_share:
            out.append({"tag": r["tag"],
                        "why": f"своего текста {r['avg']:.0%} < {min_share:.0%}"})
    return out


def weak_cards(rows: list[dict] | None = None,
               min_card_share: float = MIN_CARD_SHARE) -> dict[tuple[str, str], float]:
    """Отдельные письма, которые нельзя слать, даже если их партия годна.

    Порог партии — СРЕДНЕЕ, а среднее прикрывает близнецов: 26.08.2026
    партия H шла как готовая (41% своего текста), но пять писем из восьми
    внутри неё были 22–28% — почти шаблон, и они ушли бы живым людям
    (bl-105). Тот же класс дыры, что bl-103: мерило усредняет брак.

    Здесь отсев ПОШТУЧНЫЙ, а не по партии: выкидывать всю партию из-за двух
    слабых писем значило бы терять шесть годных. Ключ — (партия, id
    карточки), значение — доля своего текста, чтобы отсев можно было
    назвать вслух в `plan`, а не молча уронить из пула.
    """
    rows = batch_rows() if rows is None else rows
    out: dict[tuple[str, str], float] = {}
    for r in rows:
        for share, cid, _name in r.get("weak", []):
            out[(r["tag"], str(cid))] = share
    return out


def already_sent() -> set[str]:
    """Адреса из журнала (в нижнем регистре) — правда одна, не дублируем."""
    return set(S.journal_load().keys())


def pool_cards(min_share: float = MIN_SHARE,
              all_cards: list[tuple[str, dict]] | None = None,
              rows: list[dict] | None = None,
              risk: dict[tuple[str, str], str] | None = None,
              ) -> list[tuple[str, dict]]:
    """(партия, карточка), партия за партией в порядке batch_order, внутри
    партии — в порядке файла (dupcheck.load идёт по файлам по сортировке имён,
    внутри файла — по списку, это и есть порядок карточек партии).

    Сит три, а не одно: сначала партии (среднее + брак валидатора), потом
    ПОШТУЧНО письма ниже пола на карточку — weak_cards (bl-105), потом адреса
    — mailrisk (bl-055). Мёртвый адрес выкидывается совсем, подозрительный
    остаётся, но уходит в хвост своей партии: слот дня стоит контакта, и
    сомнительному адресу он достаётся последним, а не первым."""
    rows = batch_rows(min_share) if rows is None else rows
    order = batch_order(min_share, rows)
    if not order:
        return []
    rank = {t: i for i, t in enumerate(order)}
    weak = weak_cards(rows)
    cards = all_cards if all_cards is not None else D.load()
    risk = MR.levels(cards) if risk is None else risk
    picked = [(tag, card) for tag, card in cards
              if tag in rank and (tag, str(card.get("id", ""))) not in weak
              and risk.get((tag, str(card.get("id", "")))) != MR.DEAD]
    picked.sort(key=lambda tc: (rank[tc[0]],
                                risk.get((tc[0], str(tc[1].get("id", "")))) == MR.SUSPECT))
    return picked


def candidates(limit: int = QUOTA, pool: list[tuple[str, dict]] | None = None,
              sent: set[str] | None = None) -> list[tuple[str, dict]]:
    """До `limit` карточек, ещё не отправленных (по адресу из журнала)."""
    pool = pool_cards() if pool is None else pool
    sent = already_sent() if sent is None else sent
    out: list[tuple[str, dict]] = []
    for tag, card in pool:
        email = (card.get("email") or "").strip().lower()
        if not email or email in sent:
            continue
        out.append((tag, card))
        if len(out) >= limit:
            break
    return out


# ---------------------------------------------------------------------------
# План дня: чистые функции от пула и даты — тестируются без сети и без памяти
# ---------------------------------------------------------------------------

def target_dt(city: str, day: date) -> tuple[datetime, int, bool]:
    """07:00 местного времени города в указанный день + смещение + опознан ли."""
    offset, resolved = G.offset_for(city)
    tz = timezone(timedelta(hours=offset))
    return datetime(day.year, day.month, day.day, LOCAL_HOUR, tzinfo=tz), offset, resolved


def build_plan(day: date, pool: list[tuple[str, dict]] | None = None,
               sent: set[str] | None = None, limit: int = QUOTA,
               built_at: datetime | None = None) -> dict:
    """План на конкретный день — чистая функция, без чтения/записи файлов
    состояния (это делает ensure_plan)."""
    items = []
    for tag, card in candidates(limit, pool, sent):
        due, offset, resolved = target_dt(card.get("city", ""), day)
        items.append({
            "num": card.get("id", ""), "batch": tag, "name": card.get("name", ""),
            "email": (card.get("email") or "").strip(), "city": card.get("city", ""),
            "tz_offset": offset, "tz_resolved": resolved, "due": due.isoformat(),
        })
    return {"day": day.isoformat(),
            "built_at": (built_at or now_local()).isoformat(timespec="seconds"),
            "items": items}


def due_now(state: dict, now: datetime, sent: set[str] | None = None) -> list[dict]:
    """Из плана — то, чей момент уже наступил, ещё не отправлено и не
    пропущено больше чем на GRACE. Пропущенное НЕ возвращается: досылать
    вдогонку нельзя (условие Ильи), карточка просто не попадает в список —
    следующий раз она возьмётся в план заново тем же candidates(), пока не
    отправлена."""
    sent = already_sent() if sent is None else sent
    out = []
    for item in state.get("items", []):
        if item["email"].strip().lower() in sent:
            continue
        due = datetime.fromisoformat(item["due"])
        if now < due or now - due > GRACE:
            continue
        out.append(item)
    out.sort(key=lambda i: i["due"])
    return out


# ---------------------------------------------------------------------------
# Состояние — derived/outbox_plan.json
# ---------------------------------------------------------------------------

def read_state() -> dict:
    try:
        return json.loads(STATE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def write_state(state: dict) -> None:
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")


def ensure_plan(now: datetime | None = None, force: bool = False) -> dict | None:
    """План на сегодня — построить, если его ещё нет или наступил новый день.

    None означает "рано" (до START_DAY): по образцу night.py первый рабочий
    день не выбирается неявно из состояния файла, а задан датой в коде —
    иначе случайный первый тик до 26.08 создал бы пустой план и запомнил его.
    """
    now = now or now_local()
    today = now.date()
    if today < START_DAY:
        return None
    state = read_state()
    have_day = state.get("day")
    if force or not have_day or date.fromisoformat(have_day) < today:
        state = build_plan(today, built_at=now)
        write_state(state)
    return state


# ---------------------------------------------------------------------------
# Отправка одного письма — только через send_wave/send.py
# ---------------------------------------------------------------------------

def find_card(batch: str, num: str, all_cards: list[tuple[str, dict]] | None = None) -> dict | None:
    """План хранит только адрес/город/тему момента — тело и тема письма
    берутся заново из wave2/*.json на момент отправки, а не из снимка в
    плане: если карточку успели поправить (доводка партии), уйдёт актуальный
    текст, а не тот, что был на момент сборки плана."""
    cards = all_cards if all_cards is not None else D.load()
    for tag, card in cards:
        if tag == batch and card.get("id") == num:
            return card
    return None


def send_one(card: dict) -> bool:
    """Одно письмо через send_wave.to_send_format + send.run — тот же
    переходник, что и ручная рассылка партиями. Второй дороги наружу нет.

    Возвращает не код возврата S.run (0 у него означает и "ушло", и
    "пропущено" любым из предохранителей), а сам факт по журналу — который
    из источников правды один, memory/outbox/sent.jsonl.
    """
    cards, subj, letts = SW.to_send_format([card])
    S.subjects = lambda: subj
    S.letters = lambda: letts
    S.run(cards, limit=1, dry=False, only=None, to_override=None)
    email = (card.get("email") or "").strip().lower()
    rec = S.journal_load().get(email)
    return bool(rec) and S.row_ok(rec)


def rebuild_history() -> None:
    """Пересборка досье переписки — после каждой реальной отправки.
    Не должна ронять тик: письмо уже ушло, пересборка — учётная работа."""
    try:
        H.build()
    except Exception as e:                                    # noqa: BLE001
        print(f"! пересборка истории переписки упала: {type(e).__name__}: {e}")


def count_sent_today(now: datetime) -> int:
    """Сколько писем реально ушло за календарный день машины (Владивосток).

    Квота считается по факту, а не по плану: план может быть короче 15
    (пул наполовину пуст) или письмо может уйти мимо планировщика (ручная
    отправка), и то и другое обязано отражаться на суточном потолке.
    """
    today = now.date().isoformat()
    if not S.JOURNAL.exists():
        return 0
    n = 0
    for line in S.JOURNAL.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        if S.row_ok(rec) and str(rec.get("ts", "")).startswith(today):
            n += 1
    return n


def tick(now: datetime | None = None) -> int:
    """Вызывается демоном на каждом такте long-poll. Возвращает, сколько
    писем реально ушло в этом тике (0 — норма: тикает часто, писем в 07:00
    мало)."""
    now = now or now_local()
    state = ensure_plan(now)
    if not state:
        return 0
    quota_left = QUOTA - count_sent_today(now)
    if quota_left <= 0:
        return 0
    sent = already_sent()
    due = due_now(state, now, sent)
    all_cards = D.load()
    n = 0
    for item in due:
        if n >= quota_left:
            break
        card = find_card(item["batch"], item["num"], all_cards)
        if card is None:
            print(f"! карточка {item['batch']} {item['num']} пропала из пула — пропускаю")
            continue
        if send_one(card):
            n += 1
            # След в логе: без него письмо, ушедшее по расписанию, ничем не
            # отличается от отправленного руками — в sent.jsonl обе строки
            # одинаковы. По этой записи пересчёт реестра (core/registry.py)
            # считает вызовы cap-017: день, в который план был построен и
            # хотя бы одно письмо ушло по плану. Пишется после факта отправки,
            # а не до: журнал остаётся источником правды, лог — свидетелем.
            M.log("self", {"outbox_sched": item["num"], "to": item["email"],
                           "due": item["due"], "city": item.get("city", "")})
            rebuild_history()
    return n


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _status(item: dict, now: datetime, sent: set[str]) -> str:
    if item["email"].strip().lower() in sent:
        return "ушло"
    due = datetime.fromisoformat(item["due"])
    if now < due:
        return "ждёт"
    if now - due <= GRACE:
        return "СЕЙЧАС (в окне допуска)"
    return "пропущено (>3ч) — вернётся в план на завтра"


def cmd_plan(rebuild: bool) -> int:
    now = now_local()
    state = ensure_plan(now, force=rebuild)
    if not state:
        print(f"Планировщик стартует с {START_DAY.isoformat()}, сегодня "
              f"{now.date().isoformat()} — рано, план не строится.")
        return 0
    sent = already_sent()
    print(f"План на {state['day']} (собран {state.get('built_at', '?')}), "
          f"писем в плане: {len(state['items'])}/{QUOTA}, "
          f"уже ушло сегодня: {count_sent_today(now)}")
    if not state["items"]:
        print("Пул пуст: нет годных партий (38%+ своего текста и без брака "
              "валидатора) с неотправленными адресатами. Нужна новая партия "
              "или доводка существующей.")
    rows = batch_rows()
    weak = weak_cards(rows)
    if weak:
        order = set(batch_order(rows=rows))
        inside = {k: v for k, v in weak.items() if k[0] in order}
        print(f"\nОтсеяно поштучно (ниже {MIN_CARD_SHARE:.0%} своего текста): "
              f"{len(weak)} писем, из них в годных партиях {len(inside)}")
        for (tag, cid), share in sorted(inside.items(), key=lambda kv: kv[1])[:10]:
            print(f"  ↓ {share:>4.0%} — {tag} {cid}")
    risky = MR.assess(D.load())
    if risky:
        order = set(batch_order(rows=rows))
        dead = [r for r in risky if r["level"] == MR.DEAD]
        inside = [r for r in dead if r["tag"] in order]
        print(f"\nОтсеяно по адресу (bl-055): мёртвых {len(dead)}, из них в "
              f"годных партиях {len(inside)}; подозрительных "
              f"{len(risky) - len(dead)} — они не выкинуты, а сдвинуты в хвост "
              "своей партии")
        for r in inside[:10]:
            print(f"  ✗ {r['tag']} {r['id']} {r['email']} — {r['why'][0]}")
        if not MR.cache_load():
            print("  ! сетевой пробы доменов ещё не было: "
                  "python -m …wave2.mailrisk --net")
    blocked = batches_blocked(rows=rows)
    braced = [b for b in blocked if "брак" in b["why"]]
    low = [b for b in blocked if b not in braced]
    if braced:
        print("\nНе пущены в отправку из-за брака валидатора: " + ", ".join(
            f"{b['tag']} ({b['why'].split('(')[-1].rstrip(')')})" for b in braced))
        print("  разбор: python3 -m modules.outbox.wave2.status --details")
    if low:
        print(f"\nНиже порога доведённости — {len(low)} партий: "
              + ", ".join(b["tag"] for b in low))
    for item in state["items"]:
        flag = "" if item["tz_resolved"] else "  [город не опознан — взята Москва]"
        print(f"  {item['due']}  UTC{item['tz_offset']:+d}  {item['city']:<28} "
              f"{item['batch']:>3} {item['num']:>6}  {item['email']:<32} "
              f"[{_status(item, now, sent)}]{flag}")
    unresolved = sorted({item["city"] for item in state["items"] if not item["tz_resolved"]})
    if unresolved:
        print(f"\nГорода без опознанного пояса (взята Москва): {', '.join(unresolved)}")
    return 0


def cmd_tick(send: bool) -> int:
    if not send:
        now = now_local()
        state = ensure_plan(now)
        due = due_now(state, now) if state else []
        print(f"Сухой прогон тика: писем, которым сейчас пора уходить — {len(due)}")
        for item in due:
            print(f"  {item['due']}  {item['city']}  {item['email']}")
        return 0
    n = tick(now_local())
    print(f"Тик по-настоящему: ушло писем {n}")
    return 0


def main() -> None:
    ap = argparse.ArgumentParser(description="Планировщик ежедневной рассылки (07:00 по городу адресата)")
    ap.add_argument("cmd", nargs="?", default="plan", choices=["plan", "tick"])
    ap.add_argument("--rebuild", action="store_true", help="пересобрать план дня заново")
    ap.add_argument("--send", action="store_true",
                    help="tick: реально отправить due-письма (без флага — сухой прогон)")
    args = ap.parse_args()
    if args.cmd == "plan":
        sys.exit(cmd_plan(args.rebuild))
    sys.exit(cmd_tick(args.send))


if __name__ == "__main__":
    main()
