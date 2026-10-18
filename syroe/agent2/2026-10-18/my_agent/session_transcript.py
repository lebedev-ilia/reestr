#!/usr/bin/env python3
"""
session_transcript.py — выгрузка полной стенограммы нашей переписки из хранилища DSH.

Зачем: правило «фиксировать всё — мои сообщения, ответы агента, каждое действие»
выполняется по первоисточнику, а не пересказом. DSH пишет сессию в
~/.dsh/sessions/<workspace>/<session>/session.v4.jsonl.zstd — отсюда берём точные тексты.

Использование:
  session_transcript.py                 # выгрузить текущую сессию рабочей папки Agent2
  session_transcript.py --list          # показать все сессии
  session_transcript.py --session <id>  # конкретная сессия
  session_transcript.py --with-results  # включить вывод инструментов (по умолчанию только вызовы)
  session_transcript.py --out <файл>    # куда писать (по умолчанию my_agent/transcript.md)

⚠️ Стенограмма содержит секреты (пароли, ключи, session string), которые были в переписке.
Файл локальный, публиковать нельзя.
"""
import argparse
import json
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

DSH_SESSIONS = Path.home() / ".dsh" / "sessions"
WORKDIR = Path("/Users/user/Desktop/Agent2")
OUT_DEFAULT = WORKDIR / "my_agent" / "transcript.md"
TZ = timezone(timedelta(hours=10))  # UTC+10, Владивосток


def session_dirs():
    if not DSH_SESSIONS.exists():
        return []
    return sorted(p for p in DSH_SESSIONS.iterdir() if p.is_dir())


def session_meta(path: Path) -> dict:
    """Первая запись сессии: время создания, признак подагента, глубина делегирования."""
    out = subprocess.run(["unzstd", "-c", str(path)], capture_output=True)
    if out.returncode != 0:
        return {}
    first = out.stdout.decode("utf-8", errors="replace").split("\n", 1)[0]
    try:
        return json.loads(first)
    except json.JSONDecodeError:
        return {}


def is_subagent(path: Path) -> bool:
    """Сессия подагента, а не рабочей папки.

    Стенограмма родительской сессии не должна тянуть за собой переписку подагентов:
    у них свой контекст и свои сообщения. Признак берём из самой сессии
    (`origin: subagent` / `delegationDepth > 0`), а не из имени каталога.
    """
    meta = session_meta(path)
    return meta.get("origin") == "subagent" or int(meta.get("delegationDepth") or 0) > 0


def session_created_at(path: Path) -> float:
    """Время создания сессии из первой записи файла (epoch, сек).

    Нужно, чтобы упорядочить сессии по хронологии: mtime не годится — DSH
    дописывает файл живой сессии, поэтому сортировка по mtime переворачивает
    историю и выбирает не ту сессию.
    """
    meta = session_meta(path)
    ts = meta.get("createdAt")
    if isinstance(ts, (int, float)) and ts > 0:
        return float(ts) / 1000.0
    return path.stat().st_mtime


def find_sessions(name_part: str | None = None):
    """Все сессии рабочей папки, по хронологии создания (сначала старые).

    Возвращает список путей: стенограмма должна включать историю целиком,
    а не только текущую сессию.
    """
    # Каталог сессий DSH назван по рабочей папке в виде --Users-user-Desktop-Agent2--
    # (разделители заменены дефисами). Сессии подагентов живут в отдельных каталогах,
    # чьи имена содержат тот же текст как подстроку (например --Users-user-...-Agent2--),
    # поэтому сравнение идёт по каталогу, а не по вхождению подстроки.
    part = name_part or WORKDIR.name
    expected = "--" + str(WORKDIR).replace("/", "-") + "--"
    cands: list[tuple[float, Path]] = []
    for d in session_dirs():
        if d.name != expected and part.lower() not in d.name.lower():
            continue
        for sess in d.iterdir():
            if not sess.is_dir():
                continue
            f = sess / "session.v4.jsonl.zstd"
            if f.exists() and not is_subagent(f):
                cands.append((session_created_at(f), f))
    if not cands:
        sys.exit(f"сессия для «{part}» не найдена в {DSH_SESSIONS}")
    cands.sort(key=lambda x: x[0])
    return [f for _, f in cands]


def find_session(name_part: str | None = None):
    """Последняя (текущая) сессия рабочей папки — для обратной совместимости."""
    return find_sessions(name_part)[-1]


def read_jsonl(path: Path):
    out = subprocess.run(["unzstd", "-c", str(path)], capture_output=True)
    if out.returncode != 0:
        sys.exit(f"не удалось распаковать {path}: {out.stderr.decode()[:200]}")
    for line in out.stdout.decode("utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            yield json.loads(line)
        except json.JSONDecodeError:
            continue


def text_of(content):
    """Собирает текст из content-блока сообщения."""
    if isinstance(content, str):
        return content
    parts = []
    for b in content or []:
        if not isinstance(b, dict):
            continue
        t = b.get("type")
        if t == "text":
            parts.append(b.get("text", ""))
        elif t == "reasoning":
            continue
        elif t == "tool-call":
            continue
    return "\n".join(p for p in parts if p).strip()


def fmt_time(ts):
    """Время в записях — epoch в миллисекундах."""
    try:
        if isinstance(ts, (int, float)) or (isinstance(ts, str) and ts.isdigit()):
            sec = float(ts) / 1000.0
            return datetime.fromtimestamp(sec, TZ).strftime("%Y-%m-%d %H:%M")
        return datetime.fromisoformat(str(ts).replace("Z", "+00:00")).astimezone(TZ).strftime("%Y-%m-%d %H:%M")
    except Exception:
        return "?"


# Служебные вставки среды: не являются сообщениями пользователя, в стенограмму не идут
SCAFFOLD_PREFIXES = (
    "Current runtime context",
    "<system-reminder>",
    "Additional instructions from:",
    "Updated instructions from:",
    "Instructions from:",
)


def is_scaffold(txt: str) -> bool:
    t = txt.lstrip()
    return any(t.startswith(p) for p in SCAFFOLD_PREFIXES)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--session")
    ap.add_argument("--out", default=str(OUT_DEFAULT))
    ap.add_argument("--with-results", action="store_true")
    ap.add_argument("--max-result", type=int, default=400)
    args = ap.parse_args()

    if args.list:
        for d in session_dirs():
            for sess in sorted(d.iterdir()):
                f = sess / "session.v4.jsonl.zstd"
                if f.exists():
                    mt = datetime.fromtimestamp(f.stat().st_mtime, TZ).strftime("%Y-%m-%d %H:%M")
                    print(f"{mt}  {sess.name}  ({f.stat().st_size/1024:.0f} КБ)  {d.name}")
        return

    if args.session and Path(args.session).exists():
        paths = [Path(args.session)]
    else:
        paths = find_sessions(args.session)
    for p in paths:
        print(f"сессия: {p}", file=sys.stderr)

    calls = {}
    titles = []
    src = ", ".join(f"`{p}`" for p in paths)
    lines = ["# Стенограмма переписки (Agent2)", "",
             f"Источник: {src}", f"Выгружено: {datetime.now(TZ).strftime('%Y-%m-%d %H:%M')} (UTC+10)",
             "", "> ⚠️ Содержит секреты из переписки (пароли, ключи, session string). Файл локальный, не публиковать.",
             "", "---", ""]

    turn = None
    prev_path = None
    for path in paths:
        if prev_path is not None:
            lines += ["", f"---", "", f"# Сессия {path.parent.name}", ""]
        prev_path = path
        for rec in read_jsonl(path):
            t = rec.get("type")
            time_s = fmt_time(rec.get("time", ""))
            data = rec.get("data") or {}

            if t == "turn/start":
                turn = data.get("turn") or data.get("index")
                lines += ["", f"## Ход {turn}", ""]
            elif t == "user/message":
                msg = data.get("message") or data
                txt = text_of(msg.get("content"))
                if txt and not is_scaffold(txt):
                    lines += [f"### {time_s} · ПОЛЬЗОВАТЕЛЬ", "", txt, ""]
            elif t == "assistant/message":
                msg = data.get("message") or {}
                txt = text_of(msg.get("content"))
                if txt:
                    lines += [f"### {time_s} · АГЕНТ", "", txt, ""]
            elif t == "tool/call":
                name = data.get("name")
                cid = data.get("callId")
                try:
                    a = json.loads(data.get("arguments") or "{}")
                except Exception:
                    a = {"raw": data.get("arguments")}
                calls[cid] = name
                desc = a.get("description") or a.get("command") or a.get("file_path") or ""
                desc = str(desc).replace("\n", " ")[:200]
                lines.append(f"- 🔧 `{name}` — {desc}")
            elif t == "tool/result" and args.with_results:
                msg = data.get("message") or {}
                res = text_of(msg.get("content"))[: args.max_result].replace("\n", " ")
                lines.append(f"  - ↳ {res}")
            elif t == "approval/decided":
                lines.append(f"- ✅ подтверждение: {json.dumps(data, ensure_ascii=False)[:200]}")
            elif t == "session/title":
                title = data.get("title") or data
                if title and not titles:
                    titles.append(str(title))

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    header = "\n".join(lines[:8])
    if titles:
        header += f"\nЗаголовок сессии: {titles[0]}"
    text = header + "\n" + "\n".join(lines[8:]) + "\n"
    out.write_text(text, encoding="utf-8")
    n_user = text.count("· ПОЛЬЗОВАТЕЛЬ")
    n_agent = text.count("· АГЕНТ")
    print(f"записано: {out} ({out.stat().st_size/1024:.1f} КБ; "
          f"сообщений пользователя: {n_user}, ответов агента: {n_agent})")


if __name__ == "__main__":
    main()
