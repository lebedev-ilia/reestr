import sys
from pathlib import Path

sys.path.insert(0, str(Path("/home/ilya/Рабочий стол/Agent")))
from jarvis.core import memory as M  # noqa: E402

print(M.backlog_update(
    "bl-155",
    status="done",
    checkpoint=("Замер: промпт даёт 82% токенов хода (P=47 900 на шаг, история 1 100 за шаг); "
                "разница с подагентом — модель (sonnet против opus), в токенах он дороже. "
                "Сделано: свежая свёртка едет в промпт головой по разделам, промпт 99,7->92,9 КБ "
                "(коммит 31fd4e2, факт cena-hoda-iz-chego-sostoit). Дальше: ob-105 — проверка на "
                "сутках 01.09; второй множитель (длинные ходы) вынесен в bl-156."),
    bump_cycle=True))
