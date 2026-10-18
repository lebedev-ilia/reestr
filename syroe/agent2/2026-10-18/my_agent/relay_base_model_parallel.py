#!/usr/bin/env python3
"""relay_base_model_parallel.py — перенести базовую модель с Jetson на сервер быстрее.

Отличие от последовательной версии: куски качаются в несколько соединений параллельно
(узкое место — накладные расходы на установку SSH-сессии с медленным Jetson, а не канал).
Докачка: уже скачанное не перекачивается, берутся только недостающие куски.

Схема: Jetson → (base64 куски, параллельно) → локально → (base64 куски) → сервер.
"""
import base64
import hashlib
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, "/Users/user/Desktop/Agent2/my_agent")
import paramiko  # noqa: E402
import ssh_srv  # noqa: E402

SRC = "/home/user/work/waste_up/yolov5/best2023_08.pt"
LOCAL = "/Users/user/Desktop/Agent2/files/jetson/base_model.pt"
DST = "/home/user2/base_model/best2023_08.pt"
CHUNK = 1024 * 1024
WORKERS = 6


def jetson():
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect("172.30.4.31", username="user", password="[СЕКРЕТ_УДАЛЁН_2026-10-18]",
              timeout=30, banner_timeout=45, auth_timeout=45)
    return c


def src_info():
    c = jetson()
    _, out, _ = c.exec_command(f"stat -c %s {SRC}; md5sum {SRC} | cut -d' ' -f1", timeout=120)
    parts = out.read().decode().split()
    c.close()
    return int(parts[0]), parts[1]


def fetch_chunk(idx):
    for attempt in range(5):
        try:
            c = jetson()
            _, out, _ = c.exec_command(
                f"dd if={SRC} bs={CHUNK} skip={idx} count=1 2>/dev/null | base64 -w0", timeout=300)
            b = out.read().decode().strip()
            c.close()
            if not b:
                raise ValueError("пусто")
            return idx, base64.b64decode(b)
        except Exception:
            time.sleep(2)
    return idx, None


def main():
    size, md5 = src_info()
    n = (size + CHUNK - 1) // CHUNK
    have = os.path.getsize(LOCAL) if os.path.exists(LOCAL) else 0
    done = have // CHUNK
    print(f"источник: {size} байт ({size/1024/1024:.1f} МБ), кусков {n}, уже есть {done}", flush=True)

    mode = "r+b" if os.path.exists(LOCAL) and have >= CHUNK else "wb"
    if mode == "wb":
        done = 0
    f = open(LOCAL, mode)
    if mode == "r+b":
        f.truncate(size)

    t0 = time.time()
    todo = list(range(done, n))
    completed = 0
    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        for idx, data in ex.map(fetch_chunk, todo):
            if data is None:
                print(f"  кусок {idx} не скачался", flush=True)
                continue
            f.seek(idx * CHUNK)
            f.write(data)
            completed += 1
            if completed % 15 == 0:
                el = time.time() - t0
                got = completed * CHUNK
                print(f"  {completed}/{len(todo)} кусков, {got/1024/1024:.1f} МБ, "
                      f"{got/el/1024:.0f} КБ/с", flush=True)
    f.close()

    got = os.path.getsize(LOCAL)
    h = hashlib.md5(open(LOCAL, "rb").read()).hexdigest()
    print(f"локально: {got} байт, md5={h} — {'СОВПАДАЕТ' if got == size and h == md5 else 'НЕ СОВПАДАЕТ'}",
          flush=True)
    if got != size or h != md5:
        print("RELAY_INCOMPLETE")
        return

    # заливка на сервер кусками (канал тоже рвётся на больших объёмах)
    c = ssh_srv.connect(key=True)
    c.exec_command(f"mkdir -p {os.path.dirname(DST)}")[1].channel.recv_exit_status()
    c.exec_command(f"rm -f {DST}")[1].channel.recv_exit_status()
    with open(LOCAL, "rb") as fh:
        i = 0
        while True:
            d = fh.read(CHUNK)
            if not d:
                break
            b = base64.b64encode(d).decode()
            for attempt in range(6):
                try:
                    _, out, _ = c.exec_command(f"printf '%s' '{b}' | base64 -d >> {DST}", timeout=300)
                    out.read()
                    break
                except Exception:
                    time.sleep(3)
                    c = ssh_srv.connect(key=True)
            i += 1
            if i % 30 == 0:
                print(f"  залито {i} кусков", flush=True)
    _, out, _ = c.exec_command(f"stat -c %s {DST}; md5sum {DST} | cut -d' ' -f1", timeout=180)
    info = out.read().decode().split()
    c.close()
    ok = len(info) >= 2 and int(info[0]) == size and info[1] == md5
    print(f"на сервере: {info[0] if info else '?'} байт, md5={info[1] if len(info) > 1 else '?'} — "
          f"{'OK' if ok else 'НЕ СОВПАДАЕТ'}", flush=True)
    print("RELAY_DONE")


if __name__ == "__main__":
    main()
