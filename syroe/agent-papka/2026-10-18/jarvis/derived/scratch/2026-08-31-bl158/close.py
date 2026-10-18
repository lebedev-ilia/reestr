import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))

from jarvis.core import memory as M

print(M.backlog_update(
    "bl-158",
    status="done",
    bump_cycle=True,
    checkpoint=(
        "Готово: в core/selftest.py путь в проверке «взвод хука разрешён» "
        "берётся из C.ROOT (коммит 81049ff). Ложное красное воспроизведено "
        "пробой: чужой путь → deny при ожидании allow. Самотест RC=0, "
        "строка 333 ок. Остальные три зашитых пути (/home/ilya/Agent, "
        "строки 3248/3263/3332) проверены — вердикт шлюза от корня там не "
        "зависит, ложного красного не дадут, править нечего."
    ),
))
