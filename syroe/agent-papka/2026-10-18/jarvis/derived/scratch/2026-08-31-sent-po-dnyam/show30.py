"""Одноразовая проверка: что именно ушло 30.08 (memory/outbox/sent.jsonl)."""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
for line in (ROOT / "memory" / "outbox" / "sent.jsonl").read_text().splitlines():
    line = line.strip()
    if not line:
        continue
    r = json.loads(line)
    ts = str(r.get("ts") or r.get("date") or "")
    if ts.startswith("2026-08-30"):
        print(ts, "|", r.get("to"), "|", str(r.get("subject"))[:60],
              "|", {k: v for k, v in r.items()
                    if k not in ("body", "text", "html", "to", "subject", "ts")})
