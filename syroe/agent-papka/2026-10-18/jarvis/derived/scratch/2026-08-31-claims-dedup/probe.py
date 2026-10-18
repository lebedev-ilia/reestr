import sys, os
sys.path.insert(0, os.path.expanduser("~/Рабочий стол/Agent"))
from jarvis.core import claims as CL
from jarvis.core import morph

pairs = [
    ("critic work по расписанию 490000", "teamlead-vnutrennii work по расписанию 490000"),
    ("critic work по расписанию 490000", "critic work по расписанию 490000"),
    ("critic work по расписанию 490000", "lawyer work по расписанию 490000"),
    ("critic work по расписанию 490000", "critic work по расписанию 490001"),
    ("critic ask по расписанию 490000", "critic work по расписанию 490000"),
    ("critic work подъём сторожем 1756600000", "lawyer work подъём сторожем 1756600001"),
]
for a, b in pairs:
    ka, kb = CL._key(a), CL._key(b)
    print(f"{CL._similar(ka, kb):.3f}  |{a}| vs |{b}|")
    print(f"        A={sorted(ka)}")
    print(f"        B={sorted(kb)}")
