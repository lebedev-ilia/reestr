"""Пересчёт реестра способностей из лога.

Зачем. Числа в memory/registry.md были проставлены руками, и через неделю
они начали бы врать. Реестр, которому нельзя верить, хуже пустого: на вопрос
«что умеешь» он даёт уверенный неверный ответ. Здесь правило счёта каждой
способности записано кодом — один и тот же лог всегда даёт одни и те же числа.

Что важно понимать про эти числа. Машина считает то, что видит в логе, а не то,
что было полезно. «Пачка файлов скачана» и «файлы прочитаны и поняты» для неё
одно и то же событие. Поэтому колонки — это нижняя граница активности, а смысл
(что именно засчитано, чего счёт не видит) живёт в разделе «Что стоит за
строками» того же файла и пишется руками.

Провалы считаются только те, что попали в лог записью error. Провал, который
агент осознал, но не залогировал, скрипту не виден — такие остаются в тексте.

Запуск:
    python -m jarvis.core.registry            # показать, ничего не трогая
    python -m jarvis.core.registry --write    # записать колонки в registry.md
"""

import json
import re
import sys
from collections import defaultdict
from datetime import datetime

from . import config as C
from . import fixtures as FIX


REGISTRY = C.REGISTRY
SENT = C.MEM / "outbox" / "sent.jsonl"
NOTION_WRITES = C.NOTION_WRITES
JOBS = C.JOBS                       # memory/jobs/job-XXX/job.json — задания ролей

MEMORY_TOOLS = {
    "remember_fact", "remember_rule", "remember_procedure",
    "add_obligation", "close_obligation", "update_now", "write_file",
}


# ── чтение источников ────────────────────────────────────────────────────────

def _norm_tool(name: str) -> str:
    """mcp__memory__remember_fact и remember_fact — один и тот же вызов.

    Ход пишет вызов инструмента памяти дважды, под обоими именами. Без
    нормализации каждая запись факта считалась бы за две.
    """
    return re.sub(r"^mcp__[a-z0-9_]+__", "", name or "")


# ── фикстуры самотестов, застрявшие в живом логе ─────────────────────────────
# До вечера 25.08.2026 самотесты писали в НАСТОЯЩИЙ memory/log (bl-061): каждый
# прогон ./jarvis --selftest оставлял там свои моки, а пересчёт считал их
# вызовами способностей. cap-014 показывал 300 прерываний, cap-015 — 53
# просмотра вложений; в жизни их были единицы. Течь перекрыта (selftest_guard
# уводит записи в derived и сам ловит утечку), но старые строки не удалить:
# memory/log — неизменяемая зона. Значит их не считает пересчёт.
#
# Окно заражения ограничено с двух сторон, чтобы фильтр не превратился в
# «вычищаю всё, что похоже на тест»:
#   * по времени — записи старше момента, когда течь закрыли последним из
#     самотестов (коммит b0df7ed, самотест ВК). Всё, что легло позже, писалось
#     уже под guard'ом и живое по построению.
#   * по содержимому — точное совпадение с payload фикстуры, а не похожесть.
# На записи после отсечки фильтр не действует НАМЕРЕННО: будущая утечка должна
# ронять самотест, а не тихо вычищаться здесь.
FIXTURE_WINDOW_END = datetime.fromisoformat("2026-08-25T19:53:31+10:00")
_FIXTURE_PAYLOADS = [
    # core/interrupt_selftest.py — тексты «сообщений Ильи» на моках (MARKERS там же)
    {"interrupt_delivered": ["нужно 11, а не 10"], "at_tool": "Read"},
    {"interrupt_delivered": ["раз", "два"], "at_tool": "Read"},
    {"interrupt_delivered": ["сделай уже"], "at_tool": "Read"},
    {"interrupt_delivered": ["поздний вопрос"], "at_tool": "Read"},
    # modules/vk/selftest.py — мок вложения: две картинки и pdf, всегда ровно 3.
    # Из 245 записей vk_images 240 — ровно эта. Живые просмотры были на 1 и 2
    # вложениях. Настоящее сообщение с тремя вложениями фильтр тоже съест, и это
    # осознанный недосчёт: занизить cap-015 на пару вызовов лучше, чем завысить
    # в полсотни раз. Записи после 25.08 19:38 не трогаются вовсе.
    {"vk_images": 3},
]
_FIXTURE_PAYLOADS += [{"interrupt_delivered": [f"сообщение {i}"], "at_tool": "Read"}
                      for i in range(10)]
FIXTURES = {json.dumps(p, ensure_ascii=False, sort_keys=True) for p in _FIXTURE_PAYLOADS}

# сколько строк отброшено последним load_log() — для отчёта в main()
skipped_fixtures: int = 0


def is_fixture(r: dict) -> bool:
    """Запись лога — след самотеста, а не вызов способности.

    Сначала содержимое, потом время: разбирать дату у каждой строки лога дорого,
    а совпадений по сигнатуре — единицы процентов. Смещения в логе разные
    (+03:00 и +10:00), поэтому даты сравниваются разобранными, а не строками.
    """
    if r.get("type") != "self":
        return False
    payload = r.get("payload") or {}
    # gate-фикстуры ручных проверок границ (25.08, bl-087) — общий список
    # сигнатур с самоаудитом, чтобы числа у них не расходились.
    if FIX.is_gate_fixture(payload, r.get("ts") or ""):
        return True
    if json.dumps(payload, ensure_ascii=False,
                  sort_keys=True) not in FIXTURES:
        return False
    try:
        return datetime.fromisoformat(r.get("ts") or "") < FIXTURE_WINDOW_END
    except ValueError:
        return False


def load_log() -> list[dict]:
    """Все записи лога, разобранные и дедуплицированные.

    У записи появляется поле tool (нормализованное), payload (разобранный
    content) и day (YYYY-MM-DD). Дубли — записи с одинаковыми ts, типом,
    актором и содержимым — схлопываются: демон местами пишет одно событие
    дважды, и это не два вызова способности.
    """
    global skipped_fixtures
    skipped_fixtures = 0
    rows, seen = [], set()
    for path in sorted(C.LOG_DIR.glob("*.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            try:
                payload = json.loads(r.get("content") or "")
            except (json.JSONDecodeError, TypeError):
                payload = {}
            if not isinstance(payload, dict):
                payload = {}
            r["payload"] = payload
            r["tool"] = _norm_tool(payload.get("tool", "")) if r.get("type") == "action" else ""
            r["args"] = str(payload.get("input", "")) if r.get("type") == "action" else ""
            r["day"] = (r.get("ts") or "")[:10]
            # Инструмент пишется дважды: вызов (с input) и результат (с id).
            # Результат — не второй вызов способности, он выбрасывается.
            if r.get("type") == "action" and "input" not in payload:
                continue
            # след прогона самотеста, застрявший в живом логе (см. FIXTURES)
            if is_fixture(r):
                skipped_fixtures += 1
                continue
            key = (r.get("ts"), r.get("type"), r.get("actor"), r["tool"], r["args"] or r.get("content"))
            if key in seen:
                continue
            seen.add(key)
            rows.append(r)
    return rows


def load_jsonl(path) -> list[dict]:
    out = []
    if not path.exists():
        return out
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    return out


# ── правила счёта ────────────────────────────────────────────────────────────
# Каждое правило — функция от записи лога, возвращающая сколько вызовов
# способности эта запись означает (обычно 0 или 1).

def _self(r, key) -> bool:
    return r.get("type") == "self" and key in r["payload"]


def _err(r, key) -> bool:
    """Провал из лога. Ошибки на моках самотеста не считаются: fake_* — это
    проверка кода, а не отказ способности в работе с Ильёй."""
    if r.get("type") != "error" or key not in r["payload"]:
        return False
    return "fake_" not in str(r["payload"].get(key, ""))


def _bash(r, *needles) -> bool:
    return r["tool"] == "Bash" and any(n in r["args"] for n in needles)


LOG_RULES = {
    # id: (успех, провал)
    "cap-001": (lambda r: r.get("type") == "message_in" and r.get("actor") == "ilya",
                lambda r: _err(r, "turn") or _err(r, "vk_turn") or _err(r, "vk_send")),
    "cap-002": (lambda r: _self(r, "vk_reply") and r["payload"]["vk_reply"] == "voice",
                lambda r: _err(r, "vk_voice")),
    "cap-004": (lambda r: _bash(r, "modules.outbox.contacts", "modules.outbox.checkdns"), None),
    "cap-005": (lambda r: _bash(r, "modules.inbox"), None),
    # чтение Notion — любой запуск модуля, кроме правки: она считается по журналу
    "cap-006": (lambda r: _bash(r, "modules.notion") and "modules.notion.write" not in r["args"], None),
    "cap-008": (lambda r: r["tool"] in MEMORY_TOOLS, None),
    "cap-009": (lambda r: r["tool"] == "recall", None),
    # ночной цикл: вызовом считается ДОВЕДЁННАЯ до конца свёртка (night_done),
    # а не начатая. Ключей night_daily/night_weekly в логе не было никогда —
    # night.py пишет {"night_done": "daily"|"weekly"|"monthly"}
    "cap-010": (lambda r: _self(r, "night_done")
                and r["payload"]["night_done"] in ("daily", "weekly"), None),
    "cap-011": (lambda r: _bash(r, "--selftest", "core.selftest"), None),
    "cap-012": (lambda r: _self(r, "self_turn"), None),
    # автодобор пульса: в записи итога хода лежит счётчик досланных статусов
    "cap-013": (lambda r: int(r["payload"].get("auto_status", 0)) if r.get("type") == "self" else 0, None),
    "cap-014": (lambda r: _self(r, "interrupt_delivered"), None),
    "cap-015": (lambda r: _self(r, "vk_images"), None),
    "cap-016": (lambda r: _self(r, "stt_local"), None),
    # cap-017 — планировщик рассылки. Успех считается ДНЯМИ, а не письмами, и
    # потому не здесь, а ниже в recount(): вызов способности — день, в который
    # план был построен и хотя бы одно письмо ушло по плану (запись
    # outbox_sched, её пишет planner.tick после факта отправки). Пятнадцать
    # писем одного дня — один вызов планировщика, а не пятнадцать: письма уже
    # посчитаны у cap-003. Здесь только провал — сорванный тик, его пишет демон.
    "cap-017": (lambda r: 0, lambda r: _err(r, "outbox_planner_tick")),
    # cap-018 — постоянные роли. Успехи и провалы берутся из job.json заданий
    # (ниже): лог знает про job_done, но не знает, что задание было ролевым.
    # Отказов шлюза (zone_denied) в колонке провалов НЕТ намеренно, хотя
    # изначально правило писалось с ними: 27 заходов из 31 упирались в свою
    # зону и при этом сдавали результат. Смешать это с провалом захода —
    # соврать дважды: заход не провалился, а узкая зона осталась бы незаметной
    # внутри общего числа. Отказы считаются отдельно (zone_denied_jobs) и
    # печатаются строкой диагностики, единица измерения колонок одна — заход.
    "cap-018": (lambda r: 0, None),
}


# Задания ролей: origin ставит тот, кто их поднял — modules/agents/cli.py
# ("role", запуск руками) и modules/agents/schedule.py ("schedule", по часам).
# Всё остальное (origin "vk") — обычные подагенты, к ролям отношения не имеют.
ROLE_ORIGINS = ("role", "schedule")


def zone_denied_jobs() -> dict[str, int]:
    """{job_id: сколько раз шлюз отбил выход из зоны}. Диагностика cap-018.

    Связь «отказ → задание» идёт по pid: своей отметки о роли у записи
    zone_denied нет, а pid воркера лежит в job.json. Отказы, чей pid ни с
    одним ролевым заданием не сошёлся, сюда не попадают — это отказы главного
    агента, а не роли.
    """
    by_pid = {j.get("pid"): j["id"] for j in role_jobs() if j.get("pid")}
    out: dict[str, int] = defaultdict(int)
    for r in load_log():
        if _self(r, "zone_denied") and r.get("pid") in by_pid:
            out[by_pid[r["pid"]]] += 1
    return dict(out)


def role_jobs() -> list[dict]:
    """job.json всех заданий ролей. Живут в memory/jobs — переживают derived."""
    out = []
    for meta in sorted(JOBS.glob("job-*/job.json")):
        try:
            m = json.loads(meta.read_text(encoding="utf-8"))
        except Exception:                                     # noqa: BLE001
            continue
        if m.get("origin") in ROLE_ORIGINS:
            out.append(m)
    return out


def recount() -> dict:
    """{cap: {'ok': n, 'fail': n, 'last': 'YYYY-MM-DD' | '—'}}"""
    stat = defaultdict(lambda: {"ok": 0, "fail": 0, "last": ""})
    # Способность с нулём вызовов должна получить честный ноль, а не «правила нет».
    for cap in list(LOG_RULES) + ["cap-003", "cap-007"]:
        stat[cap]

    sched_days = set()          # дни, в которые письмо ушло по плану (cap-017)
    for r in load_log():
        if _self(r, "outbox_sched"):
            sched_days.add(r["day"])
        for cap, (ok_rule, fail_rule) in LOG_RULES.items():
            n = int(ok_rule(r) or 0)
            if n:
                stat[cap]["ok"] += n
                stat[cap]["last"] = max(stat[cap]["last"], r["day"])
            if fail_rule and fail_rule(r):
                stat[cap]["fail"] += 1
                stat[cap]["last"] = max(stat[cap]["last"], r["day"])

    # cap-003 — журнал отправленного, а не лог: письмо уходит внутри модуля.
    # Провал — только ЯВНЫЙ ok: false; строка без поля ok означает «ушло»
    # (bl-132). Формула одна на всех читателей журнала и живёт в send.row_ok.
    from jarvis.modules.outbox.send import row_ok
    for row in load_jsonl(SENT):
        key = "ok" if row_ok(row) else "fail"
        stat["cap-003"][key] += 1
        stat["cap-003"]["last"] = max(stat["cap-003"]["last"], (row.get("ts") or "")[:10])

    # cap-017 — планировщик рассылки: вызов = прожитый день автоотправки.
    # Днями, а не письмами: способность в том, чтобы построить план и разложить
    # его по часовым поясам, и она либо сработала в этот день, либо нет.
    stat["cap-017"]["ok"] += len(sched_days)
    if sched_days:
        stat["cap-017"]["last"] = max(stat["cap-017"]["last"], max(sched_days))

    # cap-018 — постоянные роли: вызов = задание роли, дошедшее до done с
    # результатом на диске. failed и empty — провалы: empty означает, что
    # процесс дожил до конца и не принёс работы, и это ровно тот случай, ради
    # которого статус заводился (core/jobs.py, empty_reason).
    for meta in role_jobs():
        st = meta.get("status")
        if st not in ("done", "failed", "empty"):
            continue                      # queued/running — ещё не итог
        key = "ok" if (st == "done" and meta.get("result")) else "fail"
        stat["cap-018"][key] += 1
        day = (meta.get("finished") or meta.get("created") or "")[:10]
        stat["cap-018"]["last"] = max(stat["cap-018"]["last"], day)

    # cap-007 — журнал правок Notion с «как было»
    for row in load_jsonl(NOTION_WRITES):
        key = "fail" if row.get("error") else "ok"
        stat["cap-007"][key] += 1
        stat["cap-007"]["last"] = max(stat["cap-007"]["last"], (row.get("ts") or "")[:10])

    for v in stat.values():
        v["last"] = v["last"] or "—"
    return dict(stat)


# ── таблица в registry.md ────────────────────────────────────────────────────

ROW = re.compile(r"^\|\s*(cap-\d+)\s*\|")


def apply(text: str, stat: dict) -> tuple[str, list[str]]:
    """Переписывает три последние колонки таблицы. Возвращает (текст, что изменилось)."""
    out, diff = [], []
    for line in text.splitlines():
        m = ROW.match(line)
        if not m:
            out.append(line)
            continue
        cap = m.group(1)
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) != 7 or cap not in stat:
            out.append(line)
            if cap not in stat:
                diff.append(f"{cap}: правила счёта нет — колонки оставлены как есть")
            continue
        s = stat[cap]
        was = (cells[4], cells[5], cells[6])
        now = (s["last"], str(s["ok"]), str(s["fail"]))
        if was != now:
            diff.append(f"{cap}: последний {was[0]}→{now[0]}, успехов {was[1]}→{now[1]}, провалов {was[2]}→{now[2]}")
        cells[4], cells[5], cells[6] = now
        out.append("| " + " | ".join(cells) + " |")
    return "\n".join(out) + ("\n" if text.endswith("\n") else ""), diff


def recount_and_write() -> list[str]:
    """Пересчитать реестр из лога и записать таблицу. Возвращает расхождения.

    Для тех, кто зовёт это без человека (ночной цикл): файл переписывается
    только когда числа действительно разошлись — иначе в гите каждый день
    появлялся бы пустой коммит «реестр».

    Предохранитель: если пересчёт дал круглый ноль по ВСЕМ способностям, таблица
    не трогается. Ноль везде означает не «ничем не пользовались», а «смотрим не
    туда»: именно так живой реестр обнулился, когда ночной самотест подменил
    каталог лога заглушкой, а запись пошла в настоящий файл.
    """
    stat = recount()
    if not any(s["ok"] or s["fail"] for s in stat.values()):
        return []
    text = REGISTRY.read_text(encoding="utf-8")
    new, diff = apply(text, stat)
    if new != text:
        REGISTRY.write_text(new, encoding="utf-8")
    return diff


def main(argv: list[str]) -> int:
    stat = recount()
    text = REGISTRY.read_text(encoding="utf-8")
    new, diff = apply(text, stat)

    for cap in sorted(stat):
        s = stat[cap]
        print(f"{cap}  успехов {s['ok']:>4}  провалов {s['fail']:>3}  последний {s['last']}")
    print()
    if skipped_fixtures:
        print(f"не в счёт: {skipped_fixtures} строк — следы прогонов самотеста "
              f"в живом логе до 25.08 (bl-061)\n")
    denied = zone_denied_jobs()
    if denied:
        print(f"cap-018 отдельно: зона отбивала роль в {len(denied)} заходах из "
              f"{len(role_jobs())}, всего {sum(denied.values())} отказов — "
              f"в колонку провалов это не идёт (заходы сдали результат), "
              f"но узкая зона видна отсюда\n")
    if not diff:
        print("реестр совпадает с логом, менять нечего")
    else:
        print("расхождения с тем, что записано руками:")
        for d in diff:
            print("  " + d)

    if "--write" in argv:
        REGISTRY.write_text(new, encoding="utf-8")
        print(f"\nзаписано: {REGISTRY}")
    elif diff:
        print("\nчтобы записать: python -m jarvis.core.registry --write")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
