#!/usr/bin/env python3
"""fix_model_blocks.py — перезаписать повреждённые блоки модели на сервере.

Причина повреждения: передача через netcat с дописыванием (`>>`) не переносит частичные куски —
если соединение рвалось, сервер получал кусок не целиком, и следующие куски вставали со смещением.
Пострадал диапазон 40–100 МБ.

Исправление: блоки берутся из **локальной эталонной копии** (MD5 сверен с Jetson) и записываются
по ТОЧНЫМ смещениям через SFTP (`seek`), а не дописыванием. В конце — полная проверка MD5.
"""
import hashlib
import os
import sys
import time

sys.path.insert(0, "/Users/user/Desktop/Agent2/my_agent")
import ssh_srv  # noqa: E402

LOCAL = "/Users/user/Desktop/Agent2/files/jetson/base_model.pt"
DST = "/home/user2/base_model/best2023_08.pt"
BLOCK = 1024 * 1024
MD5 = "8aae75556f749b173475f6115d27d359"
PORTION = 2 * 1024 * 1024

bad = [int(l) for l in open("/tmp/bad_blocks.txt") if l.strip()]
print(f"повреждённых блоков: {len(bad)} (диапазон {bad[0]}..{bad[-1]})", flush=True)

# непрерывный диапазон от первого до последнего повреждённого блока
first, last = bad[0], bad[-1]
start = first * BLOCK
end = min((last + 1) * BLOCK, os.path.getsize(LOCAL))
print(f"перезаписываю байты {start/1048576:.0f}..{end/1048576:.0f} МБ "
      f"({(end-start)/1048576:.1f} МБ)", flush=True)

pos = start
t0 = time.time()
with open(LOCAL, "rb") as f:
    while pos < end:
        try:
            f.seek(pos)
            data = f.read(min(PORTION, end - pos))
            c = ssh_srv.connect(key=True)
            sftp = c.open_sftp()
            rf = sftp.open(DST, "r+b")
            rf.seek(pos)
            rf.write(data)
            rf.close()
            sftp.close()
            c.close()
            pos += len(data)
            sp = (pos - start) / max(1, time.time() - t0)
            left = (end - pos) / sp if sp > 0 else 0
            print(f"  {pos/1048576:.1f}/{end/1048576:.1f} МБ, {sp/1024:.0f} КБ/с, "
                  f"осталось ~{left/60:.1f} мин", flush=True)
        except Exception as e:
            print(f"  обрыв ({type(e).__name__}), продолжаю с {pos/1048576:.1f} МБ", flush=True)
            time.sleep(4)

c = ssh_srv.connect(key=True)
_, out, _ = c.exec_command(f"md5sum {DST} | cut -d' ' -f1; stat -c %s {DST}", timeout=600)
parts = out.read().decode().split()
c.close()
ok = len(parts) >= 2 and parts[0] == MD5
print(f"итог: {parts[1] if len(parts) > 1 else '?'} байт, md5={parts[0] if parts else '?'}", flush=True)
print("MODEL_OK" if ok else "MODEL_STILL_BAD")
