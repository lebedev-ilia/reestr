"""Живая проверка ob-082/ob-083/ob-109 (job-175) — прямо по решениям шлюза.

Запуск из каталога выше корня агента:
    jarvis/.venv/bin/python jarvis/derived/scratch/2026-09-01-shlyuz/probe_zone.py
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))

from jarvis.core import agent as A  # noqa: E402

BAD = []


def check(name, cond):
    print(("  ок    " if cond else "  ПЛОХО") + f"  {name}")
    if not cond:
        BAD.append(name)


# -- ob-083(b): /dev/null в белом списке целей (путь без зоны, как у меня) --
os.environ.pop("JARVIS_ZONE", None)
os.environ.pop("JARVIS_PROJECT", None)
d, why = A.classify("Bash", {"command": "grep -n x core/agent.py 2>/dev/null"})
check("2>/dev/null проходит (root)", d == "allow")
d, why = A.classify("Bash", {"command": "echo x 2>/dev/stderr"})
check("2>/dev/stderr проходит (root)", d == "allow")
d, why = A.classify("Bash", {"command": "echo x > /etc/hosts"})
check("запись в /etc/hosts по-прежнему отбита", d != "allow")

# -- зона роли: критик, без проекта --
os.environ["JARVIS_ZONE"] = "memory/agents/critic/journal"
os.environ.pop("JARVIS_PROJECT", None)


def zbash(cmd):
    return A.outside_zone("Bash", {"command": cmd})


# ob-083(a): читающий for-цикл больше не ловится как запись
r = zbash('for c in 8950bfb 4ea88c6; do git show --stat --format="%h %s" $c; done')
check("for-цикл чтения (git show) проходит", r is None)
r = zbash("for j in a b; do echo x; grep -o y z; done")
check("for-цикл чтения (echo/grep) проходит", r is None)
r = zbash("for j in a; do rm -rf /; done")
check("for-цикл с rm внутри по-прежнему отбит", r is not None)

# ob-082(б): запись в СВОЮ зону через tee/redirect/sed -i теперь проходит адресно
r = zbash("echo привет > memory/agents/critic/journal/proba.md")
check("редирект в свою зону проходит", r is None)
r = zbash("echo привет | tee memory/agents/critic/journal/proba2.md")
check("tee в свою зону проходит", r is None)
r = zbash("sed -i 's/a/b/' memory/agents/critic/journal/proba.md")
check("sed -i в своей зоне проходит", r is None)
r = zbash("cat > memory/agents/critic/journal/proba3.md <<'EOF'\nтекст\nEOF")
check("heredoc-запись в свою зону проходит", r is None)
# но запись мимо зоны по-прежнему отбита
r = zbash("echo привет > memory/agents/teamlead-vnutrennii/journal/чужое.md")
check("редирект в ЧУЖУЮ зону роли отбит", r is not None)
r = zbash("echo привет > /tmp/proba-ob082.md")
check("редирект в /tmp по-прежнему проходит", r is None)

# ob-082(a): worktrees доступны роли с зоной, когда задан её проект
os.environ["JARVIS_PROJECT"] = "vnutrennii"
r = zbash("echo привет > worktrees/agent__t-005-status-tasks-cost/derived/proba.md")
check("запись в worktrees своего проекта проходит (зона+проект)", r is None)
r = A.outside_zone("Write", {"file_path":
    "worktrees/agent__t-005-status-tasks-cost/derived/proba-write.md"})
check("Write в worktrees своего проекта проходит (зона+проект)", r is None)
os.environ.pop("JARVIS_PROJECT", None)
r = zbash("echo привет > worktrees/agent__t-005-status-tasks-cost/derived/proba.md")
check("worktrees без project= в зоне по-прежнему отбита", r is not None)

os.environ.pop("JARVIS_ZONE", None)

print("\nСЛОМАНО:" if BAD else "\nВСЁ ХОРОШО")
for b in BAD:
    print(" -", b)
sys.exit(1 if BAD else 0)
