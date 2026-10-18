"""Самотест разбора входящих по рассылке (modules/inbox/read.py).

Запуск:

    cd "…/Agent" && jarvis/.venv/bin/python -m jarvis.modules.inbox.selftest

Ящик orient_dv@mail.ru живой, им пользуется Илья. Разбор обязан быть
безвредным: папки открываются readonly, тела тянутся BODY.PEEK[] (иначе
сервер сам ставит \\Seen), ничего не двигается и не удаляется. До 26.08 это
держалось только на честном слове кода — ни одной машинной проверки не было,
и «мы пометили ему сотню писем прочитанными» выяснилось бы уже на живом.

Второе, что здесь проверяется, — куда попадает ЧУЖАЯ почта Ильи: письма мимо
рассылки не должны ложиться в наш журнал откликов и не должны печататься
телом.

Каждый предохранитель проверяется с обеих сторон: и что он отбивает, и что
пропускает то, что пропускать обязан.

Живых путей нет ни одного:
  * imaplib подменяется целиком — забытая подмена = отказ, а не поход в сеть;
    фальшивый сервер РУГАЕТСЯ на любой изменяющий вызов (store, copy, delete,
    expunge, append);
  * read.env подменяется — живой .env не читается;
  * read.SENT и read.REPLIES уводятся во временный каталог, живой
    memory/outbox/replies.jsonl сверяется побайтово до и после прогона
    (правило samotest-ne-pishet-v-zhivuyu-pamyat).
"""

from __future__ import annotations

import contextlib
import imaplib
import io
import json
import tempfile
import types
from email.message import EmailMessage
from pathlib import Path

from jarvis.modules.inbox import read as R

fails: list[str] = []

USER = "orient_dv@mail.ru"
SPAM = "&BCEEPwQwBDw-"          # «Спам» в модифицированном UTF-7, как у mail.ru


def check(name: str, ok: bool) -> None:
    print(("  ок   " if ok else "  ПЛОХО") + f"  {name}")
    if not ok:
        fails.append(name)


def section(fn, *a) -> None:
    """Набор целиком: исключение внутри — провал набора, а не конец прогона."""
    print(f"\n— {fn.__doc__ or fn.__name__}")
    try:
        fn(*a)
    except Exception as exc:                       # noqa: BLE001
        check(f"{fn.__name__}: {type(exc).__name__}: {exc}", False)


# ─────────────────────────── фальшивый сервер ────────────────────────────

class FakeIMAP:
    """IMAP, который не умеет ничего менять. Любая попытка — провал."""

    def __init__(self, host, port, timeout=None):
        self.host, self.port, self.timeout = host, port, timeout
        self.folders: dict[str, list] = {}
        self.calls: list[tuple] = []
        self.login_exc: BaseException | None = None
        self.current: str | None = None
        self.logged_out = False
        self.search_ok = True

    # — то, что разбору разрешено —
    def login(self, user, password):
        self.calls.append(("login", user, password))
        if self.login_exc:
            raise self.login_exc
        return ("OK", [b"logged in"])

    def select(self, name, readonly=False):
        self.calls.append(("select", name, readonly))
        self.current = name.strip('"')
        if self.current not in self.folders:
            return ("NO", [b"no such folder"])
        return ("OK", [str(len(self.folders[self.current])).encode()])

    def search(self, charset, *criteria):
        self.calls.append(("search", self.current, criteria))
        if not self.search_ok:
            return ("NO", [b"cannot search"])
        msgs = self.folders.get(self.current, [])
        return ("OK", [b" ".join(str(i + 1).encode() for i in range(len(msgs)))])

    def fetch(self, uid, spec):
        self.calls.append(("fetch", self.current, uid, spec))
        raw = self.folders[self.current][int(uid) - 1]
        if raw is None:                  # сервер ответил мусором
            return ("OK", [b"()"])
        if raw == b"@BROKEN@":           # письмо не разбирается
            return ("OK", [(b"1 (BODY[] {3}", b"\xff\xfe\x00")])
        return ("OK", [(b"1 (BODY[] {%d}" % len(raw), raw), b")"])

    def logout(self):
        self.logged_out = True
        self.calls.append(("logout",))
        return ("BYE", [b"bye"])

    # — всё остальное запрещено —
    def __getattr__(self, name):
        if name.startswith("_"):
            raise AttributeError(name)

        def forbidden(*a, **kw):
            raise AssertionError(f"разбор дёрнул изменяющий вызов: {name}{a}")

        return forbidden


def fake_imaplib(srv: FakeIMAP):
    """Модуль-заглушка вместо imaplib: класс ошибки настоящий (его ловят)."""
    return types.SimpleNamespace(IMAP4_SSL=lambda *a, **kw: srv, IMAP4=imaplib.IMAP4)


# ────────────────────────────── письма ───────────────────────────────────

def mail(frm: str, subj: str, body: str = "текст", *, mid: str = "",
         headers: dict | None = None, html: str = "",
         date: str = "Wed, 26 Aug 2026 09:00:00 +1000", attach: bool = False) -> bytes:
    m = EmailMessage()
    m["From"] = frm
    m["To"] = USER
    m["Subject"] = subj
    m["Date"] = date
    if mid:
        m["Message-ID"] = mid
    for k, v in (headers or {}).items():
        m[k] = v
    m.set_content(body)
    if html:
        m.add_alternative(html, subtype="html")
    if attach:
        m.add_attachment(b"%PDF-1.4 fake", maintype="application",
                         subtype="pdf", filename="smeta.pdf")
    return m.as_bytes()


def raw_mail(text: str) -> bytes:
    """Письмо, собранное руками: нужны кривые кодировки и multipart/report."""
    return text.replace("\n", "\r\n").encode("cp1251", "replace")


SENT_FIXTURE = [
    {"to": "info@studio-a.ru", "num": "1.01", "name": "Студия А",
     "subject": "Автоматизация рутины в Студии А", "template": "t1"},
    {"to": "hello@studio-b.ru", "num": "1.02", "name": "Студия Б",
     "subject": "Разбор процессов Студии Б", "template": "t1"},
    {"to": "mail@studio-c.ru", "num": "1.03", "name": "Студия В",
     "subject": "Где у вас утекает время", "template": "t2"},
    {"to": USER, "num": "0.00", "name": "пилот", "pilot": True,
     "subject": "Пилот на себя", "template": "t1"},
]


def write_sent(tmp: Path) -> None:
    lines = [json.dumps(r, ensure_ascii=False) for r in SENT_FIXTURE]
    lines.insert(2, "{битая строка")          # мусор в журнале не роняет разбор
    lines.append("")
    (tmp / "sent.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")


# ─────────────────────── проверки чистых функций ─────────────────────────

def test_indexes(tmp: Path) -> None:
    """индексы отправленного: битые строки, пилоты, темы"""
    idx = R.sent_index()
    check("битая строка sent.jsonl не роняет разбор", len(idx) == 3)
    check("пилот на свой ящик в индекс не попал (иначе сами себе «ответили»)",
          USER not in idx)
    check("живые адресаты все на месте", set(idx) == {
        "info@studio-a.ru", "hello@studio-b.ru", "mail@studio-c.ru"})
    by_subj = R.subj_index()
    check("тема пилота в индекс тем не попала",
          R.norm_subject("Пилот на себя") not in by_subj)
    check("тема живого письма в индексе тем",
          by_subj[R.norm_subject("Где у вас утекает время")]["num"] == "1.03")


def test_norm_subject() -> None:
    """нормализация темы: Re, Fwd, «Ответ», номера"""
    base = "автоматизация рутины в студии а"
    for pref in ("Re: ", "RE: ", "Fwd: ", "FW: ", "Ответ: ", "Отв: ",
                 "Re: Re: ", "Re[2]: ", "Re: Fwd: "):
        check(f"«{pref.strip()}» срезается",
              R.norm_subject(pref + "Автоматизация рутины в Студии А") == base)
    check("тема без приставки не меняется", R.norm_subject("  Смета  ") == "смета")
    check("«Response» темой не считается приставкой",
          R.norm_subject("Response: смета") == "response: смета")


def test_classify() -> None:
    """классификация письма: отлуп, автоответ, живой ответ"""
    import email as E

    def kind(raw: bytes, frm: str, subj: str) -> str:
        msg = E.message_from_bytes(raw)
        return R.classify(msg, frm, subj, R.body_text(msg))

    report = raw_mail(
        "From: Mail Delivery System <mailer-daemon@mail.ru>\n"
        "Subject: Delivery Status Notification\n"
        "Content-Type: multipart/report; report-type=delivery-status;"
        ' boundary="b1"\n\n--b1\nContent-Type: text/plain\n\n'
        "Your message wasn't delivered\n--b1--\n")
    check("multipart/report — отлуп", kind(report, "x@y.ru", "что-то") == "bounce")
    check("mailer-daemon в отправителе — отлуп",
          kind(mail("mailer-daemon@mail.ru", "hi"), "mailer-daemon@mail.ru", "hi") == "bounce")
    check("postmaster в отправителе — отлуп",
          kind(mail("postmaster@studio-a.ru", "hi"), "postmaster@studio-a.ru", "hi") == "bounce")
    for subj in ("Undelivered Mail Returned to Sender", "Delivery has failed",
                 "Письмо не доставлено", "Возврат сообщения"):
        check(f"тема «{subj[:28]}» — отлуп",
              kind(mail("a@b.ru", subj), "a@b.ru", subj) == "bounce")

    for hdrs in ({"Auto-Submitted": "auto-replied"}, {"X-Autoreply": "yes"},
                 {"X-Autorespond": "1"}, {"Precedence": "bulk"},
                 {"Precedence": "auto_reply"}):
        raw = mail("director@studio-a.ru", "Ваш вопрос", headers=hdrs)
        check(f"заголовок {list(hdrs)[0]}={list(hdrs.values())[0]} — автоответ",
              kind(raw, "director@studio-a.ru", "Ваш вопрос") == "auto")
    for subj in ("Автоответ: я в отпуске", "Out of office", "Automatic reply",
                 "Ваше обращение зарегистрировано"):
        check(f"тема «{subj[:24]}» — автоответ",
              kind(mail("a@b.ru", subj), "a@b.ru", subj) == "auto")

    live = mail("director@studio-a.ru", "Re: Автоматизация рутины в Студии А",
                "Здравствуйте! Отпуск закончился, давайте созвонимся. "
                "Возврат к теме: пришлите смету.")
    check("живой ответ со словами «отпуск» и «возврат» В ТЕЛЕ остаётся ответом",
          kind(live, "director@studio-a.ru", "Re: Автоматизация рутины в Студии А") == "reply")
    check("обычное письмо — ответ",
          kind(mail("a@b.ru", "Смета"), "a@b.ru", "Смета") == "reply")
    check("Precedence: list автоответом не считается",
          kind(mail("a@b.ru", "Дайджест", headers={"Precedence": "list"}),
               "a@b.ru", "Дайджест") == "reply")


def test_body_text() -> None:
    """текст письма: plain, html, вложения, кодировки"""
    import email as E

    both = mail("a@b.ru", "t", "чистый текст", html="<p>из <b>html</b></p>")
    text = R.body_text(E.message_from_bytes(both))
    check("при двух частях берётся text/plain", "чистый текст" in text)
    check("html при наличии plain не подмешивается", "html" not in text.lower())

    only_html = raw_mail("From: a@b.ru\nSubject: t\nContent-Type: text/html;"
                         ' charset="windows-1251"\n\n<p>Здравствуйте,<br>это <b>html</b></p>\n')
    text = R.body_text(E.message_from_bytes(only_html))
    check("html без plain читается без тегов",
          "Здравствуйте" in text and "<" not in text and "html" in text)

    with_attach = mail("a@b.ru", "t", "тело письма", attach=True)
    text = R.body_text(E.message_from_bytes(with_attach))
    check("вложение в текст не попадает", "PDF" not in text and "тело письма" in text)

    cp = raw_mail("From: a@b.ru\nSubject: t\nContent-Type: text/plain;"
                  ' charset="windows-1251"\n\nДобрый день, Илья\n')
    check("cp1251 читается верно",
          "Добрый день, Илья" in R.body_text(E.message_from_bytes(cp)))

    lie = ("From: a@b.ru\nSubject: t\nContent-Type: text/plain;"
           ' charset="utf-8"\n\n').encode() + "Привет".encode("cp1251") + b"\n"
    text = R.body_text(E.message_from_bytes(lie))
    check("соврали про кодировку — не падаем", isinstance(text, str))

    msg = E.message_from_bytes(mail("a@b.ru", "t", "строка\n\n\n\n\nдругая   строка"))
    text = R.body_text(msg)
    check("пустые строки схлопываются", "\n\n\n" not in text)
    check("пробелы схлопываются", "другая строка" in text)


def test_hdr() -> None:
    """заголовки: MIME-кодировка и кривьё"""
    import email as E

    enc = raw_mail("From: a@b.ru\nSubject: =?utf-8?B?"
                   "0KHQvNC10YLQsCDQvdCwINC/0LjQu9C+0YI=?=\n\nтело\n")
    check("MIME-кодированная тема декодируется",
          R.hdr(E.message_from_bytes(enc), "Subject") == "Смета на пилот")
    broken = E.message_from_bytes(
        raw_mail("From: a@b.ru\nSubject: =?кривь?Q?=FF=FE?=\n\nтело\n"))
    check("кривой заголовок не роняет разбор", isinstance(R.hdr(broken, "Subject"), str))
    check("отсутствующий заголовок — пустая строка",
          R.hdr(E.message_from_bytes(mail("a@b.ru", "t")), "X-Nope") == "")


def test_quote_cut() -> None:
    """отрез цитаты: наш собственный текст в ответ не переезжает"""
    new = "Здравствуйте! Интересно, давайте обсудим."
    check("цитата через > отрезается",
          R.quote_cut(new + "\n\n> Добрый день, меня зовут Илья\n> ...") == new)
    check("«26 августа 2026 г., Илья написал:» отрезается",
          R.quote_cut(new + "\n\n26 августа 2026 г., Илья Лебедев написал:\nстарое") == new)
    check("«On … wrote:» по-английски отрезается",
          R.quote_cut(new + "\n\n26 August 2026 y., Ilya wrote:\nold") == new)
    check("«-----Original Message-----» отрезается",
          R.quote_cut(new + "\n\n-----Original Message-----\nстарое") == new)
    check("«--- Пересылаемое сообщение ---» отрезается",
          R.quote_cut(new + "\n\n--- Пересылаемое сообщение ---\nстарое") == new)

    long_new = ("Здравствуйте. " * 30).strip()
    cut = R.quote_cut(long_new + "\n--\nС уважением, Пётр")
    check("подпись после длинного текста отрезается", "Пётр" not in cut)
    short = "Ок\n--\nПётр"
    check("подпись после короткого текста остаётся (иначе теряем весь ответ)",
          R.quote_cut(short) == short)
    check("текст без цитаты не меняется", R.quote_cut(new) == new)
    check("письмо целиком из цитаты — возвращается как есть",
          R.quote_cut("> только цитата") == "> только цитата")


def test_dup_key() -> None:
    """ключ дубля: Message-ID, а без него — папка, отправитель, тема, дата, текст"""
    base = {"mid": "<x@a>", "folder": "Входящие", "from": "info@studio-a.ru",
            "subject": "Re: Смета", "ts": "2026-08-26T09:00:00", "text": "тело"}
    check("при Message-ID ключ строится по нему", R.dup_key(base) == "mid:<x@a>")
    moved = dict(base, folder="Спам", subject="совсем другая тема", text="иное")
    check("при Message-ID остальные поля на ключ не влияют",
          R.dup_key(moved) == R.dup_key(base))
    check("пробелы вокруг Message-ID нового ключа не создают",
          R.dup_key(dict(base, mid=" <x@a> ")) == R.dup_key(base))

    no_mid = dict(base, mid="")
    check("без Message-ID ключ всё равно есть", R.dup_key(no_mid).startswith("alt:"))
    check("без Message-ID одинаковые письма дают один ключ",
          R.dup_key(no_mid) == R.dup_key(dict(no_mid)))
    for field, other in (("folder", "Спам"), ("from", "hello@studio-b.ru"),
                         ("subject", "Другая тема"), ("ts", "2026-08-27T09:00:00"),
                         ("text", "другое тело")):
        check(f"без Message-ID поле «{field}» различает письма",
              R.dup_key(dict(no_mid, **{field: other})) != R.dup_key(no_mid))
    check("приставка Re: нового ключа не создаёт (та же переписка)",
          R.dup_key(dict(no_mid, subject="Re: Re: Смета")) == R.dup_key(no_mid))
    check("пустая запись ключ не роняет", isinstance(R.dup_key({}), str))


def test_dubli(tmp: Path) -> None:
    """дубли в журнале: письмо без Message-ID не ложится заново каждый прогон"""
    letter = mail("info@studio-a.ru", "Re: Автоматизация рутины в Студии А",
                  "Ответ без Message-ID", mid="")
    was = len(replies(tmp))
    code, out, _ = run_inbox(tmp, [letter])
    check("письмо без Message-ID разобрано и записано", len(replies(tmp)) - was == 1)

    code, out, _ = run_inbox(tmp, [letter])
    check("второй прогон копию письма без Message-ID не дописывает",
          len(replies(tmp)) - was == 1)
    check("второй прогон честно говорит про ноль новых",
          "Новых записей в журнал: 0" in out)
    check("но в сводке письмо всё равно показано", "ЖИВЫЕ ОТВЕТЫ: 1" in out)

    code, out, _ = run_inbox(tmp, [letter, letter])
    check("две копии в одном прогоне — тоже ноль новых",
          len(replies(tmp)) - was == 1)

    # разные письма без Message-ID слипаться не должны
    now = len(replies(tmp))
    run_inbox(tmp, [
        mail("info@studio-a.ru", "Re: Автоматизация рутины в Студии А",
             "СОВСЕМ ДРУГОЙ ТЕКСТ, тема и дата те же", mid=""),
        mail("info@studio-a.ru", "Другая тема, та же дата",
             "Ответ без Message-ID", mid=""),
        mail("info@studio-a.ru", "Re: Автоматизация рутины в Студии А",
             "Ответ без Message-ID", mid="",
             date="Thu, 27 Aug 2026 09:00:00 +1000"),
    ])
    check("другой текст, другая тема, другая дата — три новых записи",
          len(replies(tmp)) - now == 3)

    # Message-ID сильнее всего остального: тема ответа могла смениться
    now = len(replies(tmp))
    run_inbox(tmp, [mail("info@studio-a.ru", "Re: Автоматизация рутины в Студии А",
                         "первый", mid="<same@a>")])
    run_inbox(tmp, [mail("info@studio-a.ru", "Fwd: Автоматизация рутины в Студии А",
                         "переслали себе же", mid="<same@a>",
                         date="Thu, 27 Aug 2026 10:00:00 +1000")])
    check("тот же Message-ID при смене темы и даты — одна запись",
          len(replies(tmp)) - now == 1)

    # чужое письмо в журнал не попадает и от повторов его туда не заносит
    now = len(replies(tmp))
    run_inbox(tmp, [mail("bank@sberbank.ru", "Выписка по счёту", "чужое", mid="")])
    run_inbox(tmp, [mail("bank@sberbank.ru", "Выписка по счёту", "чужое", mid="")])
    check("чужое письмо без Message-ID в журнал не попало ни разу",
          len(replies(tmp)) - now == 0)


def test_bounced_addr() -> None:
    """чей адрес не принял письмо: только знакомый нам"""
    known = {"info@studio-a.ru", "hello@studio-b.ru"}
    body = ("This is the mail system at host mail.ru.\n"
            "<info@studio-a.ru>: Recipient address rejected: User unknown")
    check("знакомый адрес из тела отлупа найден",
          R.bounced_addr(body, "Undelivered Mail", known) == "info@studio-a.ru")
    check("незнакомый адрес не подставляется",
          R.bounced_addr("<kto-to@chuzhoy.ru>: user unknown", "Undelivered", known) == "")
    check("адрес из темы тоже считается",
          R.bounced_addr("", "failure notice hello@studio-b.ru", known) == "hello@studio-b.ru")
    check("регистр не мешает",
          R.bounced_addr("<INFO@Studio-A.ru>: rejected", "", known) == "info@studio-a.ru")


# ─────────────────────────── проверки run() ──────────────────────────────

def run_inbox(tmp: Path, inbox: list, spam: list | None = None, *,
              full: bool = False, login_exc=None, search_ok: bool = True,
              folders_extra: dict | None = None):
    """Один прогон разбора на фальшивом сервере. Возвращает (код, вывод, srv)."""
    srv = FakeIMAP("h", 993)
    srv.folders = {"INBOX": inbox, SPAM: spam or []}
    srv.folders.update(folders_extra or {})
    srv.login_exc = login_exc
    srv.search_ok = search_ok
    saved = R.imaplib
    R.imaplib = fake_imaplib(srv)
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            code = R.run(days=3, full=full)
    finally:
        R.imaplib = saved
    return code, buf.getvalue(), srv


def replies(tmp: Path) -> list[dict]:
    p = tmp / "replies.jsonl"
    if not p.exists():
        return []
    return [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]


def test_bezvrednost(tmp: Path) -> None:
    """безвредность: readonly, PEEK, ничего не меняем, выход всегда"""
    code, out, srv = run_inbox(tmp, [mail("director@studio-a.ru",
                                          "Re: Автоматизация рутины в Студии А", mid="<1@a>")])
    check("прогон завершился успехом", code == 0)
    selects = [c for c in srv.calls if c[0] == "select"]
    check("обе папки открыты", len(selects) == 2)
    check("папки открыты ТОЛЬКО readonly", all(c[2] is True for c in selects))
    fetches = [c for c in srv.calls if c[0] == "fetch"]
    check("тела тянутся BODY.PEEK[] (иначе сервер ставит \\Seen)",
          fetches and all(c[3] == "(BODY.PEEK[])" for c in fetches))
    check("RFC822/BODY[] не запрашивается ни разу",
          not any("RFC822" in c[3] or c[3] == "(BODY[])" for c in fetches))
    searches = [c for c in srv.calls if c[0] == "search"]
    check("ищем по SINCE с датой", all(c[2][0] == "SINCE" and len(c[2]) == 2 for c in searches))
    check("сессия закрыта logout", srv.logged_out)
    check("изменяющих вызовов не было",
          not any(c[0] not in ("login", "select", "search", "fetch", "logout")
                  for c in srv.calls))

    # разбор упал посреди работы — сессия всё равно закрывается
    srv2 = FakeIMAP("h", 993)
    srv2.folders = {"INBOX": [mail("a@b.ru", "t")], SPAM: []}
    saved_imap, saved_body = R.imaplib, R.body_text
    R.imaplib = fake_imaplib(srv2)
    R.body_text = lambda msg: (_ for _ in ()).throw(RuntimeError("бум"))
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            R.run(days=3, full=False)
    except RuntimeError:
        pass
    finally:
        R.imaplib, R.body_text = saved_imap, saved_body
    check("падение посреди разбора не оставляет сессию открытой", srv2.logged_out)


def test_vhod(tmp: Path) -> None:
    """вход: нет пароля, IMAP не пустил, сеть недоступна"""
    saved = R.env
    R.env = lambda: {"SMTP_USER": USER}
    try:
        srv = FakeIMAP("h", 993)
        R.imaplib, keep = fake_imaplib(srv), R.imaplib
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = R.run(days=3, full=False)
        R.imaplib = keep
        check("без пароля возвращается 1", code == 1)
        check("без пароля к серверу не ходим вовсе", srv.calls == [])
        check("сказано, почему не работаем", "читать нечем" in buf.getvalue())
    finally:
        R.env = saved

    was = len(replies(tmp))
    code, out, srv = run_inbox(tmp, [], login_exc=imaplib.IMAP4.error("AUTH failed"))
    check("IMAP не пустил — код 2", code == 2)
    check("подсказка про пароль для внешних приложений", "внешних приложений" in out)
    check("после отказа логина в журнал ничего не дописано", len(replies(tmp)) == was)

    code, out, _ = run_inbox(tmp, [], login_exc=OSError("Network unreachable"))
    check("сеть недоступна — код 2", code == 2)
    check("названа причина недоступности", "OSError" in out and "unreachable" in out)


def test_ustoychivost(tmp: Path) -> None:
    """устойчивость: нет папки, поиск не сработал, битое письмо"""
    code, out, srv = run_inbox(tmp, [mail("a@b.ru", "t", mid="<u1@a>")],
                               folders_extra={})
    check("папки «Спам» нет — прогон живёт", code == 0)

    code, out, _ = run_inbox(tmp, [mail("a@b.ru", "t", mid="<u2@a>")], search_ok=False)
    check("поиск не сработал — прогон живёт и не врёт", code == 0)
    check("при неудачном поиске писем не найдено", "ЖИВЫЕ ОТВЕТЫ: 0" in out)

    before = len(replies(tmp))
    code, out, _ = run_inbox(tmp, [
        None,                                        # сервер ответил мусором
        b"@BROKEN@",                                 # письмо не разбирается
        mail("director@studio-a.ru", "Re: Автоматизация рутины в Студии А",
             "Интересно", mid="<good@a>")])
    check("мусор от сервера не роняет разбор", code == 0)
    got = [r for r in replies(tmp) if r["mid"] == "<good@a>"]
    check("живое письмо после мусора всё равно разобрано", len(got) == 1)


def test_sopostavlenie(tmp: Path) -> None:
    """чей это ответ: адрес, домен, тема — и чужое мимо"""
    code, out, _ = run_inbox(tmp, [
        mail("info@studio-a.ru", "Re: Автоматизация рутины в Студии А",
             "Давайте обсудим", mid="<m1@a>"),
        mail("director@studio-b.ru", "Re: Разбор процессов Студии Б",
             "Спасибо", mid="<m2@b>"),
        mail("petr.ivanov@gmail.com", "Re: Где у вас утекает время",
             "Пишу с личной почты", mid="<m3@c>"),
        mail("bank@sberbank.ru", "Выписка по счёту", "чужое", mid="<m4@x>"),
        # домен наш, тема своя: узнать можно ТОЛЬКО по домену
        mail("sales@studio-a.ru", "Вопрос по вашему предложению",
             "Перешлю руководителю", mid="<m5@a>"),
    ])
    got = {r["mid"]: r for r in replies(tmp)}
    check("свой домен и чужая тема — карточка всё равно найдена по домену",
          "<m5@a>" in got and got["<m5@a>"]["num"] == "1.01")
    check("точный адрес — карточка найдена", got["<m1@a>"]["num"] == "1.01")
    check("шаблон письма подтянут", got["<m1@a>"]["template"] == "t1")
    check("другой ящик того же домена — та же карточка",
          got["<m2@b>"]["num"] == "1.02" and got["<m2@b>"]["contact"] == "hello@studio-b.ru")
    check("личная почта, но наша тема — карточка по теме",
          got["<m3@c>"]["num"] == "1.03" and got["<m3@c>"]["contact"] == "mail@studio-c.ru")
    check("чужое письмо в журнал откликов не попало", "<m4@x>" not in got)
    check("чужое посчитано как «мимо рассылки»", "мимо рассылки: 1" in out)
    check("чужое не попало в живые ответы", "ЖИВЫЕ ОТВЕТЫ: 4" in out)

    # своё же письмо с нашей темой (пилот на себя) под карточку не подводим
    code, out, _ = run_inbox(tmp, [
        mail(USER, "Автоматизация рутины в Студии А", "пилот", mid="<self@me>")])
    check("своё же письмо не считается ответом клиента",
          "<self@me>" not in {r["mid"] for r in replies(tmp)})


def test_otlupy(tmp: Path) -> None:
    """отлупы: адрес из тела, чужой адрес, счёт «не дошло»"""
    body = ("This is the mail system at host mail.ru.\n\n"
            "<hello@studio-b.ru>: host mx.studio-b.ru said: 550 5.1.1 User unknown")
    code, out, _ = run_inbox(tmp, [
        mail("mailer-daemon@mail.ru", "Undelivered Mail Returned to Sender",
             body, mid="<b1@d>"),
        mail("mailer-daemon@mail.ru", "Undelivered Mail Returned to Sender",
             "<nikto@chuzhoy.ru>: User unknown", mid="<b2@d>"),
    ])
    got = {r["mid"]: r for r in replies(tmp)}
    check("отлуп по нашему адресу опознан", got["<b1@d>"]["contact"] == "hello@studio-b.ru")
    check("отлуп по нашему адресу — карточка подтянута", got["<b1@d>"]["num"] == "1.02")
    check("отлуп по чужому адресу тоже сохранён (иначе потеряем сигнал)",
          got["<b2@d>"]["kind"] == "bounce" and got["<b2@d>"]["contact"] == "")
    check("в сводке названы адреса-пустышки", "hello@studio-b.ru" in out)
    check("посчитано «не дошло»", "Не дошло: 1" in out)


def test_spam_i_molchat(tmp: Path) -> None:
    """папка «Спам» и счёт молчащих"""
    code, out, _ = run_inbox(
        tmp,
        [mail("info@studio-a.ru", "Re: Автоматизация рутины в Студии А",
              "ответ", mid="<s1@a>")],
        spam=[mail("hello@studio-b.ru", "Re: Разбор процессов Студии Б",
                   "ответ из спама", mid="<s2@b>")])
    got = {r["mid"]: r for r in replies(tmp)}
    check("ответ из «Спама» найден", "<s2@b>" in got)
    check("папка названа по-человечески", got["<s2@b>"]["folder"] == "Спам")
    check("папка «Входящие» названа по-человечески", got["<s1@a>"]["folder"] == "Входящие")
    check("ответили — двое", "Ответили: 2 из 3" in out)
    check("молчит — один", "Молчат: 1" in out)

    code, out, _ = run_inbox(tmp, [
        mail("info@studio-a.ru", "Автоответ: я в отпуске", "вернусь", mid="<a1@a>")])
    check("автоответ молчащим не считается", "Молчат: 2" in out)
    check("автоответ отвечающим тоже не считается", "Ответили: 0 из 3" in out)


def test_zhurnal(tmp: Path) -> None:
    """журнал откликов: дубли, прирост, обрезка текста"""
    letters = [mail("info@studio-a.ru", "Re: Автоматизация рутины в Студии А",
                    "Первый ответ", mid="<dup@a>")]
    before = len(replies(tmp))
    code, out, _ = run_inbox(tmp, letters)
    after_first = len(replies(tmp))
    check("новая запись дописана ровно одна", after_first - before == 1)
    check("в выводе назван счёт новых", "Новых записей в журнал: 1" in out)

    code, out, _ = run_inbox(tmp, letters)
    check("повторный прогон дублей не плодит", len(replies(tmp)) == after_first)
    check("повторный прогон говорит про ноль новых", "Новых записей в журнал: 0" in out)
    check("но письмо всё равно показано в сводке", "ЖИВЫЕ ОТВЕТЫ: 1" in out)

    long_text = "А" * 4000
    run_inbox(tmp, [mail("info@studio-a.ru", "Re: Автоматизация рутины в Студии А",
                         long_text, mid="<long@a>")])
    rec = [r for r in replies(tmp) if r["mid"] == "<long@a>"][0]
    check("текст в журнале обрезан до 1500", len(rec["text"]) == 1500)
    check("дата письма разобрана", rec["ts"].startswith("2026-08-26T09:00:00"))

    run_inbox(tmp, [mail("info@studio-a.ru", "Re: Автоматизация рутины в Студии А",
                         "без даты", mid="<nodate@a>", date="")])
    rec = [r for r in replies(tmp) if r["mid"] == "<nodate@a>"][0]
    check("кривая дата не роняет запись", rec["ts"] == "")


def test_pechat(tmp: Path) -> None:
    """печать: --full и чужая переписка Ильи"""
    letters = [
        mail("info@studio-a.ru", "Re: Автоматизация рутины в Студии А",
             "СЕКРЕТНОЕ_ТЕЛО_ОТВЕТА", mid="<p1@a>"),
        mail("mama@mail.ru", "Про дачу", "ЛИЧНОЕ_ТЕЛО_ИЛЬИ", mid="<p2@x>"),
    ]
    code, out, _ = run_inbox(tmp, letters, full=False)
    check("без --full тело ответа не печатается", "СЕКРЕТНОЕ_ТЕЛО_ОТВЕТА" not in out)
    check("без --full чужое письмо даже шапкой не показано", "Про дачу" not in out)

    code, out, _ = run_inbox(tmp, letters, full=True)
    check("с --full тело нашего ответа печатается", "СЕКРЕТНОЕ_ТЕЛО_ОТВЕТА" in out)
    check("с --full у чужого письма видна только шапка",
          "Про дачу" in out and "ЛИЧНОЕ_ТЕЛО_ИЛЬИ" not in out)
    check("в сводке видно, скольким писали", "Писали 3 контактам" in out)


# ───────────────────────── живая память не тронута ───────────────────────

def test_zhivaya_pamyat(before: tuple) -> None:
    """живые файлы после прогона"""
    now = (R.REPLIES.read_bytes() if R.REPLIES.exists() else None,
           R.SENT.read_bytes() if R.SENT.exists() else None)
    check("живой memory/outbox/replies.jsonl побайтово тот же", now[0] == before[0])
    check("живой memory/outbox/sent.jsonl побайтово тот же", now[1] == before[1])


def run() -> int:
    live_replies, live_sent = R.REPLIES, R.SENT
    before = (live_replies.read_bytes() if live_replies.exists() else None,
              live_sent.read_bytes() if live_sent.exists() else None)
    saved = {"env": R.env, "imaplib": R.imaplib, "sent": R.SENT, "replies": R.REPLIES}
    try:
        with tempfile.TemporaryDirectory(prefix="jarvis-inbox-selftest-") as td:
            tmp = Path(td)
            R.SENT, R.REPLIES = tmp / "sent.jsonl", tmp / "replies.jsonl"
            R.env = lambda: {"SMTP_USER": USER, "SMTP_PASS": "секрет"}
            # imaplib подменяется на каждый прогон run_inbox; на всякий случай
            # сносим и здесь: забыли подменить — отказ, а не поход в сеть.
            R.imaplib = types.SimpleNamespace(
                IMAP4_SSL=lambda *a, **kw: (_ for _ in ()).throw(
                    AssertionError("самотест полез в живой IMAP")),
                IMAP4=imaplib.IMAP4)
            write_sent(tmp)

            section(test_indexes, tmp)
            section(test_norm_subject)
            section(test_classify)
            section(test_body_text)
            section(test_hdr)
            section(test_quote_cut)
            section(test_bounced_addr)
            section(test_dup_key)
            for t in (test_bezvrednost, test_vhod, test_ustoychivost,
                      test_sopostavlenie, test_otlupy, test_spam_i_molchat,
                      test_zhurnal, test_dubli, test_pechat):
                section(t, tmp)
    finally:
        R.env, R.imaplib = saved["env"], saved["imaplib"]
        R.SENT, R.REPLIES = saved["sent"], saved["replies"]

    print("\n— живая память")
    test_zhivaya_pamyat(before)
    check("пути восстановлены на живые",
          R.REPLIES == live_replies and R.SENT == live_sent)
    print("\nВСЁ ХОРОШО" if not fails else f"\nСЛОМАНО ({len(fails)}): {fails}")
    return 1 if fails else 0


if __name__ == "__main__":
    raise SystemExit(run())
