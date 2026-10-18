#!/usr/bin/env python3
"""Канал связи через сообщество ВК.

  ./jarvis --vk        (или python -m jarvis.modules.vk.daemon)

Долгоживущий процесс: слушает Bots Long Poll, на каждое сообщение от Ильи
дёргает тот же agent.turn() и отправляет ответ. Память общая с терминалом —
это один и тот же агент, просто вход другой.

Окружение (файл .env в корне агента, в гит не попадает):
  VK_TOKEN     — ключ доступа сообщества, права: messages
  VK_GROUP_ID  — числовой id сообщества
  VK_OWNER_ID  — числовой id Ильи; отвечаем только ему

Устройство, коротко:
  · ts (позиция в очереди событий) лежит в derived/ — производное, потеря
    не страшна: при следующем старте берём текущий и едем дальше;
  · сообщения обрабатываются строго по одному: ход занимает до минуты,
    поэтому всё это время в чат идёт «печатает»;
  · чужие сообщения игнорируются молча, но пишутся в лог;
  · сеть падает постоянно — все ошибки ловятся, процесс не умирает.
"""

import asyncio
import json
import os
import random
import re
import shutil
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from jarvis.core import (agent, config as C, heartbeat as HB, interrupt as INT,  # noqa: E402
                         jobs, memory as M, night, questions as Q)
from jarvis.modules.agents import schedule as roles_schedule  # noqa: E402
from jarvis.modules.agents import watchdog as roles_watchdog  # noqa: E402
from jarvis.modules.outbox.wave2 import planner as outbox_planner  # noqa: E402
from jarvis.modules.vk import api, plaintext  # noqa: E402

STATE = C.DERIVED / "vk.json"   # производное: позиция в очереди + последнее id
MAX_LEN = 4000                  # ВК режет длинное сообщение, режем сами
TYPING_EVERY = 8                # индикатор в ВК живёт 10 с, обновляем раньше
HEARTBEAT_EVERY = 300           # как часто демон говорит, что жив (секунды)
STALE_EVERY = 300               # как часто демон сверяет свой код с диском
INBOX_EVERY = 4                 # как часто заглядывать в чат ВО ВРЕМЯ хода

# Замерено на Piper/dmitri 22.08.2026: ~20 символов речи в секунду,
# то есть минута звучания ≈ 1200 знаков. Порог с запасом — короткое уходит
# голосом, длинное текстом. Правило Ильи: голосом то, что укладывается
# в минуту; длиннее — читать глазами удобнее, чем слушать.
VOICE_LIMIT = 1000

# Метка в первой строке ответа — способ переопределить решение по длине,
# когда Илья попросил явно («напиши текстом», «пришли голосовым»).
VOICE_MARKS = ("[голосом]", "[voice]")
TEXT_MARKS = ("[текстом]", "[text]")


# --------------------------------------------------------------------------
# Окружение
# --------------------------------------------------------------------------

def load_env() -> tuple[str, int, int]:
    """Токен и id. Берутся из окружения либо из .env в корне агента."""
    envfile = C.ROOT / ".env"
    if envfile.exists():
        for line in envfile.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

    token = os.environ.get("VK_TOKEN", "").strip()
    group = os.environ.get("VK_GROUP_ID", "").strip().lstrip("-")
    owner = os.environ.get("VK_OWNER_ID", "0").strip()

    if not token or not group.isdigit():
        raise SystemExit(
            "Нет доступов. Создай .env в корне агента:\n"
            "  VK_TOKEN=vk1.a...\n  VK_GROUP_ID=123456789\n  VK_OWNER_ID=12345\n"
            "Как получить — в modules/vk/README.md")

    return token, int(group), int(owner) if owner.lstrip("-").isdigit() else 0


# --------------------------------------------------------------------------
# Состояние: производное, сносится безболезненно
# --------------------------------------------------------------------------

LOCK = C.DERIVED / "vk.lock"
_lock_fh = None                 # держим открытым всю жизнь процесса


def acquire_lock() -> None:
    """Один демон на машину. Второй не запускается.

    23.08.2026: несколько запущенных копий разбирали одно событие каждая,
    и Илья получал один ответ по шесть раз. Они же затирали друг другу ts
    в общем файле состояния, из-за чего очередь стояла на месте.
    Замок дешевле, чем разбираться в этом второй раз.
    """
    global _lock_fh
    import fcntl

    C.DERIVED.mkdir(parents=True, exist_ok=True)
    fh = open(LOCK, "a+", encoding="utf-8")
    try:
        fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        fh.seek(0)
        pid = fh.read().strip() or "?"
        raise SystemExit(
            f"Демон ВК уже запущен (pid {pid}). Второй не нужен: копии разбирают\n"
            f"одни и те же сообщения, и ответы задваиваются.\n"
            f"  посмотреть:  pgrep -af 'run.py --vk'\n"
            f"  остановить:  kill {pid}")
    fh.seek(0)
    fh.truncate()
    fh.write(str(os.getpid()))
    fh.flush()
    _lock_fh = fh


def read_state() -> dict:
    try:
        return json.loads(STATE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def write_state(**kw) -> None:
    C.DERIVED.mkdir(parents=True, exist_ok=True)
    state = read_state()
    state.update(kw)
    STATE.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")


# --------------------------------------------------------------------------
# Разбор входящего
# --------------------------------------------------------------------------

VK_TRANSCRIPT_TRIES = 3         # ~9 секунд ожидания ВК, дальше распознаю сам


async def voice_text(token: str, msg: dict) -> tuple[str | None, str]:
    """Текст голосового и то, кем он расшифрован: "vk", "local" или "".

    Расшифровка ВК асинхронная: в момент прихода сообщения её обычно ещё нет,
    поэтому сообщение перечитывается несколько раз. Но ждать её долго незачем —
    она бесплатна, а не обязательна: приходит и `failed`, и вечный
    `in_progress`. Поэтому короткое ожидание, а потом своё распознавание
    (modules/vk/stt.py, faster-whisper на CPU): молчать в ответ на голосовое
    дороже, чем потратить полминуты процессора.
    """
    def voice(m: dict) -> dict | None:
        for a in m.get("attachments", []):
            if a.get("type") == "audio_message":
                return a
        return None

    att = voice(msg)
    if att is None:
        return None, ""

    body = att.get("audio_message") or {}
    for attempt in range(VK_TRANSCRIPT_TRIES):
        if body.get("transcript_state") == "done" or body.get("transcript"):
            text = (body.get("transcript") or "").strip()
            if text:
                return text, "vk"
            break
        if body.get("transcript_state") == "failed" or body.get("transcript_error"):
            break                           # ВК сдался — ждать нечего
        await asyncio.sleep(2 + attempt)
        try:
            fresh = await asyncio.to_thread(
                api.call, "messages.getById", token, message_ids=msg["id"],
                group_id=None)
            items = fresh.get("items") or []
            got = voice(items[0]) if items else None
            if got is not None:
                att, body = got, got.get("audio_message") or {}
        except Exception as e:
            M.log("error", {"vk_transcript": str(e)})
            break

    try:
        from . import stt
        mine = await asyncio.to_thread(
            stt.transcribe_attachment, att, f"{msg.get('id', 'x')}_voice")
    except Exception as e:
        M.log("error", {"stt_call": str(e)})
        return None, ""
    return (mine, "local") if mine else (None, "")


IMAGE_EXT = {"jpg", "jpeg", "png", "webp", "gif", "bmp", "heic"}
DOC_EXT = {"pdf"}               # не картинка, но читается: см. to_prompt
MEDIA_KEEP_DAYS = 30            # старые файлы чистятся: derived не архив


def photo_url(att: dict) -> str | None:
    """Ссылка на самый крупный размер фотографии."""
    sizes = (att.get("photo") or {}).get("sizes") or []
    if not sizes:
        return None
    best = max(sizes, key=lambda s: (s.get("width", 0) * s.get("height", 0)))
    return best.get("url")


def media_target(att: dict) -> tuple[str, str, str] | None:
    """(ссылка, расширение, вид) для вложения, которое я умею открыть.

    Вид — "image" (смотрю инструментом Read) или "pdf" (текст вынимается
    pdftotext, страницы при нужде смотрю тем же Read). Остальное — None.
    """
    t = att.get("type")
    if t == "photo":
        url = photo_url(att)
        return (url, "jpg", "image") if url else None
    if t == "doc":
        d = att.get("doc") or {}
        ext = (d.get("ext") or "").lower()
        if not d.get("url"):
            return None
        if ext in IMAGE_EXT:
            return d["url"], ext, "image"
        if ext in DOC_EXT:
            return d["url"], ext, "pdf"
        return None
    if t == "graffiti":
        url = (att.get("graffiti") or {}).get("url")
        return (url, "png", "image") if url else None
    return None


def att_label(att: dict) -> str:
    """Как назвать вложение, которое я не открыл: тип и, для файла, имя."""
    t = att.get("type") or "?"
    if t == "doc":
        d = att.get("doc") or {}
        name = d.get("title") or f"без имени.{d.get('ext') or '?'}"
        return f"файл «{name}»"
    return t


def prune_media(root: Path) -> None:
    """Папки старше MEDIA_KEEP_DAYS сносим: оригиналы остаются в ВК."""
    edge = time.time() - MEDIA_KEEP_DAYS * 86400
    for day in root.glob("20*-*-*"):
        try:
            if day.is_dir() and day.stat().st_mtime < edge:
                for f in day.iterdir():
                    f.unlink()
                day.rmdir()
        except OSError:
            pass


# --- Уборка производной зоны ------------------------------------------------
# Слепки системного промпта копятся молча: к 26.08 их лежало восемь, и вычищены
# они были руками (ревизия bl-051). Ручная уборка однажды не повторится.
#
# Убираются РОВНО ДВЕ цели, которых нет ни у одного другого уборщика:
# слепки промпта и старые дни лога самотеста. derived/scratch сюда не входит
# намеренно: его с 26.08 убирает ночной цикл (core/scratch.py, bl-085) — там
# возраст считается по самому свежему файлу ВНУТРИ каталога, а не по mtime
# самой папки, и есть защита от ссылки, уводящей удаление из зоны. Второй
# уборщик той же зоны с другим сроком — это не «надёжнее», а два кода,
# удаляющие одно и то же по разным правилам.
PROMPT_KEEP_DAYS = 1        # слепок промпта: живому процессу он ещё нужен
SELFTEST_LOG_KEEP_DAYS = 3  # лог самотеста: не «целиком», причина ниже
PRUNE_LIMIT = 200           # больше кандидатов — значит смотрю не туда: не удалять
PRUNE_EVERY = 3600          # такт уборки в главном цикле


def _pid_alive(pid: int) -> bool:
    """Жив ли процесс: слепок работающего хода сносить нельзя ни в каком возрасте."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return True
    return True


def prune_derived(root: Path | None = None, now: float | None = None) -> dict:
    """Прибрать производную зону. Возвращает счётчики снесённого.

    Две цели, каждая со своим сроком:
      слепки промпта — старше суток И чей процесс уже умер;
      лог самотеста  — файлы дней старше трёх суток.

    Почему лог самотеста не «целиком», как просила задача bl-098: 26.08 туда
    утекли настоящие строки живого лога (утечка JARVIS_SELFTEST, факт
    log-uehal-v-derived). 27.08 (bl-136) остаток — 122 строки — возвращён в
    memory/log/2026-08-26.jsonl, уникального в зоне больше нет. Запас в трое
    суток оставлен намеренно: если утечка повторится, у меня будет окно
    заметить её и перенести строки, а не найти пустоту.

    derived/scratch здесь не трогается: его убирает core/scratch.py ночью.
    """
    root = C.DERIVED if root is None else root
    now = time.time() if now is None else now
    day = 86400.0
    victims: list[tuple[str, Path]] = []

    def old(p: Path, days: float) -> bool:
        try:
            return p.stat().st_mtime < now - days * day
        except OSError:
            return False

    for f in root.glob("system_prompt.*.txt"):
        pid = f.name.split(".")[1]
        if pid.isdigit() and _pid_alive(int(pid)):
            continue
        if old(f, PROMPT_KEEP_DAYS):
            victims.append(("слепки", f))

    slog = root / "selftest" / "log"
    if slog.is_dir():
        for p in slog.iterdir():
            if p.is_file() and old(p, SELFTEST_LOG_KEEP_DAYS):
                victims.append(("самотест", p))

    if len(victims) > PRUNE_LIMIT:
        M.log("error", {"derived_prune_stop": len(victims)})
        return {"отказ": len(victims)}

    out: dict[str, int] = {}
    for kind, p in victims:
        try:
            if p.is_dir():
                shutil.rmtree(p)
            else:
                p.unlink()
        except OSError as e:
            M.log("error", {"derived_prune": f"{p.name}: {e}"})
            continue
        out[kind] = out.get(kind, 0) + 1
    if out:
        M.log("self", {"derived_prune": out})
    return out


async def save_media(msg: dict) -> tuple[list[tuple[str, str]], set[int]]:
    """Вложения, которые я умею открыть, — на диск.

    Возвращает [(путь, вид)] и индексы сохранённых. Смотреть файл я умею
    только с диска (инструментом Read), ссылку ВК открыть не могу. Поэтому
    качаем здесь, а в промпт кладём пути.
    """
    root = C.DERIVED / "vk_media"
    paths: list[tuple[str, str]] = []
    done: set[int] = set()

    for n, att in enumerate(msg.get("attachments", [])):
        target = media_target(att)
        if target is None:
            continue
        url, ext, kind = target
        try:
            data = await asyncio.to_thread(api.download, url)
        except Exception as e:
            M.log("error", {"vk_image": str(e)})
            continue
        folder = root / time.strftime("%Y-%m-%d")
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"{msg.get('id', 'x')}_{n}.{ext}"
        try:
            path.write_bytes(data)
        except OSError as e:
            M.log("error", {"vk_image_write": str(e)})
            continue
        paths.append((str(path), kind))
        done.add(n)

    if paths:
        M.log("self", {"vk_images": len(paths)})
        prune_media(root)
    return paths, done


async def to_prompt(token: str, msg: dict) -> str:
    """Сообщение ВК → текст для агента."""
    text = (msg.get("text") or "").strip()
    parts = [text] if text else []

    # Задания подагентов, закончившиеся между ходами. Это единственный способ
    # их принять: ход, который задание поставил, давно умер, а следующий ход —
    # вот этот. Блок идёт сразу за словами Ильи, а не в хвосте промпта, где
    # его легко проглядеть.
    try:
        done = jobs.take_inbox()
    except Exception as e:                                      # noqa: BLE001
        M.log("error", {"jobs_inbox": f"{type(e).__name__}: {e}"})
        done = ""
    if done:
        parts.append(done)

    spoken, source = await voice_text(token, msg)
    if spoken and source == "local":
        parts.append(f"[голосовое, расшифровал сам: имена и числа мог "
                     f"расслышать неточно] {spoken}")
    elif spoken:
        parts.append(f"[голосовое, расшифровка ВК] {spoken}")
    elif any(a.get("type") == "audio_message" for a in msg.get("attachments", [])):
        parts.append("[голосовое, расшифровать не удалось — скажи об этом Илье]")

    media, saved = await save_media(msg)
    shots = [p for p, kind in media if kind == "image"]
    docs = [p for p, kind in media if kind == "pdf"]
    if shots:
        listing = "\n".join(shots)
        parts.append(
            f"[картинок во вложении: {len(shots)}. Скачаны на диск, смотреть "
            f"инструментом Read по путям (ссылку ВК открыть нельзя):\n{listing}]")
    if docs:
        listing = "\n".join(docs)
        parts.append(
            f"[pdf во вложении: {len(docs)}. Скачаны на диск. Текст вынимать "
            f"`pdftotext -layout <путь> -` (точнее, чем глазами), страницы при "
            f"нужде смотреть инструментом Read:\n{listing}]")

    other = sorted({att_label(a) for n, a in enumerate(msg.get("attachments", []))
                    if a.get("type") != "audio_message" and n not in saved})
    if other:
        parts.append(f"[вложения, которые я разобрать не умею: {', '.join(other)}]")

    if not parts:
        parts.append("[пустое сообщение]")

    parts.append(
        "[канал: ВК. Ответ уходит текстом — голосовых Илья просил не слать "
        "(правило golos-ili-tekst). Озвучить можно только когда он сам "
        "попросил в этом разговоре: первой строкой [голосом]. "
        "Работа дольше минуты — слать статус в чат: "
        "`.venv/bin/python -m jarvis.modules.vk.notify \"что делаю\"` "
        "из каталога выше корня агента.]")

    return "\n".join(parts) or "[пустое сообщение]"


# --------------------------------------------------------------------------
# Исходящее
# --------------------------------------------------------------------------

def chunks(text: str, size: int = MAX_LEN):
    """Режем по абзацам, а не по символам: так шов не попадает в середину слова."""
    while len(text) > size:
        cut = text.rfind("\n\n", 0, size)
        if cut < size // 2:
            cut = text.rfind("\n", 0, size)
        if cut < size // 2:
            cut = text.rfind(" ", 0, size)
        if cut <= 0:
            cut = size
        yield text[:cut].rstrip()
        text = text[cut:].lstrip()
    if text:
        yield text


# Ошибки отправки, которые значат не «сеть моргнула», а «так не заработает».
# Молчать о них нельзя: в чате это выглядит как «агент завис».
SEND_HINTS = {
    7: "нет прав на сообщения — проверь права ключа доступа сообщества",
    9: "флуд-контроль ВК: слишком часто отправляем",
    901: "нельзя писать первым: Илья должен сам написать сообществу "
         "и не запрещать сообщения от него",
    914: "сообщение слишком длинное даже после нарезки",
}


async def send(token: str, group_id: int, peer_id: int, text: str) -> None:
    # Единая точка для всего исходящего текста: ВК не знает markdown, и
    # звёздочки с таблицами в нём превращаются в кашу. Чистка формы — здесь,
    # чтобы её нельзя было забыть; краткость и отсутствие ссылок — дисциплина.
    try:
        text = plaintext.render(text)
    except Exception as e:                      # форма важна, но текст важнее
        M.log("error", {"vk_plaintext": f"{type(e).__name__}: {e}"})
    parts = list(chunks(text))
    for n, part in enumerate(parts, 1):
        try:
            await asyncio.to_thread(
                api.call, "messages.send", token, peer_id=peer_id, message=part,
                random_id=random.getrandbits(31), group_id=group_id)
        except Exception as e:
            code = getattr(e, "code", 0)
            hint = SEND_HINTS.get(code, "")
            M.log("error", {"vk_send": str(e), "chunk": f"{n}/{len(parts)}",
                            "hint": hint, "lost": sum(len(p) for p in parts[n - 1:])})
            print(f"  ✗ ответ не ушёл ({n}/{len(parts)}): {e}"
                  + (f"\n    {hint}" if hint else ""))
            return


def pick_mode(reply: str) -> tuple[str, str]:
    """Как доставлять ответ: 'voice' или 'text'. Возвращает (режим, текст).

    Умолчание — ТЕКСТ, всегда. Илья 30.08.2026: «пиши всегда текстом, без
    голосовых» (правило golos-ili-tekst). До этого дня здесь решала длина:
    короткое уходило голосом. Порог отменён — не потому, что синтез плох, а
    потому что читать ему оказалось удобнее во всех случаях.

    Голос остался ровно одним путём: явная метка [голосом] первой строкой,
    которую я ставлю, когда он сам попросил озвучить конкретный ответ.
    Проверка на «после чистки речи не осталось» тоже сохранена — она страхует
    этот путь: озвучивать один путь к файлу бессмысленно даже по просьбе.
    """
    text = reply.strip()
    head = text.split("\n", 1)[0].strip().lower()
    for mark in TEXT_MARKS:
        if head == mark:
            return "text", text[len(head):].strip() or text
    for mark in VOICE_MARKS:
        if head == mark:
            body = text[len(head):].strip() or text
            if len(body) > VOICE_LIMIT:
                return "text", body
            try:
                from ..voice import speakable
                spoken, _ = speakable.prepare(body)
            except Exception:
                return "text", body
            if len(spoken) < 30 or len(spoken) < len(body) * 0.4:
                return "text", body
            return "voice", body

    return "text", text


async def deliver(token: str, group_id: int, peer_id: int, reply: str) -> None:
    """Отправить ответ тем способом, который уместнее.

    Голос — не украшение: Илья бывает за рулём. Но озвучка ломается легче
    текста (нет модели, нет места на диске, ВК не принял документ), поэтому
    любой сбой откатывается в текст. Потерять ответ хуже, чем прислать его
    не тем способом.

    Перед своим делом — попытка отдать ночную очередь (bl-034, notify.py):
    это первая точка, где демон реально что-то шлёт в ВК, и естественное
    место для «первый ход после 08:00 отдаёт накопленное». Ночью или на
    пустой очереди — no-op.
    """
    try:
        from . import notify as ntf
        await asyncio.to_thread(ntf.flush_queue)
    except Exception as e:                                     # noqa: BLE001
        M.log("error", {"vk_night_flush": f"{type(e).__name__}: {e}"})
    mode, text = pick_mode(reply)
    if mode == "voice":
        try:
            from ..voice import say, speakable
            spoken, tail = speakable.prepare(text)
            ogg = await asyncio.to_thread(say.synthesize, spoken)
            await asyncio.to_thread(say.send_voice, token, group_id, peer_id, ogg)
            M.log("self", {"vk_reply": "voice", "chars": len(spoken),
                           "tail": len(tail)})
            # Пути, ссылки, id и код Илья просил не зачитывать, а досылать
            # текстом следом: в речи от них остаётся заглушка, читать их
            # всё равно нужно глазами.
            if tail:
                await send(token, group_id, peer_id, tail)
            return
        except Exception as e:
            M.log("error", {"vk_voice": f"{type(e).__name__}: {e}",
                            "fallback": "text"})
            print(f"  ✗ озвучка не вышла ({type(e).__name__}: {e}) — шлю текстом")
    await send(token, group_id, peer_id, text)


YES = {"да", "ага", "ок", "окей", "давай", "разрешаю", "можно", "+", "y", "yes", "ok"}


def _ask_first_word(answer: str) -> str:
    """Первое слово ответа, без знаков препинания по краям.

    26.08 (job-049): `answer in YES` требовал точного совпадения ВСЕЙ фразы
    целиком. Илья ответил «да, в дальнейшем не справшивая а делай» — явное
    согласие с хвостом инструкции — и вся фраза не была равна ни одному
    слову из YES, поэтому `allowed` стало False, хотя человек сказал «да».
    Смотрим только на первое слово: «да, ...» и «да!» — согласие, «нет, ...»
    и «не делай» по-прежнему отказ, потому что «нет»/«не» в YES нет и не
    будет. Полностью противоречивую фразу («да, но не надо») это не решает
    — такой текст неоднозначен по своей природе, а не из-за разбора.
    """
    if not answer:
        return ""
    return re.split(r"[\s,.\-!?;:]+", answer, maxsplit=1)[0]


def make_ask(token: str, group_id: int, owner_id: int, mark_seen):
    """Способ спросить разрешение не через терминал, а в чате.

    Это то, чего у демона не было: ход из ВК шёл unattended, и любое действие
    средней цены отклонялось само собой — «спросить некого». Теперь вопрос
    уходит Илье сообщением, а ответ вычитывается из истории диалога, минуя
    long-poll: очередь событий в это время не разбирается, и ответ «да» иначе
    достался бы основному циклу как обычное сообщение.

    Молчание — это «нет». Ждать дольше таймаута нельзя: ход занимает слот,
    а Илья мог просто уйти.
    """
    async def ask(tool_name: str, preview: str):
        # Проверочный вызов живой проверки предохранителей (perezapusk-demona-vk,
        # шаг 6): решение шлюза нужно, ответ Ильи — нет. Раньше такой вызов и в
        # чат стучался, и мусорил в копилке (bl-115). Проверка идёт ПЕРЕД
        # глухим временем: в живое время цена ошибки выше — сообщение Илье.
        if Q.is_probe(preview):
            M.log("self", {"ask_probe": tool_name, "preview": preview[:200]})
            return Q.PROBE_REPLY
        # Глухое время — вопрос не задаётся вслепую. Ночью канал молчит, в
        # фоновом ходе Илья не у экрана: 25.08 шесть таких вопросов из шести
        # ушли в таймаут, 18 минут ожидания впустую (bl-083). Вопрос падает в
        # копилку, ход сразу узнаёт об этом и работает дальше без действия.
        deaf = Q.deaf_reason()
        if deaf:
            Q.add(tool_name, preview, deaf)
            M.log("self", {"ask_deferred": tool_name, "why": deaf,
                           "preview": preview[:200]})
            return (f"Вопрос отложен в копилку ({deaf}): memory/questions.md. "
                    f"Не жди ответа и не обходи это другим способом — "
                    f"продолжай работу без этого действия.")
        # Пауза снимается при любом исходе: иначе после первого же вопроса
        # наблюдатель за чатом онемел бы до конца хода.
        try:
            return await _ask(tool_name, preview)
        finally:
            INT.pause(False)

    async def _ask(tool_name: str, preview: str) -> bool:
        peer_id = owner_id
        text = (f"⚠ Нужно твоё решение.\n{tool_name}: {preview[:600]}\n\n"
                f"«да» — делаю, что угодно другое — не делаю. "
                f"Жду {C.ASK_TIMEOUT // 60} мин, молчание считаю отказом.")
        # Пока ждём ответа, наблюдатель за чатом молчит: иначе «да» уйдёт
        # в прерывание хода, а тот, кто его ждёт, не дождётся.
        INT.pause(True)
        try:
            await asyncio.to_thread(api.call, "messages.send", token, peer_id=peer_id,
                                    message=text, random_id=random.getrandbits(31),
                                    group_id=group_id)
        except Exception as e:
            M.log("error", {"ask_send": str(e)})
            return False

        M.log("self", {"ask_sent": tool_name, "preview": preview[:200]})
        deadline = time.monotonic() + C.ASK_TIMEOUT
        while time.monotonic() < deadline:
            await asyncio.sleep(3)
            try:
                hist = await asyncio.to_thread(
                    api.call, "messages.getHistory", token, peer_id=peer_id,
                    count=5, group_id=group_id)
            except Exception as e:
                M.log("error", {"ask_poll": str(e)})
                continue
            for m in sorted(hist.get("items", []), key=lambda x: x.get("id", 0)):
                if m.get("from_id") != owner_id:
                    continue
                mid = m.get("id") or 0
                if not mark_seen(peer_id, mid):
                    continue                 # уже видели: это не ответ на вопрос
                answer = (m.get("text") or "").strip().lower()
                allowed = _ask_first_word(answer) in YES
                M.log("self", {"ask_answer": answer[:100], "allowed": allowed})
                return allowed

        try:
            await asyncio.to_thread(api.call, "messages.send", token, peer_id=peer_id,
                                    message="Не дождался ответа — не делаю.",
                                    random_id=random.getrandbits(31), group_id=group_id)
        except Exception:
            pass
        M.log("self", {"ask_timeout": tool_name})
        return False

    return ask


async def typing(token: str, group_id: int, peer_id: int, stop: asyncio.Event) -> None:
    """Держит «печатает», пока агент думает. Ход бывает долгим, и молчащий
    чат неотличим от сломанного."""
    while not stop.is_set():
        try:
            await asyncio.to_thread(api.call, "messages.setActivity", token,
                                    peer_id=peer_id, type="typing", group_id=group_id)
        except Exception:
            pass
        try:
            await asyncio.wait_for(stop.wait(), timeout=TYPING_EVERY)
        except asyncio.TimeoutError:
            pass


# --------------------------------------------------------------------------
# Обработка одного сообщения
# --------------------------------------------------------------------------

async def watch_inbox(token: str, group_id: int, owner_id: int, peer_id: int,
                      mark_seen, stop: asyncio.Event) -> None:
    """Заглядывать в чат, пока идёт ход, и вкладывать новое прямо в работу.

    Long-poll в это время не опрашивается (его разбирает основной цикл), но
    историю диалога читать можно — тем же способом, что и ask. Сообщение
    помечается виденным сразу, чтобы основной цикл потом не ответил на него
    второй раз.

    Илье уходит короткое «принял» немедленно — раньше, чем агент до него
    доберётся: важно, чтобы он видел, что услышан, и не переспрашивал.
    """
    while not stop.is_set():
        try:
            await asyncio.wait_for(stop.wait(), timeout=INBOX_EVERY)
            return
        except asyncio.TimeoutError:
            pass
        if INT.paused():                     # ответа ждёт ask — не мешаем
            continue
        try:
            hist = await asyncio.to_thread(
                api.call, "messages.getHistory", token, peer_id=peer_id,
                count=5, group_id=group_id)
        except Exception as e:
            M.log("error", {"watch_inbox": str(e)})
            continue
        if INT.paused():                     # пауза встала, пока ходили в сеть
            continue
        for m in sorted(hist.get("items", []), key=lambda x: x.get("id", 0)):
            if m.get("from_id") != owner_id:
                continue
            mid = m.get("id") or 0
            if not mark_seen(peer_id, mid):
                continue                     # уже видели: это не новое
            text = (m.get("text") or "").strip()
            if not text and m.get("attachments"):
                text = "[сообщение с вложением — посмотри его после хода]"
            if not INT.push(text, m):
                continue
            M.log("message_in", text[:500], actor="ilya")
            M.log("self", {"interrupt_caught": text[:200]})
            print(f"  ↯ сообщение посреди хода: {text[:80]}")
            # Эха «Принял: …» здесь нет намеренно (Илья, 25.08.2026): пересказ
            # его же слов — не ответ, а шум. Настоящий ответ придёт из хода:
            # на вопрос сразу, на поручение — по мере работы.


async def handle(token: str, group_id: int, owner_id: int, msg: dict, ask=None,
                 mark_seen=None) -> list[dict]:
    """Один ход по сообщению. Возвращает сообщения, до которых ход не дошёл."""
    from_id = msg.get("from_id")
    peer_id = msg.get("peer_id", from_id)

    if owner_id and from_id != owner_id:
        M.log("message_in", (msg.get("text") or "")[:500],
              actor=f"vk:{from_id}")
        M.log("self", {"vk_ignored": from_id, "why": "не Илья"})
        return []

    if not owner_id:
        # Первый запуск: id Ильи ещё не известен. Не отвечаем никому,
        # но показываем, что вписать в .env.
        print(f"  сообщение от id {from_id} — впиши VK_OWNER_ID={from_id} в .env")
        M.log("self", {"vk_owner_unknown": from_id})
        return []

    # Индикатор поднимается до разбора, а не после: расшифровка голосового
    # ждёт ВК до полуминуты, и всё это время чат был бы молчалив.
    stop = asyncio.Event()
    indicator = asyncio.create_task(typing(token, group_id, peer_id, stop))
    watcher = None
    if mark_seen is not None:
        INT.arm()
        watcher = asyncio.create_task(
            watch_inbox(token, group_id, owner_id, peer_id, mark_seen, stop))
    try:
        prompt = await to_prompt(token, msg)
        # unattended=True — потому что терминала у демона нет. Но спросить Илью
        # теперь есть чем: ask пишет вопрос в этот же чат и ждёт ответа
        # (core.agent.make_guard). Без ask любое действие средней цены
        # отклонялось само собой — «спросить некого».
        reply = await agent.turn(prompt, actor="ilya", unattended=True, ask=ask,
                                 channel="vk")
    except Exception as e:
        M.log("error", {"vk_turn": f"{type(e).__name__}: {e}"})
        reply = f"Сломался ход: {type(e).__name__}: {e}"
    finally:
        stop.set()
        await indicator
        if watcher is not None:
            await watcher

    # Что пришло в самом конце хода, агент увидеть уже не успел: инструментов
    # больше не будет, вкладывать некуда. Такие сообщения не теряются —
    # цикл прогонит их отдельным ходом.
    leftover = INT.disarm() if mark_seen is not None else []

    await deliver(token, group_id, peer_id, reply)
    if leftover:
        M.log("self", {"interrupt_leftover": [i["text"][:120] for i in leftover]})
    return [i["msg"] for i in leftover if i.get("msg")]


# --------------------------------------------------------------------------
# Цикл
# --------------------------------------------------------------------------

_warned_old_format = False


def extract_message(upd: dict) -> dict:
    """Сообщение из события message_new.

    С версии Long Poll 5.103 оно лежит в object.message, до неё — прямо в
    object. Версию выбирает Илья в настройках сообщества, и ошибка там
    выглядела бы как «агент молчит»: событий много, сообщений ноль.
    Поэтому принимаем обе формы, но про старую предупреждаем.
    """
    global _warned_old_format
    obj = upd.get("object") or {}
    msg = obj.get("message")
    if isinstance(msg, dict):
        return msg
    if "from_id" in obj or "peer_id" in obj:
        if not _warned_old_format:
            _warned_old_format = True
            print("  ⚠ Long Poll отдаёт старый формат события. "
                  "Поставь версию 5.199 в настройках сообщества.")
            M.log("self", {"vk_longpoll_format": "old", "advice": "версия 5.199"})
        return obj
    return {}


async def get_server(token: str, group_id: int) -> dict:
    return await asyncio.to_thread(api.call, "groups.getLongPollServer", token,
                                   group_id=group_id)


async def run() -> None:
    # Демон почти всегда живёт в фоне с перенаправленным выводом, а там stdout
    # буферизуется блоками: лог оставался пустым часами и выглядел как «не
    # запустился». Строчная буферизация — чтобы `tail -f derived/vk.log` работал.
    try:
        sys.stdout.reconfigure(line_buffering=True)
    except Exception:
        pass

    acquire_lock()
    token, group_id, owner_id = load_env()

    try:
        me = await asyncio.to_thread(api.call, "groups.getById", token,
                                     group_id=group_id)
        groups = me.get("groups", []) if isinstance(me, dict) else me
        name = groups[0]["name"] if groups else f"id{group_id}"
    except Exception as e:
        raise SystemExit(f"ВК не принял токен: {e}")
    print(f"jarvis · вк «{name}» · отвечаю только id {owner_id or '(не задан)'}")
    M.log("self", {"vk_start": name, "owner": owner_id})

    # Задания подагентов пережили прошлого демона (в том числе перезагрузку
    # машины): у части процессов уже нет. Без этой уборки статус «в работе»
    # врал бы вечно, а слоты параллельности не освободились бы никогда.
    try:
        state = jobs.tick()
        if state["reaped"] or state["started"]:
            print(f"  · задания: прибрано {len(state['reaped'])}, "
                  f"запущено {len(state['started'])}, мест {jobs.capacity()}")
    except Exception as e:
        M.log("error", {"jobs_boot": f"{type(e).__name__}: {e}"})

    lp = await get_server(token, group_id)
    ts = read_state().get("ts") or lp["ts"]
    # id сообщений в ВК уникальны в рамках диалога, а не сообщества: один
    # общий счётчик отбрасывал бы сообщения Ильи после чужого с большим id.
    seen: dict[str, int] = read_state().get("seen") or {}

    def mark_seen(peer, mid: int) -> bool:
        """True — сообщение новое (и с этой секунды считается виденным)."""
        key = str(peer)
        if not mid or mid <= seen.get(key, 0):
            return False
        seen[key] = mid
        write_state(seen=seen)
        return True

    ask = make_ask(token, group_id, owner_id, mark_seen) if owner_id else None

    backoff = 1
    polls = events = handled = 0
    last_beat = time.monotonic()
    last_stale = time.monotonic()   # свой такт проверки устаревания, см. ниже
    # Уборка derived: первый заход сразу после старта (демон перезапускается
    # чаще раза в час, иначе она откладывалась бы бесконечно), дальше по такту.
    last_prune = time.monotonic() - PRUNE_EVERY

    # Работа в простое. Илья разрешил 23.08.2026: пока он молчит, агент берёт
    # задачу из memory/backlog.md и делает короткий ход. Бюджет — 20 ходов
    # в сутки, считается по логу. Ход нельзя прервать посередине, поэтому
    # фоновая работа идёт отдельной задачей: long-poll в это время не стоит,
    # события не теряются, а сообщение Ильи ждёт ровно один текущий кусок.
    last_msg_at = time.monotonic()
    self_task: asyncio.Task | None = None
    self_label = ""              # чем именно занят — чтобы сказать это Илье

    async def self_cycle_wrapped():
        try:
            reply = await agent.self_cycle(ask=ask)
            if reply:
                print(f"  · фоновый ход: {reply[:150]}")
        except Exception as e:
            M.log("error", {"self_cycle": f"{type(e).__name__}: {e}"})
            print(f"  ✗ фоновый ход сорвался: {type(e).__name__}: {e}")

    async def night_wrapped():
        """Ночной цикл — свёртка дня, недели, месяца (core/night.py).

        Живёт здесь, а не в cron: демон и так работает сутками, а cron на
        домашней машине промахивается мимо выключенного компьютера. Расписание
        с догоном — внутри night.due(), демон только даёт ему такт.
        """
        try:
            n = await night.tick()
            if n:
                print(f"  · ночной цикл: свёрток отработало {n}")
        except Exception as e:
            M.log("error", {"night": f"{type(e).__name__}: {e}"})
            print(f"  ✗ ночной цикл сорвался: {type(e).__name__}: {e}")

    async def jobs_report_wrapped():
        """Доклад о заданиях, законченных в тишине.

        Илья ничего не спрашивал, значит подложить результат в чужой промпт
        некуда — нужен свой ход. Канал self: пульс в этом ходе молчит, а
        готовый ответ уходит в ВК один раз, здесь.
        """
        try:
            prompt = jobs.take_report()
            if not prompt:
                return
            reply = await agent.turn(prompt, actor="system", unattended=True,
                                     ask=ask, channel="self")
            await deliver(token, group_id, owner_id, reply)
            HB.mark()
            print(f"  · доклад о заданиях: {reply[:120]}")
        except Exception as e:
            M.log("error", {"jobs_report": f"{type(e).__name__}: {e}"})
            print(f"  ✗ доклад о заданиях сорвался: {type(e).__name__}: {e}")

    async def settle() -> None:
        """Дождаться фонового куска, прежде чем заниматься Ильёй.

        Молчать здесь нельзя. 25.08.2026: демон перезапустился, сразу взялся
        догонять ночной цикл за прошлый день, и всё это время сообщения Ильи
        лежали в очереди без единого признака жизни — со стороны неотличимо
        от «агент сломался». Поэтому: сказать, чем занят, и не держать его
        дольше SETTLE_MAX_WAIT — человек важнее свёртки.
        """
        nonlocal self_task, self_label
        if not self_task or self_task.done():
            self_task = None
            return

        what = self_label or "фоновая работа"
        print(f"  · занят: {what} — предупреждаю Илью и доделываю")
        try:
            await send(token, group_id, owner_id,
                       f"[текстом]\nСекунду: {what}. Отвечу, как закончу — "
                       f"или прерву, если затянется.")
        except Exception as e:
            M.log("error", {"settle_notify": str(e)})

        try:
            await asyncio.wait_for(asyncio.shield(self_task), C.SETTLE_MAX_WAIT)
        except asyncio.TimeoutError:
            # Затянулось. Ночной цикл переживёт: период уже отмечен свёрнутым,
            # повтора не будет, потеряется одна свёртка. Ждущий человек — нет.
            self_task.cancel()
            M.log("self", {"settle_cancelled": what, "after_sec": C.SETTLE_MAX_WAIT})
            print(f"  ⚠ {what} прервана — Илья ждёт дольше "
                  f"{C.SETTLE_MAX_WAIT // 60} мин")
            try:
                await send(token, group_id, owner_id,
                           f"[текстом]\nПрервал: {what} затянулась. Займусь тобой.")
            except Exception:
                pass
        except Exception as e:
            M.log("error", {"settle": f"{type(e).__name__}: {e}"})
        self_task = None
        self_label = ""

    while True:
        try:
            res = await asyncio.to_thread(api.poll, lp["server"], lp["key"], ts)
            backoff = 1
        except Exception as e:
            M.log("error", {"vk_poll": str(e)})
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 60)
            try:
                lp = await get_server(token, group_id)
                ts = lp["ts"]
            except Exception:
                pass
            continue

        failed = res.get("failed")
        if failed == 1:                     # ts устарел, ВК прислал новый
            ts = res["ts"]
            write_state(ts=ts)
            continue
        if failed in (2, 3):                # ключ протух — перевыпускаем
            lp = await get_server(token, group_id)
            if failed == 3:
                ts = lp["ts"]
            continue

        ts = res.get("ts", ts)
        write_state(ts=ts)

        # Пульс. Без него живой цикл и мёртвый неотличимы: в обоих случаях
        # в логе тишина. 23.08.2026 демон полсуток опрашивал сервер вхолостую,
        # и это выглядело как «агент завис», хотя он был здоров.
        polls += 1

        # Простой — рабочее время. Запускается только когда в чате тишина,
        # бюджет не исчерпан и в очереди есть что делать.
        if self_task and self_task.done():
            self_task = None

        # Такт диспетчера подагентов: прибрать за умершими, запустить ждущих.
        # Дёшево — чтение нескольких мелких файлов, ходов не запускает.
        try:
            jobs.tick()
        except Exception as e:
            M.log("error", {"jobs_tick": f"{type(e).__name__}: {e}"})

        # Планировщик ежедневной рассылки (07:00 по городу адресата, а не
        # по Владивостоку) — modules/outbox/wave2/planner.py, по образцу
        # night.tick(): сравнивается не «сейчас 7 утра», а «какой момент уже
        # наступил и не пропущен больше чем на 3 часа». Дёшево почти всегда;
        # SMTP (блокирующий) — только когда письму реально пора уходить,
        # поэтому в отдельном потоке, чтобы не держать long-poll.
        try:
            await asyncio.to_thread(outbox_planner.tick)
        except Exception as e:
            M.log("error", {"outbox_planner_tick": f"{type(e).__name__}: {e}"})

        # Расписание постоянных ролей (modules/agents/schedule.py): критик
        # раз в час разбирает свежие изменения. Ходов здесь не запускается —
        # роль поднимается своим процессом через диспетчер заданий, поэтому
        # такт стоит чтения пары мелких файлов.
        try:
            await asyncio.to_thread(roles_schedule.tick)
        except Exception as e:
            M.log("error", {"roles_schedule_tick": f"{type(e).__name__}: {e}"})

        # Сторож постоянных ролей (modules/agents/watchdog.py, раздел 16
        # документа «Джарвис — команда и проекты»): независимо от того, что
        # думает планировщик выше, смотрит на реальный след работы роли
        # (mtime файлов в её зоне). Дольше трёх периодов без следа — роль
        # мертва, поднимается заново своим заданием. Дёшево — обход мелких
        # файлов зоны нескольких ролей, ходов не запускает без нужды.
        try:
            await asyncio.to_thread(roles_watchdog.tick)
        except Exception as e:
            M.log("error", {"roles_watchdog_tick": f"{type(e).__name__}: {e}"})

        # Живой демон против кода на диске (bl-114) — СВОИМ тактом, а не
        # прицепом к ночной свёртке. До этой правки проверка жила только
        # внутри night.tick(), а тот поднимается лишь когда night.due()
        # непуст, то есть примерно раз в сутки: правка ядра в 00:12 ждала бы
        # перезапуска до следующей ночи — ровно та беда, ради которой bl-114
        # и заводилась. Стоит проверка глоба по трём каталогам, поэтому раз
        # в STALE_EVERY секунд; постановка перезапуска идемпотентна
        # (core.restart.schedule), так что повтор такта ничего не задваивает.
        if time.monotonic() - last_stale >= STALE_EVERY:
            last_stale = time.monotonic()
            try:
                await asyncio.to_thread(night.restart_after_tick)
            except Exception as e:
                M.log("error", {"staleness_tick": f"{type(e).__name__}: {e}"})

        # Уборка производной зоны (bl-098): слепки промпта, песочницы старше
        # недели, старые дни лога самотеста. Раз в час, целями поимённо —
        # см. prune_derived. Стоит обхода трёх каталогов, ходов не запускает.
        if time.monotonic() - last_prune >= PRUNE_EVERY:
            last_prune = time.monotonic()
            try:
                await asyncio.to_thread(prune_derived)
            except Exception as e:
                M.log("error", {"derived_prune_tick": f"{type(e).__name__}: {e}"})

        # Задание закончилось, а Илья молчит — доложить самому. Ночью (23:00–08:00)
        # доклад копится до утра: правило nochnaya-tishina. Идёт разговор —
        # тоже молчим здесь: результат подложится в промпт следующего же хода.
        if self_task is None and owner_id and jobs.should_report(
                HB.quiet_now(), time.monotonic() - last_msg_at):
            print("  · есть законченные задания, Илья молчит — докладываю")
            self_label = "докладываю о законченных заданиях"
            self_task = asyncio.create_task(jobs_report_wrapped())

        # Ночной цикл идёт впереди очереди: он и есть обучение, а очередь —
        # текущая работа. Проверка дешёвая (файл состояния), запуск редкий.
        if self_task is None and night.due():
            print("  · пора сворачивать день — ночной цикл")
            self_label = "сворачиваю прошлый день в память"
            self_task = asyncio.create_task(night_wrapped())

        if (self_task is None
                and time.monotonic() - last_msg_at >= C.SELF_IDLE_SECONDS
                and M.self_turns_today() < C.SELF_TURNS_PER_DAY
                and M.backlog_top()):
            task = M.backlog_top()
            print(f"  · тишина {C.SELF_IDLE_SECONDS // 60} мин — беру своё: "
                  f"[{task['id']}] {task['what'][:80]} "
                  f"({M.self_turns_today() + 1}/{C.SELF_TURNS_PER_DAY} за сутки)")
            self_label = f"взял своё: {task['what'][:60]}"
            self_task = asyncio.create_task(self_cycle_wrapped())

        if time.monotonic() - last_beat >= HEARTBEAT_EVERY:
            last_beat = time.monotonic()
            M.log("self", {"vk_alive": {"опросов": polls, "событий": events,
                                        "сообщений": handled, "ts": ts}})
            print(f"  · жив · опросов {polls} · событий {events} "
                  f"· сообщений {handled} · ts {ts}")
            if events == 0 and polls > 20:
                print("  ⚠ событий нет вообще — похоже, дело не в агенте. "
                      "Проверь: ./jarvis --vk-check")

        for upd in res.get("updates", []):
            events += 1
            if upd.get("type") != "message_new":
                M.log("self", {"vk_skip": upd.get("type")})
                continue
            msg = extract_message(upd)
            if not msg:
                # Незнакомая форма события. Раньше терялась молча.
                M.log("error", {"vk_unparsed": str(upd)[:400]})
                print("  ✗ событие не разобрано, см. лог")
                continue
            peer = str(msg.get("peer_id") or msg.get("from_id") or 0)
            mid = msg.get("id") or msg.get("conversation_message_id") or 0
            last_msg_at = time.monotonic()
            if mid and not mark_seen(peer, mid):
                # Единственный намеренно тихий путь — но теперь он оставляет
                # след: сбитый счётчик иначе глотает всё без единой записи.
                # Сюда же попадает «да» на вопрос из ask: его уже прочитали.
                M.log("self", {"vk_dup": mid, "seen": seen.get(peer), "peer": peer})
                continue
            if not mid:
                M.log("error", {"vk_no_id": str(msg)[:300],
                                "why": "обрабатываю без защиты от повтора"})
            handled += 1
            # состояние пишется ДО хода: если агент упадёт на этом сообщении,
            # перезапуск не начнёт падать на нём же по кругу (mark_seen уже
            # сохранил его выше)
            await settle()
            # Сообщения, пойманные посреди хода, агент обычно учитывает прямо
            # в нём (core/interrupt.py). Не успевшие — доигрываются здесь,
            # по одному ходу на каждое. Глубина ограничена: если Илья пишет
            # быстрее, чем идут ходы, остальное подберёт long-poll.
            queue = [msg]
            depth = 0
            while queue and depth < 3:
                left = await handle(token, group_id, owner_id, queue.pop(0),
                                    ask=ask, mark_seen=mark_seen)
                queue.extend(left)
                depth += 1
            if queue:
                M.log("self", {"vk_late_dropped": len(queue),
                               "why": "глубина доигрывания исчерпана"})
            last_msg_at = time.monotonic()


def main() -> None:
    # Живой канал: записи демона и всех его детей идут в memory/log (bl-087).
    M.mark_live()
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        print()


if __name__ == "__main__":
    main()
