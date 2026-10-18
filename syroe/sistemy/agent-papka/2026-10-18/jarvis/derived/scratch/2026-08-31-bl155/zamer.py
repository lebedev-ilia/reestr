# Разбор цены хода: из чего складывается контекст своего хода и захода подагента.
import json
from collections import defaultdict

DAYS = ["2026-08-30", "2026-08-31"]

turns = {}
order = []
for d in DAYS:
    for line in open(f"/home/ilya/Рабочий стол/Agent/jarvis/memory/log/{d}.jsonl", encoding="utf-8"):
        try:
            r = json.loads(line)
        except Exception:
            continue
        t = r.get("turn")
        if t is None:
            continue
        if t not in turns:
            turns[t] = {"actor": None, "res": None, "actions": [], "ts": r.get("ts")}
            order.append(t)
        cur = turns[t]
        typ = r.get("type")
        content = r.get("content") or ""
        if typ == "message_in":
            cur["actor"] = r.get("actor")
            cur["in_len"] = len(content)
        elif typ == "action":
            cur["actions"].append(content)
        elif typ == "self":
            try:
                data = json.loads(content)
            except Exception:
                continue
            if isinstance(data, dict) and "result" in data and "tokens" in data:
                cur["res"] = data
            if isinstance(data, dict) and "steps" in data:
                cur["steps"] = data.get("steps")
                cur["sec"] = data.get("sec")

groups = defaultdict(list)
for t in order:
    c = turns[t]
    if not c.get("res"):
        continue
    groups[c.get("actor") or "?"].append(c)

print("== по группам (actor из message_in)")
print(f"{'группа':10} {'ходов':>6} {'шагов ср':>9} {'контекст ср':>12} {'кэш_чт ср':>11} {'кэш_зап ср':>11} {'выход ср':>9} {'$ср':>7} {'ctx/шаг':>9}")
for g, lst in sorted(groups.items()):
    n = len(lst)
    def avg(f):
        return sum(f(c) for c in lst) / n
    tk = lambda c, k: c["res"]["tokens"].get(k, 0)
    steps = avg(lambda c: c["res"].get("turns") or c.get("steps") or 1)
    ctx = avg(lambda c: tk(c, "контекст"))
    print(f"{g:10} {n:6d} {steps:9.1f} {ctx:12,.0f} {avg(lambda c: tk(c,'кэш_чтение')):11,.0f} "
          f"{avg(lambda c: tk(c,'кэш_запись')):11,.0f} {avg(lambda c: tk(c,'выход')):9,.0f} "
          f"{avg(lambda c: c['res'].get('cost_usd',0)):7.2f} {ctx/max(steps,1):9,.0f}")

# Модель: контекст = сумма по шагам (P + накопленная история).
# P (системный промпт) оценим как минимум контекст/шаг по коротким ходам группы.
print()
print("== разложение: контекст/шаг против числа шагов (свои ходы)")
own = groups.get("ilya", []) + groups.get("self", [])
rows = []
for c in own:
    steps = c["res"].get("turns") or c.get("steps") or 1
    ctx = c["res"]["tokens"].get("контекст", 0)
    rows.append((steps, ctx, ctx / max(steps, 1), c["res"].get("cost_usd", 0), c.get("actor")))
rows.sort()
for steps, ctx, per, cost, a in rows:
    print(f"  шагов {steps:3d}  контекст {ctx:10,d}  на шаг {per:8,.0f}  ${cost:5.2f}  {a}")

# линейная регрессия ctx = a*steps + b*steps^2/2 -> оценим P и прирост d на шаг
n = len(rows)
if n > 3:
    xs = [r[0] for r in rows]
    ys = [r[1] for r in rows]
    # ctx(s) = P*s + d*s*(s-1)/2
    A = sum(x * x for x in xs)
    B = sum(x * (x * (x - 1) / 2) for x in xs)
    C = sum(x * y for x, y in zip(xs, ys))
    D = sum((x * (x - 1) / 2) ** 2 for x in xs)
    E = sum((x * (x - 1) / 2) * y for x, y in zip(xs, ys))
    det = A * D - B * B
    if det:
        P = (C * D - E * B) / det
        d = (A * E - B * C) / det
        print(f"\nоценка: системный промпт P = {P:,.0f} токенов на шаг, прирост истории d = {d:,.0f} токенов за шаг")
        for s in (10, 20, 30):
            tot = P * s + d * s * (s - 1) / 2
            print(f"  при {s} шагах: контекст {tot:,.0f}, из них промпт {P*s/tot*100:.0f}%")
