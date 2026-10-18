"""Сколько приёмов класса «скобки» текло ДО правки: helpers обезврежены."""
import sys

sys.path.insert(0, "/home/ilya/Рабочий стол/Agent")
from jarvis.core import selftest as S, agent as A  # noqa: E402

OWN, CH = "vnutrennii", "proba-chuzhoy"
HOME = "$" + "HOME"
CMDS = [
    ("скобки вокруг каталога", f"cat memory/{{projects,X}}/{CH}/dossier.md", "p"),
    ("скобки внутри слова", f"cat memory/{{pro,}}jects/{CH}/dossier.md", "p"),
    ("скобки в начале слова",
     f"sed -n '1,3p' me{{mory,X}}/projects/{CH}/dossier.md", "p"),
    ("dotfile .en{v,X}", "cat ." + "en{v,X}", "p"),
    ("dotfile .{env,X}", "cat .{" + "env,X}", "p"),
    ("busybox-обёртка", f"busybox cat memory/{{projects,X}}/{CH}/backlog.md", "p"),
    ("HOME + скобки",
     "head -c 90 " + HOME + "/'Рабочий стол'/Agent/jarvis/." + "en{v,X}", "p"),
    ("диапазон {u..w}", "cat ." + "en{u..w}", "p"),
    ("корень projects", "grep -rn ключ memory/{projects,X}", "p"),
    ("tee в чужой проект", f"echo x | tee memory/{{projects,X}}/{CH}/podlog.md", "p"),
    ("редирект в чужой проект", f"echo x > memory/{{projects,X}}/{CH}/podlog.md", "p"),
    ("cp в чужой проект", f"cp derived/x memory/{{projects,X}}/{CH}/y.md", "p"),
    ("rm лога", "rm memory/{log,X}/2026-09-01.jsonl", "-"),
    ("tee поверх лога", "tee memory/{log,X}/2026-09-01.jsonl", "-"),
    ("выход за папку агента", "cp derived/x {/etc,q}/passwd", "-"),
    ("дозапись себе .env", "echo K=1 | tee -a ." + "en{v,X}", "-"),
    ("запись мимо зоны роли", "echo x | tee memory/{agents,X}/legal/y.md", "z"),
]

A._brace_expand = lambda w, limit=0: [w]
A._brace_candidates = lambda w: [w]
A._project_brace_verdict = lambda cmd, own: None

n = 0
for name, cmd, kind in CMDS:
    kw = {"project": OWN} if kind == "p" else (
        {"zone": "memory/agents/critic"} if kind == "z" else {})
    if S.gate_chain("Bash", {"command": cmd}, **kw)[0] != "deny":
        n += 1
        print("  ПРОШЁЛ на старом коде:", name)
print(f"всего дыр на старом коде: {n} из {len(CMDS)}")
