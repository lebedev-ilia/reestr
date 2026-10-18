#!/usr/bin/env bash
# cleanup_rvc_server.sh — убрать следы нашей работы на ЧУЖОМ сервере (172.30.4.134).
#
# Зачем: 2026-10-09 в /home/user2/rvc мы начали разворачивать окружение RVC для заказа
# с фриланса. Сервер принадлежит ООО «Автоматика» и выдан под их ТЗ (п. 1.2, п. 4),
# поэтому работа для другого клиента на нём — использование не по назначению.
# См. правила: my_agent/rules_foreign_servers.md
#
# Запуск (по решению пользователя): bash /home/user2/cleanup_rvc_server.sh
set -x

echo "=== ДО очистки ==="
df -h /home | tail -1
du -sh /home/user2/rvc /home/user2/.cache/pip 2>/dev/null

echo "=== Останавливаем наши процессы (если остались) ==="
pkill -f "setup_rvc_env2.sh" 2>/dev/null
pkill -f "setup_rvc_env.sh" 2>/dev/null
pkill -f "/home/user2/rvc/venv" 2>/dev/null
sleep 1

echo "=== Удаляем созданное нами ==="
rm -rf /home/user2/rvc
rm -rf /home/user2/.cache/pip
rm -f /home/user2/setup_rvc_env.sh /home/user2/setup_rvc_env2.sh
rm -f /home/user2/rvc_setup.log /home/user2/rvc_setup2.log
rm -f /home/user2/check_coverage.sh /home/user2/ann_names.tsv
rm -f /home/user2/_need.txt /home/user2/_missing.txt
rm -rf /tmp/pip-* 2>/dev/null

echo "=== ПОСЛЕ очистки ==="
df -h /home | tail -1
echo "--- остатки (должно быть пусто) ---"
ls -la /home/user2/ | grep -Ei "rvc|setup|ann_names|_need|_missing" || echo "чисто"
echo "--- GPU свободна? ---"
nvidia-smi --query-gpu=memory.used,utilization.gpu --format=csv,noheader
echo "=== ГОТОВО ==="
