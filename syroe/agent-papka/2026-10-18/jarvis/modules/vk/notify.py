#!/usr/bin/env python3
"""Промежуточное сообщение Илье в ВК — прямо из середины хода.

Зачем: ход бывает долгим (минуты). Индикатор «печатает» показывает, что
процесс жив, но не показывает, ЧТО происходит. Илья просил видеть, над чем
я работаю, а не гадать.

  ./.venv/bin/python -m jarvis.modules.vk.notify "смотрю код канала"

Пишет только владельцу (VK_OWNER_ID), только текстом, ошибки не роняют ход:
статус — вещь вспомогательная, из-за неё работа останавливаться не должна.

Предохранитель ночной тишины (bl-034, правило nochnaya-tishina). В ночь на
24.08 из фоновых и ночного ходов ушло ~25 сообщений в спящий телефон: словесный
запрет держался только вниманием модели и один раз не удержал. Здесь —
код, а не память. С 23:00 до 08:00 (Asia/Vladivostok, C.TZ) отправка идёт,
только если ход начат сообщением самого Ильи (канал "vk") — тогда он ждёт
ответа, и молчать нельзя. Всё остальное (self — фоновый ход и очередь в
простое, system — ночной цикл и доклады о заданиях) в это окно копится
строкой в derived/night_queue.jsonl, а не уходит в чат; в лог падает ключ
vk_muted_night. Первая же отправка после 08:00 — из notify() или из deliver()
в modules/vk/daemon.py — сначала отдаёт накопленное одним сообщением и чистит
очередь (flush_queue), только потом делает своё дело.

Откуда notify() знает канал текущего хода: в этом же процессе (демон) канал
известен через core.heartbeat._turn, но notify часто запускается ОТДЕЛЬНЫМ
подпроцессом (эта команда), у которого своей памяти демона нет. Канал поэтому
читается из derived/heartbeat.json (HB.current_channel()) — HB.start(channel)
пишет его туда на каждый ход, до того как ход успевает позвать инструмент.
"""
import json
import random
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from jarvis.core import config as C  # noqa: E402
from jarvis.core import heartbeat as HB  # noqa: E402
from jarvis.core import memory as M  # noqa: E402
from jarvis.modules.vk import api  # noqa: E402
from jarvis.modules.vk.daemon import load_env  # noqa: E402

QUEUE = C.DERIVED / "night_queue.jsonl"   # производное: восстановимо, чистится по утру

QUIET_FROM, QUIET_TO = 23, 8              # тот же интервал, что в heartbeat.py,
                                           # но по часовому поясу Ильи, а не сервера
MSG_CAP = 900                             # обычный статус — короткая строка
FLUSH_CAP = 4000                          # утренний пакет — как обычный ответ (см. daemon.MAX_LEN)


def _local_now() -> datetime:
    return datetime.now(ZoneInfo(C.TZ))


def quiet_hours(now: datetime | None = None) -> bool:
    """Сейчас ночь по местному времени Ильи (23:00–08:00)."""
    h = (now or _local_now()).hour
    return h >= QUIET_FROM or h < QUIET_TO


def _queue_append(text: str) -> None:
    QUEUE.parent.mkdir(parents=True, exist_ok=True)
    row = {"ts": _local_now().isoformat(timespec="seconds"), "text": text[:2000]}
    with QUEUE.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


def _queue_read() -> list[dict]:
    if not QUEUE.exists():
        return []
    rows = []
    for line in QUEUE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except Exception:
            continue
    return rows


def _send_raw(text: str, cap: int = MSG_CAP, kind: str = "status") -> bool:
    """Фактическая отправка, без проверки часа: общий хвост notify и flush.

    kind — каким типом лечь в лог (bl-077). По умолчанию "status": через эту
    дверь ходит пульс («читаю страницу»), а это след работы, а не разговор.
    Содержательное сообщение Илье — доклад роли, утренний пакет — передаёт
    kind="message_out" и остаётся видимым в окне лога, ночном разборе и поиске.
    """
    try:
        token, group_id, owner_id = load_env()
        if not owner_id:
            return False
        api.call("messages.send", token, peer_id=owner_id, message=text[:cap],
                 random_id=random.getrandbits(31), group_id=group_id)
        M.log(kind, text[:500], actor="agent")
        # Пульс общий для всех процессов: ручной статус сбрасывает таймер,
        # иначе автодобор пришлёт дубль следом за моим же сообщением.
        try:
            HB.mark()
        except Exception:
            pass
        return True
    except Exception as e:
        M.log("error", {"vk_notify": f"{type(e).__name__}: {e}"})
        return False


def flush_queue() -> bool:
    """Ночная очередь → одно сжатое сообщение, если сейчас не ночь.

    Пустая очередь или всё ещё ночь — ничего не делает и возвращает False,
    поэтому вызывать безопасно на каждый ход: реально уйдёт только первый раз
    после 08:00, дальше очередь пуста и функция молча выходит.
    """
    if quiet_hours():
        return False
    rows = _queue_read()
    if not rows:
        return False
    body = "\n\n".join(f"· {r.get('text', '')}" for r in rows)
    combined = f"Ночью копилось ({len(rows)}), тишина 23:00–08:00:\n\n{body}"
    if len(combined) > FLUSH_CAP:
        combined = combined[:FLUSH_CAP].rstrip() + "\n… (обрезано, полностью — в логе, ключ vk_muted_night)"
    # Утренний пакет — настоящее сообщение Илье (в нём и отчёты, и вопросы),
    # а не пульс: тип message_out, чтобы он остался в разговоре.
    ok = _send_raw(combined, cap=FLUSH_CAP, kind="message_out")
    if ok:
        try:
            QUEUE.unlink()
        except OSError:
            pass
        M.log("self", {"vk_night_queue_flushed": len(rows)})
    return ok


def notify(text: str, kind: str = "status") -> bool:
    """Отправить статус. True — ушло сейчас. Исключений не бросает.

    Ночью (23:00–08:00) и ход не из ВК — не отправляется, а копится в
    derived/night_queue.jsonl; возвращает False, как и любая другая
    несостоявшаяся отправка. Перед этим решением сначала пробует отдать то,
    что уже накопилось: если время уже дневное, старая очередь уходит одним
    сообщением, не дожидаясь следующего хода.

    kind="message_out" — если это не пульс, а содержательное сообщение Илье
    (доклад роли, срочная поломка): тогда след останется в разговоре."""
    text = (text or "").strip()
    if not text:
        return False

    try:
        flush_queue()
    except Exception as e:                                     # noqa: BLE001
        M.log("error", {"vk_night_flush": f"{type(e).__name__}: {e}"})

    if quiet_hours() and HB.current_channel() != "vk":
        try:
            _queue_append(text)
            M.log("self", {"vk_muted_night": text[:200]})
        except Exception as e:                                 # noqa: BLE001
            M.log("error", {"vk_night_queue": f"{type(e).__name__}: {e}"})
        return False

    return _send_raw(text, kind=kind)


if __name__ == "__main__":
    # Сообщение Илье — настоящее действие, его след место в memory/log (bl-087).
    M.mark_live()
    argv = sys.argv[1:]
    # По умолчанию из командной строки уходит промежуточный статус хода.
    # --message — редкий случай, когда это содержательное сообщение Илье.
    kind = "status"
    if argv and argv[0] == "--message":
        kind, argv = "message_out", argv[1:]
    ok = notify(" ".join(argv), kind=kind)
    if ok:
        print("отправлено")
    elif quiet_hours() and HB.current_channel() != "vk":
        print("ночь (23:00–08:00), ход не из ВК — легло в очередь "
              "derived/night_queue.jsonl, отдам утром")
    else:
        print("не ушло (см. лог)")
