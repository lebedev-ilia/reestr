#!/bin/bash
# Обновление реестра: принять беседы, собрать упомянутые файлы,
# перестроить указатель, закрепить и отправить в закрытое хранилище.
#
# Запускается крючком Stop после каждого ответа Claude Code и вручную.
# Ничего не должно падать так, чтобы уронить беседу, — отсюда set -u без -e
# и тихие отказы при отсутствии сети.
set -u
KOREN="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$KOREN" || exit 0
ZHURNAL=ukazatel/obnovlenie.log
SEYCHAS=$(date "+%Y-%m-%dT%H:%M:%S")

python3 sluzhba/prinyat.py      >> "$ZHURNAL" 2>&1
python3 sluzhba/sobrat_fayly.py >> "$ZHURNAL" 2>&1
python3 sluzhba/indeks.py       >> "$ZHURNAL" 2>&1
python3 sluzhba/svodka.py       >> "$ZHURNAL" 2>&1

# Закрепляем всегда: это дёшево и делает историю восстановимой по дням.
if [ -n "$(git status --porcelain 2>/dev/null)" ]; then
  git add -A >> "$ZHURNAL" 2>&1
  git -c user.name=jarvis -c user.email=orient_dv@mail.ru \
      commit -q -m "Реестр на $SEYCHAS" >> "$ZHURNAL" 2>&1
fi

# Отправляем не чаще раза в полчаса: каждый ход толкать наружу незачем,
# а полчаса — приемлемая потеря, если макбук откажет.
OTMETKA=ukazatel/.posledniy_push
NUZHNO=1
if [ -f "$OTMETKA" ]; then
  PROSHLO=$(( $(date +%s) - $(date -r "$OTMETKA" +%s) ))
  [ "$PROSHLO" -lt 1800 ] && NUZHNO=0
fi
if [ "$NUZHNO" = "1" ]; then
  if timeout 60 git push -q origin main >> "$ZHURNAL" 2>&1; then
    touch "$OTMETKA"
    echo "$SEYCHAS отправлено наружу" >> "$ZHURNAL"
  else
    echo "$SEYCHAS отправить не вышло — отложено до следующего раза" >> "$ZHURNAL"
  fi
fi

echo "$SEYCHAS обновлено" >> "$ZHURNAL"
