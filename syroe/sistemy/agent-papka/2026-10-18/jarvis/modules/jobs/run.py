#!/usr/bin/env python3
"""Процесс-подагент: один ход по брифу, результат в файл.

  .venv/bin/python -m jarvis.modules.jobs.run <job_id>

Запускается не человеком, а диспетчером (core/jobs.py::_spawn) в отдельной
сессии ОС, чтобы пережить конец хода, который его поставил.

Почему это отдельный процесс, а не инструмент Agent внутри хода: ход — это
одноразовый вызов SDK, он умирает на результате и уносит с собой всё, что
внутри. Отдельный процесс переживает и ход, и перезапуск демона; результат
он оставляет в файле, а не в чьей-то памяти.

Своих правил у подагента нет: он идёт обычным agent.turn() — та же
конституция, тот же шлюз разрешений, тот же лог. Отличий ровно три:
канал self (в ВК не пишет ничего: Илье докладывает главный агент, а не
подагент), своя модель и свой потолок шагов.
"""

import asyncio
import sys
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from jarvis.core import agent, config as C, jobs as J, memory as M  # noqa: E402

PROMPT = """[задание подагента {id}. Тебя запустил главный агент отдельным
процессом; Илья тебя не видит и спросить его ты не можешь — в брифе есть всё,
что тебе дали.]

{brief}

---

Как работать:
1. Бриф самодостаточен. Чего в нём нет — того тебе не разрешали: не пиши Илье,
   не отправляй ничего наружу, не делай необратимого. Не хватило данных —
   так и напиши в результате, это нормальный итог.
2. Разрешено всё внутри папки агента с нулевой ценой ошибки: читать, искать,
   считать, писать файлы в свою папку. Правишь ядро — строка в
   memory/core_changes.md в этом же ходе.
3. Потолок — {max_turns} шагов и {timeout} секунд. Не влезаешь — отдай сделанное
   и назови, что осталось, а не обрывайся молча.
4. Твой ответ целиком уходит в файл {result}, его читает главный агент и
   пересказывает Илье своими словами. Поэтому: сначала вывод в 3-5 строк,
   потом подробности, в конце — что осталось непроверенным и где лежат сырые
   данные. Не пересказывай сам бриф.
5. Илье не пиши ничего и никак."""


async def work(job_id: str) -> int:
    meta = J.read(job_id)
    if meta is None:
        print(f"нет такого задания: {job_id}", file=sys.stderr)
        return 2
    if meta.get("status") in J.DONE:
        print(f"{job_id} уже закончено: {meta['status']}")
        return 0

    brief = J.brief_path(job_id).read_text(encoding="utf-8")
    prompt = PROMPT.format(id=job_id, brief=brief.strip(),
                           max_turns=meta["max_turns"], timeout=meta["timeout"],
                           result=J.rel_path(J.result_path(job_id)))
    try:
        reply = await asyncio.wait_for(
            agent.turn(prompt, actor="job", unattended=True,
                       model=meta.get("model"), max_turns=meta.get("max_turns"),
                       channel="self"),
            timeout=meta["timeout"])
        status = "done"
    except asyncio.TimeoutError:
        reply = (f"Задание оборвано по таймауту ({meta['timeout']} с). "
                 f"Сделанное осталось на диске и в логе, но выжимки нет.")
        status = "failed"
    except Exception as e:                                      # noqa: BLE001
        reply = f"Задание сорвалось: {type(e).__name__}: {e}\n\n{traceback.format_exc()[:2000]}"
        status = "failed"
        M.log("error", {"job": job_id, "err": f"{type(e).__name__}: {e}"})

    last = getattr(agent, "LAST_RESULT", {}) or {}
    J.finish(job_id, status, reply,
             cost_usd=last.get("cost_usd"), turns=last.get("turns"))
    # finish мог переписать done на empty: работы в результате нет. Итоговый
    # статус берём из журнала, а не из своей переменной.
    status = (J.read(job_id) or {}).get("status", status)
    # Заход упёрся в потолок шагов — работа не сделана, а брошена на середине.
    # Продолжение заводится ЗДЕСЬ, а не в главном агенте: между заходами меня
    # нет, и незаведённое не подберёт никто (bl-157, тот же разрыв, что bl-150
    # закрыла для своих ходов).
    if last.get("hit_max_turns"):
        child = J.continue_maxed(job_id)
        if not child:
            # Продолжения не будет (цепочка исчерпана или submit не прошёл) —
            # тогда остаток кладём в очередь отсюда: сам подагентский ход
            # строки себе больше не заводит (иначе выходил дубль работы,
            # 01.09.2026 job-175 + bl-160), и брошенное не подберёт никто.
            parked = agent.park_unfinished(
                prompt, last.get("ref") or f"job:{job_id}", "job", "job",
                int(last.get("turns") or 0),
                int(meta.get("max_turns") or C.JOB_MAX_TURNS), force=True)
            if parked:
                print(f"{job_id}: остаток положен в очередь строкой {parked}")
        print(f"{job_id}: оборван по потолку шагов, "
              + (f"продолжение {child}" if child else "продолжений больше нет"))
    print(f"{job_id}: {status}")
    return 0 if status == "done" else 1


def main(argv: list[str]) -> int:
    # Воркер подагента — настоящая работа: его след идёт в memory/log (bl-087).
    M.mark_live()
    if len(argv) != 1:
        print(__doc__)
        return 2
    return asyncio.run(work(argv[0]))


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
