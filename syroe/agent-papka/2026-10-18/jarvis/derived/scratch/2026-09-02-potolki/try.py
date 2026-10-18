import sys, json
sys.path.insert(0, "..")
from jarvis.core import memory as M
from jarvis.core import config as C

ORIG = dict(C.BLOCK_MAX_BYTES)

def total(d):
    return sum(d.values())

candidates = {
    "morning": 5500,
    "now": 3500,
    "obligations": 10500,
    "questions": 2000,
    "rules": 21700,
    "registry": 5200,
    "index": 21500,
    "log": 12000,
    "rollups": 10500,
}

print("sum orig", total(ORIG))
print("sum cand", total(candidates))

C.BLOCK_MAX_BYTES.clear()
C.BLOCK_MAX_BYTES.update(candidates)
C.RULES_BLOCK_MAX_BYTES = candidates["rules"]
try:
    b = M.context_budget()
    print(json.dumps({"raw": b["raw"], "over": b["over"], "worst": b["worst"], "budget": b["budget"]}, ensure_ascii=False, indent=2))

    # rules margin check
    digest = M.rules_digest()
    size = len(digest.encode())
    head, _, folded = digest.partition(M.FOLDED_HEADER)
    n_full = max(1, len([l for l in head.splitlines() if l.startswith('- [')]))
    n_folded = max(1, len([l for l in folded.splitlines() if l.startswith('- [')]))
    full_price = len(head.encode()) // n_full
    folded_price = len(folded.encode()) // n_folded
    need = full_price + 3*folded_price
    free = C.RULES_BLOCK_MAX_BYTES - size
    print("rules: size", size, "free", free, "need", need, "ok", free >= need)

    # rollups formula check
    worst_digests = 15 * C.ROLLUP_DIGEST_BYTES
    rneed = C.WEEK_ROLLUP_PROMPT_BYTES + worst_digests + 500
    print("rollups: need", rneed, "limit", C.BLOCK_MAX_BYTES["rollups"], "ok", rneed <= C.BLOCK_MAX_BYTES["rollups"])

    # obligations pile stress (100 fake, per selftest)
    import tempfile
    from pathlib import Path
    saved_ob = C.OBLIGATIONS
    OB_HEADER = "| id | что | кто | срок | статус | источник | переносов |\n|----|----|----|----|----|----|----|\n"
    with tempfile.TemporaryDirectory() as td:
        C.OBLIGATIONS = Path(td) / "obligations.md"
        pile = [f"| ob-{i:03d} | {'разбор вместо обязательства ' * 20} | agent "
                f"| 2026-09-{(i % 28) + 1:02d} | open | log:2026-08-30 | 0 |"
                for i in range(100)]
        C.OBLIGATIONS.write_text(OB_HEADER + "\n".join(pile) + "\n", encoding="utf-8")
        pile_block = M._obligations_block()
    C.OBLIGATIONS = saved_ob
    print("obligations pile: bytes", len(pile_block.encode()), "limit", C.BLOCK_MAX_BYTES["obligations"],
          "ok_fits", len(pile_block.encode()) <= C.BLOCK_MAX_BYTES["obligations"],
          "all_ids", all(f"[ob-{i:03d}]" in pile_block for i in range(100)))
finally:
    C.BLOCK_MAX_BYTES.clear()
    C.BLOCK_MAX_BYTES.update(ORIG)
