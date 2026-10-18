#!/usr/bin/env python3
"""jetson_build_after_reboot.py — дождаться свежей загрузки Jetson и сразу собрать движок.

Причина: устройство перезагружается каждые 30 минут. Сборка TensorRT на Maxwell идёт
дольше, поэтому стартовать надо сразу после загрузки — тогда окно максимально.
Убивать прежние сборки здесь НЕЛЬЗЯ через `pkill -f trtexec`: шаблон совпадёт с командной
строкой самого скрипта. Проверяем и запускаем раздельно.
"""
import time

import paramiko

JETSON = dict(hostname="172.30.4.31", username="user", password="[СЕКРЕТ_УДАЛЁН_2026-10-18]")
BUILD_LOG = "/home/user/agent2/build4.log"


def connect(timeout=30):
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(JETSON["hostname"], username=JETSON["username"], password=JETSON["password"],
              timeout=timeout, banner_timeout=45, auth_timeout=45)
    return c


def uptime_seconds(c):
    _, out, _ = c.exec_command("cut -d. -f1 /proc/uptime", timeout=30)
    try:
        return int(out.read().decode().strip())
    except Exception:
        return None


def start_build(c):
    cmd = ("cd /home/user/agent2 && setsid nohup nice -n 19 "
           "/usr/src/tensorrt/bin/trtexec "
           "--onnx=/home/user/agent2/models/yolo11n_waste_up.onnx "
           "--saveEngine=/home/user/agent2/models/yolo11n_fp16.engine "
           "--fp16 --workspace=16 --avgTiming=1 --minTiming=1 "
           f"> {BUILD_LOG} 2>&1 < /dev/null & echo STARTED")
    _, out, _ = c.exec_command(cmd, timeout=60)
    return "STARTED" in out.read().decode()


print("ждём свежую загрузку (uptime < 120 c)...", flush=True)
deadline = time.time() + 15 * 60
started = False
while time.time() < deadline and not started:
    try:
        c = connect()
        up = uptime_seconds(c)
        if up is not None and up < 120:
            print(f"  устройство перезагрузилось, uptime={up} c — стартую сборку", flush=True)
            started = start_build(c)
            print("  сборка запущена" if started else "  не удалось запустить", flush=True)
        else:
            print(f"  uptime={up} c — ждём", flush=True)
        c.close()
    except Exception as e:
        print(f"  устройство недоступно ({type(e).__name__}) — вероятно, перезагружается", flush=True)
    if not started:
        time.sleep(15)

if started:
    # мониторим до конца или до перезагрузки
    print("мониторинг сборки...", flush=True)
    end = time.time() + 32 * 60
    while time.time() < end:
        time.sleep(60)
        try:
            c = connect()
            _, out, _ = c.exec_command(
                "echo \"trtexec: $(pgrep -c trtexec 2>/dev/null)\"; "
                "tail -3 /home/user/agent2/build4.log 2>/dev/null | cut -c1-110; "
                "ls -la /home/user/agent2/models/*.engine 2>/dev/null | awk '{print \"ENGINE \"$5}'",
                timeout=60)
            txt = out.read().decode("utf-8", "replace")
            c.close()
            print(txt.strip(), flush=True)
            if "ENGINE" in txt:
                print("ДВИЖОК СОБРАН", flush=True)
                break
        except Exception as e:
            print(f"  связь потеряна ({type(e).__name__})", flush=True)
print("BUILD_WATCH_DONE")
