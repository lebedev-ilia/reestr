"""Кривой ввод: шлюз обязан ответить отказом или пропуском, но не упасть."""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))

from jarvis.core import agent as A  # noqa: E402

os.environ["JARVIS_PROJECT"] = "vnutrennii"

KRIVOE = [
    "cat ~nesushchestvuyushchiy-polzovatel/.env",
    "cat file\x00name",
    "cat [",
    "cat ***/**/**",
    "grep -rn x '['",
    "cat " + "a/" * 400 + "b",
    "cd " + "../" * 60 + " && cat etc/passwd",
    "cat ''",
    "cat \"",
    "grep -rn x -- -",
    "cat -",
    "tar -czf - --exclude=* .",
    "cat {}",
    "find . -name '*' -exec",
    "",
]

upalo = 0
for cmd in KRIVOE:
    try:
        verdict = A.outside_project("Bash", {"command": cmd})
        print(("отказ  " if verdict else "прошло ") + repr(cmd[:60]))
    except Exception as exc:                                  # noqa: BLE001
        upalo += 1
        print(f"УПАЛО  {type(exc).__name__}: {exc} ← {cmd[:60]!r}")
print("падений:", upalo)
