"""Проверка (job-094): блок срывов роли в core/selftest.py не должен зависеть
от того, сколько ролей реально заведено. Подменяем SS.R.roles на список из
ДВУХ ролей (не той, что зашита в тесте) и повторяем ровно ту же
последовательность tick()'ов, что и в самом тесте, целиком на подменённых
файлах — живая память не трогается.
"""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3].parent))

from jarvis.modules.agents import schedule as SS  # noqa: E402

saved = (SS.STATE, SS.HEALTH, SS._shout, SS._busy, SS.J.submit,
         SS.J.capacity, SS.R.roles)
try:
    box = Path(tempfile.mkdtemp())
    SS.STATE, SS.HEALTH = box / "s.json", box / "h.json"
    SS.R.roles = lambda: ["critic", "teamlead-vnutrennii"]  # ДВЕ роли
    said = []
    briefs = []
    SS._shout = lambda t: said.append(t)
    SS._busy = lambda role: False
    SS.J.capacity = lambda: 5
    SS.J.submit = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("подделка"))
    t0 = 1_000_000.0
    SS.tick(t0)
    assert not said, "первый срыв роли — тихо, это ещё не поломка"
    assert not SS.health(), "первый срыв в health не всплывает"
    SS.tick(t0 + 24 * 3600)
    assert len(said) == 1, f"второй срыв подряд — сказано наружу, said={said}"
    assert SS.health(), "сорванная роль видна в health"
    assert any(r["role"] in SS.health_note() for r in SS.health()), \
        "строка про срывы называет роль"
    SS.tick(t0 + 24 * 3600 + 600)
    assert len(said) == 1, f"повтор не спамит чаще раза в 6 часов, said={said}"
    SS.J.submit = lambda brief, **k: (briefs.append(brief), "job-t")[1]
    SS.tick(t0 + 48 * 3600)
    assert any("не поднимаются по расписанию" in b for b in briefs), \
        "роль-сторож получает срывы соседей в задаче"
    assert not SS.health(), "успешный заход обнуляет счётчик срывов"
    print("ВСЁ ЗЕЛЕНО на двух ролях")
finally:
    (SS.STATE, SS.HEALTH, SS._shout, SS._busy,
     SS.J.submit, SS.J.capacity, SS.R.roles) = saved
