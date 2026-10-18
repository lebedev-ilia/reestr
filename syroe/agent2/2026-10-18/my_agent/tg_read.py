#!/usr/bin/env python3
"""
tg_read.py — чтение переписок Telegram из рабочей среды агента (Telethon + session string).

Использование:
  tg_read.py list [--limit 100] [--unread] [--filter TEXT]
  tg_read.py read  <chat> [--limit 200] [--save] [--json]
  tg_read.py search "<текст>" [--limit 50]
  tg_read.py whoami

  <chat> — имя (@username, "Сергей Редькин", часть названия) или id (напр. 126461673).
  Сохранение: файлы кладутся в telegram/ рядом с рабочей папкой.

Время выводится в UTC+10 (Владивосток). "Я" = исходящее, "ОН" = входящее.
"""
import argparse
import asyncio
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO = Path("/Users/user/Desktop/Agent2/telegram-mcp")
ENV_FILE = REPO / ".env"
OUT_DIR = Path("/Users/user/Desktop/Agent2/telegram")
TZ = timezone(timedelta(hours=10))  # UTC+10
CONNECT_TIMEOUT = 60


def load_env():
    """Читает .env telegram-mcp; переменные окружения имеют приоритет."""
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
    need = ("TELEGRAM_API_ID", "TELEGRAM_API_HASH", "TELEGRAM_SESSION_STRING")
    missing = [k for k in need if not os.environ.get(k)]
    if missing:
        sys.exit(f"Нет настроек: {', '.join(missing)} (ожидались в {ENV_FILE})")


def get_client():
    from telethon import TelegramClient
    from telethon.sessions import StringSession

    return TelegramClient(
        StringSession(os.environ["TELEGRAM_SESSION_STRING"]),
        int(os.environ["TELEGRAM_API_ID"]),
        os.environ["TELEGRAM_API_HASH"],
        device_model="Agent2-reader",
        system_version="macOS",
    )


def who(m):
    return "Я  " if m.out else "ОН "


def line(m, tz=TZ):
    ts = m.date.astimezone(tz).strftime("%Y-%m-%d %H:%M")
    body = (m.message or "").replace("\r", "")
    tags = []
    if m.media:
        tags.append("МЕДИА:" + type(m.media).__name__)
    if getattr(m, "reply_to_msg_id", None):
        tags.append(f"ответ#{m.reply_to_msg_id}")
    if getattr(m, "fwd_from", None):
        tags.append("переслано")
    tag = ("  [" + ", ".join(tags) + "]") if tags else ""
    if not body and tag:
        body = "(без текста)"
    return f"{ts}  {who(m)}{body}{tag}"


def resolve_key(d):
    ent = d.entity
    return (getattr(ent, "first_name", None), getattr(ent, "last_name", None),
            getattr(ent, "title", None), getattr(ent, "username", None))


def sort_key(d):
    return d.date.timestamp() if d.date else 0


async def cmd_list(args):
    c = get_client()
    await asyncio.wait_for(c.connect(), timeout=CONNECT_TIMEOUT)
    try:
        dialogs = await asyncio.wait_for(c.get_dialogs(limit=args.limit), timeout=120)
        rows = []
        for d in dialogs:
            if args.unread and not d.unread_count:
                continue
            if args.filter:
                hay = " ".join(str(x) for x in resolve_key(d) if x).lower()
                if args.filter.lower() not in hay:
                    continue
            ent = d.entity
            rows.append({
                "date": d.date.astimezone(TZ).strftime("%Y-%m-%d %H:%M") if d.date else "",
                "name": d.name or "",
                "id": d.id,
                "type": type(ent).__name__,
                "username": getattr(ent, "username", None),
                "unread": d.unread_count,
                "last": (d.message.message or "").replace("\n", " ⏎ ")[:110] if d.message else "",
            })
        rows.sort(key=lambda r: r["date"], reverse=True)
        if args.json:
            print(json.dumps(rows, ensure_ascii=False, indent=1))
        else:
            for r in rows:
                u = f" ({r['unread']} непроч.)" if r["unread"] else ""
                un = f" @{r['username']}" if r["username"] else ""
                print(f"{r['date']} | {r['name']}{un} | id={r['id']}{u}\n    {r['last']}")
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        (OUT_DIR / "00_dialogs.txt").write_text(
            "\n".join(f"{r['date']} | {r['type'][:4]} | {r['name']} | id={r['id']} | "
                      f"unread={r['unread']} | @{r['username'] or ''} | {r['last']}" for r in rows) + "\n",
            encoding="utf-8")
        print(f"\n[{len(rows)} чатов; список сохранён в {OUT_DIR/'00_dialogs.txt'}]", file=sys.stderr)
    finally:
        await c.disconnect()


async def find_entity(c, target):
    # Числовой id: сначала ищем среди диалогов (они гарантированно попадают в кэш сущностей),
    # иначе Telethon падает с «Could not find the input entity for PeerUser(...)».
    if target.lstrip("-").isdigit():
        want = int(target)
        dialogs = await asyncio.wait_for(c.get_dialogs(limit=300), timeout=120)
        for d in dialogs:
            if d.id == want:
                return d.entity
        try:
            return await c.get_entity(want)
        except ValueError:
            pass
        # крайний случай: чат есть, но не в списке диалогов — прогреваем кэш через историю
        try:
            await c.get_messages(want, limit=1)
            return await c.get_entity(want)
        except Exception:
            sys.exit(f"Не нашёл чат с id={want} среди диалогов (проверь id).")
    try:
        if target.startswith("@"):
            return await c.get_entity(target)
        ent = await c.get_entity(target)
        if ent:
            return ent
    except Exception:
        pass
    dialogs = await c.get_dialogs(limit=300)
    tl = target.lower()
    exact, part = None, []
    for d in dialogs:
        names = [str(x) for x in resolve_key(d)[:3] if x]
        full = " ".join(names)
        if full.lower() == tl:
            exact = d.entity
            break
        if tl in full.lower() or tl in str(getattr(d.entity, "username", "") or "").lower():
            part.append(d.entity)
    if exact:
        return exact
    if len(part) == 1:
        return part[0]
    if part:
        names = ", ".join((getattr(e, "first_name", None) or getattr(e, "title", "?")) for e in part[:10])
        sys.exit(f"Неоднозначно «{target}»: {names}. Уточни или укажи id.")
    sys.exit(f"Чат «{target}» не найден.")


async def cmd_read(args):
    c = get_client()
    await asyncio.wait_for(c.connect(), timeout=CONNECT_TIMEOUT)
    try:
        ent = await find_entity(c, args.chat)
        msgs = [m async for m in c.iter_messages(ent, limit=args.limit)]
        msgs.reverse()
        name = (getattr(ent, "first_name", None) or getattr(ent, "title", None) or str(args.chat))
        if getattr(ent, "last_name", None):
            name += " " + ent.last_name
        header = f"# Чат: {name} (id={getattr(ent,'id',args.chat)}), сообщений: {len(msgs)}\n# Время: UTC+10\n"
        text = "\n".join(line(m, TZ) if not args.json else json.dumps({
            "id": m.id, "date": m.date.astimezone(TZ).isoformat(), "out": m.out,
            "text": m.message or "", "media": type(m.media).__name__ if m.media else None,
            "reply_to": getattr(m, "reply_to_msg_id", None),
        }, ensure_ascii=False) for m in msgs)
        if args.save:
            OUT_DIR.mkdir(parents=True, exist_ok=True)
            suffix = "json" if args.json else "txt"
            p = OUT_DIR / f"chat_{getattr(ent,'id',args.chat)}.{suffix}"
            p.write_text((header if not args.json else "") + text + "\n", encoding="utf-8")
            print(f"[сохранено: {p}]", file=sys.stderr)
        print(text)
    finally:
        await c.disconnect()


async def cmd_search(args):
    from telethon.tl.functions.messages import SearchRequest
    from telethon.tl.types import InputMessagesFilterEmpty

    c = get_client()
    await asyncio.wait_for(c.connect(), timeout=CONNECT_TIMEOUT)
    try:
        me = await c.get_me()
        res = await c(SearchRequest(
            peer=me, q=args.query, filter=InputMessagesFilterEmpty(),
            min_date=None, max_date=None, offset_id=0, add_offset=0,
            limit=args.limit, max_id=0, min_id=0, hash=0))
        names = {}
        for m in res.messages:
            cid = getattr(m, "chat_id", None) or getattr(m, "user_id", None)
            if cid and cid not in names:
                try:
                    e = await c.get_entity(cid)
                    names[cid] = (getattr(e, "first_name", None) or getattr(e, "title", None) or str(cid))
                except Exception:
                    names[cid] = str(cid)
            cid = getattr(m, "chat_id", None) or getattr(m, "user_id", None)
            print(f"{m.date.astimezone(TZ).strftime('%Y-%m-%d %H:%M')} | {names.get(cid, cid)} | {who(m)}| {(m.message or '')[:200]}")
        print(f"\n[найдено: {len(res.messages)}]", file=sys.stderr)
    finally:
        await c.disconnect()


async def cmd_whoami(args):
    c = get_client()
    await asyncio.wait_for(c.connect(), timeout=CONNECT_TIMEOUT)
    try:
        me = await c.get_me()
        print(f"Аккаунт: {me.first_name} {me.last_name or ''} | @{me.username} | {me.phone} | id={me.id}")
    finally:
        await c.disconnect()


def main():
    ap = argparse.ArgumentParser(description="Чтение Telegram-переписок")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("list", help="список чатов")
    p.add_argument("--limit", type=int, default=100)
    p.add_argument("--unread", action="store_true", help="только непрочитанные")
    p.add_argument("--filter", help="фильтр по имени/юзернейму")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_list)

    p = sub.add_parser("read", help="прочитать чат")
    p.add_argument("chat")
    p.add_argument("--limit", type=int, default=200)
    p.add_argument("--save", action="store_true", help="сохранить в telegram/")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_read)

    p = sub.add_parser("search", help="поиск по всем чатам")
    p.add_argument("query")
    p.add_argument("--limit", type=int, default=50)
    p.set_defaults(func=cmd_search)

    p = sub.add_parser("whoami", help="проверить сессию")
    p.set_defaults(func=cmd_whoami)

    args = ap.parse_args()
    load_env()
    asyncio.run(args.func(args))


if __name__ == "__main__":
    main()
