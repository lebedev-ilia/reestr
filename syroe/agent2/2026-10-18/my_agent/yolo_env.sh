#!/usr/bin/env bash
# yolo_env.sh — окружение для обучения YOLO на сервере (для ТЗ «Автоматика»).
#
# Зачем: обучение baseline нужно по ТЗ, а системного torch/ultralytics на сервере нет.
# Используем uv (pip зависал). Тот же torch 2.7.1+cu128 — колесо возьмётся из кэша uv,
# повторной загрузки не будет. Отдельный venv, чтобы не ломать RVC-окружение
# (там numpy<2 и свои пины).
#
# РАЗРЕШЕНО: сервер предоставлен под ТЗ, обучение моделей — целевое назначение.
# Лог: /home/user2/yolo/yolo_env.log
set -u
UV=/home/user2/.local/bin/uv
VENV=/home/user2/yolo/venv
log() { echo "[$(date -u '+%H:%M:%S')] $*"; }

mkdir -p /home/user2/yolo
log "=== создаём venv ==="
"$UV" venv "$VENV" --python 3.12 2>&1 | tail -2

log "=== torch + torchvision (те же версии, что у RVC — из кэша) ==="
"$UV" pip install --python "$VENV/bin/python" \
  torch==2.7.1+cu128 torchvision==0.22.1+cu128 \
  --index-url https://download.pytorch.org/whl/cu128 \
  --extra-index-url https://pypi.org/simple \
  --index-strategy unsafe-best-match 2>&1 | tail -8

log "=== ultralytics + метрики ==="
"$UV" pip install --python "$VENV/bin/python" \
  ultralytics pycocotools pandas 2>&1 | tail -8

log "=== проверка ==="
"$VENV/bin/python" - <<'PY'
import torch, torchvision
print("torch:", torch.__version__, "| torchvision:", torchvision.__version__,
      "| cuda:", torch.cuda.is_available())
if torch.cuda.is_available():
    print("device:", torch.cuda.get_device_name(0), "| capability:", torch.cuda.get_device_capability(0))
try:
    import ultralytics
    print("ultralytics:", ultralytics.__version__)
except Exception as e:
    print("ultralytics FAIL:", e)
PY

log "=== размер ==="
du -sh "$VENV" 2>/dev/null
log "=== ГОТОВО ==="
echo "YOLO_ENV_DONE"
