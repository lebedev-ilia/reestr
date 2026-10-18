#!/usr/bin/env python3
"""Разведка ветки перед ответом: Message-ID писем No Logo. Ничего не меняет."""
from __future__ import annotations

import email
import imaplib
from email.header import decode_header, make_header
from pathlib import Path

ROOT = Path("/home/ilya/Рабочий стол/Agent/jarvis")
IMAP_HOST, IMAP_PORT = "imap.mail.ru", 993


def env() -> dict:
    out = {}
    for line in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def dec(v: str) -> str:
    try:
        return str(make_header(decode_header(v or "")))
    except Exception:
        return v or ""


def main() -> None:
    e = env()
    user, password = e["SMTP_USER"], e["SMTP_PASS"]
    srv = imaplib.IMAP4_SSL(IMAP_HOST, IMAP_PORT, timeout=60)
    srv.login(user, password)
    for folder in ("INBOX", '"&BB8EEATB' + '"'):
        pass
    srv.select("INBOX")
    typ, data = srv.search(None, 'FROM', 'nologostudio.ru')
    ids = data[0].split()
    print(f"писем от nologostudio.ru во входящих: {len(ids)}")
    for num in ids:
        typ, raw = srv.fetch(num, "(RFC822.HEADER)")
        msg = email.message_from_bytes(raw[0][1])
        print("---")
        print("date:", msg.get("Date"))
        print("from:", dec(msg.get("From", "")))
        print("subject:", dec(msg.get("Subject", "")))
        print("message-id:", msg.get("Message-ID"))
        print("in-reply-to:", msg.get("In-Reply-To"))
        print("references:", (msg.get("References") or "").replace("\r\n", " ").replace("\n", " "))
    srv.logout()


if __name__ == "__main__":
    main()
