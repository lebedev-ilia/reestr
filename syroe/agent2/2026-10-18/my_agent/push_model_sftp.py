#!/usr/bin/env python3
"""push_model_sftp.py — залить базовую модель на сервер, устойчиво к обрывам.

Установлено экспериментально: **любое одиночное соединение с сервером рвётся после ~7 МБ**
(проверено и на SFTP, и на netcat напрямую Jetson→сервер). Поэтому передаём порциями по ~3 МБ,
каждый раз переподключаясь и продолжая с текущего размера файла на сервере.

Скорость канала ~30–60 КБ/с, поэтому на 165 МБ уходит около часа. Скрипт можно прерывать:
при повторном запуске он продолжит с места обрыва.
"""
import base64
import hashlib
import os
import sys
import time

sys.path.insert(0, "/Users/user/Desktop/Agent2/my_agent")
import ssh_srv  # noqa: E402

LOCAL = "/Users/user/Desktop/Agent2/files/jetson/base_model.pt"
DST = "/home/user2/base_model/best2023_08.pt"
PORTION = 3 * 1024 * 1024


def remote_size():
    c = ssh_srv.connect(key=True)
    _, out, _ = c.exec_command(f"stat -c %s {DST} 2>/dev/null || echo 0", timeout=60)
    n = int(out.read().decode().strip() or 0)
    c.close()
    return n


def main():
    size = os.path.getsize(LOCAL)
    md5_local = hashlib.md5(open(LOCAL, "rb").read()).hexdigest()
    print(f"локально: {size} байт, md5={md5_local}", flush=True)

    pos = remote_size()
    print(f"на сервере уже: {pos} байт ({pos/1048576:.1f} МБ)", flush=True)
    t0 = time.time()
    stall = 0

    while pos < size:
        try:
            c = ssh_srv.connect(key=True)
            sftp = c.open_sftp()
            with open(LOCAL, "rb") as f:
                f.seek(pos)
                data = f.read(PORTION)
            rf = sftp.open(DST, "r+b" if pos else "wb")
            rf.seek(pos)
            rf.write(data)
            rf.close()
            new = sftp.stat(DST).st_size
            sftp.close()
            c.close()
            if new <= pos:
                raise IOError("размер не вырос")
            pos = new
            stall = 0
            sp = pos / (time.time() - t0)
            left = (size - pos) / sp if sp > 0 else 0
            print(f"  {pos/1048576:.1f}/{size/1048576:.1f} МБ, "
                  f"{sp/1024:.0f} КБ/с, осталось ~{left/60:.0f} мин", flush=True)
        except Exception as e:
            stall += 1
            print(f"  обрыв ({type(e).__name__}), продолжаю с {pos/1048576:.1f} МБ", flush=True)
            time.sleep(5)
            if stall > 40:
                print("слишком много обрывов подряд — выход", flush=True)
                print("PUSH_INCOMPLETE")
                return

    c = ssh_srv.connect(key=True)
    _, out, _ = c.exec_command(f"md5sum {DST} | cut -d' ' -f1; stat -c %s {DST}", timeout=300)
    parts = out.read().decode().split()
    c.close()
    ok = len(parts) >= 2 and parts[0] == md5_local and int(parts[1]) == size
    print(f"на сервере: md5={parts[0] if parts else '?'} — {'СОВПАДАЕТ' if ok else 'НЕ СОВПАДАЕТ'}",
          flush=True)
    print("PUSH_DONE" if ok else "PUSH_INCOMPLETE")


if __name__ == "__main__":
    main()
