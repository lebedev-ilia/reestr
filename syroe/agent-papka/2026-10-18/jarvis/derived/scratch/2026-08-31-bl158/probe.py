import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))

from jarvis.core import agent as A
from jarvis.core import config as C

tail = 'chmod +x core/githooks/pre-commit && git config core.hooksPath core/githooks'
cases = {
    "живой ROOT (как теперь в тесте)": f'cd "{C.ROOT}" && {tail}',
    "чужой путь (как было зашито)": f'cd "/home/other/Desktop/Agent/jarvis" && {tail}',
}
for name, cmd in cases.items():
    print(name, "->", A.classify("Bash", {"command": cmd})[0])
print("ROOT =", C.ROOT)
