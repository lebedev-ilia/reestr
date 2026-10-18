import json
from jarvis.core import memory as M
from jarvis.core import config as C

b = M.context_budget()
print("blocks:")
for k, v in b["blocks"].items():
    print(f"  {k:12} {v:7} / {b['limits'][k]:7} {'РЕЖЕТСЯ' if k in b['over'] else ''}")
print("over:", b["over"])
print("worst:", b["worst"], "budget:", b["budget"], "запас:", b["budget"] - b["worst"])
print("current:", b.get("current"), "current_tokens:", b["current_tokens"])
print("сумма потолков:", sum(C.BLOCK_MAX_BYTES.values()))
r = M._rollups()
print("блок свёрток байт:", len(r.encode()))
