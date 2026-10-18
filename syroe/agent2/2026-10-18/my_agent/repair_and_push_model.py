#!/usr/bin/env python3
"""repair_and_push_model.py — довести перенос базовой модели до конца, устойчиво к обрывам.

Jetson перезагружается каждые 30 минут и рвёт SSH-соединения, поэтому:
  * любое обращение к Jetson делается с повторами;
  * уже верные куски не перекачиваются;
  * цикл повторяется, пока файл не совпадёт с эталоном;
  * заливка на сервер — только после полного совпадения MD5.

Длина каждого куска проверяется обязательно: при обрыве канала `dd | base64` отдаёт частичный
ответ, он декодируется и без проверки длины записывается как валидный (так появились 65 битых
кусков в первой попытке).
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


def jetson(tries=8):
    last = None
    for _ in range(tries):
        try:
            c = paramiko.SSHClient()
            c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            c.connect("172.30.4.31", username="user", password="[СЕКРЕТ_УДАЛЁН_2026-10-18]",
                      timeout=30, banner_timeout=60, auth_timeout=60)
            return c
        except Exception as e:
            last = e
            time.sleep(5)
    raise last


def ref_info():
    for _ in range(20):
        try:
            c = jetson()
            _, out, _ = c.exec_command(f"stat -c %s {SRC}; md5sum {SRC} | cut -d' ' -f1", timeout=180)
            p = out.read().decode().split()
            c.close()
            if len(p) >= 2:
                return int(p[0]), p[1]
        except Exception:
            pass
        time.sleep(8)
    raise SystemExit("не удалось получить эталон с Jetson")


def chunk_md5(idx):
    for _ in range(10):
        try:
            c = jetson()
            _, out, _ = c.exec_command(
                f"dd if={SRC} bs={CHUNK} skip={idx} count=1 2>/dev/null | md5sum | cut -d' ' -f1",
                timeout=240)
            r = out.read().decode().strip()
            c.close()
            if r:
                return idx, r
        except Exception:
            pass
        time.sleep(4)
    return idx, ""


def fetch_chunk(idx, expect):
    for _ in range(12):
        try:
            c = jetson()
            _, out, _ = c.exec_command(
                f"dd if={SRC} bs={CHUNK} skip={idx} count=1 2>/dev/null | base64 -w0", timeout=300)
            b = out.read().decode().strip()
            c.close()
            if not b:
                raise ValueError("пусто")
            d = base64.b64decode(b)
            if len(d) != expect:
                raise ValueError("короткий кусок %d/%d" % (len(d), expect))
            return idx, d
        except Exception:
            time.sleep(4)
    return idx, None


def local_md5(idx):
    with open(LOCAL, "rb") as f:
        f.seek(idx * CHUNK)
        return hashlib.md5(f.read(CHUNK)).hexdigest()


def main():
    size, ref_md5 = ref_info()
    n = (size + CHUNK - 1) // CHUNK
    if not os.path.exists(LOCAL) or os.path.getsize(LOCAL) != size:
        with open(LOCAL, "wb") as f:
            f.truncate(size)
    print("эталон: %d байт, md5=%s, кусков %d" % (size, ref_md5, n), flush=True)

    for round_no in range(12):
        with ThreadPoolExecutor(max_workers=WORKERS) as ex:
            remote = dict(ex.map(chunk_md5, range(n)))
        bad = [i for i in range(n) if not remote.get(i) or local_md5(i) != remote[i]]
        print("раунд %d: битых кусков %d" % (round_no + 1, len(bad)), flush=True)
        if not bad:
            break
        with open(LOCAL, "r+b") as f:
            def job(i):
                exp = CHUNK if i < n - 1 else size - i * CHUNK
                return fetch_chunk(i, exp)
            fixed = 0
            with ThreadPoolExecutor(max_workers=WORKERS) as ex:
                for idx, d in ex.map(job, bad):
                    if d is None:
                        continue
                    f.seek(idx * CHUNK)
                    f.write(d)
                    fixed += 1
            print("  перекачано %d из %d" % (fixed, len(bad)), flush=True)
    else:
        print("не удалось починить за 12 раундов", flush=True)
        print("REPAIR_INCOMPLETE")
        return

    h = hashlib.md5(open(LOCAL, "rb").read()).hexdigest()
    print("локально md5=%s — %s" % (h, "СОВПАДАЕТ" if h == ref_md5 else "НЕ СОВПАДАЕТ"), flush=True)
    if h != ref_md5:
        print("REPAIR_INCOMPLETE")
        return

    print("заливаю на сервер...", flush=True)
    c = ssh_srv.connect(key=True)
    c.exec_command("mkdir -p %s" % os.path.dirname(DST))[1].channel.recv_exit_status()
    c.exec_command("rm -f %s" % DST)[1].channel.recv_exit_status()
    with open(LOCAL, "rb") as fh:
        i = 0
        while True:
            d = fh.read(CHUNK)
            if not d:
                break
            b = base64.b64encode(d).decode()
            for _ in range(8):
                try:
                    _, out, _ = c.exec_command("printf '%%s' '%s' | base64 -d >> %s" % (b, DST),
                                               timeout=300)
                    out.read()
                    break
                except Exception:
                    time.sleep(3)
                    c = ssh_srv.connect(key=True)
            i += 1
            if i % 30 == 0:
                print("  залито %d/%d" % (i, n), flush=True)
    _, out, _ = c.exec_command("stat -c %%s %s; md5sum %s | cut -d' ' -f1" % (DST, DST), timeout=180)
    info = out.read().decode().split()
    c.close()
    ok = len(info) >= 2 and int(info[0]) == size and info[1] == ref_md5
    print("на сервере: %s байт, md5=%s — %s" % (info[0] if info else "?",
          info[1] if len(info) > 1 else "?", "OK" if ok else "НЕ СОВПАДАЕТ"), flush=True)
    print("REPAIR_DONE")


if __name__ == "__main__":
    main()
