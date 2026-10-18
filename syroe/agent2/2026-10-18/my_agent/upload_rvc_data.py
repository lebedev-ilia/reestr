#!/usr/bin/env python3
"""
upload_rvc_data.py — загрузить тестовые аудиоданные на сервер для smoke-теста RVC.

РАЗРЕШЕНО владельцем работ 2026-10-10. Фиксируется в my_agent/server_changes.md.
Куда: /home/user2/rvc/data/{target,input}/
"""
import sys
from pathlib import Path

sys.path.insert(0, "/Users/user/Desktop/Agent2/my_agent")
import ssh_srv

LOCAL = Path("/Users/user/Desktop/Agent2/my_agent/rvc_testdata/wav")
REMOTE = "/home/user2/rvc/data"

c = ssh_srv.connect(key=True)
sftp = c.open_sftp()
for sub in ("target", "input"):
    c.exec_command(f"mkdir -p {REMOTE}/{sub}")[1].channel.recv_exit_status()

total = 0
for f in sorted(LOCAL.glob("*.wav")):
    sub = "input" if f.name.startswith("yuri") else "target"
    remote = f"{REMOTE}/{sub}/{f.name}"
    sftp.put(str(f), remote)
    size = f.stat().st_size
    total += size
    print(f"загружено: {f.name} -> {remote} ({size/1024/1024:.1f} МБ)")

sftp.close()
print(f"итого загружено: {total/1024/1024:.1f} МБ")
_, out, _ = c.exec_command(f"ls -la {REMOTE}/target {REMOTE}/input; du -sh {REMOTE}")
print(out.read().decode())
c.close()
