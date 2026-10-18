#!/usr/bin/env python3
"""
ssh_srv.py — доступ к рабочему серверу 172.30.4.134 (user2) без expect/pty.

Зачем: expect упирается в лимит pty, а пароль в командной строке небезопасен.
Скрипт один раз ставит публичный ключ на сервер, дальше работает по ключу.

Использование:
  ssh_srv.py setup            # поставить наш id_ed25519.pub в authorized_keys (нужен пароль)
  ssh_srv.py run "<команда>"  # выполнить команду на сервере (по ключу)
  ssh_srv.py put <local> <remote>
  ssh_srv.py get <remote> <local>

Пароль берётся из my_agent/access_notes.md, если не задан SRV_PASSWORD.
"""
import os
import re
import sys
from pathlib import Path

import paramiko

HOST = os.environ.get("SRV_HOST", "172.30.4.134")
USER = os.environ.get("SRV_USER", "user2")
PORT = int(os.environ.get("SRV_PORT", "22"))
NOTES = Path("/Users/user/Desktop/Agent2/my_agent/access_notes.md")
KEY = Path("/Users/user/Desktop/Agent2/.ssh/agent2_srv")
PUBKEY = Path("/Users/user/Desktop/Agent2/.ssh/agent2_srv.pub")


def password():
    if os.environ.get("SRV_PASSWORD"):
        return os.environ["SRV_PASSWORD"]
    if NOTES.exists():
        m = re.search(r"pass:\s*(\S+)", NOTES.read_text(encoding="utf-8"))
        if m:
            return m.group(1)
    sys.exit("Пароль не найден: [СЕКРЕТ_УДАЛЁН_2026-10-18] SRV_PASSWORD")


def connect(pw=None, key=False):
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    kw = dict(hostname=HOST, port=PORT, username=USER, timeout=20, banner_timeout=25, auth_timeout=25)
    if key:
        kw["key_filename"] = str(KEY)
        kw["look_for_keys"] = True
        kw["allow_agent"] = True
    else:
        kw["password"] = pw or password()
        kw["look_for_keys"] = False
        kw["allow_agent"] = False
    c.connect(**kw)
    return c


def do_setup():
    pub = PUBKEY.read_text(encoding="utf-8").strip()
    c = connect()
    try:
        cmd = ("mkdir -p ~/.ssh && chmod 700 ~/.ssh && touch ~/.ssh/authorized_keys && "
               "chmod 600 ~/.ssh/authorized_keys && "
               f"grep -qF '{pub}' ~/.ssh/authorized_keys || echo '{pub}' >> ~/.ssh/authorized_keys")
        _, out, err = c.exec_command(cmd)
        print("setup:", out.read().decode().strip(), err.read().decode().strip())
    finally:
        c.close()
    try:
        c2 = connect(key=True)
        _, out, _ = c2.exec_command("whoami; hostname")
        print("проверка по ключу:", " ".join(out.read().decode().split()))
        c2.close()
        print("OK: вход по ключу работает")
    except Exception as e:
        print("ВНИМАНИЕ: вход по ключу не сработал:", type(e).__name__, e)


def do_run(cmd, key=True):
    c = connect(key=key)
    try:
        _, out, err = c.exec_command(cmd, timeout=300)
        o = out.read().decode(errors="replace")
        e = err.read().decode(errors="replace")
        print(o, end="")
        if e.strip():
            print("--- stderr ---\n" + e, file=sys.stderr)
    finally:
        c.close()


def do_put(local, remote):
    c = connect(key=True)
    try:
        sftp = c.open_sftp()
        sftp.put(local, remote)
        st = sftp.stat(remote)
        print(f"put OK: {local} -> {remote} ({st.st_size} байт)")
        sftp.close()
    finally:
        c.close()


def do_get(remote, local):
    c = connect(key=True)
    try:
        sftp = c.open_sftp()
        sftp.get(remote, local)
        print(f"get OK: {remote} -> {local} ({Path(local).stat().st_size} байт)")
        sftp.close()
    finally:
        c.close()


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    action = sys.argv[1]
    if action == "setup":
        do_setup()
    elif action == "run":
        do_run(sys.argv[2])
    elif action == "run-pass":
        do_run(sys.argv[2], key=False)
    elif action == "put":
        do_put(sys.argv[2], sys.argv[3])
    elif action == "get":
        do_get(sys.argv[2], sys.argv[3])
    else:
        sys.exit(__doc__)
