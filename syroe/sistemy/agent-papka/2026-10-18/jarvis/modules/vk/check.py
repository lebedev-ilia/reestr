#!/usr/bin/env python3
"""Диагностика канала ВК: почему агент молчит.

  ./jarvis --vk-check

Проверяет всё, что стоит между сообщением Ильи и ходом агента, и говорит,
что именно сломано. Ничего не меняет — только читает.

Написано после случая 23.08.2026: демон был жив и переписывал состояние,
но ts не двигался, а сообщения не приходили. По логам это выглядело
неотличимо от «агент завис», хотя проблема была вне агента.
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from jarvis.core import config as C  # noqa: E402
from jarvis.modules.vk import api  # noqa: E402
from jarvis.modules.vk.daemon import load_env, read_state  # noqa: E402

OK, BAD, WARN = "  ✓", "  ✗", "  ⚠"


async def check() -> int:
    problems = []

    print("Проверка канала ВК\n")

    # 0. Сколько демонов запущено. Самая частая причина задвоенных ответов:
    #    каждая копия разбирает то же событие и отвечает своим ходом.
    import subprocess
    try:
        out = subprocess.run(["pgrep", "-af", "run.py --vk"],
                             capture_output=True, text=True, timeout=5).stdout
        running = [l for l in out.splitlines() if "--vk-check" not in l and l.strip()]
        if len(running) > 1:
            print(BAD, f"запущено демонов: {len(running)} — ответы будут дублироваться")
            for l in running:
                print("     ", l[:100])
            problems.append("Оставь один демон: kill лишним pid, затем перезапусти")
        elif len(running) == 1:
            print(OK, f"демон запущен · {running[0].split()[0]}")
        else:
            print(WARN, "демон не запущен — отвечать некому (./jarvis --vk)")
    except Exception:
        pass

    # 1. Доступы
    try:
        token, group_id, owner_id = load_env()
    except SystemExit as e:
        print(BAD, e)
        return 1
    print(OK, f"доступы на месте · сообщество {group_id} · владелец {owner_id or '—'}")
    if not owner_id:
        problems.append("VK_OWNER_ID не задан — агент не ответит никому")
        print(WARN, "VK_OWNER_ID не задан: агент никому не отвечает")

    # 2. Токен
    try:
        me = await asyncio.to_thread(api.call, "groups.getById", token, group_id=group_id)
        groups = me.get("groups", []) if isinstance(me, dict) else me
        name = groups[0]["name"] if groups else f"id{group_id}"
        print(OK, f"токен принят · сообщество «{name}»")
    except Exception as e:
        print(BAD, f"токен отклонён: {e}")
        return 1

    # 3. Настройки Long Poll — самая частая причина молчания.
    #    Демон при этом выглядит здоровым: он опрашивает сервер, тот отвечает
    #    пустотой, ts не двигается.
    try:
        s = await asyncio.to_thread(api.call, "groups.getLongPollSettings", token,
                                    group_id=group_id)
        enabled = s.get("is_enabled")
        version = str(s.get("api_version", "?"))
        events = s.get("events", {}) or {}
        msg_new = events.get("message_new")

        if enabled:
            print(OK, f"Long Poll включён · версия {version}")
        else:
            print(BAD, "Long Poll ВЫКЛЮЧЕН в настройках сообщества")
            problems.append(
                "Включи: Сообщество → Управление → Работа с API → Long Poll API")

        if msg_new:
            print(OK, "событие «Входящее сообщение» включено")
        else:
            print(BAD, "событие «Входящее сообщение» ВЫКЛЮЧЕНО")
            problems.append(
                "Включи: Работа с API → Long Poll API → Типы событий → "
                "Входящее сообщение")

        if version and not version.startswith("5.1"):
            print(WARN, f"версия событий {version} — ожидается 5.199")
            problems.append(f"Поставь версию 5.199 вместо {version}")
    except Exception as e:
        print(WARN, f"настройки Long Poll не прочитались: {e}")
        problems.append("Токену не хватает прав на чтение настроек сообщества")

    # 4. Сообщения сообщества
    try:
        st = await asyncio.to_thread(api.call, "groups.getSettings", token,
                                     group_id=group_id)
        if st.get("messages"):
            print(OK, "сообщения сообщества включены")
        else:
            print(BAD, "сообщения сообщества ВЫКЛЮЧЕНЫ — писать некуда")
            problems.append("Включи: Управление → Сообщения → Сообщения сообщества")
    except Exception:
        pass  # прав может не быть, это не критично

    # 5. Позиция в очереди: расходится ли сохранённая с настоящей
    try:
        lp = await asyncio.to_thread(api.call, "groups.getLongPollServer", token,
                                     group_id=group_id)
        live_ts = str(lp.get("ts", "?"))
        saved = read_state()
        saved_ts = str(saved.get("ts", "—"))
        print(OK, f"сервер выдан · ts сейчас {live_ts} · сохранён {saved_ts}")

        if saved_ts not in ("—", live_ts) and saved_ts.isdigit() and live_ts.isdigit():
            if int(saved_ts) < int(live_ts):
                print(WARN, f"сохранённый ts отстаёт на {int(live_ts) - int(saved_ts)} "
                            f"— пропущенное подхватится при старте")
            else:
                print(BAD, "сохранённый ts ОПЕРЕЖАЕТ настоящий — события пропускаются")
                problems.append("Удали derived/vk.json и перезапусти демон")

        seen = saved.get("seen") or {}
        if seen:
            print(OK, f"счётчик прочитанного: {seen}")
            if owner_id and str(owner_id) in seen:
                print("     если сообщения приходят, но игнорируются — "
                      "их id меньше сохранённого; удали derived/vk.json")
    except Exception as e:
        print(BAD, f"сервер Long Poll не выдан: {e}")
        return 1

    # 6. Живая проверка: ждём событие 10 секунд
    print("\n  Слушаю 10 секунд — напиши сообщество сейчас…")
    try:
        res = await asyncio.to_thread(api.poll, lp["server"], lp["key"], lp["ts"], 10)
        ups = res.get("updates", [])
        if res.get("failed"):
            print(WARN, f"ВК ответил failed={res['failed']} — ключ или ts протухли, "
                        f"демон это чинит сам")
        elif ups:
            kinds = [u.get("type") for u in ups]
            print(OK, f"событий получено: {len(ups)} · {kinds}")
            if "message_new" not in kinds:
                problems.append("События идут, но message_new среди них нет")
        else:
            print(WARN, "за 10 секунд ни одного события")
            print("     Если ты писал в это время — проблема на стороне ВК: "
                  "проверь пункты выше.")
            print("     Если не писал — это нормально.")
    except Exception as e:
        print(BAD, f"опрос не прошёл: {e}")
        problems.append("Сеть или сервер Long Poll недоступны")

    print()
    if problems:
        print("Что чинить:")
        for i, p in enumerate(problems, 1):
            print(f"  {i}. {p}")
        return 1

    print("  Со стороны ВК всё в порядке.")
    print("  Если агент всё равно молчит — проверь, что демон запущен:")
    print("    pgrep -af 'run.py --vk'  и  tail -f derived/vk.log")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(check()))
