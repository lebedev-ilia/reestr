"""Самоаудит суток: машинная сводка собственного следа из лога (bl-067).

Читает memory/log/<дата>.jsonl и отвечает на вопросы, ради которых аудит и
заводился: что упало, где я ходил по кругу, что отбил шлюз, что я делал руками
десятки раз. Ничего не чинит и никуда, кроме derived/audit/, не пишет.

    python3 -m modules.audit.daily            # сегодня
    python3 -m modules.audit.daily 2026-08-24 # конкретный день

Сводка — сырьё для чтения глазами, а не вердикт: цифра показывает, куда
смотреть в логе, вывод делает агент.
"""

import json
import re
import sys
from collections import Counter, defaultdict
from datetime import date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent

# Фикстуры шлюза, застрявшие в живом логе (25.08.2026, ручные проверки границ
# по bl-047/bl-048). Сигнатуры и окно живут в core/fixtures.py — один список на
# аудит и на пересчёт реестра, чтобы они не расходились в числах.
sys.path.insert(0, str(ROOT.parent))
from jarvis.core import config as C     # noqa: E402
from jarvis.core import fixtures as FIX  # noqa: E402

# Пути берутся из конфига в МОМЕНТ вызова, а не константами при импорте: аудит
# вызывается из ночного цикла, а его самотест подменяет C.LOG_DIR и C.DERIVED на
# временный каталог. Константа, вычисленная при импорте, такой подмены не видит —
# и прогон самотеста писал бы в живой derived (правило samotest-ne-pishet-v-zhivuyu-pamyat).


def log_path(day: str) -> Path:
    return C.LOG_DIR / f"{day}.jsonl"


def out_dir() -> Path:
    return C.DERIVED / "audit"

LOOP_TURNS = 3          # столько ходов подряд на одной задаче — уже петля
MANUAL_REPEAT = 8       # столько одинаковых команд за сутки — просится в код
GAP_MIN = 45            # разрыв в следе длиннее — окно, где меня не было


def load(day: str):
    """Строки лога за день: (номер, запись, разобранный content-словарь)."""
    path = log_path(day)
    if not path.exists():
        # Именно исключение, а не sys.exit: аудит теперь зовётся из ночного
        # цикла, и SystemExit оттуда уронил бы всю свёртку мимо except Exception.
        raise FileNotFoundError(f"нет лога за {day}: {path}")
    out, broken = [], 0
    for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            rec = json.loads(line)
        except Exception:
            broken += 1
            continue
        body = {}
        c = rec.get("content")
        if isinstance(c, str) and c.startswith("{"):
            try:
                body = json.loads(c)
            except Exception:
                body = {}
        elif isinstance(c, dict):
            body = c
        out.append((n, rec, body))
    return out, broken


def hhmm(ts: str) -> str:
    return ts[11:16] if len(ts) > 15 else ts


def norm_cmd(inp: str) -> str:
    """Команда без дат, чисел и путей — чтобы одинаковое по смыслу совпало."""
    s = re.sub(r"\s+", " ", inp)
    s = re.sub(r"\d+", "N", s)
    return s[:120]


def day_cost(results: list) -> tuple[float, bool]:
    """Цена суток и признак «это оценка, а не счёт».

    С 25.08 в логе лежит цена хода (cost_usd) и накопленное сессией
    (cumulative) — такие записи просто складываются. У записей старее в
    cost_usd лежало НАКОПЛЕННОЕ, и его приходится обратно превращать в
    приросты по скачкам ряда. Точным такой пересчёт не бывает: рядом
    работали подагенты своими сессиями, и их ряды в логе перемешаны.
    """
    total, prev, guessed = 0.0, None, False
    for r in results:
        cur = r.get("cost_usd")
        if cur is None:
            continue
        cur = float(cur)
        if r.get("cumulative") is not None:
            total += cur
            continue
        guessed = True
        total += cur if prev is None or cur < prev else cur - prev
        prev = cur
    return total, guessed


def build(day: str) -> str:
    rows, broken = load(day)
    L = []
    add = L.append

    turns = []            # итоги ходов: steps/sec/channel
    results = []          # result/turns/cost_usd
    self_turns = []       # (строка, ts, bl-id)
    jobs = defaultdict(dict)
    gates = Counter()
    gate_ask = []         # (ts, tool, reason)
    asks_sent, ask_timeouts = 0, 0
    denied = []           # отбитые шлюзом
    errors = []           # type=error
    tools = Counter()
    bash_cmds = Counter()
    msg_out = 0
    status_out = 0        # пульс: с 26.08 свой тип, до этого лежал в message_out
    auto_status = 0
    routing = Counter()
    thin_briefs = []
    rejected = []         # брифы, отбитые до создания задания (bl-074)
    empties = []          # задания, закрытые как холостые
    stamps = []

    for n, rec, body in rows:
        t, ts = rec.get("type"), rec.get("ts", "")
        if ts:
            stamps.append((ts, n, t))
        if t == "error":
            errors.append((n, ts, rec.get("content", "")[:400]))
        elif t == "verdict" and body.get("allowed") is False:
            denied.append((ts, body.get("tool"), (body.get("input") or "")[:90]))
        elif t == "message_out":
            msg_out += 1
        elif t == "status":
            status_out += 1
        elif t == "action":
            tool = body.get("tool") or "?"
            tools[tool] += 1
            if tool == "Bash":
                inp = body.get("input") or ""
                m = re.search(r"'command':\s*'(.*?)',\s*'description'", inp, re.S)
                bash_cmds[norm_cmd(m.group(1) if m else inp)] += 1
        elif t == "self":
            if "steps" in body:
                turns.append(body)
                auto_status += int(body.get("auto_status") or 0)
            if "result" in body:
                results.append(body)
            if "self_turn" in body:
                self_turns.append((n, ts, body["self_turn"], body.get("spent_today"), body.get("budget")))
            if "gate" in body and not FIX.is_gate_fixture(body, ts):
                gates[body["gate"]] += 1
                if body["gate"] == "ask":
                    gate_ask.append((ts, body.get("tool"), (body.get("reason") or "")[:70]))
            if "ask_sent" in body:
                asks_sent += 1
            if "ask_timeout" in body:
                ask_timeouts += 1
            if "routing" in body:
                routing[body["routing"]] += 1
            if "job_brief_thin" in body:
                thin_briefs.append((body.get("job_brief_thin") or body.get("title"), body.get("missing")))
            if "job_brief_rejected" in body:
                rejected.append((body.get("job_brief_rejected"), body.get("missing")))
            if "job_empty" in body:
                empties.append((body.get("job_empty"), body.get("why")))
            for key in ("job_submit", "job_start", "job_done", "job_delivered"):
                if key in body:
                    val = body[key]
                    # job_delivered приходит и списком id, и одиночным id
                    for jid in (val if isinstance(val, list) else [val]):
                        if not isinstance(jid, str):
                            continue
                        jobs[jid][key] = body
                        jobs[jid].setdefault("ts", ts)

    add(f"# Самоаудит {day}")
    add("")
    add(f"Записей в логе: {len(rows)}" + (f", битых строк: {broken}" if broken else ""))
    if stamps:
        add(f"Окно следа: {hhmm(stamps[0][0])}–{hhmm(stamps[-1][0])}")

    # --- падения -----------------------------------------------------------
    add("")
    add("## Падения и отказы")
    if errors:
        for n, ts, c in errors:
            add(f"- {hhmm(ts)} строка {n}: {c}")
    else:
        add("- ошибок типа error нет")
    cost, guessed = day_cost(results)
    add(f"- цена суток: ${cost:.2f} за {len(results)} ходов"
        + (" (оценка: часть записей старого формата, см. day_cost)" if guessed else ""))
    add(f"- ходы с исходом не success: {sum(1 for r in results if r.get('result') != 'success')} из {len(results)}")
    for r in results:
        if r.get("result") != "success":
            add(f"  - {r}")
    add(f"- вопросов разрешения задано: {asks_sent}, из них ушло в таймаут: {ask_timeouts}")
    add(f"- отбито шлюзом (verdict allowed=false): {len(denied)}")
    for ts, tool, inp in denied[:12]:
        add(f"  - {hhmm(ts)} {tool}: {inp}")

    # --- петли фоновых ходов ----------------------------------------------
    add("")
    add("## Фоновые ходы и петли")
    add(f"- фоновых ходов взято: {len(self_turns)}")
    if self_turns:
        last = self_turns[-1]
        add(f"- последний счётчик бюджета: {last[3]} из {last[4]}")
    streak_id, streak, worst = None, 0, []
    for _, ts, bl, _, _ in self_turns:
        if bl == streak_id:
            streak += 1
        else:
            if streak >= LOOP_TURNS:
                worst.append((streak_id, streak))
            streak_id, streak = bl, 1
    if streak >= LOOP_TURNS:
        worst.append((streak_id, streak))
    counts = Counter(bl for _, _, bl, _, _ in self_turns)
    for bl, c in counts.most_common(6):
        mark = "  <-- петля" if any(w[0] == bl and w[1] >= LOOP_TURNS for w in worst) else ""
        add(f"- {bl}: ходов {c}{mark}")

    # --- задания подагентам ------------------------------------------------
    add("")
    add("## Задания подагентам")
    if jobs:
        for jid in sorted(jobs):
            j = jobs[jid]
            done = j.get("job_done") or {}
            title = (j.get("job_submit") or {}).get("title", "")
            add(f"- {jid} [{done.get('status', 'не завершено')}] "
                f"{done.get('sec', '?')}с — {title[:70]}")
        add(f"- заданий всего: {len(jobs)}, "
            f"незавершённых: {sum(1 for j in jobs.values() if 'job_done' not in j)}, "
            f"не доставлено результатов: {sum(1 for j in jobs.values() if 'job_done' in j and 'job_delivered' not in j)}")
    else:
        add("- заданий не было")
    if thin_briefs:
        add(f"- брифов, помеченных тонкими: {len(thin_briefs)}")
        for title, missing in thin_briefs[:6]:
            add(f"  - {str(title)[:60]}: не хватает {missing}")
    if rejected:
        add(f"- брифов отбито до запуска: {len(rejected)}")
        for title, missing in rejected[:6]:
            add(f"  - {str(title)[:60]}: пусто в {missing}")
    if empties:
        add(f"- заданий закрыто ВХОЛОСТУЮ: {len(empties)}")
        for jid, why in empties[:6]:
            add(f"  - {jid}: {why}")

    # --- руками то, что должен делать код ----------------------------------
    add("")
    add("## Ручные повторы (кандидаты в код)")
    rep = [(c, n) for c, n in bash_cmds.most_common(15) if n >= MANUAL_REPEAT]
    if rep:
        for c, n in rep:
            add(f"- {n}x  {c}")
    else:
        add(f"- одинаковых команд чаще {MANUAL_REPEAT} раз нет")

    # --- разрывы во времени -------------------------------------------------
    add("")
    add("## Разрывы следа")
    gaps = []
    for (ts1, n1, _), (ts2, n2, _) in zip(stamps, stamps[1:]):
        try:
            d = (datetime.fromisoformat(ts2) - datetime.fromisoformat(ts1)).total_seconds() / 60
        except Exception:
            continue
        if d >= GAP_MIN:
            gaps.append((hhmm(ts1), hhmm(ts2), round(d)))
    if gaps:
        for a, b, d in gaps:
            add(f"- {a} → {b}: {d} мин тишины")
    else:
        add(f"- разрывов длиннее {GAP_MIN} мин нет")

    # --- фон ----------------------------------------------------------------
    add("")
    add("## Фон")
    add(f"- ходов с журналом шагов: {len(turns)}, "
        f"шагов суммарно: {sum(int(t.get('steps') or 0) for t in turns)}, "
        f"минут суммарно: {round(sum(float(t.get('sec') or 0) for t in turns) / 60)}")
    ch = Counter(t.get("channel") for t in turns)
    add(f"- каналы ходов: {dict(ch)}")
    # Маршрутизатор снят 26.08.2026 (bl-076) — в новых днях поля routing нет.
    # Строка остаётся ради разбора старых дней, где оно ещё писалось.
    if routing:
        add(f"- маршрутизация: {dict(routing)} (быстрый путь = fast, снят 26.08)")
    add(f"- сообщений наружу: {msg_out}, статусов пульса (тип status): {status_out}"
        f", из них добор автоматом: {auto_status}")
    add(f"- шлюз по режимам: {dict(gates)}")
    if gate_ask:
        top = Counter(t for _, t, _ in gate_ask).most_common(5)
        add(f"- чаще всего спрашивал разрешения на: {top}")
    # --- живой демон против кода на диске -----------------------------------
    add("")
    add("## Правки, которые ещё не действуют")
    try:
        from jarvis.core import staleness as ST
        st = ST.check()
        if st["ok"]:
            add(f"- демон pid {st['pid']} работает на текущем коде")
        else:
            add(f"- {st['reason']}")
            for name, m in st["stale"][:8]:
                add(f"  - {name}: {datetime.fromtimestamp(m):%H:%M}")
            add("  - до перезапуска демона правка живёт только на диске")
    except Exception as e:                                   # noqa: BLE001
        add(f"- проверка не отработала: {e}")

    add("")
    add("Деньги здесь не считаются намеренно: оплата по подписке, доллары в логе "
        "ничего не значат (правило model-ne-ekonomit). Мерило — ходы и шаги.")
    return "\n".join(L)


def write(day: str) -> str:
    """Собрать сводку за день и положить её в derived/audit/<день>.md.

    Единственная точка записи: и ручной запуск, и ночной цикл идут через неё,
    чтобы файл всегда лежал в одном месте и в одном формате.
    """
    text = build(day)
    d = out_dir()
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{day}.md").write_text(text + "\n", encoding="utf-8")
    return text


def main():
    day = sys.argv[1] if len(sys.argv) > 1 else date.today().isoformat()
    try:
        text = write(day)
    except FileNotFoundError as e:
        sys.exit(str(e))
    print(text)
    print(f"\n[сохранено: derived/audit/{day}.md]")


if __name__ == "__main__":
    main()
