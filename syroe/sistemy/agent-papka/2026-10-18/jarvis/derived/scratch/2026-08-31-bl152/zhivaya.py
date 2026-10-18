"""Живая проверка bl-152 на настоящем репозитории и настоящих ROLE.yaml.

Три части:
 1) ретроспектива по коммитам: сколько 2-часовых окон прошлых суток были бы
    тихими для тимлида по зоне и сколько — по старому счёту (весь репозиторий);
 2) обе стороны сейчас: тихо без движения в зоне, не тихо после движения
    (файл заводится в зоне и тут же убирается — живое содержимое не трогаем);
 3) движение вне зоны тишину не отменяет.
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))

from jarvis.core import config as C                        # noqa: E402
from jarvis.modules.agents import schedule as SS           # noqa: E402

ROLE = "teamlead-vnutrennii"
w = SS.watch_paths(ROLE)
print("зона наблюдения тимлида:", w)
print("зона наблюдения критика:", SS.watch_paths("critic"))

now = time.time()
print("\n--- 1. Ретроспектива по коммитам, окна по 2 часа за сутки")
tikh_zona = tikh_repo = 0
for i in range(12, 0, -1):
    start = now - i * 2 * 3600
    end = start + 2 * 3600
    c_zone = SS._commits_since(start, w["include"], w["exclude"])
    c_zone_end = SS._commits_since(end, w["include"], w["exclude"])
    c_all = SS._commits_since(start) - SS._commits_since(end)
    in_zone = c_zone - c_zone_end
    tikh_zona += in_zone == 0
    tikh_repo += c_all == 0
    print(f"  окно -{i*2}ч: коммитов в зоне {in_zone}, по всему репо {c_all}")
print(f"  ИТОГ: тихих окон по зоне {tikh_zona}/12, по всему репо {tikh_repo}/12")

print("\n--- 2. Обе стороны сейчас")
q = SS.quiet_since(now - 300, now, include=w["include"], exclude=w["exclude"])
print("  без движения в зоне:", q)
probe = C.ROOT / w["include"][0] / ".proba-bl152"
try:
    probe.write_text("проба зоны наблюдения bl-152\n", encoding="utf-8")
    q2 = SS.quiet_since(now - 300, time.time(),
                        include=w["include"], exclude=w["exclude"])
    print("  после движения в зоне:", q2)
finally:
    probe.unlink(missing_ok=True)

print("\n--- 3. Движение вне зоны")
q3 = SS.quiet_since(now - 300, time.time(),
                    include=["memory/projects/net-takogo-proekta"], exclude=[])
print("  зона, где ничего не происходит:", q3)
q4 = SS.quiet_since(now - 300, time.time())
print("  старый счёт (весь репозиторий, критик):", q4)

print("\nВЕРДИКТ:",
      "обе стороны верны" if (q["quiet"] and not q2["quiet"] and q3["quiet"])
      else "ПЛОХО — проверить руками")
