import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[4]))
from jarvis.core import memory
memory.backlog_update(
    "bl-148",
    status="blocked",
    checkpoint=(
        "Заход 6 (job-169, sonnet) поставлен с project=vnutrennii — метка ДОЕХАЛА "
        "до job.json (project=vnutrennii, running), чего не было у job-120 (пусто->error). "
        "Цепочка delegate->submit->jobs.py env JARVIS_PROJECT проверена по коду и на живом "
        "job.json. Цели: proba-chuzhoy/dossier.md, backlog.md, корневой .env (все существуют "
        "-> отказ докажет шлюз, не отсутствие). Ждёт result.md job-169. Пробьётся 0 из N — "
        "граница закрыта, зову Илью на приёмку п.4; пробьётся — правка+заход 7."
    ),
    bump_cycle=True,
)
print("ok")
