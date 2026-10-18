"""Одноразовая проверка: сколько писем ушло по дням (memory/outbox/sent.jsonl)."""
import collections
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
d = collections.Counter()
for line in (ROOT / "memory" / "outbox" / "sent.jsonl").read_text().splitlines():
    line = line.strip()
    if not line:
        continue
    r = json.loads(line)
    ts = r.get("ts") or r.get("date") or r.get("sent_at") or ""
    d[str(ts)[:10]] += 1
for k in sorted(d)[-10:]:
    print(k, d[k])
print("всего", sum(d.values()))
