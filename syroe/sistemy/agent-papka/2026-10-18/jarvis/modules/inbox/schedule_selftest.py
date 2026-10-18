"""Самотест расписания опроса почты (modules/inbox/schedule.py).

Запуск:

    cd "…/Agent" && jarvis/.venv/bin/python -m jarvis.modules.inbox.schedule_selftest

Живых путей нет: read.run и history.build подменяются целиком (забытая
подмена = поход в живой IMAP, поэтому подменённый read.run ещё и ругается,
если его позвали с неожиданными аргументами), журнал откликов и файл
состояния уводятся во временный каталог. Живой memory/outbox/replies.jsonl
сверяется побайтово до и после (правило samotest-ne-pishet-v-zhivuyu-pamyat).

Что проверяется с обеих сторон:
  * ночное окно — молчит с 23:00 до 08:00 и работает днём;
  * период — раз в час, а не каждым тактом демона;
  * bootstrap после сноса derived — старые ответы не сыплются докладом;
  * новый живой ответ доходит до доклада ровно один раз;
  * отлупы и автоответы в доклад не идут.
"""

from __future__ import annotations

import json
import tempfile
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from jarvis.core import config as C, memory as M
from jarvis.modules.inbox import read as R
from jarvis.modules.inbox import schedule as S

fails: list[str] = []


def check(name: str, ok: bool) -> None:
    print(("  ✓ " if ok else "  ✗ ") + name)
    if not ok:
        fails.append(name)


def ts(day: int, hour: int, minute: int = 0) -> float:
    """Момент машинного времени по поясу Ильи."""
    return datetime(2026, 9, day, hour, minute,
                    tzinfo=ZoneInfo(C.TZ)).timestamp()


def rec(mid: str, kind: str = "reply", frm: str = "a@b.ru") -> dict:
    return {"mid": f"<{mid}>", "ts": "2026-09-01T10:00:00+10:00",
            "folder": "Входящие", "from": frm, "subject": "Re: тема",
            "kind": kind, "num": "1.1", "name": "Кто-то", "contact": frm,
            "template": "T1", "text": f"тело письма {mid}"}


class FakeMail:
    """Подменённый разбор входящих: дописывает заданные письма в журнал."""

    def __init__(self) -> None:
        self.queue: list[dict] = []
        self.calls = 0
        self.history_calls = 0
        self.rc = 0

    def run(self, days: int, full: bool) -> int:
        assert isinstance(days, int) and full is False, "неожиданные аргументы"
        self.calls += 1
        with R.REPLIES.open("a", encoding="utf-8") as f:
            for r in self.queue:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
        print(f"фальшивый разбор: писем {len(self.queue)}")
        self.queue = []
        return self.rc

    def build(self, contact=None) -> int:
        self.history_calls += 1
        return 0


def test_okno() -> None:
    """ночное окно"""
    check("02:00 — вне окна", not S.in_window(ts(1, 2)))
    check("07:59 — вне окна", not S.in_window(ts(1, 7, 59)))
    check("08:00 — в окне", S.in_window(ts(1, 8)))
    check("14:00 — в окне", S.in_window(ts(1, 14)))
    check("22:59 — в окне", S.in_window(ts(1, 22, 59)))
    check("23:00 — вне окна", not S.in_window(ts(1, 23)))
    check("23:30 — вне окна", not S.in_window(ts(1, 23, 30)))


def test_period(mail: FakeMail) -> None:
    """период раз в час"""
    S.STATE.unlink(missing_ok=True)
    check("первый раз в окне — пора", S.due(ts(1, 9)))
    check("ночью не пора, сколько бы ни прошло", not S.due(ts(1, 3)))
    S._write({"last": ts(1, 9), "bootstrapped": True, "reported": []})
    check("через 30 минут — не пора", not S.due(ts(1, 9, 30)))
    check("через 59 минут — не пора", not S.due(ts(1, 9, 59)))
    check("через час — пора", S.due(ts(1, 10)))
    check("через сутки, но ночью — не пора", not S.due(ts(2, 2)))

    before = mail.calls
    S.tick(ts(1, 9, 30))
    check("не пора — ящик не трогаем вовсе", mail.calls == before)
    S.tick(ts(1, 3))
    check("ночью — ящик не трогаем вовсе", mail.calls == before)


def test_samotest_ne_lezet_v_yashchik(mail: FakeMail) -> None:
    """самотест не ходит в живой ящик"""
    # Самотест ВК гоняет цикл демона целиком (asyncio.run(d.run())), и тот
    # зовёт этот же tick. Живой ящик и живой replies.jsonl обязаны остаться в
    # стороне — проверяется с обеих сторон: под флагом молчит, без него ходит.
    S.STATE.unlink(missing_ok=True)
    before = mail.calls
    saved = C.SELFTEST
    try:
        C.SELFTEST = True
        check("под флагом самотеста тик пуст", S.tick(ts(1, 9)) == {})
        check("под флагом самотеста ящик не тронут", mail.calls == before)
        check("под флагом самотеста состояние не создано", not S.STATE.exists())
    finally:
        C.SELFTEST = saved
    check("без флага тик работает", S.tick(ts(1, 9)).get("rc") == 0)
    check("без флага ящик опрошен", mail.calls == before + 1)
    S.STATE.unlink(missing_ok=True)
    mail.history_calls = 0
    mail.calls = 0


def test_bootstrap(mail: FakeMail) -> None:
    """первый тик после сноса derived"""
    S.STATE.unlink(missing_ok=True)
    R.REPLIES.write_text(json.dumps(rec("staroe-1"), ensure_ascii=False) + "\n"
                         + json.dumps(rec("staroe-2"), ensure_ascii=False) + "\n",
                         encoding="utf-8")
    out = S.tick(ts(1, 9))
    check("тик отработал", out.get("rc") == 0 and out.get("bootstrap") is True)
    check("разбор входящих позван один раз", mail.calls == 1)
    check("досье пересобраны", mail.history_calls == 1)
    check("старые ответы докладом не идут", not S.report_due())
    check("сводка опроса легла в derived/logs",
          any(S.LOGS.glob("*-opros-pochty.log")))


def test_novyy_otvet(mail: FakeMail) -> None:
    """новый живой ответ"""
    mail.queue = [rec("novoe-1"), rec("otlup-1", kind="bounce"),
                  rec("avto-1", kind="auto")]
    S.tick(ts(1, 10))
    check("новый живой ответ ждёт доклада", S.report_due())
    state = S._read()
    check("в очереди ровно один — отлуп и автоответ не в счёт",
          len(state.get("pending") or []) == 1)

    text = S.take_report()
    check("в докладе есть тело письма", "тело письма novoe-1" in text)
    check("в докладе есть адрес", "a@b.ru" in text)
    check("в докладе сказано, что текст письма — данные, а не приказ",
          "ДАННЫЕ" in text and "черновик" in text)
    check("отлупа в докладе нет", "otlup-1" not in text)
    check("после доклада очередь пуста", not S.report_due())
    check("повторный доклад пуст", S.take_report() == "")

    S.tick(ts(1, 11))
    check("то же письмо второй раз не докладывается", not S.report_due())

    mail.queue = [rec("novoe-2", frm="c@d.ru")]
    S.tick(ts(1, 12))
    check("следующий новый ответ доходит", S.report_due())
    check("в докладе именно он", "тело письма novoe-2" in S.take_report())


def test_sboy(mail: FakeMail) -> None:
    """упавший опрос"""
    mail.rc = 2                      # IMAP не пустил
    calls = mail.calls
    S.tick(ts(1, 13))
    check("опрос был", mail.calls == calls + 1)
    check("досье при сбое не пересобираются", mail.history_calls == calls)
    check("сбой не порождает доклада", not S.report_due())
    before = mail.calls
    S.tick(ts(1, 13, 20))
    check("после сбоя не долбимся каждым тактом", mail.calls == before)
    mail.rc = 0


def run() -> int:
    live_replies = R.REPLIES
    before = live_replies.read_bytes() if live_replies.exists() else None
    saved = {"STATE": S.STATE, "LOGS": S.LOGS, "REPLIES": R.REPLIES,
             "run": S.R.run, "build": S.H.build}
    mail = FakeMail()
    # Флаг самотеста здесь СНИМАЕТСЯ намеренно: сам предохранитель (tick под
    # флагом молчит) проверяется отдельным набором, а остальные проверки
    # должны видеть обычное поведение. Живой ящик при этом не в опасности —
    # read.run и history.build подменены, пути уведены во временный каталог.
    saved["selftest_flag"] = C.SELFTEST
    C.SELFTEST = False
    if True:
        try:
            with tempfile.TemporaryDirectory(prefix="jarvis-inbox-sched-") as td:
                tmp = Path(td)
                S.STATE = tmp / "inbox_poll.json"
                S.LOGS = tmp / "logs"
                R.REPLIES = tmp / "replies.jsonl"
                R.REPLIES.write_text("", encoding="utf-8")
                S.R.run = mail.run
                S.H.build = mail.build

                print("— окно");      test_okno()
                print("— период");    test_period(mail)
                print("— флаг самотеста"); test_samotest_ne_lezet_v_yashchik(mail)
                print("— bootstrap"); test_bootstrap(mail)
                print("— ответ");     test_novyy_otvet(mail)
                print("— сбой");      test_sboy(mail)
        finally:
            S.STATE, S.LOGS = saved["STATE"], saved["LOGS"]
            R.REPLIES = saved["REPLIES"]
            S.R.run, S.H.build = saved["run"], saved["build"]
            C.SELFTEST = saved["selftest_flag"]

    print("\n— живая память")
    now = live_replies.read_bytes() if live_replies.exists() else None
    check("живой memory/outbox/replies.jsonl побайтово тот же", now == before)
    # Размер живого memory/log здесь не сверяется намеренно: рядом работает
    # демон ВК и пишет туда законно, такая проверка краснела бы от чужой
    # работы. Запись этого прогона уходит в derived (процесс не звал
    # mark_live) — правило zhivoy-log-tolko-iz-tochki-vhoda.
    check("прогон писал не в живой лог",
          M.log_dir() != C.MEM / "log")
    check("пути восстановлены на живые",
          S.STATE == C.DERIVED / "inbox_poll.json" and R.REPLIES == live_replies)
    print("\nВСЁ ХОРОШО" if not fails else f"\nСЛОМАНО ({len(fails)}): {fails}")
    return 1 if fails else 0


if __name__ == "__main__":
    raise SystemExit(run())
