import io

p = "/home/jarvis/agent/jarvis/core/watchdog.py"
t = io.open(p, encoding="utf-8").read()

old = '''def _spawn() -> int:
    """Поднять демон ВК заново, отвязанным от процесса сторожа."""
    OUT.parent.mkdir(parents=True, exist_ok=True)
    log = OUT.open("a", encoding="utf-8")
    try:
        proc = subprocess.Popen(
            [str(C.PYTHON), "run.py", "--vk"], cwd=str(C.ROOT),
            stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
            start_new_session=True, env=C.live_env(),
        )
    finally:
        log.close()
    return proc.pid'''

new = '''def _spawn() -> int:
    """Поднять демон ВК заново — ПЕРЕЗАПУСКОМ СЛУЖБЫ, а не своим процессом.

    До 16.09.2026 сторож поднимал `run.py --vk` сам, через Popen со
    start_new_session=True. Такой процесс живёт ВНЕ службы и переживает её
    перезапуск. Демон, увидев чужой живой процесс ВК, отказывается стартовать —
    и правильно делает: два демона дают двойные ответы в ВК и двойные письма
    клиентам. Но systemd при этом пробует снова и снова: в ночь на 16.09
    набралось 299 неудачных попыток подряд, система стояла мёртвой сорок минут,
    и заметил это не сторож, а человек.

    Теперь сторож просит systemd — у него процесс ровно один и под контролем.
    Разрешение узкое, только на эту службу: /etc/sudoers.d/jarvis-daemon-restart.
    Возврат — pid поднятого демона, 0 если поднять не вышло (след в логе).
    """
    OUT.parent.mkdir(parents=True, exist_ok=True)
    try:
        r = subprocess.run(
            ["sudo", "-n", "/usr/bin/systemctl", "restart", "jarvis-daemon"],
            capture_output=True, text=True, timeout=90,
        )
    except Exception as e:                                  # noqa: BLE001
        _note(f"перезапуск службы не удался: {type(e).__name__}: {e}")
        return 0
    if r.returncode != 0:
        _note("перезапуск службы не удался: "
              f"{(r.stderr or r.stdout).strip()[:200]}")
        return 0
    time.sleep(3)          # службе нужно мгновение, чтобы записать vk.lock
    return ST.daemon_pid() or 0'''

assert old in t, "прежний _spawn не найден — ничего не менял"
t = t.replace(old, new, 1)

if "\nimport time\n" not in t:
    t = t.replace("import subprocess", "import subprocess\nimport time", 1)

io.open(p, "w", encoding="utf-8").write(t)
print("сторож переучен")

import py_compile
py_compile.compile(p, doraise=True)
print("файл разбирается без ошибок")
