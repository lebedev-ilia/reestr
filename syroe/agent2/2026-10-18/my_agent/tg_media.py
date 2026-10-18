#!/usr/bin/env python3
"""
tg_media.py — скачать медиа из переписки Telegram (голосовые/видео/документы) по id чата.

Зачем: заказчица прислала эталонные образцы («как пример», «живой он»), их нужно
измерить объективно (F0, спектр, уровень), а не спорить на слух.

Использование:
  tg_media.py <chat_id|@username|имя> [--limit 200] [--out DIR] [--only audio|all]
"""
import argparse
import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from tg_read import get_client, find_entity, TZ, load_env  # noqa: E402

DEFAULT_OUT = Path("/Users/user/Desktop/Agent2/telegram/media")


async def main():
    load_env()
    ap = argparse.ArgumentParser()
    ap.add_argument("chat")
    ap.add_argument("--limit", type=int, default=200)
    ap.add_argument("--out", default=str(DEFAULT_OUT))
    ap.add_argument("--only", default="all", choices=["all", "audio", "photo"])
    args = ap.parse_args()

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    c = get_client()
    await asyncio.wait_for(c.connect(), timeout=60)
    got = []
    try:
        ent = await find_entity(c, args.chat)
        async for m in c.iter_messages(ent, limit=args.limit):
            if not m.media:
                continue
            kind = type(m.media).__name__
            if args.only == "audio" and "Document" not in kind:
                continue
            if args.only == "photo" and "Photo" not in kind:
                continue
            ts = m.date.astimezone(TZ).strftime("%Y%m%d_%H%M%S")
            who = "ya" if m.out else "on"
            try:
                path = await m.download_media(file=str(out / f"{ts}_{who}_{m.id}"))
            except Exception as e:  # noqa: BLE001
                print(f"[!] {m.id} {kind}: {e}")
                continue
            if path:
                size = os.path.getsize(path)
                got.append((path, kind, size))
                print(f"{ts} {who} #{m.id} {kind} {size/1024:.0f} КБ -> {path}")
    finally:
        await c.disconnect()

    print(f"\n[скачано файлов: {len(got)}; папка {out}]")


if __name__ == "__main__":
    asyncio.run(main())
