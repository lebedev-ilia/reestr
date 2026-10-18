"""Одноразовый пробник: гоняет только новый блок изоляции по диску."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))

from jarvis.core import selftest as S  # noqa: E402

try:
    S.test_project_disk()
except Exception as exc:                              # noqa: BLE001
    print("ИСКЛЮЧЕНИЕ:", type(exc).__name__, exc)
print("провалов всего:", getattr(S, "fails", "?"))
