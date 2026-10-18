"""Самотест разбора контактов. Запуск:

    cd "…/Agent" && jarvis/.venv/bin/python -m jarvis.modules.outbox.selftest

Проверяется одно: строка «- Контакт:» из карточки правильно раскладывается на
каналы. Ни памяти, ни сети, ни отправки здесь нет — только чистая функция
`contacts.channels()` на образцах живых строк из T-835.

Зачем вообще: 24.08 два факта памяти назвали разное число телеграмов и
телефонов (14/19 против 10/12). Причина оказалась не в фактах, а здесь —
регулярка телефона требовала ровно один символ-разделитель между группами
и молча теряла «+7 (4212) 55-66-18», а защита «не путать хвост почты с ником»
съедала настоящий @hochustul, потому что домен почты совпадал с ником.
Числа, посчитанные машиной, врали почти вдвое, а выглядели точными.
"""

import sys
from pathlib import Path

from jarvis.modules.outbox import contacts as K
from jarvis.modules.outbox.wave2 import dupcheck as D
from jarvis.modules.outbox.wave2 import taken as TK

fails: list[str] = []


def check(name: str, ok: bool) -> None:
    print(("  ок   " if ok else "  ПЛОХО") + f"  {name}")
    if not ok:
        fails.append(name)


# Все строки — дословно из живых карточек T-835, вместе с их кривизной.
CASES = [
    # (строка, сколько email, сколько telegram, сколько телефонов)
    ("hr@atwinta.ru", 1, 0, 0),
    ("info@daccel.tech; telegram-бот @Daccel_agency_bot.", 1, 1, 0),
    # Код города четырьмя цифрами и два разделителя подряд: «) »
    ("info@esteticadent.ru, +7 (4212) 55-66-18.", 1, 0, 1),
    # Шестизначный номер без последней пары — тоже телефон
    ("lada-estet@lada-estet.ru, +7 (3412) 400-600.", 1, 0, 1),
    # Домен почты совпадает с телеграм-ником: раньше ник терялся
    ("sale@hochustul.ru, telegram @hochustul, +7 939 380-07-14.", 1, 1, 1),
    # Телеграм назван номером — это аккаунт, а не линия для звонка
    ("hello@astrio.ru; telegram +79951039903.", 1, 1, 0),
    # Тот же номер не должен посчитаться дважды — и телеграмом, и телефоном
    ("info@interstone.su, +7 499 40-40-300, telegram t.me/+79154417060.", 1, 1, 1),
    # Маркер со слэшем: «telegram/WhatsApp»
    ("info@avitekengineering.ru, +7 495 139-60-40, telegram/WhatsApp "
     "+7 999 672-16-11.", 1, 1, 1),
    ("8 800 550-07-35, t.me/asu_js", 0, 1, 1),
    # Профиль vc.ru — не почта, не телеграм, не телефон
    ("только через профиль vc.ru — https://vc.ru/id484768", 0, 0, 0),
    # Голый @ник без маркера рядом с почтой — это хвост почты, а не телеграм
    ("box@kr.digital или job@kr.digital.", 2, 0, 0),
]


def test_channels() -> None:
    for line, ne, nt, np_ in CASES:
        ch = K.channels(line)
        got = (len(ch["email"]), len(set(ch["telegram"])), len(ch["phone"]))
        check(f"разбор: {line[:46]}…", got == (ne, nt, np_))
        if got != (ne, nt, np_):
            print(f"         ждали {(ne, nt, np_)}, получили {got}: {ch}")


def test_phone_normalised() -> None:
    ch = K.channels("info@esteticadent.ru, +7 (4212) 55-66-18.")
    check("номер приводится к цифрам", ch["phone"] == ["+74212556618"])
    ch = K.channels("+7 499 40-40-300, telegram t.me/+79154417060")
    check("телеграм-номер не попал в телефоны", ch["phone"] == ["+74994040300"])
    check("телеграм-номер попал в телеграм", "+79154417060" in ch["telegram"])


def test_no_false_phone() -> None:
    # Идентификатор vc.ru — шесть цифр, телефоном становиться не должен
    check("id профиля не телефон",
          K.channels("https://vc.ru/id484768")["phone"] == [])
    check("пустая строка ничего не даёт",
          K.channels("") == {"email": [], "telegram": [], "phone": [], "url": []})


def test_dupcheck_pool() -> None:
    """Доля своего текста не должна зависеть от того, что показывают.

    25.08: `dupcheck H` давал 37% своего, полный разбор — 26%, потому что
    штампы искались внутри выбранной партии. Порог приёмки 35% партия
    проходила, не будучи переписанной, — счётчик мерил лекало лекалом.
    """
    stamp = " ".join(f"с{i}" for i in range(12))  # общая фраза длиннее n-граммы
    pool = []
    for tag, n in (("X", 1), ("Y", 4)):
        for i in range(n):
            uniq = " ".join(f"{tag}{i}слово{j}" for j in range(12))
            pool.append((tag, {"id": f"{tag}.{i}", "name": tag,
                               "body": f"{stamp} {uniq}"}))
    x_only = [c for c in pool if c[0] == "X"]

    by_pool = D.own_shares(pool, min_hits=4, skel=set())
    by_part = D.own_shares(x_only, min_hits=4, skel=set())
    share_pool = [s[0] for s in by_pool if s[3] == "X"][0]
    share_part = [s[0] for s in by_part if s[3] == "X"][0]
    check("штамп из пула срезает долю своего (X в пуле < X в одиночку)",
          share_pool < share_part)
    check("в одиночку штамп не виден и доля завышена до 100%",
          share_part == 1.0 and share_pool < 0.6)
    check("load() не принимает фильтр партий (штампы считаются по всему пулу)",
          D.load.__code__.co_argcount == 0)


def test_dupcheck_live() -> None:
    """Те же числа на живом пуле: фильтр — это показ, а не разбор."""
    import io, contextlib, re as _re

    def run_cli(args):
        buf = io.StringIO()
        old, sys.argv = sys.argv, ["dupcheck"] + args
        try:
            with contextlib.redirect_stdout(buf):
                D.main()
        finally:
            sys.argv = old
        return dict(_re.findall(r"^\s*([A-Z]{1,2}):\s+(\d+)%", buf.getvalue(), _re.M))

    full = run_cli([])
    if not full:
        check("живой пул волны 2 на месте", False)
        return
    tag = min(full, key=lambda t: int(full[t]))
    one = run_cli([tag])
    check(f"партия {tag}: фильтр даёт ту же долю, что полный разбор "
          f"({one.get(tag)}% против {full[tag]}%)", one.get(tag) == full[tag])

    # Устойчивость печати: два прогона в одном процессе совпадут и на кривом
    # коде (порядок множества внутри процесса не меняется), поэтому проверяется
    # не повтор, а сам признак — при равном счёте теги идут по алфавиту.
    buf = io.StringIO()
    old, sys.argv = sys.argv, ["dupcheck"]
    try:
        with contextlib.redirect_stdout(buf):
            D.main()
    finally:
        sys.argv = old
    bad = []
    for line in _re.findall(r"^\d+ писем \((.+)\)$", buf.getvalue(), _re.M):
        pairs = [(-int(c), t) for t, c in _re.findall(r"([A-Z]{1,2})×(\d+)", line)]
        if pairs != sorted(pairs):
            bad.append(line)
    check(f"порядок партий в отчёте устойчив, не порядок множества (кривых строк {len(bad)})",
          not bad)


def fixture_card(cid: str, name: str, dom: str, uniq: str) -> dict:
    """Карточка, проходящая validate: все поля, обязательные строки, свой текст."""
    body = ("Добрый день!\n\n" + uniq + "\n\n"
            "Мы берём один ручной процесс и сдаём вместо него работающую систему. "
            "Готов созвониться на 15 минут — эти минуты окупятся первой же "
            "сэкономленной неделей.\n\nС уважением, Илья Лебедев")
    return {"id": cid, "segment": "seg9", "template": "T1", "name": name,
            "city": "Владивосток", "what": "проверка", "email": f"hi@{dom}",
            "source_url": "https://example.ru/", "checked": "2026-08-26",
            "why": "фикстура", "hook": "зацепка из четырёх слов ровно",
            "subject": f"Автоматизация под задачи {name}", "body": body}


def test_status_fixtures() -> int:
    """Вердикты `status` на фикстурах: БРАК, ДОРАБОТАТЬ, ГОТОВА, ОТПРАВЛЕНА.

    Живой пул и журнал не читаются: каталог свой, `sent` передаётся руками —
    самотест не должен зависеть от того, что сегодня в memory/outbox.
    """
    import json as _json
    import tempfile

    from jarvis.modules.outbox.wave2 import status as ST

    stamp = " ".join(f"штамп{i}" for i in range(14))  # общая фраза длиннее n-граммы
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)

        def dump(tag: str, cards: list[dict]) -> None:
            (root / f"seg9-partiya-{tag}.json").write_text(
                _json.dumps(cards, ensure_ascii=False), encoding="utf-8")

        good = [fixture_card(f"9.{i}", f"Компания{i}", f"good{i}.ru",
                             " ".join(f"своё{i}слово{j}" for j in range(40)))
                for i in range(4)]
        dump("GG", good)
        # ниже порога: у писем почти нет своего текста, зато общий штамп
        # первые абзацы обязаны отличаться (иначе это уже брак валидатора),
        # но всё остальное тело у писем общее — это и есть лекало
        dump("LO", [fixture_card(f"9.1{i}", f"Фирма{i}", f"low{i}.ru",
                                 f"вариант{i} {stamp}") for i in range(4)])
        # брак: в теле остались скобки скелета
        bad = [fixture_card(f"9.2{i}", f"Контора{i}", f"bad{i}.ru",
                            " ".join(f"иное{i}сл{j}" for j in range(40)))
               for i in range(4)]
        bad[0]["body"] = bad[0]["body"].replace("Добрый день!", "Добрый день, [имя]!")
        dump("BD", bad)
        dump("SE", [fixture_card(f"9.3{i}", f"Артель{i}", f"sent{i}.ru",
                                 " ".join(f"третье{i}сл{j}" for j in range(40)))
                    for i in range(4)])

        sent = {f"hi@sent{i}.ru" for i in range(4)}
        rows = {r["tag"]: r for r in ST.collect(root=root, sent=sent)}

    check("партии разобраны все четыре", sorted(rows) == ["BD", "GG", "LO", "SE"])
    check(f"скобки скелета → БРАК (у BD {len(rows['BD']['errors'])} ошибок)",
          rows["BD"]["verdict"] == "БРАК" and rows["BD"]["errors"])
    check(f"доля ниже порога → ДОРАБОТАТЬ (LO {rows['LO']['avg']:.0%})",
          rows["LO"]["verdict"] == "ДОРАБОТАТЬ" and rows["LO"]["avg"] < 0.38)
    check(f"чистая партия с неотправленными → ГОТОВА (GG {rows['GG']['avg']:.0%})",
          rows["GG"]["verdict"] == "ГОТОВА" and rows["GG"]["avg"] >= 0.38)
    check("все адреса в журнале → ОТПРАВЛЕНА",
          rows["SE"]["verdict"] == "ОТПРАВЛЕНА" and rows["SE"]["sent"] == 4)
    check("брак не считается готовым: в готовые попали только GG",
          [t for t, r in rows.items() if r["verdict"] == "ГОТОВА"] == ["GG"])
    return 0


def test_status_same_measure() -> None:
    """`status` не заводит второе мерило: доля партии — ровно та, что у dupcheck.

    И считается по всему пулу: партия с браком в вердикте не меняет чисел
    соседей, а фильтр партий в CLI ограничивает показ, не разбор.
    """
    import io
    import contextlib
    import re as _re

    from jarvis.modules.outbox.wave2 import status as ST

    rows = ST.collect(sent=set())
    if not rows:
        check("живой пул волны 2 на месте", False)
        return
    by_dup: dict[str, list[float]] = {}
    for share, _o, _t, tag, _c, _n in D.own_shares(D.load(), min_hits=ST.MIN_HITS):
        by_dup.setdefault(tag, []).append(share)
    bad = [r["tag"] for r in rows
           if abs(r["avg"] - sum(by_dup.get(r["tag"], [0])) / max(len(by_dup.get(r["tag"], [1])), 1)) > 1e-9]
    check(f"доли совпадают с dupcheck по всем {len(rows)} партиям (расходятся: {bad or '—'})",
          not bad)

    tag = min(rows, key=lambda r: r["avg"])["tag"]
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        ST.main([tag])
    m = _re.search(rf"^{tag}\s+\d+\s+\d+\s+\d+\s+\d+\s+(\d+)%", buf.getvalue(), _re.M)
    want = f"{[r for r in rows if r['tag'] == tag][0]['avg']:.0%}"
    check(f"фильтр показа не меняет долю партии {tag} ({m and m.group(1)}% против {want})",
          bool(m) and m.group(1) + "%" == want)


def test_history_body_only_from_journal() -> None:
    """Досье цитирует ТОЛЬКО журнал отправки — ни шаблон, ни карточку (bl-091).

    Дыра была молчаливой: history подставлял в письмо сегодняшний текст из
    letters.json, досье выглядело правдоподобно и соврало бы при первой же
    правке шаблона — а по досье строится второе касание живому клиенту.
    Проверяется поведение, а не комментарий в коде: журнал без тела обязан
    дать «текст утерян», журнал с телом — показать именно его.

    Живые журналы и derived/threads не трогаются: все четыре константы модуля
    уводятся во временный каталог, а после прогона сверяется, что живые файлы
    побайтово те же (правило samotest-ne-pishet-v-zhivuyu-pamyat).
    """
    import contextlib
    import io
    import json as _json
    import tempfile

    from jarvis.modules.outbox import history as H
    from jarvis.modules.outbox import send as S

    # Из сегодняшнего шаблона берём длинную фразу — ровно она и утекала в досье.
    live_body = (S.letters().get("1.1") or {}).get("body") or ""
    phrase = next((ln.strip()[:60] for ln in live_body.split("\n")
                   if len(ln.strip()) > 40), "")
    check("живой шаблон 1.1 на месте (есть чему утекать)", bool(phrase))

    live_sent = H.SENT.read_bytes() if H.SENT.exists() else b""
    live_repl = H.REPLIES.read_bytes() if H.REPLIES.exists() else b""
    live_threads = sorted(p.name for p in H.OUT.glob("*.md")) if H.OUT.is_dir() else []

    from_journal = "ОСОБЫЙ ТЕКСТ ИЗ ЖУРНАЛА, шаблону не родственник."
    from_git = "ВОССТАНОВЛЕННОЕ ИЗ ГИТА ТЕЛО письма."
    from_card = "ЗАПАСНОЙ ТЕКСТ ИЗ КАРТОЧКИ ВОЛНЫ 2."
    sent_rows = [
        # без тела: карточка живая (1.1), шаблон под неё есть — и не должен всплыть
        {"ts": "2026-08-26T09:00:00", "to": "a@example.ru", "name": "Первая",
         "num": "1.1", "template": "T1", "ok": True},
        # с телом: показывается оно, хотя номер карточки тот же самый
        {"ts": "2026-08-26T09:05:00", "to": "b@example.ru", "name": "Вторая",
         "num": "1.1", "template": "T1", "ok": True, "body": from_journal},
        # восстановленное из гита: тело есть, но помечено неточным
        {"ts": "2026-08-26T09:10:00", "to": "c@example.ru", "name": "Третья",
         "num": "9.9", "template": "T4", "ok": True, "body": from_git,
         "body_exact": False, "body_source": "letters.json@aed401f"},
        # без тела, зато карточка волны 2 со своим текстом — второй источник утечки
        {"ts": "2026-08-26T09:15:00", "to": "d@example.ru", "name": "Четвёртая",
         "num": "7.7", "template": "T4", "ok": True},
    ]

    dos: dict[str, str] = {}
    idx = ""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "wave2").mkdir()
        (root / "wave2" / "seg7-partiya-ZZ.json").write_text(_json.dumps(
            [{"id": "7.7", "name": "Четвёртая", "city": "Владивосток",
              "segment": "seg7", "what": "проверка", "email": "d@example.ru",
              "source_url": "https://example.ru/kartochka", "body": from_card}],
            ensure_ascii=False), encoding="utf-8")
        (root / "sent.jsonl").write_text(
            "\n".join(_json.dumps(r, ensure_ascii=False) for r in sent_rows) + "\n",
            encoding="utf-8")
        (root / "replies.jsonl").write_text("", encoding="utf-8")

        keep = (H.SENT, H.REPLIES, H.OUT, H.WAVE2)
        try:
            H.SENT, H.REPLIES = root / "sent.jsonl", root / "replies.jsonl"
            H.OUT, H.WAVE2 = root / "threads", root / "wave2"
            with contextlib.redirect_stdout(io.StringIO()):
                H.build()
            for addr, slug in H.slugs([r["to"] for r in sent_rows]).items():
                path = H.OUT / f"{slug}.md"
                dos[addr] = path.read_text(encoding="utf-8") if path.exists() else ""
            ipath = H.OUT / "INDEX.md"
            idx = ipath.read_text(encoding="utf-8") if ipath.exists() else ""
        finally:
            H.SENT, H.REPLIES, H.OUT, H.WAVE2 = keep

    a, b, c, d = (dos.get(x, "") for x in ("a@example.ru", "b@example.ru",
                                           "c@example.ru", "d@example.ru"))
    check("досье собраны на все четыре адреса", all((a, b, c, d)))
    check("журнал без тела → в досье «текст утерян»", "текст письма утерян" in a)
    check("журнал без тела → текст шаблона в файл НЕ попал",
          bool(phrase) and phrase not in a)
    check("тема при этом берётся из subjects.json (шаблон нужен ради темы)",
          "Команда удвоилась за год" in a)
    check("журнал с телом → показан именно он", from_journal in b)
    check("журнал с телом → шаблон той же карточки не подставлен",
          bool(phrase) and phrase not in b and "текст письма утерян" not in b)
    check("тело с body_exact=false → пометка «восстановлено из гита» и источник",
          "восстановлено из гита" in c and "aed401f" in c and from_git in c)
    check("карточка волны 2 → её текст в досье не утекает",
          from_card not in d and "текст письма утерян" in d)
    check("карточка при этом остаётся источником шапки",
          "https://example.ru/kartochka" in d)
    check("сводка считает 4 письма и 0 ответов",
          "писем отправлено: 4" in idx and "ответов получено: 0" in idx)

    now_sent = H.SENT.read_bytes() if H.SENT.exists() else b""
    now_repl = H.REPLIES.read_bytes() if H.REPLIES.exists() else b""
    now_threads = sorted(p.name for p in H.OUT.glob("*.md")) if H.OUT.is_dir() else []
    check("живые журналы отправки и ответов после прогона те же",
          now_sent == live_sent and now_repl == live_repl)
    check("живые досье в derived/threads не тронуты", now_threads == live_threads)


def test_history_merges_addresses() -> None:
    """Один собеседник — одно досье, и ни одно событие журнала не пропадает.

    bl-140: 26.08 Николай из No Logo ответил не с общего ящика студии, а со
    своего личного, и наши ответы ушли туда же. Досье группировалось по голому
    адресу — разговор развалился на два файла, и в главном последним событием
    осталось 25.08. По правилу uslovie-uzhe-nazvannoe-klientu досье читается
    перед письмом клиенту, так что черновик по такой картине был бы мимо.
    Дыра была молчаливой: оба файла выглядели целыми.

    Проверяется поведение на четырёх адресах: живой ответ с личного ящика
    склеивает досье, а отлуп от почтового робота — нет (робот один на весь
    список, склейка по нему связала бы всех контактов в одно досье; ровно так
    и вышло на первом прогоне 30.08). Мерило приёмки — счётное: событий во всех
    досье ровно столько, сколько записей с адресом в обоих журналах.

    Живые журналы и derived/threads не трогаются: константы модуля уводятся во
    временный каталог, после прогона сверяется побайтово
    (правило samotest-ne-pishet-v-zhivuyu-pamyat).
    """
    import contextlib
    import io
    import json as _json
    import tempfile

    from jarvis.modules.outbox import history as H

    live_sent = H.SENT.read_bytes() if H.SENT.exists() else b""
    live_repl = H.REPLIES.read_bytes() if H.REPLIES.exists() else b""
    live_threads = sorted(p.name for p in H.OUT.glob("*.md")) if H.OUT.is_dir() else []

    sent_rows = [
        # общий ящик студии — с него началась переписка, у него карточка
        {"ts": "2026-08-23T16:00:00", "to": "info@studio.ru", "name": "Студия",
         "num": "1.11", "template": "T1", "ok": True, "body": "первое письмо"},
        # ответ ушёл уже на личный ящик человека: карточки у него нет
        {"ts": "2026-08-26T15:00:00", "to": "kolya@studio.ru", "ok": True,
         "body": "наш ответ Коле"},
        # два чужих контакта, каждому по письму: их отлупы придут от одного робота
        {"ts": "2026-08-24T10:00:00", "to": "a@first.ru", "name": "Первый",
         "num": "4.82", "template": "T4", "ok": True, "body": "письмо первому"},
        {"ts": "2026-08-24T10:05:00", "to": "b@second.ru", "name": "Второй",
         "num": "1.55", "template": "T1", "ok": True, "body": "письмо второму"},
        # пилот на ящик Ильи — в досье не входит и в мерило тоже
        {"ts": "2026-08-24T11:00:00", "to": "", "pilot": True, "ok": True},
    ]
    reply_rows = [
        {"ts": "2026-08-24T16:00:00+03:00", "from": "info@studio.ru",
         "contact": "info@studio.ru", "num": "1.11", "kind": "reply",
         "text": "ответ с общего ящика"},
        # УЛИКА: inbox сопоставил письмо с карточкой студии, а пришло оно с личного
        {"ts": "2026-08-25T17:00:00+03:00", "from": "kolya@studio.ru",
         "contact": "info@studio.ru", "num": "1.11", "kind": "reply",
         "text": "ответ с личного ящика"},
        # inbox уже не опознал карточку — группируется по личному адресу
        {"ts": "2026-08-26T09:00:00+03:00", "from": "kolya@studio.ru",
         "contact": "kolya@studio.ru", "num": "", "kind": "reply",
         "text": "СВЕЖИЙ ОТВЕТ КОЛИ"},
        # НЕ улика: робот один и тот же у двух чужих контактов
        {"ts": "2026-08-24T10:01:00+03:00", "from": "mailer-daemon@corp.mail.ru",
         "contact": "a@first.ru", "num": "4.82", "kind": "bounce", "text": "no such user"},
        {"ts": "2026-08-24T10:06:00+03:00", "from": "mailer-daemon@corp.mail.ru",
         "contact": "b@second.ru", "num": "1.55", "kind": "bounce", "text": "no such user"},
    ]

    dos: dict[str, str] = {}
    out = ""
    n_events = n_threads = 0
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "wave2").mkdir()
        (root / "sent.jsonl").write_text(
            "\n".join(_json.dumps(r, ensure_ascii=False) for r in sent_rows) + "\n",
            encoding="utf-8")
        (root / "replies.jsonl").write_text(
            "\n".join(_json.dumps(r, ensure_ascii=False) for r in reply_rows) + "\n",
            encoding="utf-8")

        keep = (H.SENT, H.REPLIES, H.OUT, H.WAVE2)
        try:
            H.SENT, H.REPLIES = root / "sent.jsonl", root / "replies.jsonl"
            H.OUT, H.WAVE2 = root / "threads", root / "wave2"
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                H.build()
            out = buf.getvalue()
            threads = H.collect()
            n_threads = len(threads)
            n_events = sum(len(t["events"]) for t in threads.values())
            want = H.usable(H.read_jsonl(H.SENT), H.read_jsonl(H.REPLIES))
            for p in H.OUT.glob("*.md"):
                dos[p.name] = p.read_text(encoding="utf-8")
        finally:
            H.SENT, H.REPLIES, H.OUT, H.WAVE2 = keep

    # --- мерило приёмки: ни одна запись журнала не пропала
    check(f"событий в досье ({n_events}) = записей с адресом в журналах ({want})",
          n_events == want == 9)
    check("сборка не жаловалась на потерю событий", "События потерялись" not in out)

    studio = dos.get("info-studio-ru.md", "")
    check("досье студии собрано на адрес карточки", bool(studio))
    check("личный ящик отдельным досье НЕ завёлся",
          "kolya-studio-ru.md" not in dos)
    check("все шесть событий студии в одном файле — вся переписка целиком",
          studio.count("\n## ") == 5 + studio.startswith("## "))
    check("свежий ответ с личного ящика виден (тот самый провал 27.08)",
          "СВЕЖИЙ ОТВЕТ КОЛИ" in studio)
    check("наше письмо на личный ящик видно и помечено чужим адресом",
          "наш ответ Коле" in studio and "**Кому:** kolya@studio.ru" in studio)
    check("шапка называет второй адрес человека",
          "Пишет также с: kolya@studio.ru" in studio)
    check("последнее событие студии — 26.08, а не 25.08",
          "последнее событие: 2026-08-26" in studio)

    # --- предохранитель: робот не склеивает чужих
    check("чужие контакты остались двумя досье",
          bool(dos.get("a-first-ru.md")) and bool(dos.get("b-second-ru.md")))
    check("почтовый робот своего досье не завёл",
          not any("mailer-daemon" in n for n in dos))
    check("первый контакт не утащил к себе письмо второго",
          "письмо второму" not in dos.get("a-first-ru.md", "x"))
    check("всего досье три: студия и два чужих", n_threads == 3)

    now_sent = H.SENT.read_bytes() if H.SENT.exists() else b""
    now_repl = H.REPLIES.read_bytes() if H.REPLIES.exists() else b""
    now_threads = sorted(p.name for p in H.OUT.glob("*.md")) if H.OUT.is_dir() else []
    check("живые журналы после прогона склейки те же",
          now_sent == live_sent and now_repl == live_repl)
    check("живые досье после прогона склейки не тронуты", now_threads == live_threads)


def test_taken(mod=TK) -> None:
    """Занятое (домены/названия/id): список для сборщика, чтобы не зайти на то же.

    26.08 (bl-100): taken.py — единственный предохранитель от повторного захода
    на уже занятый домен, а сам никогда не проверялся. Три вопроса без ответа:
    не утечёт ли публичный почтовик (mail.ru) в «занятые домены», не роняет ли
    отчёт битый файл партии, не задвоится ли id, встреченный в двух партиях.
    Живых путей нет: mod.HERE уводится во временный каталог со своими JSON,
    mod.known_contacts (чтение T-835 первой волны) подменяется — ни живой пул
    второй волны, ни файл первой волны не читаются.
    """
    import contextlib
    import io
    import json as _json
    import re as _re
    import tempfile

    live_here, live_known = mod.HERE, mod.known_contacts

    def run_main(tmp: Path, argv: list[str]) -> tuple[str, str]:
        saved_here, saved_known = mod.HERE, mod.known_contacts
        mod.HERE = tmp
        mod.known_contacts = lambda: ({"studio-known.ru"}, {"студия known"})
        out, err = io.StringIO(), io.StringIO()
        try:
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                code = mod.main(argv)
        finally:
            mod.HERE, mod.known_contacts = saved_here, saved_known
        check("main() всегда возвращает 0 — это отчёт, а не приёмка партии", code == 0)
        return out.getvalue(), err.getvalue()

    with tempfile.TemporaryDirectory(prefix="jarvis-taken-selftest-") as td:
        tmp = Path(td)
        (tmp / "seg9-partiya-AA.json").write_text(_json.dumps([
            {"id": "9.1", "name": "Компания А", "email": "hi@newdom-a.ru"},
            {"id": "9.2", "name": "", "email": "bad-email-no-at"},
            {"id": "9.3", "name": "Компания Б", "email": "hi@mail.ru"},
            {"name": "Компания без id", "email": "hi@noidcorp.ru"},
        ], ensure_ascii=False), encoding="utf-8")
        (tmp / "seg9-partiya-AB.json").write_text(_json.dumps([
            {"id": "9.1", "name": "Компания А", "email": "hi@newdom-a.ru"},
            {"id": "9.4", "name": "Компания В", "email": "hi@newdom-v.ru"},
            {"id": "2.1", "name": "Компания Мл", "email": "hi@extra2.ru"},
            {"id": "10.2", "name": "Компания Ст", "email": "hi@extra10.ru"},
            {"id": "x.9", "name": "Компания Хвост", "email": "hi@extrax.ru"},
        ], ensure_ascii=False), encoding="utf-8")
        (tmp / "seg9-partiya-ZZBROKEN.json").write_text("{не json совсем", encoding="utf-8")

        out, err = run_main(tmp, [])
        check("битый файл партии не роняет отчёт (main дошёл до конца)", bool(out))
        check("про битый файл партии сказано в stderr, а не молча",
              "ZZBROKEN" in err and "не читается" in err)

        m = _re.search(r"# ЗАНЯТЫЕ ДОМЕНЫ \((\d+)\)[^\n]*\n([^\n]*)", out)
        doms = set(m.group(2).split(", ")) if m else set()
        check("счётчик доменов в шапке совпадает со списком (%d)" % len(doms),
              bool(m) and int(m.group(1)) == len(doms))
        check("домен волны 1 (studio-known.ru) на месте", "studio-known.ru" in doms)
        check("домены волны 2 (кроме публичных и без id-карточки тоже) на месте",
              {"newdom-a.ru", "newdom-v.ru", "noidcorp.ru", "extra2.ru",
               "extra10.ru", "extrax.ru"} <= doms)
        check("публичный почтовик (mail.ru) не попал в занятые домены",
              "mail.ru" not in doms)
        check("почта без @ (bad-email-no-at) не стала доменом", "bad-email-no-at" not in doms)
        check("дубль домена (9.1 в двух партиях) не задвоил список",
              len(doms) == 7)

        m = _re.search(r"# ЗАНЯТЫЕ НАЗВАНИЯ второй волны \((\d+)\):\n([^\n]*)", out)
        names2 = set(m.group(2).split("; ")) if m else set()
        check("счётчик названий волны 2 совпадает со списком (%d)" % len(names2),
              bool(m) and int(m.group(1)) == len(names2))
        check("названия волны 2 верные, пустое имя не попало, дубль не задвоен",
              names2 == {"Компания А", "Компания Б", "Компания без id",
                         "Компания В", "Компания Мл", "Компания Ст", "Компания Хвост"})

        check("счёт названий волны 1 читается из known_contacts, а не из файлов партий",
              "Названия первой волны (1)" in out)

        check("без --ids раздел с id не печатается вовсе", "ЗАНЯТЫЕ id" not in out)

        out_ids, _ = run_main(tmp, ["--ids"])
        m = _re.search(r"# ЗАНЯТЫЕ id \((\d+)\):\n([^\n]*)", out_ids)
        ids_list = m.group(2).split(", ") if m else []
        check("с --ids раздел появляется, счётчик совпадает со списком (%d)" % len(ids_list),
              bool(m) and int(m.group(1)) == len(ids_list))
        check("id, встреченный в двух партиях (9.1), в списке один раз",
              ids_list.count("9.1") == 1 and len(ids_list) == 7)
        check("сортировка id числовая по обеим частям (2.1 < 9.* < 10.2), "
              "нечисловой хвост (x.9) — последним, а не строковым сравнением",
              ids_list == ["2.1", "9.1", "9.2", "9.3", "9.4", "10.2", "x.9"])

        # партий нет вовсе — отчёт живёт, показывает только волну 1
        with tempfile.TemporaryDirectory(prefix="jarvis-taken-empty-") as td2:
            out_empty, _ = run_main(Path(td2), [])
        check("пустой каталог партий не роняет отчёт", "studio-known.ru" in out_empty)
        check("без единой партии занятых доменов волны 2 нет",
              "ЗАНЯТЫЕ ДОМЕНЫ (1)" in out_empty)

    check("живые HERE и known_contacts восстановлены после подмены",
          mod.HERE is live_here and mod.known_contacts is live_known)


def test_journal_ok_field() -> None:
    """Поле ok в журнале отправки: живой файл полон, новый путь наружу не
    может его забыть, а читатели считают провалом только явное ok: false.

    Зачем (bl-132): 26.08.2026 ручное письмо Николаю записал отдельный код
    мимо journal_add — без ok. Пересчёт реестра засчитал единственный за всю
    жизнь «провал рассылки» письму, которое дошло, а планировщик счёл адрес
    ненаписанным и мог отправить второе письмо тому же человеку.
    """
    import json as _json
    import tempfile

    from jarvis.modules.outbox import send as S

    live = S.JOURNAL
    bad = []
    if live.exists():
        for i, line in enumerate(live.read_text(encoding="utf-8").splitlines(), 1):
            if not line.strip():
                continue
            try:
                rec = _json.loads(line)
            except _json.JSONDecodeError:
                continue
            if "ok" not in rec:
                bad.append(i)
    check("в живом журнале отправки нет строк без поля ok (иначе: %s)" % (bad or "—"),
          not bad)

    # journal_add достраивает строку сам — даже если вызвавший о поле не знал
    keep = S.JOURNAL
    try:
        with tempfile.TemporaryDirectory(prefix="jarvis-journal-ok-") as td:
            S.JOURNAL = Path(td) / "sent.jsonl"
            S.journal_add({"ts": "2026-08-27T10:00:00", "to": "x@example.ru"})
            S.journal_add({"ts": "2026-08-27T10:01:00", "to": "y@example.ru",
                           "ok": False, "error": "SMTPException: нет сети"})
            rows = [_json.loads(l) for l in
                    S.JOURNAL.read_text(encoding="utf-8").splitlines() if l.strip()]
    finally:
        S.JOURNAL = keep
    check("journal_add проставляет ok=true, если его не передали",
          rows[0].get("ok") is True)
    check("journal_add не портит явный ok=false", rows[1].get("ok") is False)

    check("row_ok: нет поля → письмо ушло", S.row_ok({"to": "a@b.ru"}) is True)
    check("row_ok: ok=false → провал", S.row_ok({"ok": False}) is False)
    check("row_ok: ok=true → успех", S.row_ok({"ok": True}) is True)
    check("живой путь журнала после прогона тот же", S.JOURNAL == live)


def test_journal_body() -> None:
    """Тело письма в журнале: строка без текста не бывает молчащей.

    Зачем (bl-141): 26.08 и 27.08.2026 два ответа Николаю (No Logo) записал
    в журнал отдельный код, минуя send.py, — без поля body. Досье писало
    «текст утерян», и процитировать последнее сказанное живому клиенту стало
    нечем, хотя по этому строится следующий черновик. Тела восстановлены,
    а journal_add теперь помечает безтелую строку явно.
    """
    import json as _json
    import tempfile

    from jarvis.modules.outbox import send as S

    live = S.JOURNAL
    mute = []
    if live.exists():
        for i, line in enumerate(live.read_text(encoding="utf-8").splitlines(), 1):
            if not line.strip():
                continue
            try:
                rec = _json.loads(line)
            except _json.JSONDecodeError:
                continue
            if not str(rec.get("body") or "").strip() and not rec.get("body_lost"):
                mute.append(i)
    check("в живом журнале нет строк без тела и без пометки утраты (иначе: %s)"
          % (mute or "—"), not mute)

    keep = S.JOURNAL
    try:
        with tempfile.TemporaryDirectory(prefix="jarvis-journal-body-") as td:
            S.JOURNAL = Path(td) / "sent.jsonl"
            S.journal_add({"ts": "2026-08-30T10:00:00", "to": "x@example.ru"})
            S.journal_add({"ts": "2026-08-30T10:01:00", "to": "y@example.ru",
                           "body": "Николай, добрый день."})
            rows = [_json.loads(l) for l in
                    S.JOURNAL.read_text(encoding="utf-8").splitlines() if l.strip()]
    finally:
        S.JOURNAL = keep
    check("journal_add помечает строку без тела body_lost",
          rows[0].get("body_lost") is True)
    check("journal_add не трогает строку с телом",
          "body_lost" not in rows[1])
    check("живой путь журнала после проверки тела тот же", S.JOURNAL == live)


def run() -> int:
    print("\n--- РАЗБОР КОНТАКТОВ ---")
    test_channels()
    test_phone_normalised()
    test_no_false_phone()
    print("\n--- ОДНОТИПНОСТЬ ПИСЕМ (dupcheck) ---")
    test_dupcheck_pool()
    test_dupcheck_live()
    print("\n--- ПРИЁМКА ПАРТИЙ (status) ---")
    test_status_fixtures()
    test_status_same_measure()
    print("\n--- ИСТОРИЯ ПЕРЕПИСКИ (history) ---")
    test_history_body_only_from_journal()
    test_history_merges_addresses()
    print("\n--- ЖУРНАЛ ОТПРАВКИ: ПОЛЕ ok ---")
    test_journal_ok_field()
    test_journal_body()
    print("\n--- ЗАНЯТОЕ (taken: домены, названия, id) ---")
    test_taken()
    print("\nВСЁ ХОРОШО" if not fails else f"\nСЛОМАНО ({len(fails)}): {fails}")
    return 1 if fails else 0


if __name__ == "__main__":
    raise SystemExit(run())
