#!/usr/bin/env python3
import json
import os
import subprocess
import sys

REPO = "/Users/user/Desktop/Agent2/telegram-mcp"
env = dict(os.environ)
env.update({
    "TELEGRAM_API_ID": "[СЕКРЕТ_УДАЛЁН_2026-10-18]",
    "TELEGRAM_API_HASH": "[СЕКРЕТ_УДАЛЁН_2026-10-18]",
    "TELEGRAM_SESSION_STRING": open("/tmp/tg_session.txt").read().strip(),
    "TELEGRAM_EXPOSED_TOOLS": "read-only",
})

p = subprocess.Popen(
    [REPO + "/.venv/bin/telegram-mcp"],
    cwd=REPO, env=env,
    stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    text=True, bufsize=1,
)


def send(obj):
    p.stdin.write(json.dumps(obj) + "\n")
    p.stdin.flush()


def read_reply(timeout_lines=1):
    line = p.stdout.readline()
    return line


send({"jsonrpc": "2.0", "id": 1, "method": "initialize",
      "params": {"protocolVersion": "2024-11-05",
                 "capabilities": {},
                 "clientInfo": {"name": "test", "version": "1"}}})
print("INIT:", read_reply().strip()[:200])

send({"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}})
send({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
line = read_reply()
try:
    tools = json.loads(line)["result"]["tools"]
    print("TOOLS COUNT:", len(tools))
    names = [t["name"] for t in tools]
    print("HAS get_messages:", any("message" in n for n in names))
    print("SAMPLE:", ", ".join(names[:15]))
except Exception as e:
    print("tools parse err:", e, line[:300])

p.terminate()
try:
    err = p.stderr.read()
    if err.strip():
        print("STDERR:", err[:800])
except Exception:
    pass
