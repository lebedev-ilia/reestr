# bl-156: поднять длинные ходы по логу и отделить большую работу от петли.
import json, re, hashlib
from collections import Counter, defaultdict

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
                        "day": d, "task": "", "self": [], "out": []}
            order.append(t)
        cur = turns[t]
        typ = r.get("type")
        content = r.get("content") or ""
        if typ == "message_in":
            cur["actor"] = r.get("actor")
            cur["task"] = content[:400]
        elif typ == "action":
            cur["actions"].append(content)
        elif typ == "message_out":
            cur["out"].append(content[:200])
        elif typ == "self":
            cur["self"].append(content)
            try:
                data = json.loads(content)
            except Exception:
                continue
            if isinstance(data, dict) and "result" in data and "tokens" in data:
                cur["res"] = data
            if isinstance(data, dict) and "steps" in data:
                cur["steps"] = data.get("steps")
                cur["sec"] = data.get("sec")


def parse_action(c):
    """content действия -> (инструмент, ключ повтора)"""
    try:
        d = json.loads(c)
    except Exception:
        return None, None
    tool = d.get("tool")
    inp = d.get("input") or ""
    if not tool:
        return None, None
    s = str(inp)
    return tool, s


def sig(tool, s):
    """Грубая подпись действия: инструмент + цель (путь/первые слова команды)."""
    if tool == "Bash":
        m = re.search(r"'command':\s*'(.{0,160})", s)
        cmd = m.group(1) if m else s[:160]
        # убираем номера строк и даты, оставляем костяк
        cmd = re.sub(r"\d+", "#", cmd)
        return f"Bash:{cmd[:110]}"
    m = re.search(r"file_path':\s*'([^']+)'", s)
    if m:
        return f"{tool}:{m.group(1)}"
    return f"{tool}:{hashlib.md5(s.encode()).hexdigest()[:8]}"


rows = []
for t in order:
    c = turns[t]
    steps = (c["res"] or {}).get("turns") or c.get("steps") or len(c["actions"])
    ctx = ((c["res"] or {}).get("tokens") or {}).get("контекст", 0)
    rows.append((t, c, steps, ctx))

rows.sort(key=lambda r: -r[2])

print("== все ходы 30-31.08 по числу шагов (топ-15)")
for t, c, steps, ctx in rows[:15]:
    print(f"{t:>6} {c['ts'][:16]:17} {c['actor'] or '?':6} шагов={steps:4} ctx={ctx:>10,} {c['task'][:60]!r}")

print()
LIMIT = 39
long_ones = [r for r in rows if r[2] > LIMIT]
print(f"== длинных ходов (>{LIMIT} шагов): {len(long_ones)}")
for t, c, steps, ctx in long_ones:
    sigs = []
    tools = Counter()
    for a in c["actions"]:
        tool, s = parse_action(a)
        if not tool:
            continue
        tools[tool] += 1
        sigs.append(sig(tool, s))
    rep = Counter(sigs)
    dup = sum(v - 1 for v in rep.values() if v > 1)
    uniq = len(rep)
    print("=" * 100)
    print(f"ход {t} | {c['ts'][:16]} | actor={c['actor']} | шагов={steps} | ctx={ctx:,} | сек={c.get('sec')}")
    print(f"ЗАДАЧА: {c['task'][:300]}")
    print(f"действий={len(sigs)} уникальных целей={uniq} повторов={dup} ({dup/max(len(sigs),1):.0%})")
    print("инструменты:", dict(tools.most_common()))
    print("топ повторяемых целей:")
    for s, n in rep.most_common(8):
        if n > 1:
            print(f"   {n:>3}x {s[:100]}")
    # признаки провалов и отказов
    marks = Counter()
    for s in c["self"]:
        try:
            d = json.loads(s)
        except Exception:
            continue
        for k in d:
            if k in ("vk_alive", "result", "steps"):
                continue
            marks[k] += 1
    if marks:
        print("пометки хода:", dict(marks))
    if c["out"]:
        print("исходящее:", c["out"][0][:150])

print()
print("#" * 100)
print("== разделение: мои ходы (actor есть) против заходов подагентов (actor нет)")
mine = [r for r in rows if r[1]["actor"]]
subs = [r for r in rows if not r[1]["actor"]]
for name, lst in (("мои", mine), ("подагенты", subs)):
    lg = [r for r in lst if r[2] > LIMIT]
    tot = sum(r[3] for r in lst)
    tl = sum(r[3] for r in lg)
    print(f"{name}: всего={len(lst)} длинных={len(lg)} токенов_всего={tot:,} токенов_длинных={tl:,} "
          f"доля_длинных_по_токенам={tl/max(tot,1):.0%}")
