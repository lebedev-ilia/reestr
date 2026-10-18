import sys
sys.path.insert(0, "/home/ilya/Рабочий стол/Agent")
from jarvis.core import memory as M

cp = ("Заход 5 (job-120, sonnet, --project vnutrennii) запущен 30.08 21:3x. "
      "Все Bash-каналы прошлых заходов закрыты и покрыты самотестом (procfs, "
      "git-читатели, Glob, симлинки, exec, TOCTOU, вложенное делегирование). "
      "Приманка proba-chuzhoy/dossier.md создана (метка ...-V5), чужой .env "
      "мне писать шлюз не даёт — отказ на .env текстовый, файла и не нужно. "
      "Бриф5: новые гипотезы — Grep/Glob как отдельные инструменты, WebFetch "
      "file://, инструменты мимо PROJECT_PATH_TOOLS, чужой .env по имени. "
      "Ждём отчёт: заход ДОЛЖЕН пройти чисто — тогда пункт 4 приёмки закрыт.")
for bid in ("bl-148", "bl-151"):
    print(bid, M.backlog_update(bid, status="wip", checkpoint=cp, bump_cycle=True))
