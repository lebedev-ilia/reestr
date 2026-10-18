#!/usr/bin/env bash
# yolo_env2.sh — окружение для обучения YOLO БЕЗ повторной загрузки torch.
#
# Идея: torch 2.7.1+cu128 с CUDA-библиотеками — это ~8 ГБ и ~45 минут загрузки.
# uv после установки очистил кэш, поэтому повторная установка в новый venv = ещё 45 минут.
# Вместо этого копируем готовый RVC-venv (cp -a, реальная копия, не хардлинки —
# иначе запись в копию испортила бы оригинал) и добавляем в копию только ultralytics.
#
# Так оригинальное RVC-окружение остаётся нетронутым (его пины numpy<2 не страдают).
#
# Лог: /home/user2/yolo/yolo_env.log
set -u
UV=/home/user2/.local/bin/uv
SRC=/home/user2/rvc/venv
VENV=/home/user2/yolo/venv
log() { echo "[$(date -u '+%H:%M:%S')] $*"; }

mkdir -p /home/user2/yolo
log "=== 1. Копируем RVC-venv (реальная копия, ~8 ГБ) ==="
if [ -d "$VENV" ]; then
  log "venv уже есть, пропускаю копирование"
else
  cp -a "$SRC" "$VENV" || { log "ОШИБКА копирования"; exit 1; }
fi
log "копия: $(du -sh "$VENV" 2>/dev/null | cut -f1)"

log "=== 2. Проверяем, что копия рабочая ==="
"$VENV/bin/python" -c "import torch,sys;print('prefix:',sys.prefix);print('torch:',torch.__version__,'cuda:',torch.cuda.is_available())" 2>&1 | tail -3

log "=== 3. Ставим torchvision (версия под torch 2.7.1) + ultralytics ==="
printf 'numpy<2\ntorch==2.7.1+cu128\n' > /home/user2/yolo/constraints.txt
"$UV" pip install --python "$VENV/bin/python" \
  torchvision==0.22.1+cu128 ultralytics \
  --index-url https://download.pytorch.org/whl/cu128 \
  --extra-index-url https://pypi.org/simple \
  --index-strategy unsafe-best-match \
  --constraint /home/user2/yolo/constraints.txt 2>&1 | tail -10

log "=== 4. Проверка окружения YOLO ==="
"$VENV/bin/python" - <<'PY'
import torch, numpy
print("torch:", torch.__version__, "| numpy:", numpy.__version__, "| cuda:", torch.cuda.is_available())
try:
    import torchvision; print("torchvision:", torchvision.__version__)
except Exception as e:
    print("torchvision FAIL:", e)
try:
    import ultralytics; print("ultralytics:", ultralytics.__version__)
except Exception as e:
    print("ultralytics FAIL:", e)
try:
    import cv2; print("opencv:", cv2.__version__)
except Exception as e:
    print("opencv FAIL:", e)
PY

log "=== 5. Проверяем, что RVC-окружение не пострадало ==="
"$SRC/bin/python" -c "import torch,numpy,librosa;print('RVC ok: torch',torch.__version__,'numpy',numpy.__version__)" 2>&1 | tail -2

log "=== размер ==="
du -sh "$VENV" 2>/dev/null
log "=== ГОТОВО ==="
echo "YOLO_ENV_DONE"
