#!/usr/bin/env python3
"""Запуск агента.

  python run.py                — диалог в терминале
  python run.py "вопрос"       — один ход
  python run.py --vk           — канал связи через сообщество ВК
  python run.py --vk-check     — диагностика канала ВК (почему молчит)
  python run.py --night        — ночной цикл вручную (--night due: если пора)
  python run.py --context      — показать, что уходит в контекст (отладка)

Работает через подписку Claude: нужен установленный и залогиненный Claude Code,
API-ключ не требуется. Проверка: `claude --version` и `claude login`.

Каналов два: терминал (часть семени) и ВК (`modules/vk`, агент собрал себе сам).
Память у них общая — это один агент, вход разный.
"""

import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)) + "/..")

from jarvis.core import agent, memory, night, config  # noqa: E402


async def repl():
    print(f"jarvis · {config.MODEL_MAIN} · Ctrl-C для выхода\n")
    loop = asyncio.get_event_loop()
    while True:
        try:
            line = (await loop.run_in_executor(None, input, "› ")).strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return
        if not line:
            continue
        if line in ("exit", "quit"):
            return
        print()
        print(await agent.turn(line, progress=True))
        print()


async def main():
    # Точка входа настоящей работы: отсюда и ниже записи идут в живой
    # memory/log, а разовые скрипты остаются без этого права (bl-087).
    memory.mark_live()
    args = sys.argv[1:]
    if not args:
        return await repl()
    if args[0] == "--vk":
        # Демон ВК по определению живая работа: унаследованный флаг
        # самотеста здесь — всегда ошибка окружения (bl-121), а не тихая
        # особенность (core/memory.py:enforce_live_no_selftest).
        memory.enforce_live_no_selftest()
        from jarvis.modules.vk import daemon
        return await daemon.run()
    if args[0] == "--vk-check":
        from jarvis.modules.vk import check
        return sys.exit(await check.check())
    if args[0] == "--night":
        # без аргумента — свернуть последний закрытый период принудительно;
        # --night due — только если пора (то же, что делает демон сам)
        return await night.run(force=("due" not in args[1:]))
    if args[0] == "--selftest":
        # Все машинные проверки одной командой: ядро, пульс, ночной цикл.
        # Прогонять после каждой правки ядра — иначе поломка тихая.
        from jarvis.core import (selftest, heartbeat_selftest, night_selftest,
                                 interrupt_selftest, jobs_selftest,
                                 scratch_selftest, restart_selftest,
                                 projects_selftest)
        from jarvis.modules.projects import selftest as projects_sum_selftest
        from jarvis.modules.vk import plaintext_selftest
        from jarvis.modules.vk import selftest as vk_selftest
        from jarvis.modules.fs import selftest as fs_selftest
        from jarvis.modules.outbox import selftest as outbox_selftest
        from jarvis.modules.outbox import send_selftest as outbox_send_selftest
        from jarvis.modules.outbox.wave2 import planner_selftest as outbox_planner_selftest
        from jarvis.modules.outbox.wave2 import validate_selftest as outbox_validate_selftest
        from jarvis.modules.inbox import selftest as inbox_selftest
        from jarvis.modules.notion import selftest as notion_selftest
        from jarvis.modules.rootsvc import selftest as rootsvc_selftest
        from jarvis.modules.voice import speakable_selftest
        from jarvis.modules.audit import daily_selftest as audit_daily_selftest
        from jarvis.modules.export import selftest as export_selftest
        # Флаг поднимается на весь прогон и наследуется подпроцессами:
        # ни один набор проверок не пишет в живой memory/log (bl-061).
        config.SELFTEST = True
        os.environ["JARVIS_SELFTEST"] = "1"
        codes = []
        for name, mod in (("ЯДРО", selftest), ("ПУЛЬС", heartbeat_selftest),
                          ("НОЧНОЙ ЦИКЛ", night_selftest),
                          ("ПЕРЕЗАПУСК ПО УСТАРЕВШЕМУ КОДУ", restart_selftest),
                          ("УБОРКА SCRATCH", scratch_selftest),
                          ("ФОРМАТ ВК", plaintext_selftest),
                          ("ПРЕРЫВАНИЕ", interrupt_selftest),
                          ("ПОИСК ПО ФАЙЛАМ", fs_selftest),
                          ("ФОНОВЫЕ ЗАДАНИЯ", jobs_selftest),
                          ("ПРОТОКОЛ ЗАДАЧИ ПРОЕКТА", projects_selftest),
                          ("СВОДКА, РАСХОД И ОЧЕРЕДЬ ПРОЕКТА",
                           projects_sum_selftest),
                          ("РАЗБОР КОНТАКТОВ", outbox_selftest),
                          ("ОТПРАВКА ПИСЕМ", outbox_send_selftest),
                          ("ПЛАНИРОВЩИК РАССЫЛКИ", outbox_planner_selftest),
                          ("ПРИЁМКА ПИСЕМ", outbox_validate_selftest),
                          ("РАЗБОР ВХОДЯЩИХ", inbox_selftest),
                          ("NOTION", notion_selftest),
                          ("ПОДГОТОВКА РЕЧИ", speakable_selftest),
                          ("ПОСРЕДНИК ОТ ROOT", rootsvc_selftest),
                          ("АУДИТ СУТОК", audit_daily_selftest),
                          ("СБОРКА ДОСЬЕ", export_selftest),
                          ("КАНАЛ ВК", vk_selftest)):
            print(f"\n═══ {name} ═══")
            # В отдельном потоке: main() асинхронный, а тесты внутри зовут
            # asyncio.run() — внутри работающего цикла он падает, и падение
            # маскируется под провал проверки.
            entry = getattr(mod, "run", None) or mod.main
            codes.append(await asyncio.to_thread(entry))
        return sys.exit(max(codes))
    if args[0] == "--context":
        print(memory.build_context())
        return
    print(await agent.turn(" ".join(args)))


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print()
