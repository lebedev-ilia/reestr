#!/bin/bash
# cleanup_rvc_keep_e50.sh — оставить только модель, которой отправлен результат заказчице.
#
# Оставляем:
#   * assets/weights/brat_b4_e50_s700.pth              — модель (эпоха 50, batch 4)
#   * assets/indices/brat_b4_added_...index            — индекс (СНАЧАЛА превращаем симлинк
#                                                        в реальный файл: он указывает в logs/,
#                                                        а logs/ мы удаляем)
# Удаляем: чекпоинты обучения G/D (основной вес), остальные малые модели и индексы.
set -eu
cd /home/user2/rvc/RVC
log(){ echo "[$(date -u +%H:%M:%S)] $*"; }

log "=== ДО очистки ==="
du -sh /home/user2/rvc 2>/dev/null

KEEP_W=brat_b4_e50_s700.pth
KEEP_I=brat_b4_added_IVF242_Flat_nprobe_1_brat_b4_v1.index
REAL_I=logs/brat_b4/added_IVF242_Flat_nprobe_1_brat_b4_v1.index

# 1. Сохраняем индекс: заменяем симлинк реальным файлом
if [ -L "assets/indices/$KEEP_I" ]; then
  log "индекс — симлинк, копирую реальный файл из $REAL_I"
  cp -f "$REAL_I" "assets/indices/$KEEP_I.tmp"
  rm -f "assets/indices/$KEEP_I"
  mv "assets/indices/$KEEP_I.tmp" "assets/indices/$KEEP_I"
fi
ls -la "assets/indices/$KEEP_I"

# 2. Удаляем остальные малые модели
log "--- удаляю лишние модели из assets/weights"
cd assets/weights
for f in *; do
  [ "$f" = "$KEEP_W" ] && continue
  echo "    rm $f"; rm -f "$f"
done
cd ../..

# 3. Удаляем остальные индексы
log "--- удаляю лишние индексы из assets/indices"
cd assets/indices
for f in *; do
  [ "$f" = "$KEEP_I" ] && continue
  echo "    rm $f"; rm -f "$f"
done
cd ../..

# 4. Удаляем чекпоинты обучения — основной вес
log "--- удаляю чекпоинты обучения (logs/)"
du -sh logs/* 2>/dev/null || true
rm -rf logs/smoke logs/brat logs/brat_ho logs/brat_b4
mkdir -p logs

log "=== ПОСЛЕ очистки ==="
du -sh /home/user2/rvc 2>/dev/null
echo "--- что осталось ---"
ls -la assets/weights/ assets/indices/
log "=== ГОТОВО ==="
echo RVC_CLEANUP_DONE
