# bl-156, часть 2: только МОИ длинные ходы, с раздельным turns/steps и признаками петли.
import json, re, hashlib
from collections import Counter

DAYS = ["2026-08-30", "2026-08-31"]
ROOT = "/home/ilya/Рабочий стол/Agent/jarvis"
turns = {}
order = []
for d in DAYS:
    for line in open(f"{ROOT}/memory/log/{d}.jsonl", encoding="utf-8"):
        try:
            r = json.loads(line)
        except Exception:
            continue
        t = r.get("turn")
        if t is None:
            continue
        if t not in turns:
            turns[t] = {"actor": None, "res": None, "actions": [], "ts": r.get("ts"),
                        "task": "", "self": [], "out": [], "steps": None, "sec": None,
                        "channel": None, "gate": 0}
            order.append(t)
        c = turns[t]
        typ, content = r.get("type"), r.get("content") or ""
        if typ == "message_in":
            c["actor"] = r.get("actor"); c["task"] = content[:400]
        elif typ == "action":
            c["actions"].append(content)
        elif typ == "message_out":
            c["out"].append(content[:300])
        elif typ == "self":
            c["self"].append(content)
            try:
                data = json.loads(content)
            except Exception:
                continue
            if not isinstance(data, dict):
                continue
            if "result" in data and "tokens" in data:
                c["res"] = data
            if "steps" in data and "limit" not in data:
                c["steps"] = data["steps"]; c["sec"] = data.get("sec")
                c["channel"] = data.get("channel")
            if data.get("gate"):
                c["gate"] += 1

def sig(a):
    try:
        d = json.loads(a)
    except Exception:
        return None, None
    tool, s = d.get("tool"), str(d.get("input") or "")
    if not tool:
        return None, None
    if tool == "Bash":
        m = re.search(r"'command':\s*'(.{0,200})", s)
        cmd = re.sub(r"\d+", "#", m.group(1) if m else s[:200])
        return tool, f"Bash:{cmd[:120]}"
    m = re.search(r"file_path':\s*'([^']+)'", s)
    return tool, (f"{tool}:{m.group(1).split('/')[-1]}" if m
                  else f"{tool}:{hashlib.md5(s.encode()).hexdigest()[:6]}")

rows = []
for t in order:
    c = turns[t]
    st = c["steps"] or (c["res"] or {}).get("turns") or len(c["actions"])
    ctx = ((c["res"] or {}).get("tokens") or {}).get("контекст", 0)
    rows.append((t, c, st, ctx))
mine = sorted([r for r in rows if r[1]["actor"]], key=lambda r: -r[2])

print(f"моих ходов: {len(mine)}; длинных (>39 шагов): {len([r for r in mine if r[2] > 39])}")
print(f"{'ход':22} {'время':12} {'кто':7} {'кан':6} {'шаг':>4} {'turns':>6} {'ctx':>11} {'сек':>6} "
      f"{'дейст':>6} {'уник':>5} {'повт%':>6} {'read2+':>7} {'gate':>5}")
for t, c, st, ctx in mine[:14]:
    sigs = [s for _, s in map(sig, c["actions"]) if s]
    rep = Counter(sigs)
    dup = sum(v - 1 for v in rep.values() if v > 1)
    # повторное чтение одного и того же файла — признак потери контекста, а не работы
    reread = sum(v - 1 for k, v in rep.items() if k.startswith("Read:") and v > 1)
    print(f"{t.replace('log:',''):22} {c['ts'][11:16]:12} {str(c['actor'])[:6]:7} "
          f"{str(c['channel'])[:5]:6} {st:4} {(c['res'] or {}).get('turns',0):6} {ctx:11,} "
          f"{c['sec'] or 0:6.0f} {len(sigs):6} {len(rep):5} {dup/max(len(sigs),1)*100:5.0f}% "
          f"{reread:7} {c['gate']:5}")

print()
print("== детально: мои длинные ходы")
for t, c, st, ctx in [r for r in mine if r[2] > 39]:
    sigs = [s for _, s in map(sig, c["actions"]) if s]
    tools = Counter(x for x, _ in map(sig, c["actions"]) if x)
    rep = Counter(sigs)
    print("=" * 96)
    print(f"{t} | {c['ts'][:16]} | actor={c['actor']} channel={c['channel']} | шагов={st} "
          f"ctx={ctx:,} сек={c['sec']} отказов_шлюза={c['gate']}")
    print(f"ЗАДАЧА: {' '.join(c['task'].split())[:260]}")
    print(f"инструменты: {dict(tools.most_common())}")
    print(f"уникальных целей={len(rep)} из {len(sigs)} действий")
    for s, n in rep.most_common(6):
        if n > 2:
            print(f"   {n:>3}x {s[:96]}")
    if c["out"]:
        print(f"ИТОГ: {' '.join(c['out'][-1].split())[:200]}")
