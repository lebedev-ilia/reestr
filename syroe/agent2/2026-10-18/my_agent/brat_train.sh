#!/usr/bin/env bash
# brat_train.sh — обучение RVC-модели на голосе брата заказчицы (FL.ru, заказ №5524092).
#
# Данные: /home/user2/rvc/data/brat/*.wav — 2 файла, ~7,8 мин плотной речи
# (паузы сжаты до 0,4 с, моно 40 кГц). Аудит: SNR 43–47 дБ, полоса до 19 кГц,
# клиппинга нет; файл 02 ранее прошёл через mp3 (возможны артефакты кодекa).
#
# Все шаги — через `-m` (модульный режим), иначе падают импорты (см. rvc_smoke_test.sh).
# Лог: /home/user2/rvc/brat_train.log
set -u
ROOT=/home/user2/rvc/RVC
PY=/home/user2/rvc/venv/bin/python
EXP="$ROOT/logs/brat"
EPOCHS="${1:-300}"
export PYTHONPATH="$ROOT"
export PATH="/home/user2/bin:$PATH"
log() { echo "[$(date -u '+%H:%M:%S')] $*"; }

cd "$ROOT" || exit 1
log "=== ОБУЧЕНИЕ МОДЕЛИ ГОЛОСА БРАТА (v1/40k, $EPOCHS эпох) ==="

log "=== 1. Датасет ==="
mkdir -p datasets/brat
cp -f /home/user2/rvc/data/brat/*.wav datasets/brat/ 2>/dev/null
log "файлов: $(ls datasets/brat 2>/dev/null | wc -l)"
"$PY" -c "import soundfile as sf,glob
tot=0
for f in sorted(glob.glob('datasets/brat/*.wav')):
    d,sr=sf.read(f); tot+=len(d)/sr
print(f'  суммарная длительность: {tot:.0f} с ({tot/60:.1f} мин)')"

log "=== 2. Препроцессинг ==="
mkdir -p "$EXP"
"$PY" -m train.preprocess datasets/brat 40000 8 logs/brat False 3.7 2>&1 | tail -3
log "сегментов 0_gt_wavs: $(ls "$EXP/0_gt_wavs" 2>/dev/null | wc -l)"

log "=== 3. F0 (rmvpe, CUDA) ==="
"$PY" -m train.dataset.extract_f0 cuda 1 0 0 logs/brat true 2>&1 | tail -3
log "2a_f0: $(ls "$EXP/2a_f0" 2>/dev/null | wc -l)"

log "=== 4. HuBERT (v1, CUDA) ==="
"$PY" -m train.dataset.extract_hubert_feature cuda:0 1 0 0 logs/brat v1 true 2>&1 | tail -3
log "3_feature256: $(ls "$EXP/3_feature256" 2>/dev/null | wc -l)"

log "=== 5. filelist + config ==="
"$PY" - "$EXP" <<'PY'
import os, sys, shutil
exp = sys.argv[1]
gt, feat, f0, f0nsf = f"{exp}/0_gt_wavs", f"{exp}/3_feature256", f"{exp}/2a_f0", f"{exp}/2b-f0nsf"
def bases(d):
    return set(n.split('.')[0] for n in os.listdir(d)) if os.path.isdir(d) else set()
names = sorted(bases(gt) & bases(feat) & bases(f0) & bases(f0nsf))
os.makedirs(exp, exist_ok=True)
lines = [f"{gt}/{n}.wav|{feat}/{n}.npy|{f0}/{n}.wav.npy|{f0nsf}/{n}.wav.npy|0" for n in names]
open(f"{exp}/filelist.txt", "w", encoding="utf8").write("\n".join(lines) + "\n")
shutil.copy("configs/v1/40k.json", f"{exp}/config.json")
print(f"filelist: {len(lines)} строк")
PY

log "=== 6. Обучение ($EPOCHS эпох) ==="
mkdir -p assets/weights
"$PY" -m train.train -e brat -sr 40k -f0 1 -bs 8 -g 0 -te "$EPOCHS" -se 50 \
  -pg assets/pretrained/f0G40k.pth -pd assets/pretrained/f0D40k.pth \
  -l 0 -c 0 -sw 1 -v v1 2>&1 | tail -10
log "инференс-модели: $(ls -t assets/weights/*.pth 2>/dev/null | head -3)"

log "=== 7. Index ==="
"$PY" -m train.train_index brat v1 assets/indices 8 single 2>&1 | tail -3
log "index: $(ls assets/indices/ 2>/dev/null | grep -i brat | head -3)"

log "=== ГОТОВО ==="
echo "BRAT_TRAIN_DONE"
