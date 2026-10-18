"""Счётчик расхода: сколько ходов и во сколько они обошлись (bl-063).

Считает по логу ходы за день и за период, отдельно фоновые (простой),
отдельно из ВК, отдельно подагентов. Мерило — ХОДЫ и ШАГИ; доллары идут
последней колонкой как справочная величина для сравнения ходов между собой
(правило model-ne-ekonomit: оплата по подписке, деньги ничего не значат).

    python3 -m modules.audit.spend                      # сегодня
    python3 -m modules.audit.spend 2026-08-25           # один день
    python3 -m modules.audit.spend 2026-08-24 2026-08-26  # период
    python3 -m modules.audit.spend --check              # самопроверка на фикстуре

Как опознаётся ход. Ход открывается записью message_in (в ней actor: ilya,
self, job, system) и закрывается записью self с "result". С 25.08 у записей
есть поле turn — ссылка на message_in, открывшую ход, — и связь точная даже
когда рядом параллельно работают подагенты. У записей старее turn нет, и ход
привязывается к последней message_in по порядку: при параллельной работе это
ОЦЕНКА, и такой период помечается словом «оценка» в выводе.

Цена хода: с 25.08 в записи лежит cost_usd (прирост) и cumulative. У записей
старее в cost_usd лежало накопленное сессией, приросты восстанавливаются по
скачкам ряда — тоже оценка сверху (факт cena-hoda-uchyot).

Ничего не чинит и пишет только в derived/audit/.
"""

import json
import sys
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
LOG_DIR = ROOT / "memory" / "log"
OUT_DIR = ROOT / "derived" / "audit"

# Вид хода по актору, открывшему его, и по каналу доставки.
KINDS = ("вк", "фон", "подагент", "прочее")


def parse(line: str):
    """Строка лога → (запись, разобранный content). Битая строка → None."""
    try:
        rec = json.loads(line)
    except Exception:
        return None
    c = rec.get("content")
    body = {}
    if isinstance(c, str) and c.startswith("{"):
        try:
            body = json.loads(c)
        except Exception:
            body = {}
    elif isinstance(c, dict):
        body = c
    return rec, body


def load(day: str, log_dir: Path = LOG_DIR):
    path = log_dir / f"{day}.jsonl"
    if not path.exists():
        return None
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        p = parse(line)
        if p:
            out.append(p)
    return out


def kind_of(actor: str | None, channel: str | None) -> str:
    """Вид хода. Канал сильнее актора: подагент ходит с channel=self."""
    if actor == "job":
        return "подагент"
    if actor == "ilya" or channel == "vk":
        return "вк"
    if actor == "self":
        return "фон"
    return "прочее"


def turns_of_day(day: str, log_dir: Path = LOG_DIR):
    """Ходы за день: список словарей + признак «связь угадана».

    Каждый ход: ts, kind, cost, steps (шаги инструментов), model_turns
    (шаги модели), sec, task (bl-id для фоновых), result.
    """
    rows = load(day, log_dir)
    if rows is None:
        return None, False, 0
    opens = {}          # ref message_in -> actor
    turns = {}          # ключ хода -> словарь
    order = []
    last_open = None    # (ключ, actor) — для записей без поля turn
    guessed_link = False
    task_by_key = {}

    def key_for(rec, body):
        """Ключ хода: turn-ref, если он есть; иначе последняя message_in.

        Признак «связь угадана» ставит не сам факт записи без turn (хвост
        вчерашнего хода безобиден), а ход, чей ИТОГ (result) привязан по
        порядку: только такой ход может уехать не в ту колонку.
        """
        nonlocal guessed_link
        t = rec.get("turn")
        if t:
            return t
        if "result" in body:
            guessed_link = True
        return last_open

    for rec, body in rows:
        t, ts = rec.get("type"), rec.get("ts", "")
        if t == "message_in":
            ref = rec.get("ref") or f"seq:{len(order)}:{ts}"
            opens[ref] = rec.get("actor")
            last_open = ref
            continue
        if t != "self":
            continue
        k = key_for(rec, body)
        if k is None:
            continue
        if k not in turns:
            turns[k] = {"ts": ts, "actor": opens.get(k), "channel": None,
                        "cost": None, "cumulative": None, "session": None,
                        "steps": 0, "model_turns": 0, "sec": 0.0,
                        "task": None, "result": None}
            order.append(k)
        cur = turns[k]
        if cur["actor"] is None:
            cur["actor"] = opens.get(k)
        if "self_turn" in body:
            cur["task"] = body.get("self_turn")
            task_by_key[k] = body.get("self_turn")
        if "steps" in body:
            cur["steps"] = int(body.get("steps") or 0)
            cur["sec"] = float(body.get("sec") or 0)
            cur["channel"] = body.get("channel")
        if "result" in body:
            cur["result"] = body.get("result")
            cur["model_turns"] = int(body.get("turns") or 0)
            if body.get("cost_usd") is not None:
                cur["cost"] = float(body["cost_usd"])
                cur["cumulative"] = body.get("cumulative")
                cur["session"] = body.get("session")

    out = [turns[k] for k in order]
    for it in out:
        it["kind"] = kind_of(it["actor"], it["channel"])
    # Отдельно — сколько раз счётчик бюджета отметил взятый фоновый ход
    # (записи self_turn). Это то же число, что печатает самоаудит; расхождение
    # с колонкой «фон» значит потерянную привязку итога, а не лишние ходы.
    marks = sum(1 for rec, b in rows if rec.get("type") == "self" and "self_turn" in b)
    return out, guessed_link, marks


def costs(turns: list) -> bool:
    """Проставляет ходам поле cost как ПРИРОСТ. Возвращает признак оценки.

    Записи с cumulative уже держат прирост. У старых в cost_usd лежит
    накопленное сессией — прирост восстанавливается по скачкам ряда в разрезе
    сессии (а без session — общим рядом, и это оценка сверху).
    """
    guessed = False
    prev = defaultdict(lambda: None)
    for it in turns:
        c = it.get("cost")
        if c is None:
            continue
        if it.get("cumulative") is not None:
            continue
        guessed = True
        s = it.get("session") or "?"
        p = prev[s]
        it["cost"] = c if p is None or c < p else c - p
        prev[s] = c
    return guessed


def day_row(day: str, log_dir: Path = LOG_DIR):
    res = turns_of_day(day, log_dir)
    if res[0] is None:
        return None
    turns, guessed_link, marks = res
    guessed_cost = costs(turns)
    agg = {k: {"n": 0, "steps": 0, "min": 0.0, "cost": 0.0, "fail": 0}
           for k in KINDS}
    running = 0
    for it in turns:
        # Ход считается только закрытым: у идущего прямо сейчас нет ни цены,
        # ни шагов, и в счёте суток он дал бы лишнюю единицу.
        if it["result"] is None:
            # Ход без итога бывает двух родов: идущий прямо сейчас (у него уже
            # есть шаги) и пустая группа служебных записей — след привязки по
            # порядку в старом формате. Вторую в счёт не берём и не показываем.
            if it["steps"] or it["model_turns"] or it["task"]:
                running += 1
            continue
        a = agg[it["kind"]]
        a["n"] += 1
        a["steps"] += it["steps"]
        a["min"] += it["sec"] / 60
        a["cost"] += it["cost"] or 0.0
        if it["result"] and it["result"] != "success":
            a["fail"] += 1
    tasks = sorted({it["task"] for it in turns if it["task"]})
    return {"day": day, "turns": turns, "agg": agg, "tasks": tasks,
            "running": running, "marks": marks,
            "guessed": guessed_link or guessed_cost}


def fmt(rows: list) -> str:
    L = []
    add = L.append
    a, b = rows[0]["day"], rows[-1]["day"]
    add(f"# Расход {a}" + (f" — {b}" if a != b else ""))
    add("")
    add("день        всего   вк    фон   подагент  шагов   минут   $ справочно")
    tot = {k: {"n": 0, "steps": 0, "min": 0.0, "cost": 0.0, "fail": 0} for k in KINDS}
    for r in rows:
        g = r["agg"]
        n = sum(g[k]["n"] for k in KINDS)
        st = sum(g[k]["steps"] for k in KINDS)
        mn = sum(g[k]["min"] for k in KINDS)
        cs = sum(g[k]["cost"] for k in KINDS)
        add(f"{r['day']}  {n:5}  {g['вк']['n']:4}  {g['фон']['n']:4}  "
            f"{g['подагент']['n']:7}  {st:6}  {mn:6.0f}   {cs:8.2f}"
            + ("  (оценка)" if r["guessed"] else ""))
        for k in KINDS:
            for f in ("n", "steps", "min", "cost", "fail"):
                tot[k][f] += g[k][f]
    add("")
    add("## Итог за период")
    for k in KINDS:
        t = tot[k]
        if not t["n"]:
            continue
        add(f"- {k}: ходов {t['n']}, шагов {t['steps']}, минут {t['min']:.0f}, "
            f"неуспешных {t['fail']}, справочно ${t['cost']:.2f} "
            f"(в среднем ${t['cost'] / t['n']:.2f} за ход)")
    n = sum(tot[k]["n"] for k in KINDS)
    add(f"- всего ходов: {n}, шагов: {sum(tot[k]['steps'] for k in KINDS)}")
    marks = sum(r.get("marks", 0) for r in rows)
    if marks:
        add(f"- счётчик бюджета отметил взятых фоновых ходов: {marks}"
            + (f" (в колонке «фон» их {tot['фон']['n']} — у остальных итог "
               "не привязался, формат лога старый)"
               if marks != tot["фон"]["n"] else ""))
    run = sum(r.get("running", 0) for r in rows)
    if run:
        add(f"- не закрыто на момент счёта: {run} (идущий ход в счёт не берётся)")
    if tot["прочее"]["n"]:
        add("- «прочее» — ходы, чья открывающая запись осталась в логе "
            "прошлых суток, плюс запуски из терминала и ночной цикл")
    if any(r["guessed"] for r in rows):
        add("")
        add("«оценка» у дня значит: часть записей старого формата — ход "
            "привязан по порядку, а не по turn, и цена восстановлена по "
            "скачкам ряда. Числа завышены, точными они не станут никогда.")
    add("")
    add("Доллары — справочная величина для сравнения ходов между собой. "
        "Оплата по подписке, расходуется квота токенов, а не деньги "
        "(правило model-ne-ekonomit). Мерило бюджета — ходы.")
    # фоновые задачи дня — чтобы видеть, на что ушёл бюджет простоя
    tasks = sorted({t for r in rows for t in r["tasks"]})
    if tasks:
        add("")
        add(f"Фоновые ходы брали задачи: {', '.join(tasks)}")
    return "\n".join(L)


def check() -> int:
    """Самопроверка на фикстуре: живой лог не трогаем, пишем в derived/scratch."""
    import shutil
    tmp = ROOT / "derived" / "scratch" / "spend-check"
    shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir(parents=True, exist_ok=True)
    day = "2000-01-01"
    recs = [
        # ход из ВК: message_in от Ильи, result и steps связаны полем turn
        {"ts": f"{day}T10:00:00+10:00", "type": "message_in", "actor": "ilya",
         "ref": "log:x#1", "content": "привет"},
        {"ts": f"{day}T10:01:00+10:00", "type": "self", "turn": "log:x#1",
         "content": json.dumps({"result": "success", "cost_usd": 1.5,
                                "cumulative": 1.5, "session": "A", "turns": 4})},
        {"ts": f"{day}T10:01:01+10:00", "type": "self", "turn": "log:x#1",
         "content": json.dumps({"steps": 7, "sec": 60.0, "channel": "vk"})},
        # фоновый ход: actor self, есть self_turn с задачей
        {"ts": f"{day}T11:00:00+10:00", "type": "message_in", "actor": "self",
         "ref": "log:x#2", "content": "[фоновый ход]"},
        {"ts": f"{day}T11:00:01+10:00", "type": "self", "turn": "log:x#2",
         "content": json.dumps({"self_turn": "bl-063", "spent_today": 9})},
        {"ts": f"{day}T11:05:00+10:00", "type": "self", "turn": "log:x#2",
         "content": json.dumps({"result": "success", "cost_usd": 3.0,
                                "cumulative": 3.0, "session": "B", "turns": 20})},
        {"ts": f"{day}T11:05:01+10:00", "type": "self", "turn": "log:x#2",
         "content": json.dumps({"steps": 19, "sec": 300.0, "channel": "self"})},
        # подагент: actor job, канал тоже self — вид определяется актором
        {"ts": f"{day}T11:01:00+10:00", "type": "message_in", "actor": "job",
         "ref": "log:x#3", "content": "бриф"},
        {"ts": f"{day}T11:04:00+10:00", "type": "self", "turn": "log:x#3",
         "content": json.dumps({"result": "failed", "cost_usd": 2.0,
                                "cumulative": 2.0, "session": "C", "turns": 9})},
        {"ts": f"{day}T11:04:01+10:00", "type": "self", "turn": "log:x#3",
         "content": json.dumps({"steps": 8, "sec": 180.0, "channel": "self"})},
        # job_done дублирует цену подагента — считать её второй раз нельзя
        {"ts": f"{day}T11:04:02+10:00", "type": "self", "turn": "log:x#3",
         "content": json.dumps({"job_done": "job-001", "status": "done",
                                "sec": 180.0, "cost_usd": 2.0})},
        # старый формат: ни turn, ни cumulative — накопленное по сессии
        {"ts": f"{day}T12:00:00+10:00", "type": "message_in", "actor": "ilya",
         "ref": "log:x#4", "content": "ещё"},
        {"ts": f"{day}T12:01:00+10:00", "type": "self",
         "content": json.dumps({"result": "success", "cost_usd": 10.0, "turns": 3})},
        {"ts": f"{day}T12:01:01+10:00", "type": "self",
         "content": json.dumps({"steps": 2, "sec": 60.0, "channel": "vk"})},
        {"ts": f"{day}T12:02:00+10:00", "type": "message_in", "actor": "ilya",
         "ref": "log:x#5", "content": "и ещё"},
        {"ts": f"{day}T12:03:00+10:00", "type": "self",
         "content": json.dumps({"result": "success", "cost_usd": 14.0, "turns": 3})},
        {"ts": f"{day}T12:03:01+10:00", "type": "self",
         "content": json.dumps({"steps": 2, "sec": 60.0, "channel": "vk"})},
        {"ts": f"{day}T12:04:00+10:00", "type": "action", "actor": "agent",
         "content": "не json — не должно ронять"},
        # идущий ход: итога ещё нет — в счёт суток он попасть не должен
        {"ts": f"{day}T13:00:00+10:00", "type": "message_in", "actor": "self",
         "ref": "log:x#6", "content": "[фоновый ход]"},
        {"ts": f"{day}T13:00:01+10:00", "type": "self", "turn": "log:x#6",
         "content": json.dumps({"self_turn": "bl-099", "spent_today": 10})},
        "битая строка",
    ]
    (tmp / f"{day}.jsonl").write_text(
        "\n".join(r if isinstance(r, str) else json.dumps(r, ensure_ascii=False)
                  for r in recs) + "\n", encoding="utf-8")

    row = day_row(day, tmp)
    g = row["agg"]
    ok = True

    def t(name, cond):
        nonlocal ok
        print(("  ✓ " if cond else "  ✗ ") + name)
        ok = ok and cond

    t("ход из ВК опознан по актору и каналу", g["вк"]["n"] == 3)
    t("фоновый ход опознан", g["фон"]["n"] == 1)
    t("подагент отделён от фона, хоть канал у них общий", g["подагент"]["n"] == 1)
    t("цена нового формата взята как есть", abs(g["фон"]["cost"] - 3.0) < 1e-6)
    t("job_done не досчитан вторым разом", abs(g["подагент"]["cost"] - 2.0) < 1e-6)
    t("накопленное старого формата превращено в приросты (10+4)",
      abs(g["вк"]["cost"] - (1.5 + 10.0 + 4.0)) < 1e-6)
    t("неуспешный ход посчитан", g["подагент"]["fail"] == 1)
    t("шаги сложены", sum(g[k]["steps"] for k in KINDS) == 7 + 19 + 8 + 2 + 2)
    t("день помечен оценкой из-за старых записей", row["guessed"] is True)
    t("задача фонового хода видна", row["tasks"] == ["bl-063", "bl-099"])
    t("битая строка не уронила разбор", len(row["turns"]) == 6)
    t("идущий ход не посчитан за прожитый", row["running"] == 1)
    t("идущий ход не добавил ходов в счёт",
      sum(g[k]["n"] for k in KINDS) == 5)
    # день только из новых записей оценкой не помечается
    only_new = tmp / "new"
    only_new.mkdir(exist_ok=True)
    (only_new / f"{day}.jsonl").write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in recs[:7]
                  if isinstance(r, dict)) + "\n", encoding="utf-8")
    fresh = day_row(day, only_new)
    t("день без старых записей оценкой не помечен", fresh["guessed"] is False)
    shutil.rmtree(tmp, ignore_errors=True)
    print("ВСЁ ХОРОШО" if ok else "СЛОМАНО")
    return 0 if ok else 1


def main(argv: list[str]) -> int:
    if argv and argv[0] in ("--check", "-c"):
        return check()
    if not argv:
        days = [date.today().isoformat()]
    elif len(argv) == 1:
        days = [argv[0]]
    else:
        a = datetime.strptime(argv[0], "%Y-%m-%d").date()
        b = datetime.strptime(argv[1], "%Y-%m-%d").date()
        days = [(a + timedelta(days=i)).isoformat() for i in range((b - a).days + 1)]
    rows = [r for r in (day_row(d) for d in days) if r]
    if not rows:
        print(f"нет лога за {', '.join(days)}")
        return 2
    text = fmt(rows)
    print(text)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    name = f"spend-{rows[0]['day']}" + (f"-{rows[-1]['day']}" if len(rows) > 1 else "")
    (OUT_DIR / f"{name}.md").write_text(text + "\n", encoding="utf-8")
    print(f"\n[сохранено: derived/audit/{name}.md]")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
