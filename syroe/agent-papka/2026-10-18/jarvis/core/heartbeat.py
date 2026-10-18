"""Пульс хода: Илья видит, что я делаю, без опоры на мою дисциплину.

Правило «слать статус раз в минуту» жило с 23.08 и не выполнялось: внутри
работы я не чувствую времени и вспоминаю о статусе на смене этапа, а не по
часам. 23.08 Илья поймал это прямо: «за 20 минут одно сообщение». Правило,
исполнение которого зависит от памяти модели, — не механизм. Здесь механизм.

Как работает: turn() дёргает beat() на КАЖДОМ шаге (каждый вызов инструмента).
beat() пишет шаг с временем в derived/steps/<дата>.jsonl и, если с последнего
сообщения Илье прошло больше интервала канала, сам отправляет статус в ВК.

Отметка «последнее сообщение» лежит в файле, а не в памяти процесса, потому
что мои ручные статусы уходят отдельным процессом (python -m ...vk.notify):
из памяти хода их не видно, из файла — видно, и таймер не шлёт дубль следом.
"""
from __future__ import annotations

import json
import threading
import time
from datetime import datetime
from pathlib import Path

from jarvis.core import config as C

# Секунды молчания, после которых статус уходит сам. 0 — не слать никогда.
#
# cli=0 — в терминале Илья видит вывод своими глазами, слать в ВК нечего.
# self=0 — фоновый ход идёт молча (с 24.08; было 180). Статус нужен тому, кто
#   ЖДЁТ ответа: в ходе из ВК Илья задал вопрос и смотрит в экран. Фоновый ход
#   начинается как раз тогда, когда он молчит дольше 10 минут, — он ничего не
#   ждёт, и 20 таких ходов за день дали бы ему в чат десятки сообщений ни о
#   чём. Конституция §7: его внимание дороже моего удобства отчитываться.
#   След фонового хода остаётся в derived/steps/<дата>.jsonl и в логе, отчёт —
#   одним сообщением утром. Исключение делается руками, а не автоматом: если
#   сломалось то, что мешает Илье прямо сейчас, шлю сам через vk.notify.
INTERVALS = {"vk": 60, "self": 0, "cli": 0}

# Ночью автостатусы не шлём: фоновая работа не повод будить. Ручные статусы
# и ответы на сообщения это не трогает — только автоматический добор.
QUIET_FROM, QUIET_TO = 23, 8

STATE = C.DERIVED / "heartbeat.json"

# Метка «этот процесс прямо сейчас внутри хода», видимая ДРУГИМ процессам.
# Нужна отложенному перезапуску (core/restart.py): с bl-119 демон сверяет свой
# код с диском каждые пять минут, а не раз в сутки, поэтому перезапуск стал
# частым — и его kill по SIGTERM обрывает ход на любом месте (обработчика
# сигнала у демона нет). По логу 25–26.08 демон проводит внутри ходов ~35%
# суток, то есть примерно каждый третий перезапуск рубил бы живую работу:
# ни ответа Илье, ни чекпоинта в очереди. Файл на процесс, а не одно поле в
# STATE, потому что подагенты зовут turn() своими процессами и затирали бы
# метку демона.
TURNS = C.DERIVED / "turns"
TURN_MAX_AGE = 3600.0     # сек: метка старше — забытая, а не живой ход

_lock = threading.Lock()
_turn = {"channel": "cli", "interval": 0, "started": 0.0, "steps": 0, "auto": 0}


def _read_state() -> dict:
    try:
        return json.loads(STATE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _write_state(**kw) -> None:
    """Пишет поля в STATE, не затирая соседние (channel живёт там же)."""
    try:
        STATE.parent.mkdir(parents=True, exist_ok=True)
        state = _read_state()
        state.update(kw)
        STATE.write_text(json.dumps(state), encoding="utf-8")
    except Exception:
        pass


def _read_last() -> float:
    try:
        return float(_read_state()["last"])
    except Exception:
        return 0.0


def _write_last(ts: float) -> None:
    _write_state(last=ts)


def mark() -> None:
    """Статус Илье только что ушёл (в том числе из другого процесса)."""
    _write_last(time.time())


def current_channel() -> str:
    """Канал текущего (или последнего) хода, как его видит ДРУГОЙ процесс.

    Нужен предохранителю ночной тишины (modules/vk/notify.py, bl-034): модель
    шлёт статус подпроцессом `python -m ...vk.notify`, у него нет доступа к
    памяти демона (_turn — на процесс), только к файлу STATE, который start()
    обновляет на каждый ход."""
    return _read_state().get("channel", "")


def turn_channel() -> str:
    """Канал текущего хода внутри ЭТОГО процесса (демон и ход — один процесс).

    Точнее current_channel(): файл состояния пишется на старте хода и живёт до
    следующего, а _turn всегда про ход, который идёт прямо сейчас."""
    return _turn.get("channel", "")


def quiet_now(now: datetime | None = None) -> bool:
    h = (now or datetime.now()).hour
    return h >= QUIET_FROM or h < QUIET_TO


def _alive(pid: int) -> bool:
    return Path(f"/proc/{pid}").exists()


def _marker(pid: int | None = None) -> Path:
    import os
    return TURNS / f"{pid or os.getpid()}.json"


def mark_turn_start(channel: str) -> None:
    """Поставить метку «я внутри хода» и убрать метки умерших процессов."""
    import os
    try:
        TURNS.mkdir(parents=True, exist_ok=True)
        for p in TURNS.glob("*.json"):
            try:
                if not _alive(int(p.stem)):
                    p.unlink(missing_ok=True)
            except (ValueError, OSError):
                pass
        _marker().write_text(
            json.dumps({"pid": os.getpid(), "started": time.time(),
                        "channel": channel}), encoding="utf-8")
    except Exception:
        pass          # метка — удобство перезапуска, ход из-за неё не падает


def finish() -> None:
    """Ход закончился (в том числе аварийно) — метку снять."""
    try:
        _marker().unlink(missing_ok=True)
    except Exception:
        pass


def busy(pid: int | None) -> bool:
    """Тот ли это процесс, что прямо сейчас тянет ход?

    Отвечает по метке ЧУЖОГО процесса, поэтому проверяет три вещи: метка есть,
    процесс жив, метка не протухла (ход, который забыли закрыть, не должен
    блокировать перезапуск навсегда). Нет метки — считаем, что свободен:
    перезапуск ждать нечего, а вечно откладывать его дороже.
    """
    if not pid:
        return False
    try:
        data = json.loads(_marker(pid).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    started = data.get("started")
    if not isinstance(started, (int, float)):
        return False
    if time.time() - started > TURN_MAX_AGE:
        return False
    return _alive(pid)


def start(channel: str) -> None:
    """Начало хода. channel: vk | self | cli."""
    with _lock:
        _turn.update(channel=channel, interval=INTERVALS.get(channel, 0),
                     started=time.time(), steps=0, auto=0)
    _write_state(channel=channel)
    mark_turn_start(channel)


def _steps_file() -> Path:
    d = C.DERIVED / "steps"
    d.mkdir(parents=True, exist_ok=True)
    return d / f"{datetime.now():%Y-%m-%d}.jsonl"


def send(text: str) -> bool:
    """Отправить статус в ВК, не задерживая ход: отправка идёт в отдельном
    потоке, ошибки глотаются. Возвращает False, если отправлять некуда."""
    text = (text or "").strip()
    if not text:
        return False

    def go():
        try:
            from jarvis.modules.vk.notify import notify
            notify(text)
        except Exception:
            pass

    threading.Thread(target=go, daemon=True).start()
    mark()
    return True


def beat(description: str, sender=send, say: str | None = None) -> bool:
    """Один шаг хода. Пишет его со временем и при нужде шлёт статус сам.

    description — для журнала шагов: путь, команда, всё как есть.
    say — то же самое словами для Ильи. 24.08 он получил в чат десяток строк
    сырых shell-команд и сказал, что не читает их. В ВК уходит say, в файл —
    description. Без say (так зовёт только самотест) шлём description.

    Возвращает True, если статус ушёл. sender подменяется в самотесте.
    """
    now = time.time()
    with _lock:
        _turn["steps"] += 1
        n = _turn["steps"]
        interval = _turn["interval"]
        started = _turn["started"] or now
    since_last = now - _read_last()

    try:
        with _steps_file().open("a", encoding="utf-8") as f:
            f.write(json.dumps({"t": datetime.now().strftime("%H:%M:%S"),
                                "ch": _turn["channel"], "n": n,
                                "sec": round(now - started, 1),
                                "what": (description or "")[:200]},
                               ensure_ascii=False) + "\n")
    except Exception:
        pass

    if not interval or since_last < interval or quiet_now():
        return False
    text = (say if say is not None else description) or ""
    if not text.strip():
        return False
    if sender(f"{datetime.now():%H:%M} · {text[:180]}"):
        mark()          # таймер сбрасывает сам beat: sender бывает и подменным
        with _lock:
            _turn["auto"] += 1
        return True
    return False


def summary() -> dict:
    """Итог хода для лога: сколько шагов, сколько минут, сколько автостатусов."""
    with _lock:
        started = _turn["started"] or time.time()
        return {"steps": _turn["steps"], "sec": round(time.time() - started, 1),
                "auto_status": _turn["auto"], "channel": _turn["channel"]}
