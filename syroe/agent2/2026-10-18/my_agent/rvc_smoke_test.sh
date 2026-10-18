#!/usr/bin/env bash
# rvc_smoke_test.sh — сквозной smoke-тест RVC на сервере (v1/40k, один диктор).
#
# ЗАПУСКАЕМ ВСЁ ЧЕРЕЗ `-m` (модульный режим). Почему: при запуске файлом
# (`python train/preprocess.py`) Python кладёт в sys.path[0] папку скрипта (train/),
# из-за чего `import train` резолвится в train/train.py → циклический импорт
# (`from train import utils`), и падают все шаги. В модульном режиме sys.path[0] = корень
# репозитория, поэтому `train.*`, `infer.*`, `i18n.*`, `configs.*` импортируются корректно.
#
# РАЗРЕШЕНО владельцем работ 2026-10-10. Фиксируется в server_changes.md.
# Запуск: bash /home/user2/rvc_smoke_test.sh > /home/user2/rvc/smoke3.log 2>&1
set -u
ROOT=/home/user2/rvc/RVC
PY=/home/user2/rvc/venv/bin/python
EXP="$ROOT/logs/smoke"
export PYTHONPATH="$ROOT"
# ffmpeg на сервере нет и sudo нет — используем статическую сборку из /home/user2/bin
export PATH="/home/user2/bin:$PATH"
log() { echo "[$(date -u '+%H:%M:%S')] $*"; }

cd "$ROOT" || exit 1
log "=== СМОУК-ТЕСТ RVC (v1/40k), попытка 3 (модульный запуск) ==="

log "=== 1. Модели ==="
log "hubert=$(ls assets/hubert_base 2>/dev/null | wc -l) файлов, rmvpe=$([ -f assets/rmvpe/rmvpe.pt ] && echo да || echo НЕТ), f0G40k=$([ -f assets/pretrained/f0G40k.pth ] && echo да || echo НЕТ)"

log "=== 2. Датасет ==="
mkdir -p datasets/milena
cp -n /home/user2/rvc/data/target/*.wav datasets/milena/ 2>/dev/null
log "файлов: $(ls datasets/milena 2>/dev/null | wc -l)"

log "=== 3. Препроцессинг (40k) ==="
mkdir -p "$EXP"   # preprocess.py пишет лог в $EXP/preprocess.log и требует, чтобы каталог был
"$PY" -m train.preprocess datasets/milena 40000 8 logs/smoke False 3.7 2>&1 | tail -3
log "сегментов 0_gt_wavs: $(ls "$EXP/0_gt_wavs" 2>/dev/null | wc -l)"

log "=== 4. F0 (rmvpe, CUDA) ==="
"$PY" -m train.dataset.extract_f0 cuda 1 0 0 logs/smoke true 2>&1 | tail -3
log "2a_f0: $(ls "$EXP/2a_f0" 2>/dev/null | wc -l), 2b-f0nsf: $(ls "$EXP/2b-f0nsf" 2>/dev/null | wc -l)"

log "=== 5. HuBERT (v1, CUDA) ==="
"$PY" -m train.dataset.extract_hubert_feature cuda:0 1 0 0 logs/smoke v1 true 2>&1 | tail -3
log "3_feature256: $(ls "$EXP/3_feature256" 2>/dev/null | wc -l)"

log "=== 6. filelist + config ==="
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

log "=== 7. Обучение (10 эпох) ==="
mkdir -p assets/weights   # без этого training падает: "Parent directory assets/weights does not exist"
"$PY" -m train.train -e smoke -sr 40k -f0 1 -bs 8 -g 0 -te 10 -se 5 \
  -pg assets/pretrained/f0G40k.pth -pd assets/pretrained/f0D40k.pth \
  -l 0 -c 0 -sw 1 -v v1 2>&1 | tail -8
log "инференс-модели (assets/weights): $(ls assets/weights/*.pth 2>/dev/null | head -3)"
log "чекпоинты (logs/smoke): $(ls "$EXP"/G_*.pth 2>/dev/null | head -3)"

log "=== 8. Index ==="
"$PY" -m train.train_index smoke v1 assets/indices 8 single 2>&1 | tail -3
INDEX=$(ls assets/indices/*smoke*.index 2>/dev/null | head -1)
log "index: $INDEX"

log "=== 9. Инференс (Yuri -> Milena) ==="
# Для инференса нужен «малый» чекпоинт (ключи вида weight.*), который training кладёт
# в assets/weights. Сырой G_*.pth из logs/ не подходит: CLI ругается "no weight/emb_g.weight".
MODEL=$(ls -t assets/weights/*.pth 2>/dev/null | head -1)
[ -z "$MODEL" ] && MODEL=$(ls -t "$EXP"/G_*.pth 2>/dev/null | head -1)
log "модель: $MODEL"
if [ -n "$MODEL" ]; then
  "$PY" -m infer.cli --model "$MODEL" \
    --input /home/user2/rvc/data/input/yuri_test_30s_16k.wav \
    --output /home/user2/rvc/smoke_output.wav \
    --f0-method rmvpe --index-rate 0.75 ${INDEX:+--index "$INDEX"} 2>&1 | tail -5
  log "результат: $(ls -la /home/user2/rvc/smoke_output.wav 2>/dev/null)"
else
  log "ОШИБКА: инференс-модель не создана"
fi

log "=== ГОТОВО ==="
echo "SMOKE_DONE"
