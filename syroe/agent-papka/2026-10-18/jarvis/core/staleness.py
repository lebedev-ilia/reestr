"""Живой демон против кода на диске: работает ли он на том, что написано.

Зачем. 22.08.2026 голос «сменили» файлом, а демон сутки говорил старым — код
на диске новый, процесс в памяти старый. С тех пор каждая правка ядра рождает
одну и ту же ручную проверку: жив ли демон, свежее ли его старт, чем mtime
core/agent.py (задача bl-047, пять циклов подряд). Ручная проверка привязана к
pid и протухает при следующей же правке, поэтому здесь она сделана машинной.

Что считается. Демон грузит модули в память один раз при старте. Значит любой
файл кода, изменённый ПОЗЖЕ старта процесса, в живом демоне не действует —
неважно, закоммичен он или нет. Отсюда правило: время старта процесса против
mtime файлов кода. git checkout тоже двигает mtime без смысловой правки — и это
не ложная тревога: процесс всё равно исполняет то, что успел прочитать.

    python3 -m jarvis.core.staleness     # из каталога выше папки агента

Ничего не чинит и никуда не пишет: возвращает причину, решение принимает тот,
кто вызвал (самоаудит, отчёт утром, процедура перезапуска демона).
"""

from __future__ import annotations

import os
from pathlib import Path

from . import config as C

LOCK = C.DERIVED / "vk.lock"

# Что демон держит в памяти. Правка любого из этих файлов не действует, пока
# процесс не перезапущен.
WATCHED = (
    ("core", "*.py"),
    ("modules/vk", "*.py"),
    ("modules/voice", "*.py"),
)
WATCHED_FILES = ("run.py",)

_CLK = os.sysconf("SC_CLK_TCK")


def daemon_pid() -> int | None:
    """Pid живого демона ВК или None. Замок мог остаться от мёртвого процесса."""
    try:
        pid = int(LOCK.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return None
    if pid <= 0 or not Path(f"/proc/{pid}").exists():
        return None
    return pid


def _boot_time() -> float | None:
    try:
        for line in Path("/proc/stat").read_text(encoding="utf-8").splitlines():
            if line.startswith("btime "):
                return float(line.split()[1])
    except OSError:
        pass
    return None


def started_at(pid: int) -> float | None:
    """Момент старта процесса (unix-время).

    Считается по /proc/<pid>/stat, а не по выводу ps: ps печатает время в
    локали и его разбор ломается на русском «Ср авг 26».
    """
    boot = _boot_time()
    if boot is None:
        return None
    try:
        raw = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
    except OSError:
        return None
    # comm в скобках может содержать пробелы — режем по последней скобке
    tail = raw[raw.rfind(")") + 1:].split()
    try:
        starttime = float(tail[19])          # поле 22 в man 5 proc
    except (IndexError, ValueError):
        return None
    return boot + starttime / _CLK


def _watched_paths() -> list[Path]:
    out: list[Path] = []
    for sub, pattern in WATCHED:
        d = C.ROOT / sub
        if d.is_dir():
            out += sorted(p for p in d.glob(pattern) if p.is_file())
    out += [C.ROOT / name for name in WATCHED_FILES if (C.ROOT / name).is_file()]
    return out


def stale_files(started: float, root: Path | None = None) -> list[tuple[str, float]]:
    """Файлы кода, изменённые позже старта процесса: (путь от корня, mtime)."""
    base = root or C.ROOT
    out = []
    for p in _watched_paths() if root is None else sorted(
            q for q in base.rglob("*.py") if q.is_file()):
        m = p.stat().st_mtime
        if m > started:
            out.append((str(p.relative_to(base)), m))
    return sorted(out, key=lambda t: -t[1])


def check() -> dict:
    """Состояние демона одним словарём.

    ok=True значит: демон жив и ни один наблюдаемый файл не новее его старта.
    ok=False с pid=None значит: демона нет вовсе — правка не проверена на живом
    просто потому, что живого нет.
    """
    pid = daemon_pid()
    if pid is None:
        return {"ok": False, "pid": None, "started": None, "stale": [],
                "reason": "демон ВК не запущен: проверять правку не на чем"}
    started = started_at(pid)
    if started is None:
        return {"ok": False, "pid": pid, "started": None, "stale": [],
                "reason": f"демон pid {pid} жив, но время старта не читается"}
    stale = stale_files(started)
    if not stale:
        return {"ok": True, "pid": pid, "started": started, "stale": [],
                "reason": ""}
    names = ", ".join(n for n, _ in stale[:5])
    if len(stale) > 5:
        names += f" и ещё {len(stale) - 5}"
    return {"ok": False, "pid": pid, "started": started, "stale": stale,
            "reason": f"демон pid {pid} работает на устаревшем коде: {names}"}


def main() -> int:
    from datetime import datetime
    st = check()
    if st["started"]:
        print(f"демон pid {st['pid']}, старт "
              f"{datetime.fromtimestamp(st['started']):%Y-%m-%d %H:%M:%S}")
    if st["ok"]:
        print("код на диске и код в памяти совпадают")
        return 0
    print(st["reason"])
    for name, m in st["stale"]:
        print(f"  {name}: {datetime.fromtimestamp(m):%Y-%m-%d %H:%M:%S}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
