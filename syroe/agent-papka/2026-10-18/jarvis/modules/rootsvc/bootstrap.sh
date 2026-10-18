#!/bin/bash
# Бутстрап посредника от root. Запускает ИЛЬЯ, руками, один раз:
#
#   cd "/home/ilya/Рабочий стол/Agent/jarvis" && sudo modules/rootsvc/bootstrap.sh
#
# Тем же способом ставится обновление: агент правит файлы в репозитории,
# а в /opt они попадают только после этой команды. Ровно поэтому агент не
# может расширить себе список разрешённых операций — это делает Илья.
set -euo pipefail

DST=/opt/jarvis-root
SRC="$(cd "$(dirname "$0")" && pwd)"
UNIT=/etc/systemd/system/jarvis-root.service
AGENT_UID=1000

[ "$(id -u)" -eq 0 ] || { echo "нужен root: sudo $0"; exit 1; }

echo "== проверяю белый список до установки"
python3 "$SRC/daemon.py" --check --allow "$SRC/allow.json"

echo "== кладу демон и список в $DST (владелец root, агенту не записать)"
install -d -o root -g root -m 755 "$DST"
install -o root -g root -m 755 "$SRC/daemon.py" "$DST/daemon.py"
install -o root -g root -m 644 "$SRC/allow.json" "$DST/allow.json"

echo "== журнал вызовов /var/log/jarvis-root.jsonl (группа adm читает)"
touch /var/log/jarvis-root.jsonl
chown root:adm /var/log/jarvis-root.jsonl
chmod 640 /var/log/jarvis-root.jsonl

echo "== юнит systemd"
install -o root -g root -m 644 "$SRC/jarvis-root.service" "$UNIT"
systemctl daemon-reload
systemctl enable --now jarvis-root.service
sleep 1
systemctl --no-pager --lines=5 status jarvis-root.service || true

echo
echo "== проверка живьём (от имени агента, uid $AGENT_UID)"
ls -ld /run/jarvis-root/req /run/jarvis-root/resp
setpriv --reuid "$AGENT_UID" --regid "$AGENT_UID" --clear-groups \
    python3 "$SRC/client.py" sys.list path=/etc/sudoers.d | head -5

echo
echo "Готово. Снять всё обратно:"
echo "  sudo systemctl disable --now jarvis-root.service"
echo "  sudo rm -f $UNIT && sudo systemctl daemon-reload"
echo "  sudo rm -rf $DST"
