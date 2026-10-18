#!/usr/bin/env python3
"""jetson_benchmark.py — замер нашей модели на Jetson (TensorRT) и сравнение с базовой.

Замеры:
  1. trtexec --loadEngine: чистое GPU-время инференса (латентность, пропускная способность),
     отдельно для FP16-движка; при желании — FP32.
  2. Память: сколько занимает процесс/движок (берём из вывода trtexec и free).
  3. Базовая модель: её цифры берём из логов Заказчика (process_file.log) — она считает на CPU,
     `torch.cuda.is_available()` = False; повторно грузить её на устройство не нужно
     (это боевой сервис, лишняя нагрузка ни к чему).

Результат сохраняется в JSON, чтобы потом попасть в отчёт по п. 7.4.
"""
import json
import re
import sys
import time

import paramiko

JETSON = dict(hostname="172.30.4.31", username="user", password="[СЕКРЕТ_УДАЛЁН_2026-10-18]")
ENGINE = "/home/user/agent2/models/yolo11n_fp16.engine"
OUT_JSON = "/Users/user/Desktop/Agent2/files/jetson/benchmark.json"

# базовая модель: цифры из их лога (CPU)
BASELINE = {
    "source": "process_file.log Заказчика",
    "device": "CPU (torch.cuda.is_available() = False)",
    "inference_ms_per_image": 6468.9,
    "files_per_sec": 0.15,
}


def connect():
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(JETSON["hostname"], username=JETSON["username"], password=JETSON["password"],
              timeout=30, banner_timeout=45, auth_timeout=45)
    return c


def run(c, cmd, timeout=600):
    _, out, err = c.exec_command(cmd, timeout=timeout)
    return out.read().decode("utf-8", "replace") + err.read().decode("utf-8", "replace")


def main():
    c = connect()
    print("=== наличие движка ===")
    print(run(c, f"ls -la {ENGINE} 2>/dev/null || echo НЕТ", timeout=60))

    print("=== замер trtexec (FP16) ===")
    cmd = (f"nice -n 19 /usr/src/tensorrt/bin/trtexec --loadEngine={ENGINE} "
           f"--iterations=100 --warmUp=1000 --duration=15 --avgRuns=10 2>&1 | tail -30")
    txt = run(c, cmd, timeout=900)
    print(txt)

    # разбор чисел
    def grab(pattern, s, cast=float):
        m = re.search(pattern, s)
        return cast(m.group(1)) if m else None

    res = {
        "baseline": BASELINE,
        "ours_fp16": {
            "engine": ENGINE,
            "gpu_compute_ms_median": grab(r"GPU Compute Time:.*?median = ([\d.]+) ms", txt),
            "gpu_compute_ms_mean": grab(r"GPU Compute Time:.*?mean = ([\d.]+) ms", txt),
            "throughput_qps": grab(r"Throughput: ([\d.]+) qps", txt),
            "latency_ms_median": grab(r"Latency:.*?median = ([\d.]+) ms", txt),
            "latency_ms_mean": grab(r"Latency:.*?mean = ([\d.]+) ms", txt),
        },
        "memory": {
            "free_before": run(c, "free -m | awk 'NR==2{print $3\" used / \"$2\" total MB\"}'", 60).strip(),
        },
        "raw_trtexec_tail": txt[-1500:],
        "measured_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    c.close()

    ours = res["ours_fp16"]
    base_ms = BASELINE["inference_ms_per_image"]
    if ours.get("gpu_compute_ms_median"):
        res["speedup_vs_baseline"] = round(base_ms / ours["gpu_compute_ms_median"], 1)
    if ours.get("throughput_qps"):
        res["fps_ours"] = ours["throughput_qps"]
        res["fps_baseline"] = BASELINE["files_per_sec"]

    with open(OUT_JSON, "w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False, indent=2)
    print(f"\nсохранено: {OUT_JSON}")
    print(json.dumps({k: v for k, v in res.items() if k != "raw_trtexec_tail"},
                     ensure_ascii=False, indent=2))
    print("BENCH_DONE")


if __name__ == "__main__":
    main()
