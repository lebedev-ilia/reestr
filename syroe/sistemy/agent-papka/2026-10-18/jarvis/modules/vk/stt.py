"""Своё распознавание речи: faster-whisper на CPU, без сети и без оплаты.

Зачем это есть. ВК расшифровывает голосовые сообществу не всегда: приходит
`transcript_state: failed` или расшифровка не появляется вовсе, и тогда ход
уходит на «повтори текстом». Голос — основной способ Ильи со мной говорить,
поэтому расшифровка не может зависеть от чужого сервиса.

Модель лежит в derived/stt — производное: снесли папку, скачается заново
(464 МБ, ~1.5 минуты). Уникального там ничего нет.

Железо (Ryzen 3 1200, 4 ядра, без CUDA) диктует настройки: размер small,
целочисленная арифметика int8, луч поиска 1 вместо 5 и отсечка тишины.
Без этого распознавание минутной записи идёт дольше самой записи.

Запуск проверки:  ./.venv/bin/python -m jarvis.modules.vk.stt <файл.ogg>
"""
import os
import time
from pathlib import Path

from ...core import config as C
from ...core import memory as M

MODEL_DIR = C.DERIVED / "stt"
MODEL_NAME = os.environ.get("JARVIS_STT_MODEL", "small")
LANG = "ru"
_model = None


def model():
    """Модель, загруженная один раз на процесс.

    Первый вызов после чистки derived качает веса из интернета — это долго,
    поэтому он отдельной строкой в логе: иначе «демон завис» выглядит
    загадочно, а на деле идут 464 МБ.
    """
    global _model
    if _model is None:
        from faster_whisper import WhisperModel      # импорт тяжёлый, ленивый
        fresh = not MODEL_DIR.exists()
        if fresh:
            M.log("self", {"stt_download": MODEL_NAME, "why": "модели нет на диске"})
        t = time.time()
        _model = WhisperModel(MODEL_NAME, device="cpu", compute_type="int8",
                              download_root=str(MODEL_DIR))
        M.log("self", {"stt_model": MODEL_NAME, "sec": round(time.time() - t, 1)})
    return _model


def transcribe(path: str | Path) -> str:
    """Файл с речью → текст. Пустая строка, если речи не нашлось.

    vad_filter выкидывает тишину до распознавания: в голосовых её обычно
    треть, и это самая дешёвая экономия времени из возможных.
    """
    segments, info = model().transcribe(
        str(path), language=LANG, beam_size=1, vad_filter=True)
    text = " ".join(s.text.strip() for s in segments).strip()
    M.log("self", {"stt": "ok", "sec_audio": round(info.duration, 1),
                   "chars": len(text)})
    return text


def voice_url(att: dict) -> str | None:
    """Ссылка на запись голосового сообщения ВК."""
    a = att.get("audio_message") or {}
    return a.get("link_ogg") or a.get("link_mp3")


def transcribe_attachment(att: dict, tag: str = "voice") -> str | None:
    """Голосовое вложение ВК → текст. None, если не вышло.

    Файл кладётся туда же, куда картинки и pdf (derived/vk_media/<дата>),
    и чистится тем же сроком: запись остаётся в ВК, дубль на диске не нужен.
    """
    url = voice_url(att)
    if not url:
        return None
    from . import api
    try:
        data = api.download(url)
    except Exception as e:
        M.log("error", {"stt_download": str(e)})
        return None

    folder = C.DERIVED / "vk_media" / time.strftime("%Y-%m-%d")
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{tag}.ogg"
    try:
        path.write_bytes(data)
        t = time.time()
        text = transcribe(path)
        M.log("self", {"stt_local": bool(text), "sec": round(time.time() - t, 1)})
        return text or None
    except Exception as e:
        M.log("error", {"stt": str(e)})
        return None


def main() -> int:
    import sys
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    t = time.time()
    print(transcribe(sys.argv[1]))
    print(f"--- {round(time.time() - t, 1)} сек", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
