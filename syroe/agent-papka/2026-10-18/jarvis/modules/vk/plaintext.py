"""Markdown → читаемый текст ВК.

ВК не знает разметки: `**жирный**` приходит со звёздочками, таблица
расползается, а абзац в тысячу знаков читается как стена. Илья сказал об этом
прямо 23.08.2026: «вк это не маркдаун, всё сливается», «текст сплошной».

Модуль занимается только формой. Что писать коротко и без ссылок на источники —
дисциплина, а не код: memory/rules/tekst-v-vk.md.

Правила простые и намеренно консервативные: разметка снимается, смысл не
трогается. Потерять символ хуже, чем оставить лишний.
"""
from __future__ import annotations

import re

# Длиннее — абзац рубится по границе предложений. 250 знаков это примерно
# 3 строки на телефоне: дальше глаз теряет строку.
PARA_LIMIT = 250
SENTENCE_END = re.compile(r'(?<=[.!?…])\s+(?=[«"(A-ZА-ЯЁ0-9])')


def _cell_split(line: str) -> list[str]:
    return [c.strip() for c in line.strip().strip("|").split("|")]


def _is_divider(line: str) -> bool:
    s = line.strip().strip("|")
    return bool(s) and set(s) <= set("-: |")


def _table(rows: list[str]) -> str:
    """Таблица → плоские строки. Шапка становится подписями полей.

    | id | что | статус |      cap-001
    |----|-----|--------|  →   что: канал ВК
    | cap-001 | канал ВК | ok |  статус: ok
    """
    grid = [_cell_split(r) for r in rows if not _is_divider(r)]
    if not grid:
        return ""
    head, body = grid[0], grid[1:]
    if not body:                       # шапка без строк — просто перечисление
        return " · ".join(c for c in head if c)
    out = []
    for row in body:
        first = row[0] if row else ""
        lines = [first] if first else []
        for name, val in zip(head[1:], row[1:]):
            if not val or val in {"—", "-"}:
                continue
            lines.append(f"{name}: {val}" if name else val)
        if lines:
            out.append("\n".join(lines))
    return "\n\n".join(out)


def _inline(s: str) -> str:
    """Снять внутристрочную разметку."""
    s = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", s)              # картинки — прочь
    s = re.sub(r"\[([^\]]+)\]\((\S+?)\)",
               lambda m: m.group(1) if m.group(1).strip() == m.group(2).strip()
               else f"{m.group(1)}: {m.group(2)}", s)        # ссылка → текст: url
    s = re.sub(r"\*\*\*(.+?)\*\*\*", r"\1", s)
    s = re.sub(r"\*\*(.+?)\*\*", r"\1", s)
    s = re.sub(r"__(.+?)__", r"\1", s)
    s = re.sub(r"(?<![\w*])\*(?!\s)(.+?)(?<!\s)\*(?![\w*])", r"\1", s)
    s = re.sub(r"(?<![\w`])`([^`]+)`", r"\1", s)
    return s


def _wrap(para: str) -> list[str]:
    """Длинный абзац — на куски по границе предложений."""
    if len(para) <= PARA_LIMIT or "\n" in para:
        return [para]
    parts, cur = [], ""
    for sent in SENTENCE_END.split(para):
        if cur and len(cur) + len(sent) + 1 > PARA_LIMIT:
            parts.append(cur.strip())
            cur = sent
        else:
            cur = f"{cur} {sent}".strip()
    if cur:
        parts.append(cur.strip())
    return parts


def render(text: str) -> str:
    """Главное: markdown на входе, текст для ВК на выходе."""
    if not text:
        return text
    lines = text.replace("\r\n", "\n").split("\n")
    out: list[str] = []       # готовые блоки
    table: list[str] = []
    code = False

    def flush_table():
        if table:
            t = _table(table)
            table.clear()
            if t:
                out.append(t)

    for raw in lines:
        line = raw.rstrip()
        stripped = line.strip()

        if stripped.startswith("```"):
            flush_table()
            code = not code
            continue
        if code:                                  # код — как есть, без разметки
            out.append(line if line else "")
            continue

        if "|" in stripped and stripped.startswith("|"):
            table.append(stripped)
            continue
        flush_table()

        if not stripped:
            out.append("")
            continue
        if re.fullmatch(r"[-*_=]{3,}", stripped):  # горизонтальная линия
            out.append("")
            continue

        m = re.match(r"^(#{1,6})\s+(.*)$", stripped)
        if m:                                      # заголовок — своей строкой
            out.append("")
            out.append(_inline(m.group(2)).rstrip(":") + ":")
            continue

        m = re.match(r"^\s*(?:[-*+•]|\d+[.)])\s+(.*)$", line)
        if m:
            out.append("— " + _inline(m.group(1)))
            continue

        m = re.match(r"^>\s?(.*)$", stripped)      # цитата
        if m:
            out.append(_inline(m.group(1)))
            continue

        out.append(_inline(stripped))

    flush_table()

    # Сборка: подряд идущие обычные строки склеиваются в абзац, пункты списка
    # остаются каждый на своей строке, между блоками — пустая строка.
    blocks: list[str] = []
    buf: list[str] = []

    def flush_buf():
        if buf:
            blocks.extend(_wrap(" ".join(buf).strip()))
            buf.clear()

    for chunk in out:
        if not chunk.strip():
            flush_buf()
            continue
        if chunk.startswith("— ") or "\n" in chunk or chunk.endswith(":"):
            flush_buf()
            blocks.append(chunk)
        else:
            buf.append(chunk.strip())
    flush_buf()

    # Пункты одного списка держим вместе (одиночный перевод строки),
    # остальное разделяем пустой строкой — так абзацы видны.
    res = ""
    prev = ""
    for b in blocks:
        if not res:
            res = b
        elif b.startswith("— ") and prev.startswith("— "):
            res += "\n" + b
        elif prev.endswith(":") and (b.startswith("— ") or len(prev) < 40):
            res += "\n" + b
        else:
            res += "\n\n" + b
        prev = b
    return res.strip()
