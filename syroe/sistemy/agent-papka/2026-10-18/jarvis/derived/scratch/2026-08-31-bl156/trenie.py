# Трение внутри длинных ходов: отказы шлюза и повторные чтения — шаги без продвижения.
import json, re
from collections import Counter
DAYS = ["2026-08-30", "2026-08-31"]
ROOT = "/home/ilya/Рабочий стол/Agent/jarvis"
gate = Counter(); gate_turn = Counter(); per_turn_steps = {}
reread = 0; acts_total = 0
reads = {}
for d in DAYS:
    for line in open(f"{ROOT}/memory/log/{d}.jsonl", encoding="utf-8"):
        try: r = json.loads(line)
        except Exception: continue
        t = r.get("turn"); ty = r.get("type"); ct = r.get("content") or ""
        if t is None: continue
        if ty == "action":
            acts_total += 1
            m = re.search(r"'tool': '(\w+)'.*file_path':\s*'([^']+)'", ct)
            if m and m.group(1) == "Read":
                k = (t, m.group(2))
                reads[k] = reads.get(k, 0) + 1
                if reads[k] > 1: reread += 1
        elif ty == "self":
            try: d2 = json.loads(ct)
            except Exception: continue
            if isinstance(d2, dict):
                if d2.get("gate"):
                    gate[d2.get("reason", "?")[:60]] += 1; gate_turn[t] += 1
                if "steps" in d2 and "limit" not in d2: per_turn_steps[t] = d2["steps"]

tot_steps = sum(per_turn_steps.values())
g = sum(gate.values())
print(f"действий за 30-31.08: {acts_total}; шагов по журналам: {tot_steps}")
print(f"отказов шлюза: {g} ({g/max(acts_total,1):.1%} действий), ходов с отказами: {len(gate_turn)}")
print(f"повторных чтений того же файла в том же ходе: {reread} ({reread/max(acts_total,1):.1%})")
print(f"итого трения: {g+reread} шагов = {(g+reread)/max(acts_total,1):.1%} всех действий")
print("\nпричины отказов шлюза (топ):")
for k, v in gate.most_common(8):
    print(f"  {v:>3}x {k}")
print("\nходы-рекордсмены по отказам:")
for t, v in gate_turn.most_common(6):
    print(f"  {v:>3} отказов — {t} (шагов={per_turn_steps.get(t)})")
