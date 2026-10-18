"""Мутационная проба: снимаем запрет — самотест обязан покраснеть."""
import sys
sys.path.insert(0, "/home/ilya/Рабочий стол/Agent")

from jarvis.core import agent as A          # noqa: E402
from jarvis.core import selftest as S       # noqa: E402

S.test_task_md_cap()
print("на здоровом коде: зелено")

assert not S.fails, f"на здоровом коде красные строки: {S.fails}"

A.task_md_cap_danger = lambda t, i: None
S.test_task_md_cap()
print("красных строк после снятия запрета:", len(S.fails), S.fails)
print("ХОРОШО: самотест краснеет" if S.fails
      else "ПЛОХО: самотест не заметил снятого запрета")
