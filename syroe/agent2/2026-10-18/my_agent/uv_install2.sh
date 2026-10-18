#!/usr/bin/env bash
# uv_install2.sh — установка RVC через uv (устойчив к обрывам сети).
#
# История: pip зависал (4 соединения к CDN в idle), поэтому перешли на uv.
# Ошибка uv №1: по умолчанию берёт только первый индекс, где есть пакет →
#   торч 2.7.1+cu128 не нашёлся. Лечится --index-strategy unsafe-best-match.
#
# РАЗРЕШЕНО владельцем работ 2026-10-10. Фиксируется в server_changes.md.
# Лог: /home/user2/rvc/uv_install.log
set -u
UV=/home/user2/.local/bin/uv
VENV=/home/user2/rvc/venv
ROOT=/home/user2/rvc/RVC
log() { echo "[$(date -u '+%H:%M:%S')] $*"; }

cd "$ROOT" || exit 1
log "=== uv-установка RVC, этап 1: torch 2.7.1+cu128 ==="
"$UV" pip install --python "$VENV/bin/python" \
  torch==2.7.1+cu128 torchaudio==2.7.1+cu128 \
  --index-url https://download.pytorch.org/whl/cu128 \
  --extra-index-url https://pypi.org/simple \
  --index-strategy unsafe-best-match 2>&1 | tail -15

log "=== проверка GPU ==="
"$VENV/bin/python" - <<'PY'
import torch
print("torch:", torch.__version__, "| cuda:", torch.version.cuda, "| available:", torch.cuda.is_available())
if torch.cuda.is_available():
    print("device:", torch.cuda.get_device_name(0), "| capability:", torch.cuda.get_device_capability(0))
    a = torch.randn(2000, 2000, device="cuda")
    print("matmul OK:", float((a @ a).sum()) != 0.0)
PY

log "=== этап 2: requirements (без китайского зеркала) ==="
grep -v '^--index-url' "$ROOT/requirments_cu128_py312.txt" > /home/user2/rvc/requirements_clean.txt
"$UV" pip install --python "$VENV/bin/python" -r /home/user2/rvc/requirements_clean.txt \
  --index-url https://pypi.org/simple \
  --index-strategy unsafe-best-match 2>&1 | tail -10

log "=== этап 3: проверка импортов ==="
"$VENV/bin/python" - <<'PY'
mods = ["torch","torchaudio","numpy","scipy","librosa","soundfile","faiss","parselmouth",
        "av","einops","local_attention","sklearn","tensorboard","tqdm","transformers"]
for m in mods:
    try:
        __import__(m); print("OK  ", m)
    except Exception as e:
        print("FAIL", m, type(e).__name__, str(e)[:90])
PY

log "=== размер ==="
du -sh "$VENV" "$ROOT" 2>/dev/null
log "=== ГОТОВО ==="
echo "UV_INSTALL_DONE"
