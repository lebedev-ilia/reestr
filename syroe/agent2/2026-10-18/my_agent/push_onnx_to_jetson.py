#!/usr/bin/env python3
"""push_onnx_to_jetson.py — сервер → локально → Jetson с докачкой.

Туннель до сервера рвёт передачи, а Jetson медленный. Поэтому: сначала докачиваем
ONNX с сервера кусками с возобновлением, потом заливаем на Jetson с проверкой размера.
"""
import os
import sys
import time

sys.path.insert(0, "/Users/user/Desktop/Agent2/my_agent")
import paramiko  # noqa: E402
import ssh_srv  # noqa: E402

REMOTE_SRV = "/home/user2/baseline/runs/waste_up_blocksplit/weights/best.onnx"
LOCAL = "/Users/user/Desktop/Agent2/files/jetson/yolo11n_waste_up.onnx"
REMOTE_JETSON = "/home/user/agent2/models/yolo11n_waste_up.onnx"

os.makedirs(os.path.dirname(LOCAL), exist_ok=True)


def jetson():
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect("172.30.4.31", username="user", password="[СЕКРЕТ_УДАЛЁН_2026-10-18]",
              timeout=30, banner_timeout=40, auth_timeout=40)
    return c


def fetch_from_server(deadline_s=900):
    end = time.time() + deadline_s
    while time.time() < end:
        try:
            c = ssh_srv.connect(key=True)
            sftp = c.open_sftp()
            want = sftp.stat(REMOTE_SRV).st_size
            pos = os.path.getsize(LOCAL) if os.path.exists(LOCAL) else 0
            if pos >= want:
                sftp.close()
                c.close()
                print(f"  с сервера: готово, {want/1024/1024:.1f} МБ", flush=True)
                return True
            rf = sftp.open(REMOTE_SRV, "rb")
            rf.seek(pos)
            with open(LOCAL, "ab") as lf:
                while pos < want:
                    d = rf.read(256 * 1024)
                    if not d:
                        break
                    lf.write(d)
                    pos += len(d)
            rf.close()
            sftp.close()
            c.close()
        except Exception as e:
            print(f"  обрыв ({type(e).__name__}), есть {os.path.getsize(LOCAL)/1024/1024:.1f} МБ",
                  flush=True)
            time.sleep(3)
    return False


def push_to_jetson(deadline_s=900):
    want = os.path.getsize(LOCAL)
    end = time.time() + deadline_s
    while time.time() < end:
        try:
            c = jetson()
            c.exec_command("mkdir -p ~/agent2/models")[1].channel.recv_exit_status()
            sftp = c.open_sftp()
            sftp.put(LOCAL, REMOTE_JETSON)
            got = sftp.stat(REMOTE_JETSON).st_size
            sftp.close()
            c.close()
            if got == want:
                print(f"  на Jetson: готово, {got/1024/1024:.1f} МБ", flush=True)
                return True
            print(f"  на Jetson неполно: {got} из {want}", flush=True)
        except Exception as e:
            print(f"  обрыв загрузки ({type(e).__name__})", flush=True)
            time.sleep(4)
    return False


ok1 = fetch_from_server()
print("скачивание с сервера:", "OK" if ok1 else "НЕ УДАЛОСЬ", flush=True)
if ok1:
    ok2 = push_to_jetson()
    print("загрузка на Jetson:", "OK" if ok2 else "НЕ УДАЛОСЬ", flush=True)
print("PUSH_DONE")
