# Цена шага в зависимости от длины хода: проверка квадратичности.
import json
from collections import defaultdict
DAYS = ["2026-08-30", "2026-08-31"]
ROOT = "/home/ilya/Рабочий стол/Agent/jarvis"
T = {}
for d in DAYS:
    for line in open(f"{ROOT}/memory/log/{d}.jsonl", encoding="utf-8"):
        try: r = json.loads(line)
        except Exception: continue
        t = r.get("turn")
        if t is None: continue
        c = T.setdefault(t, {"actor": None, "res": None, "steps": None})
        if r.get("type") == "message_in": c["actor"] = r.get("actor")
        elif r.get("type") == "self":
            try: data = json.loads(r.get("content") or "")
            except Exception: continue
            if not isinstance(data, dict): continue
            if "result" in data and "tokens" in data: c["res"] = data
            if "steps" in data and "limit" not in data: c["steps"] = data["steps"]

rows = []
for t, c in T.items():
    if not c["res"]: continue
    st = c["steps"] or c["res"].get("turns") or 0
    ctx = c["res"]["tokens"].get("контекст", 0)
    if st < 2 or ctx < 1000: continue
    rows.append((t, bool(c["actor"]), st, ctx, c["res"].get("cost_usd", 0)))

BINS = [(2,9),(10,19),(20,29),(30,39),(40,59),(60,99),(100,200)]
for name, want in (("МОИ ходы", True), ("подагенты", False)):
    print(f"\n== {name}: цена одного шага по длине хода")
    print(f"{'длина хода':12} {'ходов':>6} {'ср.шагов':>9} {'ср.ctx':>12} {'ctx/шаг':>10} {'рост к базе':>12}")
    base = None
    for lo, hi in BINS:
        sel = [r for r in rows if r[1] == want and lo <= r[2] <= hi]
        if not sel: continue
        n = len(sel)
        ms = sum(r[2] for r in sel)/n
        mc = sum(r[3] for r in sel)/n
        per = mc/ms
        if base is None: base = per
        print(f"{lo:>4}-{hi:<7} {n:6} {ms:9.1f} {mc:12,.0f} {per:10,.0f} {per/base:11.2f}x")
