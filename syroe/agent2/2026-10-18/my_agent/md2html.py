#!/usr/bin/env python3
"""md2html.py — перевод документа из Markdown в HTML для печати в PDF.

Содержание НЕ меняется: только разметка.
Особый случай — строка `---` сразу после текста: по правилам Markdown она превратила бы последнюю
строку абзаца в заголовок (setext-заголовок). Здесь она всегда трактуется как разделитель,
чтобы текст остался дословно таким, как написан.

Поддерживается: заголовки ##, полужирный, таблицы, разделители, абзацы.
"""
import html
import re
import sys

SRC, DST = sys.argv[1], sys.argv[2]
lines = open(SRC, encoding="utf-8").read().split("\n")

CSS = """
@page { size: A4; margin: 20mm 18mm 18mm 18mm; }
body { font-family: "PT Serif", "Times New Roman", Georgia, serif;
       font-size: 11.5pt; line-height: 1.55; color: #1a1a1a; margin: 0; }
h1 { font-size: 17pt; margin: 0 0 14pt 0; color: #111; page-break-after: avoid; }
h2 { font-size: 14pt; margin: 22pt 0 8pt 0; color: #111; page-break-after: avoid;
     border-bottom: 1px solid #d8d8d8; padding-bottom: 4pt; }
p { margin: 0 0 8pt 0; text-align: justify; hyphens: auto; }
strong { font-weight: 600; }
hr { border: none; border-top: 1px solid #cccccc; margin: 16pt 0; }
blockquote { margin: 8pt 0 10pt 0; padding: 2pt 0 2pt 12pt;
             border-left: 3px solid #b8b8b8; background: #fafafa; }
blockquote p { margin: 0 0 6pt 0; }
blockquote p:last-child { margin-bottom: 0; }
table { border-collapse: collapse; width: 100%; margin: 6pt 0 12pt 0;
        font-size: 10.5pt; page-break-inside: avoid; }
th, td { border: 1px solid #c8c8c8; padding: 5pt 7pt; text-align: left;
         vertical-align: top; }
th { background: #f2f2f2; font-weight: 600; }
td { text-align: left; }
code { font-family: "SF Mono", Menlo, Consolas, monospace; font-size: 9pt;
       background: #f4f4f4; padding: 0.5pt 3pt; border-radius: 2px; }
"""

out, para, tbl, quote = [], [], [], []


def inline(s):
    s = html.escape(s)
    # инлайн-код: `текст` -> <code>текст</code>; делаем до жирного, чтобы не конфликтовало
    s = re.sub(r"`([^`]+)`", r"<code>\1</code>", s)
    return re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", s)


def flush_para():
    if para:
        txt = " ".join(para).strip()
        if txt:
            out.append(f"<p>{inline(txt)}</p>")
        para.clear()


def flush_quote():
    """Цитата: строки, начинающиеся с «>». Внутри пустая строка «>» делит абзацы."""
    if not quote:
        return
    blocks, cur = [], []
    for q in quote:
        if q == "":
            if cur:
                blocks.append(" ".join(cur)); cur = []
        else:
            cur.append(q)
    if cur:
        blocks.append(" ".join(cur))
    if blocks:
        inner = "".join(f"<p>{inline(b)}</p>" for b in blocks)
        out.append(f"<blockquote>{inner}</blockquote>")
    quote.clear()


def flush_table():
    if not tbl:
        return
    rows = [r for r in tbl if not re.fullmatch(r"[\s|:\-]+", r)]
    if not rows:
        tbl.clear(); return
    cells = lambda r: [c.strip() for c in r.strip().strip("|").split("|")]
    head = cells(rows[0])
    body = [cells(r) for r in rows[1:]]
    h = "<tr>" + "".join(f"<th>{inline(c)}</th>" for c in head) + "</tr>"
    b = "".join("<tr>" + "".join(f"<td>{inline(c)}</td>" for c in r) + "</tr>" for r in body)
    out.append(f"<table><thead>{h}</thead><tbody>{b}</tbody></table>")
    tbl.clear()


for raw in lines:
    s = raw.strip()
    if s.startswith(">"):
        flush_para(); flush_table()
        quote.append(s.lstrip(">").strip()); continue
    flush_quote()
    if s.startswith("|") and s.endswith("|"):
        flush_para(); tbl.append(s); continue
    flush_table()
    if not s:
        flush_para(); continue
    if re.fullmatch(r"-{3,}", s):
        flush_para(); out.append("<hr>"); continue
    m = re.match(r"^(#{1,3})\s+(.*)$", s)
    if m:
        flush_para()
        lvl = len(m.group(1))
        out.append(f"<h{lvl}>{inline(m.group(2))}</h{lvl}>")
        continue
    para.append(s)
flush_quote(); flush_table(); flush_para()

open(DST, "w", encoding="utf-8").write(
    "<!doctype html><html lang='ru'><head><meta charset='utf-8'>"
    f"<style>{CSS}</style></head><body>" + "\n".join(out) + "</body></html>")
print(f"HTML готов; блоков: {len(out)}")
