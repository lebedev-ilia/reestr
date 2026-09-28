#!/bin/bash
# Обновление реестра: принять новые беседы, перестроить указатель.
# Запускается крючком Claude Code после каждого ответа и вручную.
set -u
KOREN="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$KOREN" || exit 0
python3 sluzhba/prinyat.py >> ukazatel/obnovlenie.log 2>&1
python3 sluzhba/indeks.py  >> ukazatel/obnovlenie.log 2>&1
echo "$(date "+%Y-%m-%dT%H:%M:%S") обновлено" >> ukazatel/obnovlenie.log
