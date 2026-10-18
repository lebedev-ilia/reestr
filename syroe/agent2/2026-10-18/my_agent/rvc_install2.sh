#!/usr/bin/env bash
# rvc_install2.sh — КОРРЕКТНАЯ установка RVC на сервере (по README репозитория).
#
# Причина переделки: первая версия ставила torch последней версии (2.11.0) и библиотеки
# без версий. README прямо требует: torch==2.7.1+cu128 (RTX 50 серия, "must stay below 2.8")
# и полный requirments_cu128_py312.txt с версиями (numpy<2, librosa<0.11 и т.д.).
#
# РАЗРЕШЕНО владельцем работ 2026-10-10. Фиксируется в server_changes.md.
# Лог: /home/user2/rvc/install2.log — метки СТАДИЯ.
set -u
RVC=/home/user2/rvc
VENV="$RVC/venv"
PY="$VENV/bin/python"
PIP="$VENV/bin/pip"
log() { echo "[$(date -u '+%Y-%m-%d %H:%M:%S')] $*"; }
stage() { log "=== СТАДИЯ: $* ==="; }

log "старт КОРРЕКТНОЙ установки RVC (torch 2.7.1+cu128)"

stage "1. Клонируем репозиторий RVC (нужен requirements)"
cd "$RVC" || exit 1
if [ -d RVC/.git ]; then
  log "репо уже есть"
else
  git clone --depth 1 https://github.com/RVC-Project/Retrieval-based-Voice-Conversion-WebUI.git RVC 2>&1 | tail -2
fi

stage "2. Чистый venv"
[ -f "$RVC/virtualenv.pyz" ] || curl -sSL -o "$RVC/virtualenv.pyz" https://bootstrap.pypa.io/virtualenv.pyz
rm -rf "$VENV"
python3 "$RVC/virtualenv.pyz" "$VENV" || { log "ОШИБКА: venv"; exit 2; }
"$PIP" install -q --upgrade pip setuptools wheel 2>&1 | tail -2
log "python: $($PY -V)"

stage "3. torch==2.7.1+cu128 + torchaudio (RTX 50 / sm_120)"
"$PIP" install torch==2.7.1+cu128 torchaudio==2.7.1+cu128 \
  --index-url https://download.pytorch.org/whl/cu128 \
  --extra-index-url https://pypi.org/simple 2>&1 | tail -4
RC=$?
[ $RC -ne 0 ] && { log "ОШИБКА на стадии 3 (torch)"; exit 3; }

stage "4. Проверка GPU"
"$PY" - <<'PY'
import torch
print("torch:", torch.__version__, "| cuda:", torch.version.cuda, "| available:", torch.cuda.is_available())
if torch.cuda.is_available():
    print("device:", torch.cuda.get_device_name(0), "| capability:", torch.cuda.get_device_capability(0))
    a = torch.randn(2000, 2000, device="cuda")
    print("matmul OK:", float((a @ a).sum()) != 0.0)
else:
    raise SystemExit("CUDA недоступна")
PY
[ $? -ne 0 ] && { log "ОШИБКА: GPU не виден"; exit 4; }

stage "5. Полный requirements (без китайского зеркала, через pypi.org)"
# убираем --index-url из файла требований, чтобы качать с pypi.org (быстрее из РФ)
grep -v '^--index-url' "$RVC/RVC/requirments_cu128_py312.txt" > "$RVC/requirements_clean.txt"
"$PIP" install -r "$RVC/requirements_clean.txt" --index-url https://pypi.org/simple 2>&1 | tail -6
log "код стадии 5: $?"

stage "6. Итоговая проверка импортов"
"$PY" - <<'PY'
mods = ["torch","torchaudio","numpy","scipy","librosa","soundfile","faiss","parselmouth",
        "av","einops","local_attention","sklearn","tensorboard","tqdm","transformers","torchfcpe"]
for m in mods:
    try:
        __import__(m); print("OK  ", m)
    except Exception as e:
        print("FAIL", m, type(e).__name__, str(e)[:100])
PY

stage "7. Итог"
du -sh "$RVC/RVC" "$VENV" 2>/dev/null
log "ГОТОВО. Лог: /home/user2/rvc/install2.log"
echo "EXIT=0"
