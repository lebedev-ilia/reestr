"""Самотест пульса — без сети и без ВК. Запуск:

    cd "…/Agent" && jarvis/.venv/bin/python -m jarvis.core.heartbeat_selftest

Проверяет ровно то, из-за чего пульс вообще написан: статус уходит сам, когда
я долго молчу, и НЕ уходит, когда я только что писал или когда ночь.
"""
import time

from jarvis.core import heartbeat as HB

sent: list[str] = []
fake = lambda text: (sent.append(text), True)[1]  # noqa: E731
fails: list[str] = []


def check(name: str, ok: bool) -> None:
    print(("  ок   " if ok else "  ПЛОХО") + f"  {name}")
    if not ok:
        fails.append(name)


def run() -> int:
    HB.quiet_now = lambda now=None: False        # день, автостатусы разрешены

    # 1. Терминал: интервал 0 — молчим всегда, сколько бы ни прошло.
    HB.start("cli")
    HB._write_last(time.time() - 3600)
    sent.clear()
    HB.beat("читаю файл", sender=fake)
    check("cli: статус в ВК не шлётся", not sent)

    # 2. ВК: только что писал сам — добора быть не должно.
    HB.start("vk")
    HB.mark()
    sent.clear()
    HB.beat("читаю файл", sender=fake)
    check("vk: свежий ручной статус гасит автодобор", not sent)

    # 3. ВК: молчу дольше минуты — статус уходит, и в нём время и дело.
    HB._write_last(time.time() - 90)
    sent.clear()
    HB.beat("правлю core/agent.py", sender=fake)
    check("vk: после 90 секунд молчания статус ушёл", len(sent) == 1)
    check("vk: в статусе есть время и дело",
          bool(sent) and ":" in sent[0] and "core/agent.py" in sent[0])

    # 3б. В ВК уходят слова, а не команда: 24.08 Илья получил в чат десяток
    #     строк вида «Bash: cd ... && git commit -q -F -». Журналу шагов
    #     команда нужна, ему — нет.
    HB._write_last(time.time() - 90)
    sent.clear()
    HB.beat("Bash: git commit -q -F - <<'MSGEOF'", sender=fake,
            say="Коммичу правки памяти")
    check("vk: в статусе слова, а не команда",
          bool(sent) and "Коммичу правки памяти" in sent[0]
          and "git commit" not in sent[0])

    # 4. Отправка сбрасывает таймер: следующий шаг сразу — тишина.
    sent.clear()
    HB.beat("следующий шаг", sender=fake)
    check("vk: два статуса подряд не шлются", not sent)

    # 5. Фоновый ход молчит всегда: Илья его не ждёт (см. INTERVALS в
    #    heartbeat.py). Проверяем и на трёх минутах, и на часе — раньше здесь
    #    стоял порог 180 с, и 20 фоновых ходов за день шли ему в чат.
    HB.start("self")
    HB._write_last(time.time() - 200)
    sent.clear()
    HB.beat("фоновая работа", sender=fake)
    check("self: через 200 секунд молчим", not sent)
    HB._write_last(time.time() - 3600)
    HB.beat("фоновая работа", sender=fake)
    check("self: и через час молчим", not sent)
    check("self: шаги всё равно считаются", HB.summary()["steps"] == 2)

    # 6. Ночь: не будим ни при каком молчании.
    HB.quiet_now = lambda now=None: True
    HB.start("vk")
    HB._write_last(time.time() - 3600)
    sent.clear()
    HB.beat("ночная работа", sender=fake)
    check("ночь: автостатус не уходит", not sent)

    # 7. Шаги считаются и попадают в файл со временем.
    HB.start("vk")
    HB.beat("шаг раз", sender=fake)
    HB.beat("шаг два", sender=fake)
    s = HB.summary()
    check("сводка: два шага и канал vk", s["steps"] == 2 and s["channel"] == "vk")
    check("журнал шагов на диске", HB._steps_file().exists())

    print("\nВСЁ ХОРОШО" if not fails else f"\nСЛОМАНО: {fails}")
    return 1 if fails else 0


if __name__ == "__main__":
    raise SystemExit(run())
