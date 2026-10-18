"""Проверка modules/audit/daily.py: счёт шлюза берёт только type=self+gate (bl-087).

Зачем отдельным файлом. У daily.py до этой правки не было ни одной машинной
проверки: карантин gate-фикстур (FIX.is_gate_fixture) был испытан только на
уровне core/fixtures.py и core/registry.py — сам build() из modules/audit/daily.py
никто не гонял на подделке лога. Риск конкретный: 25.08 диагностика маркером
(grep по «SomeNewTool» в живом логе) сама оставила записи type=action, где этот
же маркер лежит текстом в аргументе Bash-команды. Если бы счёт шлюза когда-нибудь
стал искать маркер по содержимому записи вместо разбора по типу и полю gate —
такие action-строки задвоили бы счётчик. Эта проверка ловит именно такую регрессию:
конструирует лог с четырьмя строками (живой self+gate, gate-фикстура self+gate,
action с тем же текстом что фикстура, action с тем же текстом что живой self) и
требует, чтобы счётчик gates видел только две первые.

Запуск: ./.venv/bin/python -m jarvis.modules.audit.daily_selftest
"""
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from jarvis.core import config as C            # noqa: E402
from jarvis.modules.audit import daily as D     # noqa: E402


def _line(ts, type_, content):
    return json.dumps({"ts": ts, "type": type_, "actor": "agent",
                       "content": json.dumps(content, ensure_ascii=False)
                       if isinstance(content, dict) else content, "refs": []},
                      ensure_ascii=False)


def main() -> int:
    bad = []
    saved_log, saved_derived = C.LOG_DIR, C.DERIVED
    day = "2026-08-24"
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        C.LOG_DIR = tmp / "log"
        C.DERIVED = tmp / "derived"
        C.LOG_DIR.mkdir(parents=True, exist_ok=True)
        try:
            before_window = "2026-08-25T10:36:04+10:00"   # до заслонки (GATE_WINDOW_END)
            after_window = "2026-08-26T01:00:00+10:00"     # после
            rows = [
                # живой отказ шлюза, type=self+gate — обязан считаться
                _line(after_window, "self", {"gate": "deny", "tool": "Write",
                                              "input": "{'file_path': 'memory/facts/ilya.md'}"}),
                # gate-фикстура ручной проверки bl-047/bl-048, type=self+gate,
                # до окна заслонки — обязана уйти в карантин
                _line(before_window, "self", {"gate": "deny", "tool": "SomeNewTool", "input": "{}"}),
                # тот же текст, что у фикстуры, но type=action — это и есть след
                # диагностики маркером (grep по SomeNewTool), а не отказ шлюза.
                # Разбор по типу обязан пройти мимо неё вовсе, не только в карантин.
                _line(before_window, "action",
                      {"tool": "Bash", "input": "grep -rn SomeNewTool memory/log",
                       "gate": "deny"}),
                # тот же текст, что у живого self, но type=action — тоже не в счёт
                _line(after_window, "action",
                      {"tool": "Bash", "input": "смотрю на gate: deny, tool: Write",
                       "gate": "deny"}),
            ]
            (C.LOG_DIR / f"{day}.jsonl").write_text("\n".join(rows) + "\n", encoding="utf-8")

            text = D.build(day)
            import ast
            import re
            m = re.search(r"шлюз по режимам: (\{.*?\})", text)
            if not m:
                bad.append("строка «шлюз по режимам» не найдена в отчёте")
            else:
                gates = ast.literal_eval(m.group(1))
                if gates != {"deny": 1}:
                    bad.append(f"счёт шлюза должен быть {{'deny': 1}} (фикстура и обе action-строки "
                               f"вне счёта), получено {gates}")
        finally:
            C.LOG_DIR, C.DERIVED = saved_log, saved_derived

    if bad:
        print("✗ аудит суток, счёт шлюза:")
        for b in bad:
            print("  " + b)
        return 1
    print("✓ аудит суток: шлюз считает только type=self с полем gate, живой отказ не теряется")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
