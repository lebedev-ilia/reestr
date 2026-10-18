#!/usr/bin/env python3
"""fetch_onnx_chunked.py — скачать файл с сервера Заказчика кусками через base64.

SFTP-канал рвётся на ~7 МБ, поэтому файл читается на сервере через dd и передаётся
в base64 по exec-каналу. После сборки проверяется размер и MD5.
Затем файл заливается на Jetson.
"""
import base64
import hashlib
import os
import sys
import time

sys.path.insert(0, "/Users/user/Desktop/Agent2/my_agent")
import paramiko  # noqa: E402
import ssh_srv  # noqa: E402

SRC = "/home/user2/baseline/runs/waste_up_blocksplit/weights/best.onnx"
LOCAL = "/Users/user/Desktop/Agent2/files/jetson/yolo11n_waste_up.onnx"
JETSON_DST = "/home/user/agent2/models/yolo11n_waste_up.onnx"
CHUNK = 512 * 1024
os.makedirs(os.path.dirname(LOCAL), exist_ok=True)

# 1. размер и md5 источника
c = ssh_srv.connect(key=True)
_, out, _ = c.exec_command(f"stat -c %s {SRC}; md5sum {SRC} | cut -d' ' -f1", timeout=60)
info = out.read().decode().split()
size, md5 = int(info[0]), info[1]
print(f"источник: {size} байт, md5={md5}", flush=True)

# 2. качаем кусками
n_chunks = (size + CHUNK - 1) // CHUNK
with open(LOCAL, "wb") as f:
    for i in range(n_chunks):
        for attempt in range(5):
            try:
                _, out, _ = c.exec_command(
                    f"dd if={SRC} bs={CHUNK} skip={i} count=1 2>/dev/null | base64 -w0",
                    timeout=180)
                b = out.read().decode().strip()
                data = base64.b64decode(b)
                if len(data) == 0 and i < n_chunks - 1:
                    raise ValueError("пустой кусок")
                f.write(data)
                print(f"  кусок {i+1}/{n_chunks}: {len(data)} байт", flush=True)
                break
            except Exception as e:
                print(f"  кусок {i+1} сбой ({type(e).__name__}), повтор", flush=True)
                time.sleep(3)
                c = ssh_srv.connect(key=True)
        else:
            raise SystemExit(f"не удалось скачать кусок {i+1}")
c.close()

# 3. проверка
got = os.path.getsize(LOCAL)
h = hashlib.md5(open(LOCAL, "rb").read()).hexdigest()
print(f"скачано: {got} байт, md5={h}", flush=True)
print("СОВПАДАЕТ" if (got == size and h == md5) else "НЕ СОВПАДАЕТ", flush=True)

if got == size and h == md5:
    # 4. заливаем на Jetson
    for attempt in range(5):
        try:
            j = paramiko.SSHClient()
            j.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            j.connect("172.30.4.31", username="user", password="[СЕКРЕТ_УДАЛЁН_2026-10-18]",
                      timeout=30, banner_timeout=40, auth_timeout=40)
            j.exec_command("mkdir -p ~/agent2/models")[1].channel.recv_exit_status()
            sftp = j.open_sftp()
            sftp.put(LOCAL, JETSON_DST)
            st = sftp.stat(JETSON_DST)
            sftp.close()
            j.close()
            print(f"на Jetson: {st.st_size} байт — "
                  f"{'OK' if st.st_size == size else 'НЕПОЛНО'} ", flush=True)
            break
        except Exception as e:
            print(f"  заливка сбой ({type(e).__name__}), повтор", flush=True)
            time.sleep(5)
print("CHUNKED_DONE")
