#!/usr/bin/env bash
# setup_rvc_env.sh — развернуть окружение для RVC на рабочем сервере (172.30.4.134).
# Запуск на сервере: bash /home/user2/rvc/setup_rvc_env.sh
# Логи: /home/user2/rvc/setup.log
set -x
RVC=/home/user2/rvc
mkdir -p "$RVC"
cd "$RVC" || exit 1

echo "=== 1. ОС, GPU, драйвер ==="
. /etc/os-release; echo "$PRETTY_NAME"
nvidia-smi --query-gpu=name,driver_version,compute_cap --format=csv,noheader

echo "=== 2. Какие Python есть ==="
for p in python3 python3.10 python3.11 python3.12; do
  command -v $p >/dev/null 2>&1 && echo "$p -> $($p -V 2>&1)"
done

echo "=== 3. Есть ли uv ==="
command -v uv && uv --version || echo "uv нет"

echo "=== 4. Свободное место ==="
df -h /home | tail -1

echo "=== 5. Создаём venv на python3.10 (если есть), иначе python3.12 ==="
PYBIN=$(command -v python3.10 || command -v python3.12 || command -v python3)
echo "используем: $PYBIN"
$PYBIN -m venv "$RVC/venv" || { echo "venv НЕ СОЗДАН"; exit 2; }
"$RVC/venv/bin/pip" install -q --upgrade pip wheel 2>&1 | tail -3

echo "=== 6. Ставим torch (cu128 = sm_120 для RTX 50xx) ==="
"$RVC/venv/bin/pip" install torch torchaudio --index-url https://download.pytorch.org/whl/cu128 2>&1 | tail -5

echo "=== 7. ПРОВЕРКА: видит ли torch GPU ==="
"$RVC/venv/bin/python" - <<'PY'
import torch
print("torch:", torch.__version__)
print("cuda available:", torch.cuda.is_available())
if torch.cuda.is_available():
    print("device:", torch.cuda.get_device_name(0))
    print("capability:", torch.cuda.get_device_capability(0))
    x = torch.randn(1000, 1000, device="cuda")
    print("matmul OK:", (x @ x).sum().item() is not None)
PY

echo "=== 8. Базовые пакеты RVC ==="
"$RVC/venv/bin/pip" install -q numpy scipy librosa soundfile faiss-cpu ffmpeg-python praat-parselmouth pyworld 2>&1 | tail -3

echo "=== 9. Проверка импортов ==="
"$RVC/venv/bin/python" - <<'PY'
mods = ["numpy","scipy","librosa","soundfile","faiss","parselmouth","pyworld","torch"]
for m in mods:
    try:
        __import__(m); print("OK  ", m)
    except Exception as e:
        print("FAIL", m, type(e).__name__, str(e)[:120])
PY

echo "=== ГОТОВО ==="
