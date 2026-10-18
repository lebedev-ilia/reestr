#!/usr/bin/env python3
"""fetch_rvc_model.py — докачать модель RVC с сервера заказчика с возобновлением.

Туннель рвёт длинные передачи, поэтому качаем кусками с дозаписью: каждый повтор
продолжает с текущего размера файла, а не с нуля. Скрипт работает до победного конца.
"""
import os
import sys
import time

sys.path.insert(0, "/Users/user/Desktop/Agent2/my_agent")
import ssh_srv  # noqa: E402

R = "/home/user2/rvc/RVC"
JOBS = [
    (f"{R}/assets/weights/brat_b4_e50_s700.pth",
     "/Users/user/Desktop/Agent2/files/rvc_model/brat_b4_e50_s700.pth"),
    (f"{R}/assets/indices/brat_b4_added_IVF242_Flat_nprobe_1_brat_b4_v1.index",
     "/Users/user/Desktop/Agent2/files/rvc_model/brat_b4_added.index"),
]


def fetch(remote, local, chunk=256 * 1024, deadline_hours=3):
    end = time.time() + deadline_hours * 3600
    attempt = 0
    while time.time() < end:
        attempt += 1
        try:
            c = ssh_srv.connect(key=True)
            sftp = c.open_sftp()
            want = sftp.stat(remote).st_size
            pos = os.path.getsize(local) if os.path.exists(local) else 0
            if pos >= want:
                sftp.close()
                c.close()
                return True
            rf = sftp.open(remote, "rb")
            rf.seek(pos)
            with open(local, "ab") as lf:
                while pos < want:
                    data = rf.read(chunk)
                    if not data:
                        break
                    lf.write(data)
                    pos += len(data)
                    if attempt % 1 == 0 and pos % (5 * 1024 * 1024) < chunk:
                        print(f"    {pos/1024/1024:.1f}/{want/1024/1024:.1f} МБ", flush=True)
            rf.close()
            sftp.close()
            c.close()
            if os.path.getsize(local) == want:
                return True
        except Exception as e:
            print(f"    обрыв на попытке {attempt} ({type(e).__name__}), "
                  f"есть {os.path.getsize(local)/1024/1024:.1f} МБ, продолжаю", flush=True)
            time.sleep(3)
    return False


for src, dst in JOBS:
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    print(f"качаю {os.path.basename(dst)}", flush=True)
    ok = fetch(src, dst)
    size = os.path.getsize(dst) / 1024 / 1024 if os.path.exists(dst) else 0
    print(f"{'ГОТОВО' if ok else 'НЕ ПОЛНОСТЬЮ'}: {os.path.basename(dst)} — {size:.1f} МБ", flush=True)
print("FETCH_DONE")
