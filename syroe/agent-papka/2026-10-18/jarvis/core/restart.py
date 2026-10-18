"""Отложенный перезапуск демона ВК, когда живой процесс работает на
устаревшем коде (bl-114).

Зачем. Каждая правка ядра рождала одну и ту же ручную работу: свериться с
core.staleness, собрать одноразовый скрипт по процедуре
memory/procedures/perezapusk-demona-vk.md, поднять его отвязанным. В ночь на
26.08 в живой демон 18 часов не входили правки core/agent.py, run.py и
планировщика рассылки — свёртка отработала, а перезапустить было некому:
никто не позвал процедуру руками. Раз это повторяется — чинится кодом.

Устройство — то же, что у modules/jobs/run.py: работа, которая должна
пережить конец вызвавшего её хода, поднимается ОТДЕЛЬНЫМ процессом ОС
(`start_new_session=True`), а не корутиной внутри демона. Демон, который
поставил перезапуск, не обязан (и не должен) дожидаться его исхода.

Три шага строго по процедуре:
  1. задержка (DELAY) — дольше, чем может тянуться дописываемый ход;
  2. ожидание тишины: нет ни одного задания running/queued (modules/jobs, а
     не подгляд за процессами через pgrep) И сам демон не внутри хода
     (метка core.heartbeat, файл на процесс) — kill обрывает ход молча;
  3. kill СТРОГО по pid из pid-файла (derived/vk.lock через
     core.staleness.daemon_pid, читается ЗАНОВО в момент убийства — не тот
     pid, что был на момент планирования), и следом поднять демон заново.
     pkill по имени команды НЕ используется: "run.py --vk" совпадает и с
     командной строкой планирующего процесса (грабля, уже описанная в
     процедуре).

Если тишина по заданиям так и не наступила за отведённое окно, перезапуск
всё равно происходит (как и в ручной процедуре) — но это отдельно и заметно
логируется: молчаливого решения "рискнуть работой" быть не должно.

Ничего из этого модуля не пишет в memory/log из ОТДЕЛЬНОГО процесса-наблюдателя:
у него нет и не будет memory.mark_live() (право не наследуется через
окружение, см. core/memory.py). Планирующий процесс (демон, уже живой) —
единственный, кто логирует сам факт постановки перезапуска.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

from . import config as C
from . import memory as M
from . import staleness as ST

DELAY = 480          # сек: дольше остатка хода, см. процедуру (грабля 25.08)
POLL_EVERY = 30       # сек: шаг ожидания тишины по заданиям
POLL_MAX = 20         # 20 × 30 с = 10 минут ожидания сверх задержки

PENDING = C.DERIVED / "restart_pending.json"   # производное: кто уже наблюдает
WATCH_MARK = "jarvis.core.restart"   # чем наблюдатель опознаётся в cmdline


def _quiet() -> bool:
    """Можно ли убивать демон прямо сейчас.

    Два условия, и оба про живую работу, которую kill оборвёт молча:
      1. нет живых заданий подагентов (running/queued, modules/jobs);
      2. САМ демон не внутри хода — метка core.heartbeat, файл на процесс.

    Второе добавлено с bl-119: пока проверка устаревания жила в ночной
    свёртке, перезапуск случался раз в сутки, а теперь — с любой правки кода,
    то есть по нескольку раз за ночь. По логу 25–26.08 демон проводит внутри
    ходов около 35% суток; без этой проверки примерно каждый третий перезапуск
    рубил бы ход на середине (обработчика SIGTERM у демона нет): ни ответа
    Илье, ни чекпоинта в очереди, ни записи в память.
    """
    from . import heartbeat as HB
    from . import jobs as J
    if J.by_status(*J.LIVE):
        return False
    return not HB.busy(ST.daemon_pid())


def _spawn_daemon() -> int:
    """Поднять демон ВК заново, отвязанным от этого процесса."""
    log = (C.DERIVED / "vk.log").open("a", encoding="utf-8")
    try:
        proc = subprocess.Popen(
            [str(C.PYTHON), "run.py", "--vk"], cwd=str(C.ROOT),
            stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
            start_new_session=True, env=C.live_env(),
        )
    finally:
        log.close()
    return proc.pid


def _spawn_watch() -> int:
    """Поднять процесс-наблюдатель, который доживёт до самого перезапуска."""
    proc = subprocess.Popen(
        [str(C.PYTHON), "-m", "jarvis.core.restart", "--watch"],
        cwd=str(C.ROOT.parent), stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        start_new_session=True, env=C.live_env(),
    )
    return proc.pid


def restart_once(sleep=time.sleep, quiet=_quiet, get_pid=None,
                 kill=os.kill, spawn=None) -> dict:
    """Тело отложенного перезапуска — вся логика ожидания и убийства.

    Зависимости приняты параметрами ровно затем, чтобы самотест мог прогнать
    её целиком за миллисекунды, не трогая ни настоящее время, ни настоящий
    демон, ни настоящие задания.
    """
    get_pid = get_pid or ST.daemon_pid
    spawn = spawn or _spawn_daemon

    sleep(DELAY)
    tries = 0
    while tries < POLL_MAX and not quiet():
        sleep(POLL_EVERY)
        tries += 1
    gave_up_busy = tries >= POLL_MAX and not quiet()

    pid = get_pid()                       # заново, в момент убийства
    if pid is None:
        return {"killed": None, "tries": tries, "gave_up_busy": gave_up_busy}
    try:
        kill(pid, signal.SIGTERM)
    except OSError:
        return {"killed": None, "tries": tries, "gave_up_busy": gave_up_busy}
    sleep(3)
    new_pid = spawn()
    return {"killed": pid, "new_pid": new_pid, "tries": tries,
            "gave_up_busy": gave_up_busy}


def _cmdline(pid: int) -> str | None:
    """Командная строка процесса одной строкой или None, если её не прочесть."""
    try:
        raw = Path(f"/proc/{pid}/cmdline").read_bytes()
    except OSError:
        return None
    return raw.decode("utf-8", "replace").replace("\0", " ")


def _pending_watch_pid() -> int | None:
    """Pid уже поставленного наблюдателя, если он всё ещё жив.

    Наблюдатель — тот процесс, которого вернул spawn_watch() в прошлый раз,
    не демон и не тот, кто зовёт schedule(). Пока он жив, второй наблюдатель
    не нужен: он либо ещё спит DELAY секунд, либо ждёт тишины по заданиям —
    в обоих случаях один и тот же час перезапуска уже назначен.

    Голого pid для «жив» НЕ хватает (bl-120): pid'ы в Linux переиспользуются
    по кругу, и через сутки под тем же номером живёт чужой процесс. Тогда
    schedule() решил бы, что наблюдатель на месте, второго не поднял — и
    перезапуск тихо не состоялся бы, а в журнале осталось бы «уже стоит».
    Поэтому сверяется признак ИМЕННО ТОГО процесса: время старта из
    /proc/<pid>/stat, записанное в PENDING рядом с pid. Не совпало — считаем,
    что наблюдателя нет. Ошибка в эту сторону дешёвая: лишний наблюдатель
    перезапустит демон повторно, пропущенный — оставит сутки на старом коде.

    В файле старого формата (без "started") сверяться не с чем — там признак
    берётся из cmdline: наблюдатель поднят как `python -m jarvis.core.restart`.
    """
    try:
        data = json.loads(PENDING.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    pid = data.get("pid")
    if not isinstance(pid, int) or pid <= 0:
        return None
    if not Path(f"/proc/{pid}").exists():
        return None                       # наблюдатель уже закончил работу
    started = data.get("started")
    if isinstance(started, (int, float)):
        now = ST.started_at(pid)
        # допуск в секунду: одно и то же значение читается через btime и
        # тики ядра, и на этом пути возможна дробная погрешность
        if now is None or abs(now - float(started)) > 1.0:
            return None                   # pid переиспользован под чужой процесс
        return pid
    cmd = _cmdline(pid)
    if cmd is None or WATCH_MARK not in cmd:
        return None
    return pid


def schedule(reason: str, spawn_watch=None) -> int:
    """Поставить отложенный перезапуск. Живой демон здесь НЕ трогается —
    только поднимается процесс, который дождётся своего часа сам.

    Идемпотентно (bl-118): если предыдущий наблюдатель ещё жив (его DELAY
    ещё не истёк или он ждёт тишины по заданиям), второй не поднимается и
    срок не отодвигается — вызывающему возвращается pid уже идущего.
    Гарантия держится на файле PENDING и проверке живости pid в /proc, а не
    на том, что вызывающий сам не позовёт schedule() дважды подряд: tick()
    теперь зовёт эту проверку на каждом тике (см. core/night.py), пока
    демон остаётся устаревшим.

    Вызывается из процесса, уже объявившего себя живым (демон, в конце
    ночного цикла) — запись в лог здесь идёт штатным путём, через M.log.
    """
    pending = _pending_watch_pid()
    if pending is not None:
        return pending
    spawn_watch = spawn_watch or _spawn_watch
    pid = spawn_watch()
    C.DERIVED.mkdir(parents=True, exist_ok=True)
    # рядом с pid — время старта именно этого процесса: по нему потом видно,
    # тот ли это наблюдатель или ОС уже отдала номер кому-то другому (bl-120)
    PENDING.write_text(json.dumps({"pid": pid, "reason": reason,
                                   "started": ST.started_at(pid)}),
                       encoding="utf-8")
    M.log("self", {"night_restart_scheduled": {
        "причина": reason, "pid_наблюдателя": pid,
        "задержка_сек": DELAY, "макс_ожидание_тишины_сек": POLL_EVERY * POLL_MAX,
    }})
    return pid


def main(argv: list[str]) -> int:
    if "--watch" in argv:
        restart_once()
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
