"""Инструменты памяти как встроенный MCP-сервер Agent SDK.

Файлы, шелл, интернет и поиск дают штатные инструменты SDK (Read, Write, Edit,
Bash, WebFetch, WebSearch, Glob, Grep) — писать их заново незачем.

Здесь остаётся только то, чего в SDK нет и не может быть: запись в память
в строго заданном формате. Формат — единственное, что нельзя нарушать,
поэтому он задан кодом, а не надеждой на аккуратность модели.
"""

from typing import Any

from claude_agent_sdk import tool, create_sdk_mcp_server, ToolAnnotations

from . import memory as M

CLOSED = ToolAnnotations(readOnlyHint=False, openWorldHint=False)


def _ok(text: str) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": text}]}


@tool("remember_fact",
      "Записать устойчивый факт о мире Ильи. Только то, что останется верным через "
      "месяц. События сюда не пишутся — они уже в логе.",
      {"id": str, "type": str, "name": str, "body": str, "source": str},
      annotations=CLOSED)
async def remember_fact(args):
    path = M.write_fact(args["id"], args["type"], args["name"], args["body"],
                        args.get("source", ""), args.get("links"))
    M.log("action", {"tool": "remember_fact", "id": args["id"]})
    return _ok(f"факт записан: {path}")


@tool("remember_rule",
      "Записать выученное правило поведения. Вызывать после каждой поправки Ильи. "
      "Причина (why) и область применения (scope) обязательны: без причины правило "
      "нельзя обобщить и нельзя вовремя отменить, без области оно станет догмой. "
      "Тот же id — обновление: переписываются только переданные разделы, дата "
      "рождения и приписки в файле остаются. Пустой how ничего не стирает.",
      {"id": str, "rule": str, "why": str, "scope": str, "how": str, "source": str},
      annotations=CLOSED)
async def remember_rule(args):
    path = M.write_rule(args["id"], args["rule"], args["why"], args["scope"],
                        args.get("how", ""), args.get("source", ""))
    M.log("action", {"tool": "remember_rule", "id": args["id"]})
    return _ok(f"правило записано: {path}")


@tool("remember_procedure",
      "Записать способ решения задачи. Только после реального успешного выполнения. "
      "Со второго раза задача станет дешёвой. Тот же id — обновление: тело "
      "переписывается целиком, а счётчики запусков, дата рождения, статус и "
      "связи из старой шапки сохраняются.",
      {"id": str, "trigger": str, "steps": str, "preconditions": str,
       "pitfalls": str, "cost": str},
      annotations=CLOSED)
async def remember_procedure(args):
    path = M.write_procedure(args["id"], args["trigger"], args["steps"],
                             args.get("preconditions", ""), args.get("pitfalls", ""),
                             args.get("cost", "slow"), args.get("links"))
    M.log("action", {"tool": "remember_procedure", "id": args["id"]})
    return _ok(f"процедура записана: {path}")


@tool("add_obligation",
      "Зафиксировать обязательство или срок. Всё, что нельзя забыть, идёт сюда, "
      "а не в факты: реестр перечитывается механически каждый цикл, "
      "а факты нужно найти — и однажды они не найдутся.",
      {"what": str, "who": str, "due": str, "source": str},
      annotations=CLOSED)
async def add_obligation(args):
    what = args["what"]
    ob_id = M.add_obligation(what, args["who"], args["due"], args.get("source", ""))
    M.log("action", {"tool": "add_obligation", "id": ob_id})
    msg = f"обязательство {ob_id} записано"
    # Жалоба в лог никого не остановила: 24–25.08 она честно сработала на
    # ob-032 и ob-033 и осталась незамеченной — своё же событие я вижу только
    # СЛЕДУЮЩИМ ходом, среди тысяч других, когда уже не помню, что там было
    # существенно. Поэтому предупреждение возвращается в ответе инструмента:
    # оно приходит сразу и в том ходе, где текст ещё под рукой.
    if len(what) > M.OB_WHAT_LIMIT:
        msg += (f". ВНИМАНИЕ: {len(what)} знаков при норме {M.OB_WHAT_LIMIT} — "
                "перепиши сейчас короче («что сделать, к какому сроку, чего нельзя»), "
                "разбор уведи в факт или оставь в логе. Правило obyazatelstvo-ne-razbor")
    return _ok(msg)


@tool("close_obligation",
      "Изменить статус обязательства: done, dropped или open. open — это перенос: "
      "он увеличивает счётчик переносов (ценный сигнал) и ТРЕБУЕТ due — новый срок "
      "в формате ГГГГ-ММ-ДД, если обязательство уже просрочено. Без нового срока "
      "перенос не принимается: строка снова станет overdue завтра.",
      {"id": str, "status": str, "due": str}, annotations=CLOSED)
async def close_obligation(args):
    res = M.set_obligation_status(args["id"], args["status"], args.get("due") or None)
    M.log("action", {"tool": "close_obligation", "id": args["id"],
                     "ok": res["ok"], "due": res.get("due"),
                     "deferrals": res.get("deferrals")})
    if not res["ok"]:
        return _ok(f"НЕ СДЕЛАНО: {res['reason']}")
    msg = f"{args['id']} → {args['status']}, срок {res['due']}"
    if res["deferred"]:
        msg += f", перенос №{res['deferrals']}"
    return _ok(msg)


@tool("morning_review",
      "Отметить утренний разбор сроков сделанным. Вызывать ПОСЛЕ того, как решение "
      "по каждому обязательству со сроком «сегодня или раньше» сказано Илье вслух: "
      "делаю сегодня / переношу, причина / снимаю, причина. В decisions — эти самые "
      "решения, по строке на обязательство, с его id. Пропущенный id инструмент не "
      "примет: тогда разбор не засчитан и блок останется в промпте.",
      {"decisions": str}, annotations=CLOSED)
async def morning_review(args):
    res = M.mark_morning_review(args.get("decisions", ""))
    M.log("action", {"tool": "morning_review", "ok": res["ok"],
                     "missing": res["missing"]})
    if res["ok"]:
        return _ok(f"разбор сроков засчитан: {res['total']} обязательств")
    return _ok("разбор НЕ засчитан — нет решения по: "
               + ", ".join(res["missing"])
               + ". Скажи решение по каждому и вызови снова.")


@tool("update_now",
      "Перезаписать memory/now.md — снимок текущего момента. Не длиннее экрана, "
      "только то, что собирается из данных.",
      {"content": str}, annotations=CLOSED)
async def update_now(args):
    M.C.NOW.write_text(args["content"], encoding="utf-8")
    M.log("action", {"tool": "update_now"})
    return _ok("now.md обновлён")


@tool("recall",
      "Поиск по логу и фактам — для длинного хвоста, когда загруженного контекста "
      "не хватило. Сначала пробуй структуру (дата, проект, человек), потом смысл.",
      {"query": str, "days": int}, annotations=ToolAnnotations(readOnlyHint=True,
                                                              openWorldHint=False))
async def recall(args):
    hits = M.search(args["query"], int(args.get("days") or 400))
    M.log("action", {"tool": "recall", "query": args["query"], "hits": len(hits)})
    if not hits:
        return _ok("ничего не найдено")
    return _ok("\n".join(hits[:40]))


@tool("delegate",
      "Отдать работу подагенту и освободиться. Возвращает id задания сразу — "
      "ход не блокируется, результат придёт отдельно. Так делается ЛЮБАЯ работа "
      "в поле: сбор данных, обход сайтов, разбор пачек, поиск контактов. "
      "Бриф самодостаточен: подагент видит только его и спросить Илью не может — "
      "цель, что уже известно, где искать, что считать результатом, стоп-условие.",
      {"brief": str, "title": str, "model": str},
      annotations=CLOSED)
async def delegate(args):
    """Делегирование должно стоить один вызов.

    До 25.08.2026 поставить задание можно было только через Bash с python-кодом,
    то есть отдать работу было дороже, чем сделать её самому. Правило
    dnyom-rabotayut-podagenty при этом повторялось Ильёй трижды и проигрывало —
    не потому, что его игнорировали, а потому, что правильный путь был неудобным.
    Пока делегирование дороже работы, побеждает работа.
    """
    from . import jobs
    try:
        job_id = jobs.submit(args["brief"], args.get("title", ""),
                             args.get("model") or None)
    except jobs.ThinBrief as thin:
        # Отказ по существу, а не ошибка: задание не создано, деньги не
        # потрачены. Ответ говорит, что именно дописать.
        M.log("action", {"tool": "delegate", "thin_brief": thin.missing})
        return _ok(str(thin))
    except jobs.DuplicateWork as dup:
        # Отбой дубля — не ошибка выполнения, а ответ по существу: работа уже
        # делается, и нужно не ставить второе задание, а дождаться первого.
        M.log("action", {"tool": "delegate", "dup": dup.ref})
        return _ok(str(dup))
    M.log("action", {"tool": "delegate", "job": job_id})
    return _ok(f"задание {job_id} поставлено, подагент взял его. "
               f"Ты свободен: отвечай Илье, результат придёт отдельно.")


@tool("jobs_status",
      "Что сейчас делают подагенты: очередь, работающие, законченные.",
      {}, annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=False))
async def jobs_status(args):
    from . import jobs
    metas = jobs.all_jobs()
    if not metas:
        return _ok("заданий нет")
    lines = [f"{m['id']} · {m['status']} · {m['title'][:70]}" for m in metas[-15:]]
    return _ok("\n".join(lines))


TOOLS = [delegate, jobs_status,
         remember_fact, remember_rule, remember_procedure,
         add_obligation, close_obligation, morning_review, update_now, recall]

server = create_sdk_mcp_server(name="memory", version="1.0.0", tools=TOOLS)

# Имена для allowed_tools. Записи в память автоодобряются всегда: цена ошибки
# нулевая, а трение здесь означало бы, что агент перестанет учиться.
NAMES = [f"mcp__memory__{n}" for n in
         ("delegate", "jobs_status",
          "remember_fact", "remember_rule", "remember_procedure",
          "add_obligation", "close_obligation", "morning_review",
          "update_now", "recall")]
