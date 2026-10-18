#!/usr/bin/env python3
"""Посредник от root: файловая очередь и строгий белый список операций.

Зачем он есть. Агенту запрещено вызывать `sudo` собственным шлюзом
(NEVER_BASH в core/agent.py), и запрет снимать нельзя — он и есть
предохранитель. Поэтому привилегированное делает отдельный маленький демон
от root, а агент только кладёт заявку в очередь.

Чем эта конструкция держится, а не просто «даёт root окольным путём»:

1. Демон и белый список лежат в /opt/jarvis-root/, владелец root.
   Агент туда писать не может. Значит расширить себе список операций он не
   в состоянии — это делает Илья руками (граница раздела 8: доступы выдаёт он).
2. Заявка не содержит команды. Она называет ОПЕРАЦИЮ из списка и параметры.
   argv собирается здесь по шаблону, шелла нет вовсе (shell=False), поэтому
   ни `;`, ни `$(...)`, ни кавычки в параметре ничего не значат.
3. Каждый параметр обязан целиком совпасть со своей регуляркой из списка.
   Лишний параметр — отказ, недостающий — отказ.
4. Заявка принимается только от заданного uid и только обычным файлом
   (O_NOFOLLOW: симлинком чужой файл не подсунуть).
5. Всё, что пришло, — и принятое, и отклонённое, — попадает в журнал.

Чего он НЕ умеет и что здесь честно записано: демон не может спросить Илью.
Флаг confirm у опасных операций защищает от случайного вызова, но не от
намеренного — настоящее согласие спрашивает агент до заявки, и это дисциплина
плюс шлюз на стороне клиента, а не свойство этого файла.

Запуск: /opt/jarvis-root/daemon.py --uid 1000
Тестовый режим (не от root) берёт пути из JARVIS_ROOT_QUEUE / _ALLOW / _LOG.
"""

from __future__ import annotations

import argparse
import json
import os
import pwd
import re
import subprocess
import sys
import time
from pathlib import Path

QUEUE_DIR = Path("/run/jarvis-root")
ALLOW_FILE = Path("/opt/jarvis-root/allow.json")
LOG_FILE = Path("/var/log/jarvis-root.jsonl")

MAX_REQ_BYTES = 8192          # заявка — это несколько полей, а не данные
MAX_OUT_BYTES = 256 * 1024    # хвост вывода, чтобы ответ не разнесло
STEP_TIMEOUT = 180            # на один шаг операции
POLL_SEC = 0.4
ENV = {"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LC_ALL": "C.UTF-8",
       "DEBIAN_FRONTEND": "noninteractive"}

PLACEHOLDER = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)(\*?)\}")


class Reject(Exception):
    """Заявка отклонена. Текст уходит и в ответ, и в журнал."""


def _paths(test_mode: bool) -> tuple[Path, Path, Path]:
    """Пути. Подмена из окружения разрешена ТОЛЬКО не под root.

    Под root окружение задаёт тот, кто запускает демона, — а это уже root,
    и подменять ему нечего. Не под root это тестовый прогон самотеста.
    """
    if not test_mode:
        return QUEUE_DIR, ALLOW_FILE, LOG_FILE
    return (Path(os.environ.get("JARVIS_ROOT_QUEUE", QUEUE_DIR)),
            Path(os.environ.get("JARVIS_ROOT_ALLOW", ALLOW_FILE)),
            Path(os.environ.get("JARVIS_ROOT_LOG", LOG_FILE)))


def load_allow(allow_file: Path) -> dict:
    data = json.loads(allow_file.read_text(encoding="utf-8"))
    ops = data.get("ops")
    if not isinstance(ops, dict) or not ops:
        raise ValueError(f"{allow_file}: нет ни одной операции")
    for name, spec in ops.items():
        if not isinstance(spec.get("steps"), list) or not spec["steps"]:
            raise ValueError(f"{name}: нет шагов")
        for step in spec["steps"]:
            if not isinstance(step, list) or not step or not step[0].startswith("/"):
                raise ValueError(f"{name}: шаг должен быть argv с полным путём")
        for pat in spec.get("params", {}).values():
            re.compile(pat)   # кривая регулярка должна падать при старте
    return ops


def build_argv(spec: dict, args: dict) -> list[list[str]]:
    """Проверяет параметры и собирает argv каждого шага.

    Значение подставляется как ОДИН элемент argv (или как список для `имя*`).
    Строка склеивается только с уже проверенным регуляркой значением, и всё
    равно остаётся одним аргументом: шелла, который мог бы её разобрать, нет.
    """
    params = spec.get("params", {})
    want = {k.rstrip("*") for k in params}
    got = set(args)
    if got - want:
        raise Reject(f"лишние параметры: {sorted(got - want)}")
    if want - got:
        raise Reject(f"не хватает параметров: {sorted(want - got)}")

    checked: dict[str, list[str] | str] = {}
    for key, pat in params.items():
        name, many = key.rstrip("*"), key.endswith("*")
        value = args[name]
        values = value if many else [value]
        if many and (not isinstance(value, list) or not value or len(value) > 20):
            raise Reject(f"{name}: ожидался непустой список до 20 значений")
        for v in values:
            if not isinstance(v, str) or not re.fullmatch(pat, v):
                raise Reject(f"{name}: значение {v!r} не подходит под {pat}")
            # Отдельно от регулярки, потому что автор регулярки про это забывает
            # (и забыл: `^/(etc|...)/[A-Za-z0-9._/-]+$` пропускал
            # `/etc/../root/.ssh/id_rsa`, то есть чтение чего угодно от root).
            # Ни одной операции подъём по дереву не нужен, поэтому запрет общий.
            if ".." in v.split("/"):
                raise Reject(f"{name}: выход из каталога запрещён: {v!r}")
        checked[name] = values if many else values[0]

    steps: list[list[str]] = []
    for step in spec["steps"]:
        argv: list[str] = []
        for item in step:
            m = PLACEHOLDER.fullmatch(item)
            if m and m.group(2) == "*":
                many = checked.get(m.group(1))
                if not isinstance(many, list):
                    raise Reject(f"{m.group(1)}: ожидался список")
                argv.extend(many)
                continue
            argv.append(PLACEHOLDER.sub(
                lambda mm: _one(checked, mm.group(1)), item))
        steps.append(argv)
    return steps


def _one(checked: dict, name: str) -> str:
    value = checked.get(name)
    if not isinstance(value, str):
        raise Reject(f"{name}: ожидалось одно значение")
    return value


def read_request(path: Path, uid: int) -> dict:
    """Читает заявку, не идя по симлинкам и не доверяя размеру."""
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        st = os.fstat(fd)
        if st.st_uid != uid:
            raise Reject(f"заявка от чужого uid {st.st_uid}, ждём {uid}")
        if st.st_size > MAX_REQ_BYTES:
            raise Reject(f"заявка больше {MAX_REQ_BYTES} байт")
        raw = os.read(fd, MAX_REQ_BYTES + 1)
    finally:
        os.close(fd)
    try:
        req = json.loads(raw.decode("utf-8"))
    except Exception as e:
        raise Reject(f"не разбирается как json: {e}")
    if not isinstance(req, dict):
        raise Reject("заявка должна быть объектом")
    return req


def run_steps(steps: list[list[str]]) -> dict:
    out, err = [], []
    for argv in steps:
        try:
            p = subprocess.run(argv, capture_output=True, timeout=STEP_TIMEOUT,
                               env=ENV, shell=False, close_fds=True)
        except subprocess.TimeoutExpired:
            return {"ok": False, "code": -1, "stdout": "".join(out),
                    "stderr": "".join(err) + f"\nшаг завис дольше {STEP_TIMEOUT} с: {argv}"}
        except OSError as e:
            return {"ok": False, "code": -1, "stdout": "".join(out),
                    "stderr": "".join(err) + f"\nне запустилось: {e}"}
        out.append(p.stdout.decode("utf-8", "replace"))
        err.append(p.stderr.decode("utf-8", "replace"))
        if p.returncode != 0:
            return {"ok": False, "code": p.returncode,
                    "stdout": "".join(out)[-MAX_OUT_BYTES:],
                    "stderr": "".join(err)[-MAX_OUT_BYTES:]}
    return {"ok": True, "code": 0, "stdout": "".join(out)[-MAX_OUT_BYTES:],
            "stderr": "".join(err)[-MAX_OUT_BYTES:]}


def handle(req: dict, ops: dict) -> dict:
    op = req.get("op")
    if not isinstance(op, str) or op not in ops:
        raise Reject(f"операция {op!r} не из белого списка")
    spec = ops[op]
    args = req.get("args", {})
    if not isinstance(args, dict):
        raise Reject("args должен быть объектом")
    if spec.get("confirm") and req.get("confirm") is not True:
        raise Reject(f"{op}: операция требует согласия Ильи (confirm)")
    steps = build_argv(spec, args)
    return run_steps(steps)


class Service:
    def __init__(self, uid: int, test_mode: bool = False, allow_file: Path = None):
        self.queue, self.allow_file, self.log_file = _paths(test_mode)
        if allow_file is not None:          # --check до установки, из репозитория
            self.allow_file = Path(allow_file)
        self.uid = uid
        self.test_mode = test_mode
        self.req_dir = self.queue / "req"
        self.resp_dir = self.queue / "resp"
        self.ops = load_allow(self.allow_file)

    def prepare(self) -> None:
        """Каталоги очереди: писать заявки может uid, читать чужое — нет."""
        self.queue.mkdir(parents=True, exist_ok=True)
        for d, mode in ((self.req_dir, 0o730), (self.resp_dir, 0o750)):
            d.mkdir(exist_ok=True)
            if not self.test_mode:
                os.chown(d, 0, pwd.getpwuid(self.uid).pw_gid)
                os.chmod(d, mode)
        if not self.log_file.exists():
            self.log_file.parent.mkdir(parents=True, exist_ok=True)
            self.log_file.touch(mode=0o640)

    def log(self, row: dict) -> None:
        row["ts"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        with self.log_file.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")

    def answer(self, rid: str, body: dict) -> None:
        tmp = self.resp_dir / f".{rid}.tmp"
        tmp.write_text(json.dumps(body, ensure_ascii=False), encoding="utf-8")
        if not self.test_mode:
            os.chown(tmp, 0, pwd.getpwuid(self.uid).pw_gid)
            os.chmod(tmp, 0o640)
        tmp.rename(self.resp_dir / f"{rid}.json")

    def process(self, path: Path) -> dict:
        """Одна заявка: разобрать, выполнить, ответить, записать в журнал."""
        rid, req, started = path.stem, {}, time.monotonic()
        try:
            req = read_request(path, self.uid)
            body = handle(req, self.ops)
        except Reject as e:
            body = {"ok": False, "code": None, "stdout": "", "stderr": "",
                    "rejected": str(e)}
        except Exception as e:                        # чужая беда не роняет демона
            body = {"ok": False, "code": None, "stdout": "", "stderr": "",
                    "rejected": f"внутренняя ошибка: {e!r}"}
        body["op"] = req.get("op")
        body["sec"] = round(time.monotonic() - started, 2)
        try:
            path.unlink()
        except OSError:
            pass
        self.answer(rid, body)
        self.log({"id": rid, "op": req.get("op"), "args": req.get("args"),
                  "confirm": bool(req.get("confirm")),
                  "verdict": "выполнено" if body["ok"] else (
                      body.get("rejected") or f"код {body.get('code')}"),
                  "sec": body["sec"]})
        return body

    def tick(self) -> int:
        done = 0
        for path in sorted(self.req_dir.glob("*.json")):
            self.process(path)
            done += 1
        return done

    def serve(self) -> None:
        self.prepare()
        self.log({"id": "-", "op": "start", "verdict": f"операций: {len(self.ops)}"})
        while True:
            try:
                if not self.tick():
                    time.sleep(POLL_SEC)
            except Exception as e:
                self.log({"id": "-", "op": "loop", "verdict": f"сбой: {e!r}"})
                time.sleep(2)


def main() -> int:
    ap = argparse.ArgumentParser(description="Посредник от root")
    ap.add_argument("--uid", type=int, default=1000, help="кому разрешено класть заявки")
    ap.add_argument("--check", action="store_true", help="проверить белый список и выйти")
    ap.add_argument("--allow", help="путь к белому списку (для --check до установки)")
    a = ap.parse_args()
    test_mode = os.geteuid() != 0
    if test_mode and not a.check:
        print("демон должен работать от root", file=sys.stderr)
        return 2
    # Проверять список до установки надо там, где он лежит СЕЙЧАС, — иначе
    # бутстрап падал бы на своём первом шаге, ещё ничего не скопировав.
    allow = Path(a.allow) if a.allow else (
        Path(__file__).with_name("allow.json") if a.check and not ALLOW_FILE.exists()
        else None)
    svc = Service(a.uid, test_mode=test_mode, allow_file=allow)
    if a.check:
        print(f"белый список в порядке, операций: {len(svc.ops)}")
        return 0
    svc.serve()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
