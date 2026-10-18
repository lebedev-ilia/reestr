"""Прогон цикла демона ВК на подменённом API. Без сети и без токена.

Проверяет то, что иначе проверялось бы вручную и по одному разу:
дедупликацию, фильтр чужих, коды failed 1/2/3, отложенную расшифровку
голосового, нарезку длинного ответа, ночную тишину (bl-034).

Запуск: ./.venv/bin/python -m jarvis.modules.vk.selftest

Живая память (bl-033): демон на фикстурах шлёт message_in/message_out/error
через M.log, и до этой правки они падали в настоящий memory/log/ — 12 из 19
«провалов канала» в реестре способностей оказались этой фикстурой, а не живым
отказом. C.LOG_DIR подменяется на время всего прогона, живой файл дня
сверяется до и после (правило samotest-ne-pishet-v-zhivuyu-pamyat).

Сверка живого лога (bl-040): побайтовое равенство красило самотест ложно —
демон ВК и параллельные подагенты дозаписывают тот же файл дня, пока идёт
прогон. Сверяем честно: прежнее содержимое обязано остаться нетронутым
префиксом (не переписано, не укорочено), а строки, добавленные за время
прогона, обязаны не содержать следов фикстуры самотеста (см. FIXTURE_MARKERS).
"""
import asyncio
import contextlib
import io
import json
import os
import shutil
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from jarvis.core import config as C, heartbeat as HB  # noqa: E402
from jarvis.modules.vk import api, daemon as d, notify as ntf, stt  # noqa: E402

SENT = []       # ушло текстом
VOICED = []     # ушло голосовым
PROMPTS = []
RECOGNIZED = []  # голосовые, дошедшие до своего распознавания
STATE = {"polls": 0}

# bl-101: жалобы, которые цикл демона обязан выдать на фикстурах, — и больше
# никаких. Каждая строка — набор слов, которые обязаны стоять в одной строке
# вывода. Демон о сорванной доставке только печатает («✗ ответ не ушёл»),
# отдельного признака у него нет: раньше такая строка молча уезжала в зелёный
# прогон и была неотличима от настоящей поломки канала. Теперь любое лишнее ✗
# или ⚠ красит самотест, а ожидаемые помечены прямо в выводе.
EXPECTED_ALARMS = (
    ("✗ ответ не ушёл", "VK error 901"),   # опрос №10: ВК отказался принять ответ
    ("⚠", "старый формат"),                # опрос №8: событие без вложенного message
)


def fake_call(method, token, **p):
    if method == "groups.getById":
        return {"groups": [{"name": "Тест", "id": 1}]}
    if method == "groups.getLongPollServer":
        return {"server": "https://lp", "key": "k", "ts": "100"}
    if method == "messages.send":
        if "СБОЙ" in p["message"]:
            raise api.VKError(901, "Can't send messages to this user")
        SENT.append(p["message"])
        return 1
    if method == "messages.setActivity":
        return 1
    if method == "messages.getById":
        voice = {"type": "audio_message",
                 "audio_message": {"transcript": "поздняя расшифровка",
                                   "transcript_state": "done"}}
        return {"items": [{"id": 5, "attachments": [voice]}]}
    raise AssertionError("неизвестный метод " + method)


def upd(msg):
    return {"type": "message_new", "object": {"message": msg}}


def fake_poll(server, key, ts, wait=25):
    STATE["polls"] += 1
    n = STATE["polls"]
    own = {"id": 1, "from_id": 42, "peer_id": 42, "text": "привет"}
    if n == 1:                       # обычное сообщение от владельца
        return {"ts": "101", "updates": [upd(own)]}
    if n == 2:                       # то же самое ещё раз — дедуп обязан отсечь
        return {"ts": "101", "updates": [upd(own)]}
    if n == 3:                       # чужой человек
        return {"ts": "102", "updates": [upd(
            {"id": 2, "from_id": 999, "peer_id": 999, "text": "дай доступ"})]}
    if n == 4:                       # ключ протух
        return {"failed": 2}
    if n == 5:                       # ts устарел
        return {"failed": 1, "ts": "200"}
    if n == 6:                       # голосовое, расшифровка ещё не готова
        voice = {"type": "audio_message",
                 "audio_message": {"transcript_state": "in_progress"}}
        return {"ts": "201", "updates": [upd(
            {"id": 5, "from_id": 42, "peer_id": 42, "text": "",
             "attachments": [voice]})]}
    if n == 7:                       # длинный ответ — проверяем нарезку
        return {"ts": "202", "updates": [upd(
            {"id": 6, "from_id": 42, "peer_id": 42, "text": "длинно"})]}
    if n == 8:                       # старый формат: сообщение прямо в object
        return {"ts": "203", "updates": [{"type": "message_new", "object":
            {"id": 7, "from_id": 42, "peer_id": 42, "text": "старый формат"}}]}
    if n == 9:                       # ВК не смог — распознаю сам
        voice = {"type": "audio_message",
                 "audio_message": {"transcript_state": "failed",
                                   "link_ogg": "http://x/v.ogg"}}
        return {"ts": "204", "updates": [upd(
            {"id": 8, "from_id": 42, "peer_id": 42, "text": "",
             "attachments": [voice]})]}
    if n == 10:                      # ВК отказался принять ответ
        return {"ts": "205", "updates": [upd(
            {"id": 9, "from_id": 42, "peer_id": 42, "text": "сбой отправки"})]}
    if n == 11:                      # Илья попросил текстом — метка сильнее длины
        return {"ts": "206", "updates": [upd(
            {"id": 10, "from_id": 42, "peer_id": 42, "text": "хочу текстом"})]}
    if n == 12:                      # озвучка сломалась — ответ обязан дойти
        return {"ts": "207", "updates": [upd(
            {"id": 11, "from_id": 42, "peer_id": 42, "text": "сломай голос"})]}
    if n == 13:                      # ни ВК, ни я: ссылки на запись нет
        voice = {"type": "audio_message",
                 "audio_message": {"transcript_state": "failed"}}
        return {"ts": "208", "updates": [upd(
            {"id": 12, "from_id": 42, "peer_id": 42, "text": "",
             "attachments": [voice]})]}
    raise KeyboardInterrupt


async def fake_turn(prompt, actor="ilya", unattended=False, **kw):
    # **kw намеренно: сигнатура turn() растёт (ask, model, max_turns), и тест
    # не должен падать целиком из-за нового необязательного параметра — иначе
    # он превращается из проверки в помеху.
    PROMPTS.append(prompt)
    # Смотрим только первую строку: дальше идёт служебный хвост про канал,
    # и слова из него («длинное», «текстом») иначе срабатывают как команды.
    prompt = prompt.split("\n", 1)[0]
    if "длинно" in prompt:
        return "\n\n".join("я" * 3000 for _ in range(3))
    if "сбой отправки" in prompt:
        return "[текстом]\nСБОЙ: этот ответ ВК не примет"
    if "хочу текстом" in prompt:
        return "[текстом]\nкороткий ответ, но глазами"
    if "сломай голос" in prompt:
        return "этот ответ не озвучится"
    # Ответ намеренно длиннее 30 знаков: короче этого порога доставка штатно
    # уходит текстом (см. pick_mode), и обрубок вроде «ответ на: привет»
    # проверял бы не выбор голоса, а сам порог. Порог проверяет check_pick_mode.
    return "вот что я на это отвечу по существу дела: " + prompt[:40]


# Подмена озвучки: тест не должен зависеть от того, скачаны ли голоса
# (61 МБ на голос) и установлен ли piper. Проверяем решение «голос или
# текст», а не качество синтеза.
def fake_synthesize(text, voice=None):
    if "не озвучится" in text:
        raise RuntimeError("модель голоса не найдена")
    return text.encode("utf-8")


def fake_send_voice(token, group_id, peer_id, ogg, caption=""):
    VOICED.append(ogg.decode("utf-8"))


def install_fake_voice():
    """Подсунуть фальшивый modules.voice.say до того, как deliver его импортирует."""
    import types
    import jarvis.modules.voice as voice_pkg
    say = types.ModuleType("jarvis.modules.voice.say")
    say.synthesize = fake_synthesize
    say.send_voice = fake_send_voice
    sys.modules["jarvis.modules.voice.say"] = say
    voice_pkg.say = say


def check_images():
    """Вложения: картинки и pdf ложатся на диск, остальное честно названо.

    Без сети: api.download подменяется. Проверяется и то, что неизвестные
    вложения не выдаются за разобранные и что файл назван по имени —
    иначе на «doc» не понять, что именно прислал Илья.
    """
    api.download = lambda url: b"\x89PNG\r\n\x1a\n" + b"0" * 200
    msg = {"id": 424242, "text": "выписка", "attachments": [
        {"type": "photo", "photo": {"sizes": [
            {"width": 130, "height": 100, "url": "http://x/small.jpg"},
            {"width": 1280, "height": 900, "url": "http://x/big.jpg"}]}},
        {"type": "doc", "doc": {"ext": "png", "url": "http://x/f.png"}},
        {"type": "doc", "doc": {"ext": "pdf", "title": "Выписка.pdf",
                                "url": "http://x/f.pdf"}},
        {"type": "doc", "doc": {"ext": "xlsx", "title": "Бюджет.xlsx",
                                "url": "http://x/f.xlsx"}},
        {"type": "sticker"}]}

    assert d.photo_url(msg["attachments"][0]) == "http://x/big.jpg", "взят не крупный размер"

    # save_media пишет {"vk_images": N} через M.log — и делает это ДО того, как
    # _main подменяет C.LOG_DIR. Пока прогон шёл без флага самотеста, эта строка
    # ложилась в живой лог и потом считалась вызовом cap-015 (bl-061). Теперь её
    # уводит guard, и проверяется именно ПРИРОСТ в derived за этот вызов: файл
    # дня там остаётся от прошлых прогонов, и «строка есть» ничего не доказывает.
    from jarvis.core import memory as M
    probe = C.SELFTEST_LOG / f"{M.today().isoformat()}.jsonl"
    was = probe.read_text(encoding="utf-8").count("vk_images") if probe.exists() else 0

    media, saved = asyncio.run(d.save_media(msg))
    assert saved == {0, 1, 2}, saved
    kinds = [k for _, k in media]
    assert kinds == ["image", "image", "pdf"], kinds
    assert all(Path(p).stat().st_size > 100 for p, _ in media), media

    now = probe.read_text(encoding="utf-8").count("vk_images") if probe.exists() else 0
    assert now == was + 1, \
        "запись о вложениях ушла мимо derived — подмена лога отвалилась"

    prompt = asyncio.run(d.to_prompt("t", msg))
    assert "картинок во вложении: 2" in prompt, prompt
    assert "pdf во вложении: 1" in prompt, prompt
    assert "pdftotext" in prompt, "не сказано, чем читать pdf"
    assert "Бюджет.xlsx" in prompt, "неразобранный файл назван безлико"
    assert "sticker" in prompt, "нераспознанные вложения потерялись"
    for p, _ in media:
        assert p in prompt, "путь к файлу не попал в промпт"
        Path(p).unlink()
    print("--- ВЛОЖЕНИЯ: сохранено", len(media))


def fake_transcribe(att, tag="voice"):
    """Своё распознавание без модели и без сети.

    Веса весят 464 МБ, а прогон занимает секунды — тест не должен от них
    зависеть. Но ссылку на запись достаём настоящей voice_url: именно там
    ошибиться легче всего, ответ ВК меняется от версии к версии.
    """
    RECOGNIZED.append(tag)
    return "своя расшифровка" if stt.voice_url(att) else None


def check_stt():
    """Ссылка на запись голосового: что берём и когда сдаёмся."""
    cases = [
        ({"audio_message": {"link_ogg": "o", "link_mp3": "m"}}, "o"),
        ({"audio_message": {"link_mp3": "m"}}, "m"),          # ogg не дали
        ({"audio_message": {"duration": 5}}, None),           # ссылки нет вовсе
        ({}, None),
    ]
    for att, want in cases:
        got = stt.voice_url(att)
        assert got == want, f"ждал {want}, получил {got} на {att}"
    print("--- ГОЛОСОВОЕ: разбор ссылки, проверок", len(cases))


def check_ask_answer():
    """Разбор ответа на `ask` (job-049, находка критика 26.08 ~16:03).

    `answer in YES` требовал точного совпадения ВСЕЙ фразы одному слову из
    YES. Илья ответил «да, в дальнейшем не справшивая а делай» — это
    записалось как allowed:false, хотя согласие явное. Проверяем обе
    стороны: «да» с хвостом инструкции обязано пройти, а отказ с хвостом
    обязан остаться отказом — иначе чинили бы одну дыру и открывали
    симметричную (правило granicu-proveryaet-kod).
    """
    yes_cases = [
        "да",
        "да, в дальнейшем не справшивая а делай",   # ровно фраза 26.08
        "Да!",
        "ОК, погнали",
        "давай, действуй",
        "+",
    ]
    no_cases = [
        "",
        "нет",
        "нет, не делай",
        "не сейчас",
        "подожди",
        "не уверен, но давай подумаем",   # начинается не с "да" — отказ
        "неа",
    ]
    for text in yes_cases:
        answer = text.strip().lower()
        assert d._ask_first_word(answer) in d.YES, f"должно быть 'да': {text!r}"
    for text in no_cases:
        answer = text.strip().lower()
        assert d._ask_first_word(answer) not in d.YES, f"должно быть отказом: {text!r}"
    print("--- РАЗБОР ОТВЕТА НА ASK: проверок", len(yes_cases) + len(no_cases))


def check_pick_mode():
    """Выбор «голос или текст» — без демона, по одному правилу на строку.

    Главная строка здесь — обычный короткий ответ БЕЗ метки обязан уйти
    текстом. До 30.08.2026 та же строка требовала обратного (голос по длине);
    Илья отменил голосовые целиком, и проверка перевёрнута вместе с правилом
    golos-ili-tekst. Если она снова начнёт ждать 'voice' — значит умолчание
    вернули втихую.
    """
    cases = [
        ("Готово.", "text", "совсем короткое незачем озвучивать"),
        ("[голосом]\nГотово.", "text", "по просьбе, но речи из «Готово» не выйдет"),
        ("[голосом]\nПосчитал траты за неделю, всё сошлось до копейки.",
         "voice", "явная просьба Ильи — единственный путь к голосу"),
        ("[текстом]\n" + "нормальный ответ, " * 5, "text", "метка сильнее длины"),
        ("я" * (d.VOICE_LIMIT + 1), "text", "длинное уходит текстом"),
        ("Посчитал траты за неделю, всё сошлось с итогами банка до копейки.",
         "text", "умолчание — текст: голосовых без просьбы больше нет"),
        ("[голосом]\n/home/ilya/Рабочий стол/Agent/jarvis/memory/facts/dengi-kto-est-kto.md",
         "text", "от одного пути речи не остаётся даже по просьбе"),
    ]
    for reply, want, why in cases:
        got, _ = d.pick_mode(reply)
        assert got == want, f"{why}: ждал {want}, получил {got} на {reply[:40]!r}"
    print("--- ВЫБОР РЕЖИМА: проверок", len(cases))


# bl-040: значения, которых в живом логе быть не может — они существуют
# только внутри фикстур этого файла. Если строка, дописанная за время
# прогона, содержит хоть одно из них — писал самотест, а не кто-то живой.
FIXTURE_MARKERS = (
    "vk:999",               # actor чужого отправителя (fake_poll, id 999 — не Илья)
    "дай доступ",            # текст фиктивного чужого сообщения
    "поздняя расшифровка",  # fake_call: messages.getById отдаёт эту расшифровку
    "старый формат",        # fake_poll: событие старого формата longpoll
    "своя расшифровка",     # fake_transcribe: собственное распознавание фикстуры
    "сбой отправки",        # fake_poll: провоцирует VKError у messages.send
    "хочу текстом",         # fake_poll: метка [текстом] сильнее длины
    "сломай голос",         # fake_poll: провоцирует сбой синтеза речи
)


def check_live_log_untouched(live_before: bytes | None, live_after: bytes | None) -> None:
    """Честная сверка живого лога дня до/после прогона (bl-040).

    Побайтовое равенство красило самотест ложно: лог дозаписывают демон ВК
    и параллельные подагенты, пока идёт прогон (правило dnyom-rabotayut-
    podagenty — днём это штатно). Настоящая беда — самотест переписал или
    укоротил чужие строки, либо в лог попала его собственная фикстура.
    Рост файла чужими, честными строками — не беда.
    """
    if live_before is None:
        if live_after is None:
            return  # файла не было и не появилось — первый ход дня, всё штатно
        before = b""
    else:
        if live_after is None:
            raise AssertionError("живой лог исчез за время прогона")
        before = live_before

    if len(live_after) < len(before):
        raise AssertionError(
            f"живой лог укоротился: было {len(before)} байт, стало {len(live_after)}"
        )
    if not live_after.startswith(before):
        raise AssertionError(
            "префикс живого лога разошёлся: прежнее содержимое переписано "
            "(файл дописан не с конца)"
        )

    tail = live_after[len(before):].decode("utf-8", errors="replace")
    hits = [m for m in FIXTURE_MARKERS if m in tail]
    if hits:
        raise AssertionError(
            "в живой лог за время прогона попала фикстура самотеста: " + ", ".join(hits)
        )


def _log_tail() -> str:
    """Текст сегодняшнего файла лога — из ТЕКУЩЕГО C.LOG_DIR (подменяется на
    время прогона, см. main)."""
    from jarvis.core import memory as M
    path = C.LOG_DIR / f"{M.today().isoformat()}.jsonl"
    return path.read_text(encoding="utf-8") if path.exists() else ""


def check_prune():
    """Уборка производной зоны (bl-098): сносится ровно названное.

    Работает на временном каталоге — настоящий derived тест не трогает, хотя
    зона и восстановима: prune_derived удаляет, а необратимое в самотесте
    мокается так же строго, как запись в память (samotest-ne-pishet-v-zhivuyu-pamyat).
    Проверяем не «функция что-то удалила», а поимённо: что снесено, что уцелело.
    Главное здесь — что уцелело: древние scratch и hh_profile лежат рядом с
    целями и не трогаются вовсе (scratch — забота core/scratch.py).
    """
    tmp = Path(tempfile.mkdtemp(prefix="vk_prune_"))
    old = time.time() - 40 * 86400          # заведомо древнее любого срока
    try:
        def touch(rel: str, age: float | None = old, dir_: bool = False) -> Path:
            p = tmp / rel
            (p if dir_ else p.parent).mkdir(parents=True, exist_ok=True)
            if not dir_:
                p.write_text("x", encoding="utf-8")
            if age is not None:
                os.utime(p, (age, age))
            return p

        dead = touch("system_prompt.999999.txt")
        mine = touch(f"system_prompt.{os.getpid()}.txt")     # процесс жив
        fresh = touch("system_prompt.888888.txt", age=time.time())
        sand_old = touch("scratch/2026-01-01-proba", dir_=True)
        (sand_old / "f.txt").write_text("x", encoding="utf-8")
        os.utime(sand_old, (old, old))
        hh = touch("hh_profile/Default/cookies", age=old)
        st_old = touch("selftest/log/2026-01-01.jsonl")
        st_new = touch("selftest/log/2026-08-27.jsonl", age=time.time())

        out = d.prune_derived(root=tmp)
        assert not dead.exists(), "слепок мёртвого процесса не снесён"
        assert mine.exists(), "снесён слепок ЖИВОГО процесса — это текущий ход"
        assert fresh.exists(), "снесён свежий слепок промпта"
        assert sand_old.exists(), "тронут scratch — его убирает core/scratch.py, не этот код"
        assert hh.exists(), "тронут профиль hh.ru — восстановим только через Илью"
        assert not st_old.exists(), "старый день лога самотеста остался"
        assert st_new.exists(), "снесён свежий день лога самотеста (ob-047)"
        assert out == {"слепки": 1, "самотест": 1}, out

        # Предохранитель: слишком много кандидатов — значит смотрю не туда,
        # и правильный ответ «ничего не удалять», а не «удалить всё».
        # Порог на время проверки опущен до пяти: брать настоящий PRUNE_LIMIT
        # и плодить столько каталогов — значит держать тест заложником
        # константы (на пробе 27.08 мутация порога в миллиард забила диск).
        saved_limit = d.PRUNE_LIMIT
        d.PRUNE_LIMIT = 5
        try:
            for n in range(6):
                touch(f"selftest/log/2026-01-{n + 10}.jsonl")
            before = len(list((tmp / "selftest" / "log").iterdir()))
            stop = d.prune_derived(root=tmp)
            assert "отказ" in stop, f"порог не сработал: {stop}"
            assert len(list((tmp / "selftest" / "log").iterdir())) == before, \
                "удалял, несмотря на порог"
        finally:
            d.PRUNE_LIMIT = saved_limit
        print("--- УБОРКА derived: сносится названное, живое и исключения целы")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def check_night_mute():
    """Ночная тишина кодом (bl-034, правило nochnaya-tishina).

    Проверяется прямо на notify.py, без прогона демона: предохранитель решает
    по двум вещам — часу (quiet_hours, часовой пояс Ильи, не сервера) и каналу
    текущего хода (HB.current_channel() — записан в файл, потому что notify
    обычно запускается ОТДЕЛЬНЫМ подпроцессом, которому память демона
    недоступна). QUEUE и HB.STATE подменены на временные пути — это derived,
    восстановимая зона, но подмена всё равно нужна, чтобы прогон теста не
    затирал канал реального хода демона, если тот сейчас работает.
    """
    tmp = Path(tempfile.mkdtemp(prefix="vk_night_"))
    saved_queue, saved_state, saved_quiet = ntf.QUEUE, HB.STATE, ntf.quiet_hours
    ntf.QUEUE = tmp / "night_queue.jsonl"
    HB.STATE = tmp / "heartbeat.json"
    try:
        # (а) ночь, фоновый ход (self) — молчит и копит в очередь + лог.
        ntf.quiet_hours = lambda now=None: True
        HB.start("self")
        SENT.clear()
        ok = ntf.notify("итог фонового куска ночной работы")
        assert ok is False, "фоновый ход ночью не должен слать в ВК"
        assert not SENT, f"фоновый ход всё-таки ушёл в ВК: {SENT}"
        rows = ntf._queue_read()
        assert len(rows) == 1, f"сообщение не легло в ночную очередь: {rows}"
        assert rows[0]["text"] == "итог фонового куска ночной работы", rows
        assert "vk_muted_night" in _log_tail(), "в лог не попал ключ vk_muted_night"

        # (б) ночь, ход начат сообщением Ильи (канал vk) — шлёт как обычно.
        HB.start("vk")
        SENT.clear()
        ok = ntf.notify("статус хода, начатого Ильёй, ночью")
        assert ok is True, "ход из ВК ночью обязан слать как обычно"
        assert SENT and "статус хода, начатого Ильёй, ночью" in SENT[-1], SENT
        assert len(ntf._queue_read()) == 1, "ход из ВК не должен трогать ночную очередь"

        # (в) утро (после 08:00) — первый ход отдаёт накопленное ОДНИМ
        # сообщением и чистит очередь; повторный ход не отдаёт дважды.
        ntf.quiet_hours = lambda now=None: False
        SENT.clear()
        flushed = ntf.flush_queue()
        assert flushed is True, "утренний ход не отдал накопленную ночную очередь"
        assert len(SENT) == 1, f"очередь должна уйти ОДНИМ сообщением: {SENT}"
        assert "итог фонового куска ночной работы" in SENT[0], "накопленное потерялось при сборке"
        assert not ntf._queue_read(), "очередь не очистилась после отдачи"

        # bl-077: пульс ложится в лог своим типом (status), а утренний пакет —
        # настоящим сообщением (message_out). Иначе окно лога, ночной разбор и
        # поиск считают «читаю страницу» разговором с Ильёй.
        types = []
        for line in _log_tail().splitlines():
            try:
                rec = json.loads(line)
            except Exception:                                  # noqa: BLE001
                continue
            if "статус хода, начатого Ильёй" in rec.get("content", ""):
                types.append(("статус", rec.get("type")))
            if "Ночью копилось" in rec.get("content", ""):
                types.append(("пакет", rec.get("type")))
        got = dict(types)
        assert got.get("статус") == "status", f"статус лёг в лог не тем типом: {got}"
        assert got.get("пакет") == "message_out", f"утренний пакет лёг не тем типом: {got}"

        SENT.clear()
        flushed_again = ntf.flush_queue()
        assert flushed_again is False, "пустая очередь не должна слать повторно"
        assert not SENT, f"утренняя отдача повторилась: {SENT}"
    finally:
        ntf.QUEUE, HB.STATE, ntf.quiet_hours = saved_queue, saved_state, saved_quiet
        shutil.rmtree(tmp, ignore_errors=True)
    print("--- НОЧНАЯ ТИШИНА: фон молчит, ход из ВК как обычно, утро одним пакетом — OK")


def check_voice_upload():
    """Загрузка голосового в ВК: осечка загрузчика видна и переживается (bl-062).

    Сети здесь нет — подменяются api.call и urlopen внутри modules.voice.say.
    Проверяется ровно то, чего не хватило 23 и 25.08, когда голос дважды
    молча свалился в текст: в ошибке остаётся сырой ответ загрузчика (а не
    бесполезное «file is undefined» от следующего шага), разовая осечка
    переживается повтором, а пустая озвучка не уезжает в сеть вовсе.
    """
    try:
        from jarvis.modules.voice import say
    except ImportError as e:                                   # noqa: BLE001
        print(f"--- ЗАГРУЗКА ГОЛОСА: пропущено, нет синтеза ({e})")
        return

    sent: list[str] = []
    bodies: list[str] = []
    tries: list[int] = []

    def fake_api_call(method, token, **p):
        if method == "docs.getMessagesUploadServer":
            return {"upload_url": "https://pu.vk.com/c1/upload.php"}
        if method == "docs.save":
            assert p.get("file"), "в docs.save ушёл ответ загрузчика без file"
            return {"audio_message": {"owner_id": -1, "id": 7, "access_key": "k"}}
        if method == "messages.send":
            sent.append(p.get("attachment", ""))
            return {}
        raise AssertionError(f"лишний вызов API: {method}")

    class Resp:
        def __init__(self, body: str):
            self._b = body.encode()

        def read(self):
            return self._b

    def fake_urlopen(req, timeout=None):
        tries.append(1)
        return Resp(bodies[min(len(tries) - 1, len(bodies) - 1)])

    ogg = b"O" * (say.MIN_OGG + 10)
    saved = (say.api.call, urllib.request.urlopen, say.UPLOAD_PAUSE)
    say.api.call, urllib.request.urlopen, say.UPLOAD_PAUSE = (
        fake_api_call, fake_urlopen, 0)
    try:
        # 1. Загрузчик отказал совсем: сырой ответ обязан быть в тексте ошибки.
        bodies[:] = ['{"error":"upload not allowed"}']
        try:
            say.send_voice("t", 1, 42, ogg)
            raise AssertionError("сбой загрузчика прошёл незамеченным")
        except say.VoiceUploadError as e:
            assert "upload not allowed" in str(e), f"сырой ответ потерян: {e}"
        assert len(tries) == say.UPLOAD_TRIES, f"повторов {len(tries)}"
        assert not sent, "при сбое загрузки что-то ушло в чат"

        # 2. Разовая осечка: со второй попытки голос уходит как надо.
        tries.clear()
        bodies[:] = ["<html>500</html>", '{"file":"ok"}']
        say.send_voice("t", 1, 42, ogg)
        assert sent == ["doc-1_7_k"], f"вложение собрано неверно: {sent}"
        assert len(tries) == 2, f"повтор не сработал: попыток {len(tries)}"

        # 3. Пустая озвучка не должна даже уезжать в сеть.
        tries.clear()
        try:
            say.send_voice("t", 1, 42, b"")
            raise AssertionError("пустая озвучка ушла в загрузчик")
        except say.VoiceUploadError:
            pass
        assert not tries, "на пустой озвучке всё равно полезли в сеть"
    finally:
        say.api.call, urllib.request.urlopen, say.UPLOAD_PAUSE = saved
    print("--- ЗАГРУЗКА ГОЛОСА: сбой виден, осечка пережита — OK")


def _main():
    check_images()
    check_stt()
    check_ask_answer()
    check_pick_mode()
    check_voice_upload()
    api.call = fake_call
    api.poll = fake_poll
    stt.transcribe_attachment = fake_transcribe
    d.agent.turn = fake_turn
    install_fake_voice()
    # Замок «один демон на машину» тесту мешает: боевой демон обычно запущен,
    # и прогон падал бы на старте. Тест не слушает ВК по-настоящему.
    d.acquire_lock = lambda: None
    # Фоновая работа в простое в этом тесте выключена: она правит настоящую
    # очередь задач в memory/. Её проверяет отдельный тест ядра (bl-002).
    d.C.SELF_IDLE_SECONDS = 10 ** 9
    d.STATE = d.C.DERIVED / "vk_selftest_state.json"
    d.STATE.unlink(missing_ok=True)
    d.C.ROOT = Path("/nonexistent")          # не читать настоящий .env
    os.environ.update(VK_TOKEN="t", VK_GROUP_ID="1", VK_OWNER_ID="42")

    # bl-033: демон на фикстурах пишет message_in/message_out/error через
    # M.log в C.LOG_DIR — подменяем каталог на время всего прогона (не только
    # цикла демона: check_night_mute тоже пишет ключ vk_muted_night) и сверяем
    # живой файл дня побайтово до и после — правило samotest-ne-pishet-v-zhivuyu-pamyat.
    from jarvis.core import memory as M
    live_log = C.LOG_DIR / f"{M.today().isoformat()}.jsonl"
    live_before = live_log.read_bytes() if live_log.exists() else None
    tmp_log = Path(tempfile.mkdtemp(prefix="vk_selftest_log_"))
    saved_log_dir = C.LOG_DIR
    C.LOG_DIR = tmp_log
    try:
        started = time.monotonic()
        # bl-101: вывод цикла ловим в буфер и разбираем. Демон о несостоявшейся
        # доставке ТОЛЬКО печатает «✗ ответ не ушёл» — раньше эта строка уходила
        # в общий вывод как есть: прогон оставался зелёным, а в нём стояло ✗,
        # неотличимое от настоящей поломки канала. Теперь число таких строк —
        # проверяемое число (ровно одно, фикстурное), а сама она в выводе
        # помечена как ожидаемая.
        buf = io.StringIO()
        try:
            with contextlib.redirect_stdout(buf):
                try:
                    asyncio.run(d.run())
                except KeyboardInterrupt:
                    pass
        finally:
            cycle_out = buf.getvalue()
        spent = time.monotonic() - started

        alarms = [l for l in cycle_out.splitlines()
                  if "✗" in l or "⚠" in l]
        unexpected = [l for l in alarms
                      if not any(all(w in l for w in pat) for pat in EXPECTED_ALARMS)]
        for line in cycle_out.splitlines():
            if line in unexpected:
                line = "  [НЕОЖИДАННО]" + line
            elif line in alarms:
                line = "  [ожидаемо по фикстуре]" + line
            print(line)

        assert not unexpected, (
            "цикл пожаловался там, где фикстура этого не задумывала: " + str(unexpected))
        for pat in EXPECTED_ALARMS:
            hits = [l for l in alarms if all(w in l for w in pat)]
            assert len(hits) == 1, (
                f"жалоба {pat} должна прозвучать ровно раз, а прозвучала "
                f"{len(hits)}: {hits}")
        assert any("сбой отправки" in p for p in PROMPTS), "фикстура отказа не отыграла"

        print("--- промптов к агенту:", len(PROMPTS))
        for p in PROMPTS:
            print("   <", repr(p[:80]))
        print("--- отправлено текстом:", len(SENT), [len(s) for s in SENT])
        print("--- отправлено голосом:", len(VOICED), [s[:30] for s in VOICED])
        print("--- состояние:", d.STATE.read_text(encoding="utf-8"))
        print(f"--- прогон занял {spent:.1f} с")

        assert len(PROMPTS) == 9, f"ожидал 9 ходов, получил {len(PROMPTS)}"
        assert "поздняя расшифровка" in PROMPTS[1], "голосовое не подхватилось"
        assert "старый формат" in PROMPTS[3], "событие старого формата потеряно"
        # ВК сдался — работает своё распознавание, и Илья видит, чья расшифровка.
        assert "своя расшифровка" in PROMPTS[4], "не подхватил своё распознавание"
        assert "расшифровал сам" in PROMPTS[4], "источник расшифровки не назван"
        assert len(RECOGNIZED) == 2, f"своё распознавание звали не там: {RECOGNIZED}"
        # А когда и ссылки на запись нет — честное «не смог», а не пустой ход.
        assert "расшифровать не удалось" in PROMPTS[8], "нераспознанное выдано за текст"
        assert all(len(s) <= d.MAX_LEN for s in SENT), "кусок длиннее лимита ВК"
        assert sum(len(s) for s in SENT if set(s) == {"я"}) >= 9000, "длинный ответ потерян"
        assert len([s for s in SENT if set(s) == {"я"}]) >= 3, "длинный ответ не нарезался"
        assert not any("СБОЙ" in s for s in SENT), "отказ ВК не должен считаться отправкой"

        # Доставка: всё уходит текстом (30.08.2026 Илья отменил голосовые),
        # метка [текстом] снимается, сломанная озвучка не съедает ответ.
        # Фикстуры цикла меток [голосом] не ставят — значит голоса быть не должно
        # ни одного. Вернулось умолчание на голос — эта строка падает первой.
        assert not VOICED, f"без просьбы Ильи голосовых быть не должно: {VOICED}"
        assert any("глазами" in s for s in SENT), "метка [текстом] проигнорирована"
        assert not any("[текстом]" in s for s in SENT + VOICED), "метка попала в чат"
        assert any("не озвучится" in s for s in SENT), "сбой озвучки съел ответ"

        st = __import__("json").loads(d.STATE.read_text(encoding="utf-8"))
        assert st["seen"]["42"] == 12 and st["seen"]["999"] == 2, st
        # На failed-расшифровке демон не должен ждать ВК: раньше это были ~30 с
        # молчания в чате на каждое нераспознанное голосовое.
        assert spent < 40, f"цикл где-то спит лишнее: {spent:.0f} с"
        print("OK")

        check_night_mute()
        check_prune()
    finally:
        C.LOG_DIR = saved_log_dir
        shutil.rmtree(tmp_log, ignore_errors=True)

    live_after = live_log.read_bytes() if live_log.exists() else None
    check_live_log_untouched(live_before, live_after)
    print("--- ЖИВОЙ ЛОГ: не тронут")


def main() -> int:
    """Прогон целиком идёт под защитой: живой лог тестом не пишется.

    Через `./jarvis --selftest` флаг поднимает run.py, но модуль часто гоняют
    отдельно (процедура perezapusk-demona-vk) — тогда флага нет, и до этой
    правки check_images клала строку в настоящий memory/log (bl-061).
    """
    from jarvis.core import memory as M
    code = 0
    with M.selftest_guard(FIXTURE_MARKERS) as guard:
        try:
            _main()
        except AssertionError as e:
            print(f"ПЛОХО: {e}")
            code = 1
    if not guard["ok"]:
        print(f"ПЛОХО: фикстуры попали в живой лог: {guard['leaked']}")
        code = 1
    return code


if __name__ == "__main__":
    raise SystemExit(main())
