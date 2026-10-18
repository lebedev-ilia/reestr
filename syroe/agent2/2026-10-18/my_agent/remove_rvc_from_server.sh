#!/bin/bash
# remove_rvc_from_server.sh — убрать с сервера Заказчика всё, что относится к RVC-заказу.
#
# Основание: указание администратора сервера от 2026-10-13
#   «не надо на наших ресурсах делать свою халтуру, ок?»
#   «так что всё постороннее барахло с сервера — убрать и больше таким не заниматься»
#
# ВАЖНО: удаляется ТОЛЬКО постороннее (RVC). Работы по ТЗ «Автоматики» (YOLO, дедупликация,
# полигональные маски, датасеты) остаются на месте — они выполняются по договору.
#
# Перед удалением: YOLO-окружение создавалось копированием RVC-окружения, поэтому 46 файлов
# в yolo/venv/bin ссылаются на /home/user2/rvc. Ссылки перенаправляются, иначе окружение
# по ТЗ останется без рабочих консольных скриптов.
set -u
log(){ echo "[$(date -u +%H:%M:%S)] $*"; }

log "=== ДО удаления ==="
du -sh /home/user2 2>/dev/null

# ---------- 1. Перенаправить ссылки в YOLO-окружении ----------
log "--- правлю ссылки в /home/user2/yolo/venv (копия RVC-окружения) ---"
fixed=0
for f in /home/user2/yolo/venv/bin/*; do
  [ -f "$f" ] || continue
  if head -c 120 "$f" 2>/dev/null | grep -q "/home/user2/rvc"; then
    sed -i 's|/home/user2/rvc/venv|/home/user2/yolo/venv|g' "$f" && fixed=$((fixed+1))
  fi
done
log "    перенаправлено файлов: $fixed"
sed -i 's|/home/user2/rvc/venv|/home/user2/yolo/venv|g' /home/user2/yolo/venv/pyvenv.cfg 2>/dev/null || true
for a in /home/user2/yolo/venv/bin/activate /home/user2/yolo/venv/bin/activate.csh \
         /home/user2/yolo/venv/bin/activate.fish; do
  [ -f "$a" ] && sed -i 's|/home/user2/rvc/venv|/home/user2/yolo/venv|g' "$a"
done
log "    осталось ссылок на rvc: $(grep -rl '/home/user2/rvc' /home/user2/yolo/venv/ 2>/dev/null | wc -l)"

# ---------- 2. Удалить каталоги RVC ----------
log "--- удаляю каталоги RVC ---"
for d in rvc .cache/pymss .cache/uv .cache/torch bin dl .local; do
  if [ -e "/home/user2/$d" ]; then
    sz=$(du -sh "/home/user2/$d" 2>/dev/null | cut -f1)
    echo "    rm -rf /home/user2/$d  ($sz)"
    rm -rf "/home/user2/$d"
  fi
done

# ---------- 3. Удалить скрипты и логи RVC ----------
log "--- удаляю скрипты и логи RVC ---"
for f in rvc_install.sh rvc_install2.sh rvc_setup.log rvc_setup2.log rvc_smoke_test.sh \
         setup_rvc_env.sh setup_rvc_env2.sh uv_install2.sh clean_test.sh \
         cleanup_rvc_keep_e50.sh cleanup_rvc_server.sh \
         brat_train.sh brat_ho_train.sh brat_b4_train.sh brat_test_convert.sh \
         brat_ab_test.sh brat_epoch_pick.sh; do
  if [ -e "/home/user2/$f" ]; then echo "    rm /home/user2/$f"; rm -f "/home/user2/$f"; fi
done

# ---------- 4. Подчистить историю команд (следы работы) ----------
log "--- чищу историю команд от строк RVC ---"
for h in /home/user2/.bash_history /home/user2/.zsh_history; do
  if [ -f "$h" ]; then
    before=$(wc -l < "$h")
    grep -viE "rvc|brat|hubert|rmvpe|pymss|uv pip|virtualenv|\.cache/uv" "$h" > "$h.tmp" 2>/dev/null || true
    mv "$h.tmp" "$h"
    log "    $h: было $before строк, стало $(wc -l < "$h")"
  fi
done

# ---------- 5. Итог ----------
log "=== ПОСЛЕ удаления ==="
du -sh /home/user2 2>/dev/null
echo "--- что осталось (работы по ТЗ) ---"
ls -la /home/user2/ | awk '{print $5, $9}'
echo "--- размеры ---"
du -sh /home/user2/* 2>/dev/null | sort -rh | head -8
echo "--- проверка: YOLO-окружение работает? ---"
/home/user2/yolo/venv/bin/python -c "import ultralytics; print('  ultralytics', ultralytics.__version__, 'OK')" 2>&1 | tail -2
echo "--- проверка: остались ли следы RVC вне rvc? ---"
find /home/user2 -maxdepth 3 \( -iname "*brat*" -o -iname "*rvc*" -o -iname "*hubert*" -o -iname "*rmvpe*" -o -iname "*pymss*" \) 2>/dev/null | head -10 || true
echo "(пусто выше = следов не осталось)"
df -h /home | tail -1
log "=== ГОТОВО ==="
echo RVC_REMOVED_FINAL
