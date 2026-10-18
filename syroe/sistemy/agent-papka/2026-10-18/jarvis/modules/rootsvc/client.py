#!/usr/bin/env python3
"""Сторона агента: положить заявку посреднику и дождаться ответа.

Команды не собираются здесь вовсе — только имя операции из белого списка и
параметры. Если посредник не установлен, вызов честно говорит об этом и не
пытается ничего обойти.

Из кода:   from modules.rootsvc.client import call
           r = call("disk.mount", device="/dev/sdb1")
Из шелла:  python -m modules.rootsvc.client disk.mount device=/dev/sdb1
           python -m modules.rootsvc.client --confirm pkg.install names=fd,rg
           python -m modules.rootsvc.client --ops        # что вообще можно
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

QUEUE = Path(os.environ.get("JARVIS_ROOT_QUEUE", "/run/jarvis-root"))
ALLOW = Path(os.environ.get("JARVIS_ROOT_ALLOW", "/opt/jarvis-root/allow.json"))
BOOTSTRAP = "sudo modules/rootsvc/bootstrap.sh  (запускает Илья, один раз)"


class NotInstalled(RuntimeError):
    pass


def ops() -> dict:
    """Белый список глазами клиента: только чтение, править его нельзя."""
    try:
        return json.loads(ALLOW.read_text(encoding="utf-8"))["ops"]
    except FileNotFoundError:
        raise NotInstalled(f"посредник не установлен: нет {ALLOW}. Бутстрап: {BOOTSTRAP}")


def call(op: str, confirm: bool = False, timeout: float = 200.0, **args) -> dict:
    """Кладёт заявку и ждёт ответа. Возвращает разбор ответа демона."""
    req_dir, resp_dir = QUEUE / "req", QUEUE / "resp"
    if not req_dir.is_dir():
        raise NotInstalled(f"посредник не запущен: нет {req_dir}. Бутстрап: {BOOTSTRAP}")
    rid = f"{time.strftime('%H%M%S')}-{os.getpid()}-{os.urandom(3).hex()}"
    body = {"op": op, "args": args, "confirm": bool(confirm)}
    tmp = req_dir / f".{rid}.tmp"
    tmp.write_text(json.dumps(body, ensure_ascii=False), encoding="utf-8")
    tmp.rename(req_dir / f"{rid}.json")          # демон видит заявку целиком

    answer, deadline = resp_dir / f"{rid}.json", time.monotonic() + timeout
    while time.monotonic() < deadline:
        if answer.exists():
            out = json.loads(answer.read_text(encoding="utf-8"))
            try:
                answer.unlink()
            except OSError:
                pass
            return out
        time.sleep(0.2)
    return {"ok": False, "code": None, "stdout": "", "stderr": "",
            "rejected": f"посредник не ответил за {timeout:.0f} с"}


def main(argv: list[str]) -> int:
    argv = list(argv)
    confirm = "--confirm" in argv
    if confirm:
        argv.remove("--confirm")
    if not argv or argv[0] == "--ops":
        for name, spec in ops().items():
            mark = " [нужно согласие Ильи]" if spec.get("confirm") else ""
            print(f"{name}: {spec.get('desc', '')}{mark}")
            print(f"    параметры: {', '.join(spec.get('params', {})) or 'нет'}")
        return 0
    op, args = argv[0], {}
    for pair in argv[1:]:
        if "=" not in pair:
            print(f"аргумент должен быть вида ключ=значение: {pair}", file=sys.stderr)
            return 2
        k, v = pair.split("=", 1)
        args[k] = v.split(",") if "," in v else v
    r = call(op, confirm=confirm, **args)
    if r.get("rejected"):
        print(f"отказ: {r['rejected']}", file=sys.stderr)
        return 1
    sys.stdout.write(r.get("stdout", ""))
    sys.stderr.write(r.get("stderr", ""))
    return 0 if r.get("ok") else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main(sys.argv[1:]))
    except NotInstalled as e:
        print(e, file=sys.stderr)
        raise SystemExit(3)
