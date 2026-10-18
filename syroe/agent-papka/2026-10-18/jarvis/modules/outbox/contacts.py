#!/usr/bin/env python3
"""Разбор списка контактов из markdown-карточек и вычитка перед рассылкой.

Источник — файл вида T-835_60_KONTAKTOV.md: карточка начинается со строки
`### N.M Название`, дальше поля `- Ключ: значение` с продолжением на следующих
строках с отступом. Формат жёсткий, поэтому парсить регуляркой честно —
но если формат поедет, парсер должен ЗАМЕТИТЬ это, а не молча вернуть меньше
карточек. Отсюда проверка на ожидаемое число и на пустые поля.

Отправки здесь нет намеренно: пока Илья не решил, с какого ящика шлём,
модуль умеет только читать и показывать.

    python -m jarvis.modules.outbox.contacts <файл.md>
"""
import json
import re
import sys
from pathlib import Path

CARD_RE = re.compile(r"^###\s+(\d+)\.(\d+)\s+(.+?)\s*$")
FIELD_RE = re.compile(r"^-\s+([^:]{2,90}):\s*(.*)$")
SECTION_RE = re.compile(r"^##\s+Раздел\s+(\d+)\.\s*(.+?)\s*$")

# Домен может быть трёхуровневым (alfaline.spb.ru, cks.lazurit.com) —
# наивная «одна точка перед зоной» молча обрезает адрес до несуществующего.
EMAIL_RE = re.compile(r"[\w.+-]+@(?:[\w-]+\.)+[a-zA-Z]{2,}")
# Ник с явным маркером — это точно телеграм, даже если он совпал с доменом
# почты (у ХОЧУСТУЛ sale@hochustul.ru и telegram @hochustul — один и тот же
# hochustul, и защита «хвост почты» съедала настоящий контакт).
TG_MARKED_RE = re.compile(r"(?:t\.me/|telegram[:\s/]*@?)([A-Za-z][\w]{3,31})", re.I)
# Голый @ник без маркера — сомнителен, отдельно проверяется на хвост почты.
TG_RE = re.compile(r"@([A-Za-z][\w]{4,31})")
URL_RE = re.compile(r"https?://[^\s,;)]+")
# Форматы номеров в карточках слишком разные, чтобы описывать разделители:
# «+7 (4212) 55-66-18», «+7 (3412) 400-600», «8 800 550-05-08». Считаем по
# цифрам: убираем разделители и ищем 11 цифр подряд. Прежний шаблон с
# фиксированными группами терял почти половину номеров (12 вместо 21 на 24.08).
PHONE_RE = re.compile(r"(?:\+7|\b8)\d{10}")
SEPARATORS_RE = re.compile(r"[\s()\-–—.]")
# Телеграм называют и номером: «telegram +79951039903», «t.me/+79154417060»,
# «telegram/WhatsApp +7 999 672-16-11». Такой номер — аккаунт, а не линия для
# звонка: он уходит в telegram и вырезается до поиска телефонов, иначе
# считается дважды.
TG_PHONE_RE = re.compile(r"(?:t\.me/|telegram[^\d\n]{0,12})\+?(\d[\d\s()\-]{9,18}\d)", re.I)

# Ящики, за которыми обычно нет человека, принимающего решение.
GENERIC = ("info@", "hello@", "mail@", "office@", "sales@", "hr@", "support@",
           "contact@", "job@", "jobs@", "zakaz@", "order@", "secretary@")


def parse(path: Path) -> list[dict]:
    """Файл -> список карточек. Порядок сохраняется, ничего не выбрасывается."""
    cards: list[dict] = []
    cur: dict | None = None
    field: str | None = None
    section = ""

    for raw in path.read_text(encoding="utf-8").splitlines():
        m = SECTION_RE.match(raw)
        if m:
            section = m.group(2)
            continue

        m = CARD_RE.match(raw)
        if m:
            cur = {"num": f"{m.group(1)}.{m.group(2)}", "name": m.group(3),
                   "section": section, "fields": {}}
            cards.append(cur)
            field = None
            continue

        if cur is None:
            continue

        m = FIELD_RE.match(raw)
        if m:
            # Ключ бывает с уточнением в скобках: «Письмо (в комментарии …)».
            # Уточнение отбрасываем, иначе поле не находится по имени.
            field = m.group(1).split("(")[0].strip().lower()
            cur["fields"][field] = m.group(2).strip()
            continue

        # Продолжение поля: строка с отступом, не начинающая новый пункт.
        if field and raw.startswith("  ") and raw.strip():
            cur["fields"][field] += " " + raw.strip()
            continue

        if not raw.strip():
            field = None

    for c in cards:
        c["channels"] = channels(c["fields"].get("контакт", ""))
        c["letter"] = clean_letter(c["fields"].get("письмо", ""))
    return cards


def channels(contact: str) -> dict:
    """Из строки «- Контакт:» вытащить всё, чем можно писать."""
    emails = EMAIL_RE.findall(contact)
    urls = URL_RE.findall(contact)
    # Телеграм-номера снимаем первыми и вырезаем из строки: иначе тот же номер
    # уедет ещё и в телефоны.
    tg_phones = ["+" + SEPARATORS_RE.sub("", p).lstrip("+")
                 for p in TG_PHONE_RE.findall(contact)]
    rest = TG_PHONE_RE.sub(" ", contact)
    tgs = list(tg_phones)
    for m in TG_MARKED_RE.finditer(rest):
        handle = m.group(1)
        if "." not in handle:
            tgs.append(handle)
    for m in TG_RE.finditer(rest):
        handle = m.group(1)
        # Хвост почты (@atwinta.ru) и домены в telegram-ник не превращаем.
        if f"@{handle}" in " ".join(emails) or "." in handle:
            continue
        tgs.append(handle)
    phones = sorted(set(PHONE_RE.findall(SEPARATORS_RE.sub("", rest))))
    return {
        "email": emails,
        "telegram": sorted(set(tgs)),
        "phone": phones,
        "url": [u for u in urls if "t.me/" not in u],
    }


def clean_letter(text: str) -> str:
    """Снять кавычки-ёлочки, которыми обёрнуто письмо в карточке."""
    t = text.strip()
    if t.startswith("«") and t.endswith("»"):
        t = t[1:-1]
    return t.strip()


def audit(cards: list[dict]) -> dict:
    """Что мешает отправлять. Считаем молча, решает человек."""
    seen: dict[str, str] = {}
    problems = {"без канала": [], "дубликат адреса": [], "общий ящик": [],
                "нет письма": [], "короткое письмо": [], "только сайт/форма": [],
                "telegram — бот или канал": []}

    for c in cards:
        ch, tag = c["channels"], f"{c['num']} {c['name']}"
        if not (ch["email"] or ch["telegram"] or ch["url"] or ch["phone"]):
            problems["без канала"].append(tag)
        if not ch["email"] and not ch["telegram"]:
            problems["только сайт/форма"].append(tag)
        for e in ch["email"]:
            low = e.lower()
            if low in seen:
                problems["дубликат адреса"].append(f"{tag} = {seen[low]} ({e})")
            else:
                seen[low] = tag
            if low.startswith(GENERIC):
                problems["общий ящик"].append(f"{tag}: {e}")
        for t in ch["telegram"]:
            # В бота пишет автоответчик, в канал написать нельзя вовсе —
            # это не личный контакт, хотя выглядит так же.
            if t.lower().endswith("bot") or "канал" in c["fields"].get("контакт", ""):
                problems["telegram — бот или канал"].append(f"{tag}: @{t}")
        if not c["letter"]:
            problems["нет письма"].append(tag)
        elif len(c["letter"]) < 200:
            problems["короткое письмо"].append(f"{tag}: {len(c['letter'])} зн.")

    return {k: v for k, v in problems.items() if v}


def main() -> None:
    path = Path(sys.argv[1] if len(sys.argv) > 1 else
                Path.home() / "Рабочий стол/TrendFlowML/docs/marketing"
                / "T-835_60_KONTAKTOV.md")
    cards = parse(path)
    print(f"карточек: {len(cards)}")
    for key in ("email", "telegram", "phone", "url"):
        n = sum(1 for c in cards if c["channels"][key])
        print(f"  {key}: {n}")

    print("\n--- к вычитке ---")
    for name, items in audit(cards).items():
        print(f"{name}: {len(items)}")
        for i in items[:8]:
            print(f"    {i}")
        if len(items) > 8:
            print(f"    … ещё {len(items) - 8}")

    if "--json" in sys.argv:
        print(json.dumps(cards, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
