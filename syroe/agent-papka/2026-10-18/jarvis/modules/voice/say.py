"""Голос: синтез речи локально (Piper) и отправка голосовым в ВК.

Синтез идёт на CPU без интернета и без оплаты: 5-11x реалтайма на этой машине.
Голоса лежат в derived/voices/ — производное. Вернуть после сноса зоны:
python3 -m jarvis.modules.voice.fetch (см. modules/voice/fetch.py).

Запуск проверки:  ./.venv/bin/python -m jarvis.modules.voice.say "текст" [голос]
"""
import io
import json
import random
import time
import urllib.request
import uuid
import wave
from pathlib import Path

import numpy as np
import soundfile as sf
import soxr
from piper import PiperVoice

from ..vk import api
from ...core import config as C

VOICES = C.DERIVED / "voices"
DEFAULT_VOICE = "ruslan"          # выбран Ильёй 22.08.2026 из трёх проб
_cache: dict[str, PiperVoice] = {}


def model_path(name: str) -> Path:
    return VOICES / f"ru_RU-{name}-medium.onnx"


PAUSE = 0.35   # секунд тишины между предложениями


def synthesize(text: str, voice: str = DEFAULT_VOICE,
               pause: float = PAUSE) -> bytes:
    """Текст -> ogg/opus, пригодный для голосового сообщения ВК.

    Между предложениями вставляется тишина. SSML Piper не понимает, а сам
    паузу по точке почти не делает: без этого перечисление из пяти пунктов
    звучит одной длинной фразой, и на слух непонятно, где кончается мысль.
    Piper режет текст на предложения сам — берём его же границы.
    """
    if voice not in _cache:
        p = model_path(voice)
        if not p.exists():
            raise FileNotFoundError(
                f"нет голоса {voice}: {p}. Скачать — modules/voice/README.md")
        _cache[voice] = PiperVoice.load(str(p))
    chunks = list(_cache[voice].synthesize(text))
    if not chunks:
        raise ValueError("синтезировать нечего: пустой текст")
    rate = chunks[0].sample_rate
    gap = np.zeros(int(rate * pause), dtype="float32")
    parts = []
    for i, c in enumerate(chunks):
        if i:
            parts.append(gap)
        parts.append(c.audio_float_array)
    pcm, sr = np.concatenate(parts), rate
    # Opus принимает только 8/12/16/24/48 кГц, а piper-medium отдаёт 22050.
    # Без ресемпла запись просто не сохранится — это не мелочь, а стоп.
    if sr not in (8000, 12000, 16000, 24000, 48000):
        pcm, sr = soxr.resample(pcm, sr, 48000), 48000
    out = io.BytesIO()
    sf.write(out, pcm, sr, format="OGG", subtype="OPUS")
    return out.getvalue()


class VoiceUploadError(RuntimeError):
    """Загрузчик ВК не принял файл. В тексте — сырой ответ сервера.

    Заведено 25.08.2026 по bl-062. До этого ответ загрузчика уходил в
    docs.save как есть, и при сбое ВК отвечал «file is undefined» — ошибка
    про параметр метода, хотя ломался предыдущий шаг. Причина не
    восстанавливалась ни по логу, ни повторным прогоном.
    """


UPLOAD_TRIES = 3      # загрузчик отказывает разово: за сутки одна осечка на десятки писем
UPLOAD_PAUSE = 1.5    # секунд между попытками
MIN_OGG = 512         # меньше — синтез отдал огрызок, грузить нечего


def _upload(token: str, peer_id: int, ogg: bytes) -> dict:
    """Один заход: взять адрес загрузчика и залить файл multipart-ом.

    Возвращает поля для docs.save. Любой невнятный ответ превращается в
    VoiceUploadError с сырым телом — чинить вслепую дороже, чем один раз
    сохранить то, что сервер реально сказал.
    """
    srv = api.call("docs.getMessagesUploadServer", token,
                   type="audio_message", peer_id=peer_id)
    url = srv.get("upload_url")
    if not url:
        raise VoiceUploadError(f"ВК не дал адрес загрузчика: {srv!r:.200}")
    b = uuid.uuid4().hex
    body = b"".join([
        f"--{b}\r\n".encode(),
        b'Content-Disposition: form-data; name="file"; filename="file0.ogg"\r\n',
        b"Content-Type: audio/ogg\r\n\r\n", ogg, b"\r\n",
        f"--{b}--\r\n".encode()])
    req = urllib.request.Request(
        url, data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={b}"})
    raw = urllib.request.urlopen(req, timeout=60).read().decode(errors="replace")
    try:
        up = json.loads(raw)
    except ValueError:
        raise VoiceUploadError(f"загрузчик ответил не json: {raw[:200]}") from None
    if not up.get("file"):
        host = url.split("/")[2] if "//" in url else url[:40]
        raise VoiceUploadError(
            f"загрузчик не вернул file (ogg {len(ogg)} Б, {host}): {raw[:200]}")
    return up


def send_voice(token: str, group_id: int, peer_id: int, ogg: bytes,
               caption: str = "") -> None:
    """Загрузить ogg/opus как audio_message и отправить.

    Три шага, каждый может отвалиться отдельно: получить адрес загрузчика,
    залить файл multipart-ом, сохранить документ. Строка вложения —
    doc<owner_id>_<id>[_<access_key>]. Первые два шага повторяются с новым
    адресом загрузчика: осечка там разовая, а откат в текст Илья слышит как
    ухудшение канала, который он сам выбирал.
    """
    if len(ogg) < MIN_OGG:
        raise VoiceUploadError(f"озвучка пустая или огрызок: {len(ogg)} Б")
    last: Exception | None = None
    up = None
    for i in range(UPLOAD_TRIES):
        try:
            up = _upload(token, peer_id, ogg)
            break
        except (VoiceUploadError, api.VKError, OSError, ValueError) as e:
            last = e
            if i + 1 < UPLOAD_TRIES:
                time.sleep(UPLOAD_PAUSE)
    if up is None:
        raise VoiceUploadError(
            f"загрузчик не принял файл за {UPLOAD_TRIES} попытки: {last}") from last
    saved = api.call("docs.save", token, **up)
    am = saved["audio_message"]
    att = f"doc{am['owner_id']}_{am['id']}"
    if am.get("access_key"):
        att += "_" + am["access_key"]
    api.call("messages.send", token, peer_id=peer_id, message=caption,
             attachment=att, random_id=random.getrandbits(31), group_id=group_id)


if __name__ == "__main__":
    import sys
    text = sys.argv[1] if len(sys.argv) > 1 else "Проверка голоса."
    voice = sys.argv[2] if len(sys.argv) > 2 else DEFAULT_VOICE
    t0 = time.monotonic()
    ogg = synthesize(text, voice)
    print(f"{voice}: {len(ogg)//1024} КБ за {time.monotonic()-t0:.1f} с")
