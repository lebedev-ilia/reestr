#!/usr/bin/env bash
# rvc_install.sh — установка окружения RVC на сервере 172.30.4.134.
#
# РАЗРЕШЕНО владельцем работ 2026-10-10 (обучение RVC для заказа с фриланса).
# Все действия фиксируются: /home/user2/AGENT2_CHANGES.log и my_agent/server_changes.md
#
# Запуск (отсоединённо, чтобы пережить разрыв SSH):
#   nohup setsid bash /home/user2/rvc_install.sh > /home/user2/rvc/install.log 2>&1 &
#
# Лог: /home/user2/rvc/install.log — искать метки СТАДИЯ и EXIT.
set -u
RVC=/home/user2/rvc
VENV="$RVC/venv"
PY="$VENV/bin/python"
PIP="$VENV/bin/pip"

log() { echo "[$(date -u '+%Y-%m-%d %H:%M:%S')] $*"; }
stage() { log "=== СТАДИЯ: $* ==="; }

mkdir -p "$RVC"
log "старт установки RVC. Сервер: $(hostname). Пользователь: $(whoami)"

stage "1. Окружение и место"
df -h /home | tail -1
nvidia-smi --query-gpu=name,driver_version,compute_cap --format=csv,noheader

stage "2. Создание venv (virtualenv, без root)"
if [ ! -x "$PY" ]; then
  [ -f "$RVC/virtualenv.pyz" ] || curl -sSL -o "$RVC/virtualenv.pyz" https://bootstrap.pypa.io/virtualenv.pyz
  rm -rf "$VENV"
  python3 "$RVC/virtualenv.pyz" "$VENV" || { log "ОШИБКА: venv не создан"; exit 2; }
fi
log "python: $($PY -V 2>&1)"
"$PIP" install -q --upgrade pip wheel 2>&1 | tail -2

stage "3. Загрузка torch + torchaudio (cu128, sm_120). Долго: ~3 ГБ при ~300 КБ/с"
START=$(date +%s)
"$PIP" install torch torchaudio --index-url https://download.pytorch.org/whl/cu128
RC=$?
log "pip torch вернул код $RC, прошло $(( ($(date +%s)-START)/60 )) мин"
[ $RC -ne 0 ] && { log "ОШИБКА на стадии 3 — прерываю"; exit 3; }

stage "4. Проверка GPU в torch"
"$PY" - <<'PY'
import torch
print("torch:", torch.__version__, "| cuda:", torch.cuda.is_available())
if torch.cuda.is_available():
    print("device:", torch.cuda.get_device_name(0), "| capability:", torch.cuda.get_device_capability(0))
    a = torch.randn(2000, 2000, device="cuda")
    print("matmul OK:", float((a @ a).sum()) != 0.0)
else:
    raise SystemExit("CUDA недоступна — sm_120 не поддержан сборкой torch")
PY
[ $? -ne 0 ] && { log "ОШИБКА: GPU не виден torch"; exit 4; }

stage "5. Базовые библиотеки RVC"
"$PIP" install numpy scipy librosa soundfile faiss-cpu pyworld praat-parselmouth \
  ffmpeg-python tensorboard 2>&1 | tail -5
log "код стадии 5: $?"
"$PY" - <<'PY'
for m in ["numpy","scipy","librosa","soundfile","faiss","pyworld","parselmouth","torch","torchaudio"]:
    try:
        __import__(m); print("OK  ", m)
    except Exception as e:
        print("FAIL", m, type(e).__name__, str(e)[:100])
PY

stage "6. Клонирование RVC"
cd "$RVC" || exit 5
if [ -d RVC/.git ]; then
  log "RVC уже склонирован"
else
  git clone --depth 1 https://github.com/RVC-Project/Retrieval-based-Voice-Conversion-WebUI.git RVC 2>&1 | tail -3
fi
ls RVC | head -15

stage "7. Итог"
du -sh "$RVC" "$VENV" 2>/dev/null
log "ГОТОВО. Лог: /home/user2/rvc/install.log"
echo "EXIT=0"
