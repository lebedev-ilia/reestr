#!/usr/bin/env bash
# brat_epoch_pick.sh — выбрать лучшую эпоху прослушиванием (рекомендация AI HUB Docs).
#
# Документация прямо говорит: «The best way to know when to stop training is to test and
# hear each saved epoch» — то есть качество определяет слух, а не метрика. Поэтому делаем
# по одному образцу с разных чекпоинтов и даём послушать.
#
# Гиперпараметры этого прогона исправлены по документации:
#   * batch 4 (для датасета < 30 мин рекомендуется именно 4, а не 8);
#   * 100 эпох вместо 300 (ориентир «эпохи × минуты ≈ 300» → для 6,6 мин это ~45);
#   * сохранение каждые 10 эпох, чтобы был выбор.
#
# Настройки инференса тоже по документации:
#   * --index-rate 0.5 — ниже дефолта, «lowering it can reduce artifacting»;
#   * --protect 0.5    — «a value of 0.5 disables this feature» (меньше «нечеловечности»);
#   * --rms-mix-rate 0 — «leave it at 0 if you want the audio to try to keep its original volume».
#
# Запуск: bash /home/user2/brat_epoch_pick.sh
set -u
ROOT=/home/user2/rvc/RVC
PY=/home/user2/rvc/venv/bin/python
export PYTHONPATH="$ROOT"
export PATH="/home/user2/bin:$PATH"
OUT=/home/user2/rvc/epoch_out
log() { echo "[$(date -u '+%H:%M:%S')] $*"; }

cd "$ROOT" || exit 1
mkdir -p "$OUT"
INDEX=$(ls assets/indices/*brat_b4*.index 2>/dev/null | head -1)
log "index: $INDEX"

for M in $(ls -t assets/weights/brat_b4*.pth 2>/dev/null); do
  ep=$(basename "$M" | grep -oE 'e[0-9]+' | head -1)
  log "--- чекпоинт $ep"
  "$PY" -m infer.cli --model "$M" \
    --input /home/user2/rvc/test_in_ho/held_1.wav \
    --output "$OUT/sample_${ep}.wav" \
    --pitch 0 --f0-method rmvpe --index-rate 0.5 --protect 0.5 --rms-mix-rate 0 \
    ${INDEX:+--index "$INDEX"} 2>&1 | tail -2
done

cp -f /home/user2/rvc/test_in_ho/held_1.wav "$OUT/ORIGINAL.wav"
log "=== ИТОГ ==="
ls -la "$OUT"/*.wav 2>/dev/null
log "=== ГОТОВО ==="
echo "EPOCH_PICK_DONE"
