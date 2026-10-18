#!/usr/bin/env python3
"""Полный обход воркспейса Notion в один читаемый файл.

Зачем не «читать по запросу»: чтобы понять, что вообще есть у Ильи, нужен
разовый снимок целиком — иначе каждый вопрос про его дела стоит десяток
запросов вслепую. Снимок кладётся в derived/ (производное, сносится и
пересобирается), а выводы из него — в память фактами.

    python -m jarvis.modules.notion.dump

Результат: derived/notion/workspace.md (человекочитаемо) и raw.json (сырьё).
"""
import concurrent.futures as cf
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from jarvis.core import config as C  # noqa: E402
from jarvis.modules.notion import api  # noqa: E402

OUT = C.DERIVED / "notion"


def run(with_blocks: bool = True) -> Path:
    OUT.mkdir(parents=True, exist_ok=True)
    objs = api.search()
    by_id = {o["id"]: o for o in objs}
    pages = [o for o in objs if o["object"] == "page"]
    dbs = [o for o in objs if o["object"] == "database"]
    print(f"видно: {len(pages)} страниц, {len(dbs)} баз", flush=True)

    # Строки баз выводим внутри своей базы, а не отдельными страницами:
    # иначе снимок превращается в плоскую свалку из сотен пунктов.
    in_db = {p["id"] for p in pages if p.get("parent", {}).get("type") == "database_id"}

    lines: list[str] = ["# Снимок воркспейса Notion", ""]
    me = api.whoami()
    ws = me.get("bot", {}).get("owner", {})
    lines += [f"Воркспейс: {me.get('bot', {}).get('workspace_name', ws)}",
              f"Объектов видно интеграции: {len(objs)} ({len(pages)} страниц, {len(dbs)} баз)",
              "", "---", ""]

    # Обход идёт в потоках: каждый запрос к Notion ждёт больше секунды, и
    # последовательный проход по сотне объектов вырождается в ожидание.
    # Темп всё равно держит шлагбаум в api (~3 rps), потоки только его насыщают.
    def safe(fn, *a):
        try:
            return fn(*a)
        except Exception as e:
            return e

    with cf.ThreadPoolExecutor(max_workers=6) as pool:
        rows_by_db = dict(zip(
            [d["id"] for d in dbs],
            pool.map(lambda d: safe(api.db_rows, d["id"]), dbs)))
        tops = [p for p in pages if p["id"] not in in_db]
        body_by_page = {}
        if with_blocks:
            done = [0]

            def one(pg):
                r = safe(api.blocks_text, pg["id"])
                done[0] += 1
                print(f"  {done[0]}/{len(tops)} {api.title_of(pg)[:50]}", flush=True)
                return r

            body_by_page = dict(zip([p["id"] for p in tops], pool.map(one, tops)))

    lines.append("## Базы данных")
    lines.append("")
    for d in sorted(dbs, key=lambda x: api.title_of(x)):
        rows = rows_by_db.get(d["id"])
        if isinstance(rows, Exception):            # доступ мог быть отозван между вызовами
            lines.append(f"### {api.title_of(d)} — ошибка чтения: {rows}")
            continue
        props = list((d.get("properties") or {}).keys())
        lines.append(f"### {api.title_of(d)}  `{d['id']}`")
        lines.append(f"свойства: {', '.join(props)}")
        lines.append(f"записей: {len(rows)}")
        for r in rows:
            vals = []
            for name, v in (r.get("properties") or {}).items():
                if v.get("type") == "title":
                    continue
                s = api.prop_text(v)
                if s:
                    vals.append(f"{name}: {s}")
            lines.append(f"- **{api.title_of(r)}** — " + "; ".join(vals[:8]))
        lines.append("")

    lines += ["---", "", "## Страницы", ""]
    for p in tops:
        parent = p.get("parent", {})
        where = parent.get("type")
        pid = parent.get("page_id") or parent.get("database_id")
        ptitle = api.title_of(by_id[pid]) if pid in by_id else where
        lines.append(f"### {api.title_of(p)}  `{p['id']}`")
        lines.append(f"родитель: {ptitle} | изменена: {p.get('last_edited_time', '')[:16]}")
        if with_blocks:
            body = body_by_page.get(p["id"], "")
            if isinstance(body, Exception):
                body = f"(не прочиталось: {body})"
            if body:
                lines.append("")
                lines.append(body)
        lines.append("")

    md = OUT / "workspace.md"
    md.write_text("\n".join(lines), encoding="utf-8")
    (OUT / "raw.json").write_text(json.dumps(objs, ensure_ascii=False), encoding="utf-8")
    print(f"готово: {md} ({md.stat().st_size // 1024} КБ)")
    return md


if __name__ == "__main__":
    run(with_blocks="--no-blocks" not in sys.argv)
