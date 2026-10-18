#!/usr/bin/env bash
# setup_rvc_env2.sh — окружение RVC без root (через virtualenv, не python3-venv).
# Запуск: bash /home/user2/rvc/setup2.sh > /home/user2/rvc_setup2.log 2>&1
set -x
RVC=/home/user2/rvc
mkdir -p "$RVC"; cd "$RVC" || exit 1

echo "=== 1. Диагностика python и ensurepip ==="
python3 -V
python3 -c "import ensurepip; print('ensurepip ЕСТЬ')" 2>&1 | tail -2
python3 -m ensurepip --version 2>&1 | tail -2
command -v pip3 && pip3 --version || echo "pip3 нет"

echo "=== 2. Достаём virtualenv (zipapp, без root) ==="
curl -sSL -o "$RVC/virtualenv.pyz" https://bootstrap.pypa.io/virtualenv.pyz
ls -la "$RVC/virtualenv.pyz"

echo "=== 3. Создаём venv ==="
rm -rf "$RVC/venv"
python3 "$RVC/virtualenv.pyz" "$RVC/venv" 2>&1 | tail -5
"$RVC/venv/bin/python" -V
"$RVC/venv/bin/pip" --version

echo "=== 4. Ставим torch cu128 (sm_120 для RTX 50xx) ==="
"$RVC/venv/bin/pip" install -q --upgrade pip wheel 2>&1 | tail -2
"$RVC/venv/bin/pip" install torch torchaudio --index-url https://download.pytorch.org/whl/cu128 2>&1 | tail -6

echo "=== 5. ПРОВЕРКА GPU в torch ==="
"$RVC/venv/bin/python" - <<'PY'
import torch
print("torch:", torch.__version__, "| cuda:", torch.cuda.is_available())
if torch.cuda.is_available():
    print("device:", torch.cuda.get_device_name(0), "| capability:", torch.cuda.get_device_capability(0))
    x = torch.randn(2000, 2000, device="cuda")
    print("matmul:", float((x @ x).sum()) != 0.0)
PY

echo "=== 6. Клонируем RVC ==="
cd "$RVC" || exit 1
if [ -d RVC ]; then echo "RVC уже есть"; else
  git clone --depth 1 https://github.com/RVC-Project/Retrieval-based-Voice-Conversion-WebUI.git RVC 2>&1 | tail -3
fi
ls RVC | head -20

echo "=== 7. ffmpeg ==="
command -v ffmpeg && ffmpeg -version | head -1 || echo "ffmpeg нет (понадобится)"

echo "=== ГОТОВО ==="
