#!/usr/bin/env bash
# brat_ab_test.sh — честный A/B-тест: фрагменты, НЕ участвовавшие в обучении.
#
# Логика: модель обучалась на data/brat_ho/train.wav (398 с), а фрагменты взяты
# из отложенной части (69 с). Значит качество измеряется на новых данных, а не на заученных —
# это принципиально: тест на обучающих данных завышает результат (та же ошибка, что была
# с утечкой в YOLO-сплите).
#
# Для каждого фрагмента делаем пару: оригинал (A) и сконвертированный (B).
# Слушатель сравнивает и слышит, что именно теряет модель.
#
# Запуск: bash /home/user2/brat_ab_test.sh
set -u
ROOT=/home/user2/rvc/RVC
PY=/home/user2/rvc/venv/bin/python
export PYTHONPATH="$ROOT"
export PATH="/home/user2/bin:$PATH"
OUT=/home/user2/rvc/ab_out
log() { echo "[$(date -u '+%H:%M:%S')] $*"; }

cd "$ROOT" || exit 1
mkdir -p "$OUT"

MODEL=$(ls -t assets/weights/brat_ho*.pth 2>/dev/null | head -1)
INDEX=$(ls assets/indices/*brat_ho*.index 2>/dev/null | head -1)
log "модель: $MODEL"
log "index: $INDEX"
[ -z "$MODEL" ] && { log "ОШИБКА: модель brat_ho не найдена"; exit 1; }

for i in 1 2 3; do
  cp -f "/home/user2/rvc/test_in_ho/held_${i}.wav" "$OUT/A_original_${i}.wav"
  log "--- фрагмент $i (pitch 0, тот же диктор)"
  "$PY" -m infer.cli --model "$MODEL" \
    --input "/home/user2/rvc/test_in_ho/held_${i}.wav" \
    --output "$OUT/B_converted_${i}.wav" \
    --pitch 0 --f0-method rmvpe --index-rate 0.75 \
    ${INDEX:+--index "$INDEX"} 2>&1 | tail -2
done

log "=== ИТОГ ==="
ls -la "$OUT"/*.wav 2>/dev/null
log "=== ГОТОВО ==="
echo "AB_TEST_DONE"
