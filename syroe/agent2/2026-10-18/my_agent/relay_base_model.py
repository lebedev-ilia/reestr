#!/usr/bin/env python3
"""relay_base_model.py — перенести базовую модель Заказчика с Jetson на сервер.

Зачем: сравнить базовую и нашу модели на одной выборке. На Jetson инференс идёт на CPU
(6,5 с/кадр), поэтому 1062 кадра там считались бы ~2 часа и мешали бы боевому сервису.
На сервере с RTX 5080 то же самое займёт минуты.

Схема: Jetson → (base64 кусками) → локально → (base64 кусками) → сервер.
Обычный SFTP не годится: канал рвётся примерно на 7 МБ.
"""
import base64
import hashlib
import os
import sys
import time

sys.path.insert(0, "/Users/user/Desktop/Agent2/my_agent")
import paramiko  # noqa: E402
import ssh_srv  # noqa: E402

SRC = "/home/user/work/waste_up/yolov5/best2023_08.pt"
LOCAL = "/Users/user/Desktop/Agent2/files/jetson/base_model.pt"
DST = "/home/user2/base_model/best2023_08.pt"
CHUNK = 1024 * 1024
os.makedirs(os.path.dirname(LOCAL), exist_ok=True)


def jetson():
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect("172.30.4.31", username="user", password="[СЕКРЕТ_УДАЛЁН_2026-10-18]",
              timeout=30, banner_timeout=45, auth_timeout=45)
    return c


def jetson_size_md5():
    c = jetson()
    _, out, _ = c.exec_command(f"stat -c %s {SRC}; md5sum {SRC} | cut -d' ' -f1", timeout=120)
    parts = out.read().decode().split()
    c.close()
    return int(parts[0]), parts[1]


def jetson_chunk(idx):
    c = jetson()
    _, out, _ = c.exec_command(
        f"dd if={SRC} bs={CHUNK} skip={idx} count=1 2>/dev/null | base64 -w0", timeout=300)
    data = out.read().decode().strip()
    c.close()
    return base64.b64decode(data) if data else b""


size, md5 = jetson_size_md5()
print(f"источник: {size} байт ({size/1024/1024:.1f} МБ), md5={md5}", flush=True)

n = (size + CHUNK - 1) // CHUNK
have = os.path.getsize(LOCAL) if os.path.exists(LOCAL) else 0
start = have // CHUNK
print(f"кусков: {n}, начинаю с {start}", flush=True)

mode = "ab" if start else "wb"
with open(LOCAL, mode) as f:
    for i in range(start, n):
        for attempt in range(6):
            try:
                d = jetson_chunk(i)
                if not d and i < n - 1:
                    raise ValueError("пусто")
                f.write(d)
                f.flush()
                if (i + 1) % 20 == 0 or i == n - 1:
                    print(f"  {i+1}/{n} ({os.path.getsize(LOCAL)/1024/1024:.1f} МБ)", flush=True)
                break
            except Exception as e:
                print(f"  кусок {i+1} сбой ({type(e).__name__}), повтор", flush=True)
                time.sleep(3)
        else:
            raise SystemExit(f"не удалось скачать кусок {i+1}")

got = os.path.getsize(LOCAL)
h = hashlib.md5(open(LOCAL, "rb").read()).hexdigest()
print(f"локально: {got} байт, md5={h} — {'СОВПАДАЕТ' if (got == size and h == md5) else 'НЕ СОВПАДАЕТ'}",
      flush=True)

if got == size and h == md5:
    c = ssh_srv.connect(key=True)
    c.exec_command(f"mkdir -p {os.path.dirname(DST)}")[1].channel.recv_exit_status()
    # заливаем кусками: канал тоже рвётся на больших объёмах
    c.exec_command(f"rm -f {DST}")[1].channel.recv_exit_status()
    with open(LOCAL, "rb") as f:
        i = 0
        while True:
            d = f.read(CHUNK)
            if not d:
                break
            b = base64.b64encode(d).decode()
            for attempt in range(6):
                try:
                    _, out, _ = c.exec_command(
                        f"printf '%s' '{b}' | base64 -d >> {DST}; stat -c %s {DST}",
                        timeout=300)
                    out.read()
                    break
                except Exception as e:
                    print(f"  заливка куска {i+1} сбой ({type(e).__name__}), повтор", flush=True)
                    time.sleep(3)
                    c = ssh_srv.connect(key=True)
            i += 1
            if i % 20 == 0:
                print(f"  залито {i} кусков", flush=True)
    _, out, _ = c.exec_command(f"stat -c %s {DST}; md5sum {DST} | cut -d' ' -f1", timeout=180)
    info = out.read().decode().split()
    c.close()
    ok = len(info) >= 2 and int(info[0]) == size and info[1] == md5
    print(f"на сервере: {info[0] if info else '?'} байт, md5={info[1] if len(info) > 1 else '?'} — "
          f"{'OK' if ok else 'НЕ СОВПАДАЕТ'}", flush=True)
print("RELAY_DONE")
