"""Скачивание голосов Piper в derived/voices — команда восстановления зоны.

Голоса весят по 60 МБ и лежат в производной зоне, которую можно снести в любой
момент. До 26.08.2026 в say.py стояло обещание «качаются заново скриптом ниже»,
а скрипта не было: 181 МБ жили без команды восстановления (правило
v-derived-tolko-vosstanovimoe, задача bl-051). Этот модуль и есть тот скрипт.

    python3 -m modules.voice.fetch            # добрать недостающее (ruslan)
    python3 -m modules.voice.fetch --all      # ruslan, denis, dmitri
    python3 -m modules.voice.fetch denis      # конкретный голос
    python3 -m modules.voice.fetch --check    # только проверить ссылки, не качать

Качается с huggingface.co/rhasspy/piper-voices (открыто, без ключа). Канал у
Ильи слабый: 60 МБ на голос — это минуты, поэтому уже скачанное не трогается,
а лишние голоса добираются только по имени.
"""
import shutil
import sys
import urllib.request
from pathlib import Path

from ...core import config as C

VOICES_DIR = C.DERIVED / "voices"
BASE = ("https://huggingface.co/rhasspy/piper-voices/resolve/main/"
        "ru/ru_RU/{v}/medium/ru_RU-{v}-medium.onnx")
KNOWN = ("ruslan", "denis", "dmitri")     # русские medium-голоса
DEFAULT = ("ruslan",)                     # выбран Ильёй 22.08.2026


def _url(voice: str, meta: bool = False) -> str:
    return BASE.format(v=voice) + (".json" if meta else "")


def _dest(voice: str, meta: bool = False) -> Path:
    return VOICES_DIR / f"ru_RU-{voice}-medium.onnx{'.json' if meta else ''}"


def remote_size(voice: str, meta: bool = False) -> int:
    req = urllib.request.Request(_url(voice, meta), method="HEAD")
    with urllib.request.urlopen(req, timeout=30) as r:
        return int(r.headers.get("Content-Length") or 0)


def fetch(voice: str, force: bool = False) -> str:
    """Скачивает голос и его json, если их нет. Возвращает строку отчёта."""
    if voice not in KNOWN:
        return f"{voice}: неизвестный голос (известны: {', '.join(KNOWN)})"
    VOICES_DIR.mkdir(parents=True, exist_ok=True)
    out = []
    for meta in (False, True):
        dst, url = _dest(voice, meta), _url(voice, meta)
        try:
            need = remote_size(voice, meta)
        except Exception as e:                           # noqa: BLE001
            # сеть отвалилась или адрес переехал: уже скачанное не трогаем
            out.append(f"{dst.name}: адрес не отвечает ({e}) — "
                       f"{'оставлен как был' if dst.exists() else 'НЕ СКАЧАН'}")
            continue
        if dst.exists() and not force and (not need or dst.stat().st_size == need):
            out.append(f"{dst.name}: уже на месте")
            continue
        tmp = dst.with_suffix(dst.suffix + ".part")
        with urllib.request.urlopen(url, timeout=300) as r, tmp.open("wb") as f:
            shutil.copyfileobj(r, f, 1 << 20)
        got = tmp.stat().st_size
        if need and got != need:
            tmp.unlink(missing_ok=True)
            out.append(f"{dst.name}: ОБРЫВ ({got} из {need} байт), не сохранено")
            continue
        tmp.replace(dst)
        out.append(f"{dst.name}: скачано, {got // 1024} КБ")
    return "; ".join(out)


def main(argv: list[str]) -> int:
    args = [a for a in argv if not a.startswith("--")]
    flags = {a for a in argv if a.startswith("--")}
    voices = KNOWN if "--all" in flags else (tuple(args) or DEFAULT)
    if "--check" in flags:
        for v in voices:
            try:
                size = remote_size(v)
                here = _dest(v).exists()
                print(f"{v}: ссылка жива, {size // 1048576} МБ, "
                      f"{'есть локально' if here else 'локально нет'}")
            except Exception as e:                       # noqa: BLE001
                print(f"{v}: ССЫЛКА НЕ ОТВЕЧАЕТ — {e}")
                return 1
        return 0
    for v in voices:
        print(fetch(v, force="--force" in flags))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
