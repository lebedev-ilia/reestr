#!/usr/bin/env python3
"""Автоматизация входа в Telegram для telegram-mcp.

Драйвит session_string_generator.py --phone через pty:
  - ждёт файл /tmp/tg_phone.txt  -> отправляет номер
  - ждёт файл /tmp/tg_code.txt   -> отправляет код
  - ждёт файл /tmp/tg_2fa.txt    -> отправляет облачный пароль (если нужен)
Пишет весь вывод в /tmp/tg_login.log, статус в /tmp/tg_status.txt,
сессию в /tmp/tg_session.txt и в .env.
"""
import os
import pty
import re
import select
import time

REPO = "/Users/user/Desktop/Agent2/telegram-mcp"
PY = REPO + "/.venv/bin/python"
LOG = "/tmp/tg_login.log"
STATUS = "/tmp/tg_status.txt"
PHONE_FILE = "/tmp/tg_phone.txt"
CODE_FILE = "/tmp/tg_code.txt"
PASS_FILE = "/tmp/tg_2fa.txt"
SESSION_FILE = "/tmp/tg_session.txt"


def rf(path):
    try:
        with open(path) as f:
            return f.read().strip()
    except FileNotFoundError:
        return ""


def status(text):
    with open(STATUS, "w") as f:
        f.write(text + "\n")


with open(LOG, "w", buffering=1) as log:
    log.write("driver start\n")
    status("starting")

    pid, fd = pty.fork()
    if pid == 0:  # child
        os.chdir(REPO)
        os.execv(PY, [PY, "session_string_generator.py", "--phone"])
        os._exit(1)

    buf = ""
    sent = {"label": False, "phone": False, "code": False, "pass": False, "done": False}
    deadline = time.time() + 900
    pending_code = False
    pending_pass = False

    while time.time() < deadline:
        r, _, _ = select.select([fd], [], [], 1.0)
        if r:
            try:
                data = os.read(fd, 4096)
            except OSError:
                break
            if not data:
                break
            text = data.decode("utf-8", "replace")
            buf += text
            log.write(text)

        low = buf.lower()
        if not sent["label"] and "account label" in low:
            os.write(fd, b"\n")
            sent["label"] = True
            pending_code = False
        elif not sent["phone"] and "please enter your phone" in low:
            phone = rf(PHONE_FILE)
            if phone:
                os.write(fd, (phone + "\n").encode())
                sent["phone"] = True
                pending_code = True
                status("waiting_code")
            else:
                status("waiting_phone")
        elif pending_code and not sent["code"] and "please enter the code" in low:
            code = rf(CODE_FILE)
            if code:
                os.write(fd, (code + "\n").encode())
                sent["code"] = True
                pending_code = False
                status("code_sent")
            else:
                status("waiting_code")
        elif "two-factor authentication enabled" in low and not sent["pass"]:
            pending_pass = True
            pw = rf(PASS_FILE)
            if pw:
                os.write(fd, (pw + "\n").encode())
                sent["pass"] = True
                pending_pass = False
                status("pass_sent")
            else:
                status("waiting_2fa")
        elif not sent["done"] and "would you like to automatically update" in low:
            os.write(fd, b"n\n")
            sent["done"] = True
            status("done")

    # склеить и вытащить сессию
    m = re.search(r"TELEGRAM_SESSION_STRING(?:_[A-Z]+)?=(\S+)", buf)
    if m:
        sess = m.group(1)
        with open(SESSION_FILE, "w") as f:
            f.write(sess)
        env = REPO + "/.env"
        with open(env) as f:
            content = f.read()
        content = re.sub(r"(?m)^#?\s*TELEGRAM_SESSION_STRING.*$", "", content).rstrip() + "\n"
        content += f"TELEGRAM_SESSION_STRING={sess}\n"
        with open(env, "w") as f:
            f.write(content)
        status("SUCCESS")
        log.write("\n[driver] session saved to .env\n")
    else:
        status("FAILED")
        log.write("\n[driver] no session string found\n")
