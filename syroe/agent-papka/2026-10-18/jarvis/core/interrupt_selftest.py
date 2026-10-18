"""Самотест мягкого прерывания хода (core/interrupt.py).

Проверяется то, что ломается тихо и обнаруживается только на живом Илье:
сообщение потерялось, сообщение доставлено дважды, «да» на вопрос разрешения
съедено прерыванием, ход зациклился на прерываниях.

    cd "…/Agent" && jarvis/.venv/bin/python -m jarvis.core.interrupt_selftest
"""
import asyncio
from concurrent.futures import ThreadPoolExecutor

from jarvis.core import agent as A
from jarvis.core import interrupt as INT
from jarvis.core import memory as M

fails: list[str] = []

# Фикстуры прогона. По ним же проверяется, что ни одна из них не попала в
# живой лог: раньше их там за сутки набиралось больше пятисот (bl-061).
MARKERS = ("нужно 11, а не 10", "поздний вопрос", "сообщение 0", "сделай уже")


def check(name: str, ok: bool) -> None:
    print(("  ок   " if ok else "  ПЛОХО") + f"  {name}")
    if not ok:
        fails.append(name)


def gate(tool: str = "Read", args: dict | None = None) -> dict:
    """Решение шлюза целиком — тем же путём, что у живого вызова."""
    hook = A.make_gate(unattended=True, can_ask=True)
    with ThreadPoolExecutor(max_workers=1) as pool:
        out = pool.submit(asyncio.run, hook(
            {"tool_name": tool, "tool_input": args or {"file_path": str(A.C.ROOT / "README.md")}},
            "id", None)).result()
    return out["hookSpecificOutput"]


def run() -> int:
    """Прогон целиком идёт под защитой: живой лог тестом не пишется."""
    with M.selftest_guard(MARKERS) as guard:
        _body()
    check("живой лог самотестом не тронут", guard["ok"])
    print("\nВСЁ ХОРОШО" if not fails else f"\nСЛОМАНО: {fails}")
    return 1 if fails else 0


def _body() -> None:
    print("ПРЕРЫВАНИЕ ХОДА")

    # 1. Вне хода вбрасывать некуда: сообщение должно идти обычным путём.
    INT.disarm()
    check("вне хода push отбит", INT.push("привет") is False)
    check("вне хода шлюз работает как раньше", gate()["permissionDecision"] == "allow")

    # 2. Сообщение посреди хода доходит до агента и ровно один раз.
    INT.arm()
    check("в ходе push принят", INT.push("нужно 11, а не 10") is True)
    out = gate()
    check("шлюз отдал прерывание", out["permissionDecision"] == "deny")
    check("текст Ильи внутри", "нужно 11" in out["permissionDecisionReason"])
    check("сказано, что это не запрет", "НЕ ЗАПРЕТ" in out["permissionDecisionReason"])
    check("сказано повторить действие", "повтори" in out["permissionDecisionReason"])
    check("следующий вызов уже разрешён", gate()["permissionDecision"] == "allow")
    check("очередь пуста", not INT.pending())
    check("после доставки ничего не осталось", INT.disarm() == [])

    # 3. Два сообщения подряд приходят одним вбросом, а не двумя.
    INT.arm()
    INT.push("раз")
    INT.push("два")
    out = gate()
    body = out["permissionDecisionReason"]
    check("оба сообщения в одном вбросе", "раз" in body and "два" in body)
    check("второй вызов не повторяет их", gate()["permissionDecision"] == "allow")
    INT.disarm()

    # 4. Пауза на время вопроса разрешения: «да» не должно уйти в прерывание.
    INT.arm()
    INT.pause(True)
    check("на паузе наблюдатель молчит", INT.paused() is True)
    INT.pause(False)
    check("пауза снимается", INT.paused() is False)
    INT.disarm()

    # 5. Не успевшее к ходу не теряется, а возвращается демону.
    INT.arm()
    INT.push("поздний вопрос", {"id": 7, "text": "поздний вопрос"})
    left = INT.disarm()
    check("опоздавшее вернулось демону", len(left) == 1 and left[0]["msg"]["id"] == 7)
    check("и не задваивается", INT.disarm() == [])

    # 6. Предохранитель: поток сообщений не превращает ход в одни прерывания.
    INT.arm()
    for i in range(INT.MAX_PER_TURN + 3):
        INT.push(f"сообщение {i}")
        gate()
    check(f"вбросов за ход не больше {INT.MAX_PER_TURN}",
          gate()["permissionDecision"] == "allow")
    left = INT.disarm()
    check("лишнее ушло в опоздавшие, а не пропало", len(left) == 3)

    # 6a. Подмена лога жива: фикстура легла в производную зону, а не в память.
    ref = M.log("self", {"selftest_probe": MARKERS[0]})
    probe = (A.C.SELFTEST_LOG / f"{M.today().isoformat()}.jsonl")
    check("запись самотеста ушла в derived",
          bool(ref) and probe.exists() and MARKERS[0] in probe.read_text(encoding="utf-8"))

    # 7. Запрещённое навсегда остаётся запрещённым и в прерывании.
    INT.arm()
    INT.push("сделай уже")
    gate()                                   # прерывание съедено
    out = gate("Bash", {"command": "sudo rm -rf /"})
    check("sudo по-прежнему запрещён",
          out["permissionDecision"] == "deny" and "прерыв" not in out["permissionDecisionReason"])
    INT.disarm()


if __name__ == "__main__":
    raise SystemExit(run())
