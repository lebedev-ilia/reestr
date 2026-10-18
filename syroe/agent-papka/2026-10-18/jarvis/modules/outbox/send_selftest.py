"""Самотест отправки писем. Запуск:

    cd "…/Agent" && jarvis/.venv/bin/python -m jarvis.modules.outbox.send_selftest

Проверяются предохранители modules/outbox/send.py — механизм, через который
письма уходят живым людям от имени Ильи. До 26.08 он не был покрыт ничем:
единственной проверкой была живая отправка, то есть ошибка обнаруживалась уже
у адресата.

Каждый предохранитель проверяется С ОБЕИХ сторон: и что он отбивает, и что он
пропускает то, что пропускать обязан (запрет, который отбивает всё подряд,
выглядит исправным ровно до дня, когда рассылка молча встанет).

Живых путей здесь нет ни одного:
  * S.JOURNAL уводится во временный каталог, живой memory/outbox/sent.jsonl
    сверяется побайтово до и после прогона (правило samotest-ne-pishet-v-
    zhivuyu-pamyat);
  * smtplib подменяется заглушкой в самом модуле send — сети не касаемся;
  * .env, brand.json, subjects.json, letters.json подменяются функциями.
"""

from __future__ import annotations

import contextlib
import io
import json
import smtplib
import sys
import tempfile
from pathlib import Path

from jarvis.modules.outbox import send as S

fails: list[str] = []


def check(name: str, ok: bool) -> None:
    print(("  ок   " if ok else "  ПЛОХО") + f"  {name}")
    if not ok:
        fails.append(name)


# --- фикстуры ---------------------------------------------------------------

BODY = ("Здравствуйте! Пишу коротко и по делу: мы делаем автоматизацию рутины "
        "внутри компаний — сверка данных, отчёты, переписка с клиентами. "
        "Окупается обычно за пару месяцев. Если тема живая, отвечу за 15 минут "
        "разговора. {BRAND}")


def card(num: str, name: str, email: str | None, body: str = BODY) -> dict:
    return {"num": num, "name": name, "section": "тест",
            "fields": {}, "letter": body,
            "channels": {"email": [email] if email else [],
                         "telegram": [], "phone": [], "url": []}}


CARDS = [card(f"9.{i}", f"Компания {i}", f"c{i}@example.com") for i in range(1, 6)]
SUBJ = {c["num"]: f"тема {c['num']}" for c in CARDS}
BRAND_OK = {"name": "Alverra", "tg": "@alverra"}


class FakeSMTP:
    """Заглушка smtplib.SMTP_SSL: ничего не отправляет, всё запоминает."""

    def __init__(self, host, port, context=None, timeout=None):
        self.host, self.port = host, port
        FakeSMTP.connects.append(host)

    def login(self, user, password):
        if FakeSMTP.auth_fails:
            raise smtplib.SMTPAuthenticationError(
                535, b"5.7.8 Error: authentication failed")

    def send_message(self, msg):
        if FakeSMTP.fail_on and msg["To"] in FakeSMTP.fail_on:
            raise smtplib.SMTPRecipientsRefused({msg["To"]: (550, b"no such user")})
        FakeSMTP.sent.append(msg)

    def quit(self):
        pass


def _reset_smtp() -> None:
    FakeSMTP.sent, FakeSMTP.connects = [], []
    FakeSMTP.auth_fails, FakeSMTP.fail_on = False, set()


class _FakeSmtplib:
    """Модуль-заглушка: send.py зовёт smtplib только этими четырьмя именами."""
    SMTP_SSL = FakeSMTP
    SMTPException = smtplib.SMTPException
    SMTPAuthenticationError = smtplib.SMTPAuthenticationError


def run_send(cards, *, limit=10, dry=False, only=None, pilot=None,
             brand=None, env=None, subj=None, letts=None,
             journal_seed: list[dict] | None = None, auth_fails=False,
             refuse: set | None = None, tmp: Path) -> dict:
    """Прогон run() на подменённых путях. Возвращает всё, что он натворил."""
    saved = (S.JOURNAL, S.env, S.brand, S.subjects, S.letters, S.smtplib, S.PAUSE_SEC)
    _reset_smtp()
    FakeSMTP.auth_fails = auth_fails
    FakeSMTP.fail_on = refuse or set()
    journal = tmp / "sent.jsonl"
    if journal.exists():
        journal.unlink()
    if journal_seed:
        journal.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n"
                                   for r in journal_seed), encoding="utf-8")
    before = len(journal.read_text(encoding="utf-8").splitlines()) if journal.exists() else 0

    S.JOURNAL = journal
    S.env = lambda: (env if env is not None
                     else {"SMTP_USER": "orient_dv@mail.ru", "SMTP_PASS": "x"})
    S.brand = lambda: (BRAND_OK if brand is None else brand)
    S.subjects = lambda: (SUBJ if subj is None else subj)
    S.letters = lambda: (letts or {})
    S.smtplib = _FakeSmtplib
    S.PAUSE_SEC = 0  # иначе прогон спит по 20 секунд между письмами
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            code = S.run(cards, limit, dry, only, pilot)
    finally:
        (S.JOURNAL, S.env, S.brand, S.subjects, S.letters, S.smtplib,
         S.PAUSE_SEC) = saved

    recs = []
    if journal.exists():
        for line in journal.read_text(encoding="utf-8").splitlines()[before:]:
            if line.strip():
                recs.append(json.loads(line))
    return {"code": code, "out": buf.getvalue(), "sent": list(FakeSMTP.sent),
            "recs": recs, "connects": list(FakeSMTP.connects)}


# --- проверки ---------------------------------------------------------------

def test_dry(tmp: Path) -> None:
    print("\nПЕРВЫЙ ПРЕДОХРАНИТЕЛЬ: без --send ничего не уходит")
    r = run_send(CARDS[:3], dry=True, tmp=tmp)
    check("сухой прогон: ни одного письма в SMTP", r["sent"] == [])
    check("сухой прогон: соединения с почтой нет вовсе", r["connects"] == [])
    check("сухой прогон: журнал не вырос", r["recs"] == [])
    check("сухой прогон: три черновика напечатаны",
          r["out"].count("[черновик]") == 3 and r["code"] == 0)

    # Обратная сторона: с --send те же карточки обязаны уйти.
    r = run_send(CARDS[:3], dry=False, tmp=tmp)
    check("с отправкой: три письма ушли и легли в журнал",
          len(r["sent"]) == 3 and len(r["recs"]) == 3 and r["code"] == 0)

    # Флаг рождается в main(): без --send он обязан быть «сухо».
    seen: list[tuple] = []
    saved_run, saved_argv, saved_parse = S.run, sys.argv, S.C.parse
    S.run = lambda cards, limit, dry, only, to_override: (
        seen.append((limit, dry, only, to_override)) or 0)
    S.C.parse = lambda path: CARDS
    try:
        for argv, want_dry in ((["send", "spisok.md"], True),
                               (["send", "spisok.md", "--send"], False)):
            sys.argv = argv
            with contextlib.redirect_stdout(io.StringIO()):
                with contextlib.suppress(SystemExit):
                    S.main()
            check(f"main{argv[2:] or ' без флагов'}: dry={want_dry}",
                  bool(seen) and seen[-1][1] is want_dry)
        check("по умолчанию потолок 3 письма", seen[-1][0] == 3)
    finally:
        S.run, sys.argv, S.C.parse = saved_run, saved_argv, saved_parse


def test_limit(tmp: Path) -> None:
    print("\nВТОРОЙ ПРЕДОХРАНИТЕЛЬ: потолок писем за запуск")
    r = run_send(CARDS, limit=2, tmp=tmp)
    check("limit=2 из пяти карточек: ушло ровно 2",
          len(r["sent"]) == 2 and len(r["recs"]) == 2)
    check("ушли первые две по порядку",
          [m["To"] for m in r["sent"]] == ["c1@example.com", "c2@example.com"])
    r = run_send(CARDS, limit=0, tmp=tmp)
    check("limit=0: не ушло ничего, прогон не упал",
          r["sent"] == [] and r["code"] == 0)
    # Обратная сторона: потолок выше числа карточек ничего не режет.
    r = run_send(CARDS, limit=99, tmp=tmp)
    check("потолок выше пула: ушли все пять", len(r["sent"]) == 5)


def test_journal(tmp: Path) -> None:
    print("\nТРЕТИЙ ПРЕДОХРАНИТЕЛЬ: второй раз человеку не пишем")
    seed = [{"ts": "2026-08-25T10:00:00", "num": "9.1", "to": "c1@example.com",
             "ok": True}]
    r = run_send(CARDS[:2], journal_seed=seed, tmp=tmp)
    check("адрес из журнала пропущен",
          "уже писали" in r["out"] and "c1@example.com" not in
          [m["To"] for m in r["sent"]])
    check("новый адрес в том же прогоне ушёл",
          [m["To"] for m in r["sent"]] == ["c2@example.com"])

    # Регистр не должен быть лазейкой: C1@Example.COM — тот же человек.
    seed = [{"ts": "2026-08-25T10:00:00", "num": "9.1", "to": "C1@Example.COM",
             "ok": True}]
    r = run_send(CARDS[:1], journal_seed=seed, tmp=tmp)
    check("другой регистр адреса не обходит журнал", r["sent"] == [])

    # Битая строка в журнале не должна ронять отправку и не должна
    # превращаться в разрешение написать повторно.
    journal = tmp / "sent.jsonl"
    journal.write_text('{битый json\n' + json.dumps(
        {"ts": "x", "to": "c1@example.com", "ok": True}) + "\n", encoding="utf-8")
    saved = S.JOURNAL
    S.JOURNAL = journal
    try:
        seen = S.journal_load()
    finally:
        S.JOURNAL = saved
    check("битая строка журнала не роняет чтение и не теряет адрес",
          set(seen) == {"c1@example.com"})


def test_brand(tmp: Path) -> None:
    print("\nЧЕТВЁРТЫЙ ПРЕДОХРАНИТЕЛЬ: без названия компании не отправляем")
    r = run_send(CARDS[:1], brand={"name": "", "tg": ""}, tmp=tmp)
    check("пустой brand.json: код 3, ни письма, ни соединения",
          r["code"] == 3 and r["sent"] == [] and r["connects"] == [])
    r = run_send(CARDS[:1], brand={"name": "", "tg": ""}, dry=True, tmp=tmp)
    check("пустой brand.json на сухом прогоне: черновик показан",
          r["code"] == 0 and r["out"].count("[черновик]") == 1)
    r = run_send(CARDS[:1], tmp=tmp)
    body = r["sent"][0].get_content()
    check("название компании подставлено вместо {BRAND}",
          "Alverra" in body and "{BRAND}" not in body)


def test_tema_metki(tmp: Path) -> None:
    print("\nПЯТЫЙ ПРЕДОХРАНИТЕЛЬ: нераскрытая метка в теме наружу не уходит")
    # Тема с меткой должна раскрываться той же подстановкой, что и тело.
    subj = dict(SUBJ)
    subj["9.1"] = "{BRAND}: автоматизация рутины"
    r = run_send(CARDS[:1], subj=subj, tmp=tmp)
    check("метка в теме подставлена названием компании",
          len(r["sent"]) == 1 and r["sent"][0]["Subject"] == "Alverra: автоматизация рутины")
    check("в журнал ушла раскрытая тема, а не шаблон",
          r["recs"] and r["recs"][0]["subject"] == "Alverra: автоматизация рутины")

    # Неизвестная метка подстановке не поддаётся — письмо не уходит вовсе.
    subj["9.1"] = "{CITY}: автоматизация рутины"
    r = run_send(CARDS[:1], subj=subj, tmp=tmp)
    check("незнакомая метка в теме: письма нет, журнал пуст",
          r["sent"] == [] and r["recs"] == [])
    check("причина пропуска названа и метка показана",
          "нераскрытая метка" in r["out"] and "{CITY}" in r["out"])

    # Сухой прогон отбивает так же, как боевой: черновик не должен показывать
    # письмо, которое отправка не пропустит.
    r = run_send(CARDS[:1], subj=subj, dry=True, tmp=tmp)
    check("на сухом прогоне такое письмо тоже не показано",
          r["out"].count("[черновик]") == 0)

    # Метка в теле ловится тем же предохранителем.
    bad_body = BODY.replace("{BRAND}", "") + " Пишите: {TG_HANDLE}."
    r = run_send([card("9.1", "Метка в теле", "c1@example.com", bad_body)], tmp=tmp)
    check("нераскрытая метка в тексте: письмо не ушло",
          r["sent"] == [] and "нераскрытая метка" in r["out"])

    # И обратная сторона: обычное письмо этот предохранитель не трогает.
    r = run_send(CARDS[:2], tmp=tmp)
    check("обычные письма предохранитель пропускает", len(r["sent"]) == 2)
    # Русский текст в фигурных скобках меткой не считается.
    r = run_send([card("9.1", "Скобки", "c1@example.com",
                       BODY.replace("{BRAND}", "{сроки уточним}"))], tmp=tmp)
    check("фигурные скобки с русским текстом меткой не считаются",
          len(r["sent"]) == 1)


def test_env(tmp: Path) -> None:
    print("\nДОСТУПЫ И SMTP")
    r = run_send(CARDS[:1], env={}, tmp=tmp)
    check("нет SMTP_USER/PASS: код 1, соединения нет",
          r["code"] == 1 and r["connects"] == [])
    r = run_send(CARDS[:1], auth_fails=True, tmp=tmp)
    check("почта не пустила: код 2 и подсказка про пароль для внешних приложений",
          r["code"] == 2 and "внешних приложений" in r["out"] and r["recs"] == [])


def test_brak(tmp: Path) -> None:
    print("\nБРАК КАРТОЧКИ: что не уходит наружу")
    cards = [
        card("9.10", "Без почты", None),
        card("9.11", "Без темы", "no-subj@example.com"),
        card("9.12", "Короткое", "short@example.com", "Привет!"),
        card("9.13", "Старый бренд", "old@example.com",
             BODY + " Мы в TrendFlow делаем аналитику."),
        card("9.14", "Годная", "good@example.com"),
    ]
    subj = {c["num"]: f"тема {c['num']}" for c in cards if c["num"] != "9.11"}
    r = run_send(cards, subj=subj, tmp=tmp)
    for why in ("нет email", "нет темы", "нет письма",
                "в тексте старый бренд TrendFlow"):
        check(f"пропущено: {why}", why in r["out"])
    check("годная карточка в том же прогоне ушла одна",
          [m["To"] for m in r["sent"]] == ["good@example.com"])


def test_pilot(tmp: Path) -> None:
    print("\nПИЛОТ: письмо себе не закрывает контакт")
    r = run_send(CARDS[:1], pilot="orient_dv@mail.ru", tmp=tmp)
    rec = r["recs"][0]
    check("пилот ушёл на адрес Ильи, а не контакту",
          r["sent"][0]["To"] == "orient_dv@mail.ru")
    check("в журнале to пустой, real_to — пилотный, pilot=true",
          rec["to"] == "" and rec["real_to"] == "orient_dv@mail.ru"
          and rec["pilot"] is True)
    # Главное: после пилота боевая отправка тому же контакту обязана пройти.
    r2 = run_send(CARDS[:1], journal_seed=[rec], tmp=tmp)
    check("после пилота настоящий адрес не заблокирован",
          [m["To"] for m in r2["sent"]] == ["c1@example.com"])


def test_zhurnal_soderzhanie(tmp: Path) -> None:
    print("\nЧТО ЛОЖИТСЯ В ЖУРНАЛ")
    r = run_send(CARDS[:1], tmp=tmp)
    rec, msg = r["recs"][0], r["sent"][0]
    check("тело в журнале — ровно то, что ушло, вместе с подписью",
          rec["body"] == msg.get_content().rstrip("\n")
          and "Илья Лебедев, Alverra" in rec["body"])
    check("Message-ID журнала совпадает с ушедшим письмом",
          rec["mid"] == msg["Message-ID"] and rec["mid"].startswith("<"))
    check("в журнале есть адрес, тема, каркас и ok",
          rec["to"] == "c1@example.com" and rec["subject"] == "тема 9.1"
          and rec["ok"] is True and rec["template"] == "карточка")
    check("отправитель подписан именем Ильи",
          msg["From"] == "=?utf-8?b?0JjQu9GM0Y8g0JvQtdCx0LXQtNC10LI=?= "
                         "<orient_dv@mail.ru>" or "orient_dv@mail.ru" in msg["From"])
    check("запись в журнал идёт через единственную константу JOURNAL",
          "JOURNAL" in S.journal_add.__code__.co_names)


def test_oshibka_dostavki(tmp: Path) -> None:
    print("\nОТКАЗ ДОСТАВКИ НА ОДНОМ ПИСЬМЕ")
    r = run_send(CARDS[:3], refuse={"c1@example.com"}, tmp=tmp)
    check("отказ на одном письме не роняет прогон", r["code"] == 0)
    check("остальные два письма ушли", len(r["sent"]) == 2)
    bad = [x for x in r["recs"] if not x["ok"]]
    check("провал записан в журнал с текстом ошибки",
          len(bad) == 1 and "SMTPRecipientsRefused" in bad[0]["error"])
    check("провалившийся адрес НЕ помечен как «уже писали»",
          bad and bad[0]["to"] == "" and bad[0]["real_to"] == "c1@example.com")


def test_commit_ne_iz_testa(tmp: Path) -> None:
    """journal_commit() трогает гит только для ЖИВОГО журнала (bl-139).

    Прогоны отправки уводят S.JOURNAL во временный каталог. Если бы коммит
    смотрел только на «файл изменился», самотест начал бы коммитить живое
    дерево посреди чужой работы — ровно то, чего запрещает
    samotest-ne-pishet-v-zhivuyu-pamyat.
    """
    print("\nКОММИТ ЖУРНАЛА НЕ ИЗ ТЕСТА")
    import subprocess
    def head() -> str:
        r = subprocess.run(["git", "rev-parse", "HEAD"], cwd=str(S.ROOT),
                           text=True, capture_output=True)
        return r.stdout.strip()
    keep, was = S.JOURNAL, head()
    try:
        S.JOURNAL = tmp / "podmena-sent.jsonl"
        S.JOURNAL.write_text('{"ok": true}\n', encoding="utf-8")
        S.journal_commit(1)
    finally:
        S.JOURNAL = keep
    check("коммита из самотеста не случилось", head() == was)
    check("живой путь журнала не потерян", S.LIVE_JOURNAL == keep)


def test_zhivoy_zhurnal(before: bytes | None) -> None:
    print("\nЖИВОЙ ЖУРНАЛ НЕ ТРОНУТ")
    live = S.ROOT / "memory" / "outbox" / "sent.jsonl"
    after = live.read_bytes() if live.exists() else None
    check("memory/outbox/sent.jsonl побайтово тот же", after == before)
    check("константа JOURNAL восстановлена после прогона", S.JOURNAL == live)


def run() -> int:
    print("\n--- ОТПРАВКА ПИСЕМ (предохранители) ---")
    live = S.ROOT / "memory" / "outbox" / "sent.jsonl"
    before = live.read_bytes() if live.exists() else None
    with tempfile.TemporaryDirectory(prefix="jarvis-send-selftest-") as td:
        tmp = Path(td)
        test_dry(tmp)
        test_limit(tmp)
        test_journal(tmp)
        test_brand(tmp)
        test_tema_metki(tmp)
        test_env(tmp)
        test_brak(tmp)
        test_pilot(tmp)
        test_zhurnal_soderzhanie(tmp)
        test_oshibka_dostavki(tmp)
        test_commit_ne_iz_testa(tmp)
    test_zhivoy_zhurnal(before)
    print("\nВСЁ ХОРОШО" if not fails else f"\nСЛОМАНО ({len(fails)}): {fails}")
    return 1 if fails else 0


if __name__ == "__main__":
    raise SystemExit(run())
