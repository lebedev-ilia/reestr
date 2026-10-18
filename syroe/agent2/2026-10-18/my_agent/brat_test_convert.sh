#!/usr/bin/env bash
# brat_test_convert.sh — генерация тестовых фраз голосом брата заказчицы.
#
# Заказчица попросила бесплатный тест: «три фразы с разной интонацией».
# Делаем 4 файла:
#   1–3 — мужской вход (Yuri, F0 ≈ 103 Гц), сдвиг +1 полутон: спокойная / вопрос / эмоция;
#   4   — женский вход (Milena, F0 = 216 Гц), сдвиг −12 полутонов: реальный кейс заказчицы
#         (она женщина, цель — мужской голос брата).
#
# Сдвиги посчитаны по данным: измерены медианы F0 входа и цели (брат ≈ 108–123 Гц).
#
# Запуск: bash /home/user2/brat_test_convert.sh
set -u
ROOT=/home/user2/rvc/RVC
PY=/home/user2/rvc/venv/bin/python
export PYTHONPATH="$ROOT"
export PATH="/home/user2/bin:$PATH"
OUT=/home/user2/rvc/test_out
log() { echo "[$(date -u '+%H:%M:%S')] $*"; }

cd "$ROOT" || exit 1
mkdir -p "$OUT"

MODEL=$(ls -t assets/weights/brat*.pth 2>/dev/null | head -1)
INDEX=$(ls assets/indices/*brat*.index 2>/dev/null | head -1)
log "модель: $MODEL"
log "index: $INDEX"
[ -z "$MODEL" ] && { log "ОШИБКА: модель не найдена"; exit 1; }

convert() {
  local inp="$1" outp="$2" pitch="$3" label="$4"
  log "--- $label (pitch $pitch)"
  "$PY" -m infer.cli --model "$MODEL" \
    --input "$inp" --output "$outp" \
    --pitch "$pitch" --f0-method rmvpe --index-rate 0.75 \
    ${INDEX:+--index "$INDEX"} 2>&1 | tail -3
  log "  -> $(ls -la "$outp" 2>/dev/null | awk '{print $5" байт"}')"
}

D=/home/user2/rvc/test_in
convert "$D/t1_calm.wav"     "$OUT/test_1_spokoynaya.wav" 1   "1. спокойная фраза"
convert "$D/t2_question.wav" "$OUT/test_2_vopros.wav"     1   "2. вопрос"
convert "$D/t3_emotion.wav"  "$OUT/test_3_emocii.wav"     1   "3. эмоциональная фраза"
convert "$D/t4_female.wav"   "$OUT/test_4_zhenskiy_vhod.wav" -12 "4. женский вход (реальный кейс)"

log "=== ИТОГ ==="
ls -la "$OUT"/*.wav 2>/dev/null
log "=== ГОТОВО ==="
echo "BRAT_TEST_DONE"
