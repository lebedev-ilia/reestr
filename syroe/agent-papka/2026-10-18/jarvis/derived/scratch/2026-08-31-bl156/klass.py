# Кто есть кто: классификация ходов по channel/session, а не по наличию message_in.
import json
from collections import Counter, defaultdict
DAYS = ["2026-08-30", "2026-08-31"]
ROOT = "/home/ilya/Рабочий стол/Agent/jarvis"
T = {}
for d in DAYS:
    for line in open(f"{ROOT}/memory/log/{d}.jsonl", encoding="utf-8"):
        try: r = json.loads(line)
        except Exception: continue
        t = r.get("turn")
        if t is None: continue
        c = T.setdefault(t, {"actor": None, "res": None, "steps": None, "channel": None})
        ty = r.get("type"); ct = r.get("content") or ""
        if ty == "message_in": c["actor"] = r.get("actor")
        elif ty == "self":
            try: d2 = json.loads(ct)
            except Exception: continue
            if not isinstance(d2, dict): continue
            if "result" in d2 and "tokens" in d2: c["res"] = d2
            if "steps" in d2 and "limit" not in d2:
                c["steps"] = d2["steps"]; c["channel"] = d2.get("channel")

cnt = Counter()
for t, c in T.items():
    cnt[(c["channel"], bool(c["actor"]))] += 1
print("channel × есть message_in:", dict(cnt))

# мой ход = есть self-запись со steps и channel in (vk, self, cli); остальное — заход подагента
groups = defaultdict(list)
for t, c in T.items():
    if not c["res"]: continue
    st = c["steps"] or c["res"].get("turns") or 0
    ctx = c["res"]["tokens"].get("контекст", 0)
    kind = "мой" if c["channel"] in ("vk", "self", "cli") else "подагент"
    groups[kind].append((st, ctx, c["res"].get("cost_usd", 0)))

L = 40
for k, lst in groups.items():
    tot = sum(x[1] for x in lst)
    lg = [x for x in lst if x[0] >= L]
    tl = sum(x[1] for x in lg)
    print(f"{k:9}: ходов={len(lst):3} токенов={tot:>12,} $={sum(x[2] for x in lst):7.2f} | "
          f"длинных(>={L})={len(lg):3} ({len(lg)/len(lst):.0%}) их_токенов={tl:>12,} ({tl/max(tot,1):.0%})")
