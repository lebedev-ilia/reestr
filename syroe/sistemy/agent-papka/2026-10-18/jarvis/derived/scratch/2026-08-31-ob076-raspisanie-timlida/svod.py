"""ob-076: свод первых суток расписания тимлида vnutrennii.

Считаем по СОБЫТИЯМ лога (type=self, content — json со своим ключом),
а не по подстроке: мои собственные grep-команды тоже попадают в лог
как action и раздувают счёт (правило merilo-schitaetsya-po-vsemu-polyu).
"""
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[3]
LOGS = sorted((ROOT / "memory" / "log").glob("2026-08-*.jsonl"))

ROLE = "teamlead-vnutrennii"

scheduled = []   # (ts, job)
skipped = []     # (ts, ...)
jobs = {}        # job -> {"sec":, "cost":, "status":}
titles = {}

for p in LOGS:
    for line in p.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            rec = json.loads(line)
        except Exception:
            continue
        if rec.get("type") != "self":
            continue
        c = rec.get("content")
        if isinstance(c, str):
            try:
                c = json.loads(c)
            except Exception:
                continue
        if not isinstance(c, dict):
            continue
        ts = rec.get("ts", "")
        if c.get("role_scheduled") == ROLE:
            scheduled.append((ts, c.get("job"), c.get("mode")))
        if "quiet_skip" in c or c.get("role_quiet_skip") == ROLE:
            skipped.append((ts, json.dumps(c, ensure_ascii=False)[:120]))
        if "job_submit" in c:
            titles[c["job_submit"]] = c.get("title", "")
        if "job_done" in c:
            jobs[c["job_done"]] = {
                "sec": c.get("sec"),
                "cost": c.get("cost_usd"),
                "status": c.get("status"),
            }

print(f"заходов по расписанию ({ROLE}): {len(scheduled)}")
for ts, job, mode in scheduled:
    j = jobs.get(job, {})
    print(f"  {ts[:16]}  {job}  {mode}  {j.get('status')}  {j.get('sec')}с  ${j.get('cost')}")

total = sum((jobs.get(j, {}).get("cost") or 0) for _, j, _ in scheduled)
secs = sum((jobs.get(j, {}).get("sec") or 0) for _, j, _ in scheduled)
print(f"\nитого: ${total:.2f}, {secs/60:.0f} мин работы")
print(f"пропусков по тишине в логе: {len(skipped)}")
for s in skipped[:5]:
    print("  ", s)

# для сравнения — все роли
by_role = {}
for p in LOGS:
    for line in p.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            rec = json.loads(line)
        except Exception:
            continue
        if rec.get("type") != "self":
            continue
        c = rec.get("content")
        if isinstance(c, str):
            try:
                c = json.loads(c)
            except Exception:
                continue
        if isinstance(c, dict) and c.get("role_scheduled"):
            r = c["role_scheduled"]
            cost = (jobs.get(c.get("job"), {}).get("cost") or 0)
            a, b = by_role.get(r, (0, 0.0))
            by_role[r] = (a + 1, b + cost)
print("\nвсе роли по расписанию:")
for r, (n, cost) in sorted(by_role.items()):
    print(f"  {r}: {n} заходов, ${cost:.2f}")
