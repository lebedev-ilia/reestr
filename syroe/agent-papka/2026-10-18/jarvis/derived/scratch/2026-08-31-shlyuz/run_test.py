"""Запуск отдельных проверок самотеста без полного прогона."""
import os
import sys
os.environ.setdefault("JARVIS_SELFTEST", "1")
from jarvis.core import selftest as S  # noqa: E402

for name in sys.argv[1:]:
    getattr(S, name)()
print("\nПРОВАЛЫ:", S.fails or "нет")
sys.exit(1 if S.fails else 0)
