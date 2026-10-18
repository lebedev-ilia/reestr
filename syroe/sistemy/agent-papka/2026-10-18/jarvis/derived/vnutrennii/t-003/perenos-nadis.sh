#!/usr/bin/env bash
# Перенос крупных стабильных данных TrendFlowML на второй диск.
# НЕ ЗАПУСКАТЬ ВСЛЕПУЮ. Это черновик по итогам t-003 (карта дисков),
# ни разу не выполнялся в ходе задачи. Перед запуском:
#  - проверить, что второй диск примонтирован (после перезагрузки монтирование
#    не переживает, см. memory/facts/pc-dostup-ustroystvo.md);
#  - решить с Ильёй, какие именно пункты переносить сейчас;
#  - убедиться, что источники не используются процессом в момент переноса.
set -euo pipefail

DEST="/run/media/ilya/Новый том/perenos-trendflowml"
# На момент написания скрипта (30.08.2026) диск НЕ смонтирован и не в fstab —
# монтирование делает Илья (нужен root). Проверка mountpoint ниже сама остановит
# скрипт, если это условие ещё не выполнено.

# Первая и обязательная проверка: назначение реально смонтировано,
# а не пустой каталог на системном диске (иначе перенос "прошёл бы" на sda2).
mountpoint -q "/run/media/ilya/Новый том" || {
    echo "Второй диск не смонтирован — прерываю, ничего не делаю." >&2
    exit 1
}

mkdir -p "$DEST"

# Список источник -> подпапка назначения. rsync с -a сохраняет права и время,
# без --remove-source-files: источник не трогаем, пока перенос не подтверждён
# и не проверен вручную. Удаление исходников — отдельный осознанный шаг,
# в этот скрипт сознательно не включён.
SOURCES=(
  "/home/ilya/Рабочий стол/TrendFlowML/DataProcessor/dp_models:dp_models"
  "/home/ilya/Рабочий стол/TrendFlowML/storage:storage"
  "/home/ilya/Рабочий стол/TrendFlowML/DataProcessor/triton:triton"
  "/home/ilya/Рабочий стол/TrendFlowML/wav2vec2_checkpoint:wav2vec2_checkpoint"
  "/home/ilya/Рабочий стол/TrendFlowML_evac:TrendFlowML_evac"
  "/home/ilya/rp_backups:rp_backups"
)

for entry in "${SOURCES[@]}"; do
  src="${entry%%:*}"
  dst="${entry##*:}"
  if [ ! -e "$src" ]; then
    echo "Пропуск: источник не найден: $src" >&2
    continue
  fi
  echo "Перенос: $src -> $DEST/$dst"
  rsync -aH --info=progress2 "$src"/ "$DEST/$dst"/
done

echo "Готово. Исходники на месте, не удалены — сверить содержимое и решить об удалении отдельно."
