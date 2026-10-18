# Действует ли потолок шагов подагента (JOB_MAX_TURNS=80)?
import json
DAYS = ["2026-08-30", "2026-08-31"]
ROOT = "/home/ilya/Рабочий стол/Agent/jarvis"
T = {}
for d in DAYS:
    for line in open(f"{ROOT}/memory/log/{d}.jsonl", encoding="utf-8"):
        try: r = json.loads(line)
        except Exception: continue
        t = r.get("turn")
        if t is None: continue
        c = T.setdefault(t, {"actor": None, "res": None, "steps": None, "acts": 0, "hit": False})
        ty = r.get("type"); ct = r.get("content") or ""
        if ty == "message_in": c["actor"] = r.get("actor")
        elif ty == "action": c["acts"] += 1
        elif ty == "message_out" and "потолок шагов" in ct: c["hit"] = True
        elif ty == "self":
            try: d2 = json.loads(ct)
            except Exception: continue
            if not isinstance(d2, dict): continue
            if "result" in d2 and "tokens" in d2: c["res"] = d2
            if "steps" in d2 and "limit" not in d2: c["steps"] = d2["steps"]
            if "limit" in d2: c["hit"] = c["hit"] or ("ОБРЫВ" in ct or d2.get("steps_warning") is not None)

sub = [(t, c) for t, c in T.items() if not c["actor"] and c["res"]]
over = [(t, c) for t, c in sub if (c["res"].get("turns") or 0) > 80]
print(f"заходов подагентов с результатом: {len(sub)}; из них turns>80: {len(over)}")
for t, c in sorted(over, key=lambda x: -(x[1]["res"].get("turns") or 0)):
    print(f"  {t:22} turns={c['res'].get('turns'):4} steps_log={c['steps']} действий={c['acts']:4} "
          f"ctx={c['res']['tokens'].get('контекст',0):>11,} обрыв_по_потолку={c['hit']}")
mine_over = [(t, c) for t, c in T.items() if c["actor"] and c["res"] and (c["res"].get("turns") or 0) > 80]
print(f"\nмоих ходов turns>80: {len(mine_over)}")
for t, c in mine_over:
    print(f"  {t:22} turns={c['res'].get('turns'):4} steps_log={c['steps']} обрыв={c['hit']}")
