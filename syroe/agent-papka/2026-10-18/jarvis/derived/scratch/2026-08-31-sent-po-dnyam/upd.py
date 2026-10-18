"""Обновление строки очереди bl-054 после разбора входящих 31.08."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))
from jarvis.core import memory  # noqa: E402

ok = memory.backlog_update(
    "bl-054",
    status="sleep:2026-09-01",
    checkpoint=(
        "2026-08-31 00:0x разбор за 28-31.08: новых ответов НЕТ ни одного "
        "(живые от 27.08 уже разобраны), отлупов и автоответов нет, мимо "
        "рассылки 2 служебных письма. Досье пересобраны: 69 контактов, 71 "
        "письмо, 9 ответов, ответили 4. Главное: рассылка НЕ в простое — 30.08 "
        "планировщик сам отправил 15 писем; 28-29.08 ноль, потому что агента "
        "не было (логов за эти дни нет). Факт planirovshchik-rassylki-po-poyasam "
        "дополнен, сводка derived/logs/2026-08-31-razbor-vhod.md. "
        "Дальше: прогон 01.09, смотреть ответы на партию 30.08"
    ),
    bump_cycle=True,
)
print("backlog_update:", ok)
