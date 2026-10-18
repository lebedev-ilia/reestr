"""История переписки по каждому контакту: досье в derived/threads/.

Зачем: контактов десятки, переписка растёт, и вопрос «что мы этому человеку
писали и что он ответил» не должен требовать чтения двух журналов подряд.
На каждый адрес — один файл: шапка (компания, карточка, каркас, источник),
дальше хронология «→ отправлено» и «← ответ» вперемешку, по времени.

Откуда данные:
  * memory/outbox/sent.jsonl   — что мы отправили (пишет modules/outbox/send.py);
  * memory/outbox/replies.jsonl — что нам ответили (пишет modules/inbox/read.py);
  * modules/outbox/subjects.json, wave2/*.json — тема и карточка контакта.
    ТЕКСТ письма отсюда не берётся никогда (bl-091): шаблоны переписываются,
    и досье соврало бы задним числом. Тело письма живёт в журнале отправки;
    старым записям оно восстановлено из гита (modules/outbox/backfill_bodies.py),
    а где не нашлось — досье честно пишет «текст утерян».

Зона derived/ сносится целиком в любой момент (правило
v-derived-tolko-vosstanovimoe), поэтому здесь нет ничего уникального: всё
досье целиком выводится из двух журналов, запуск с нуля даёт тот же результат.
Каталог перед полной пересборкой вычищается, чтобы удалённый контакт не остался
висеть файлом-призраком.

Запуск (из корня агента, как modules.outbox.wave2.send_wave):
    python3 -m modules.outbox.history                              # пересобрать всё
    python3 -m modules.outbox.history --contact info@nologostudio.ru   # одно досье

Из каталога над jarvis работает и полное имя: python3 -m jarvis.modules.outbox.history
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path

from . import send as S

ROOT = Path(__file__).resolve().parents[2]
SENT = ROOT / "memory" / "outbox" / "sent.jsonl"
REPLIES = ROOT / "memory" / "outbox" / "replies.jsonl"
OUT = ROOT / "derived" / "threads"
WAVE2 = Path(__file__).resolve().parent / "wave2"

KIND_RU = {"reply": "ответ", "auto": "автоответ", "bounce": "отлуп"}


class Aliases:
    """Один человек — одно досье, даже если адресов у него два.

    Зачем (bl-140): 26.08 Николай из No Logo ответил не с общего ящика студии
    `info@nologostudio.ru`, с которого шла переписка, а со своего личного
    `n.a.kochubeev@…`; наши ответы ушли туда же. Досье группировалось по голому
    адресу, и один разговор развалился на два файла. В `info-…` последним
    событием осталось 25.08 — по нему выходило, что последнее слово за клиентом
    и четыре вопроса без ответа, хотя ответ давно ушёл. Правило
    uslovie-uzhe-nazvannoe-klientu велит открывать досье перед письмом клиенту,
    так что черновик по такой картине был бы мимо.

    Склейка идёт ТОЛЬКО по прямой улике из журнала, без догадок про домены:
    запись в replies.jsonl, где `contact` (карточка, с которой inbox/read.py
    сопоставил письмо) и `from` (настоящий отправитель) — разные адреса, сама
    доказывает, что это один и тот же собеседник.

    Предохранитель от склейки чужих: если у обоих адресов есть СВОЯ карточка и
    номера карточек разные — это два разных контакта, и объединять их нельзя
    (скорее всего inbox ошибся привязкой). Такие пары складываются в `refused`
    и печатаются при сборке, чтобы ошибка не осталась молчаливой.
    """

    def __init__(self) -> None:
        self.parent: dict[str, str] = {}
        self.num: dict[str, str] = {}      # адрес -> номер его собственной карточки
        self.refused: list[tuple[str, str]] = []

    def find(self, addr: str) -> str:
        self.parent.setdefault(addr, addr)
        while self.parent[addr] != addr:
            self.parent[addr] = self.parent[self.parent[addr]]
            addr = self.parent[addr]
        return addr

    def own_card(self, addr: str, num: str) -> None:
        """Запомнить, что у адреса есть своя карточка (он был адресатом письма)."""
        if addr and num:
            self.num.setdefault(addr, str(num))

    def cards_of(self, addr: str) -> set[str]:
        """Номера карточек всей группы адреса, а не одного его самого."""
        root = self.find(addr)
        return {n for a, n in self.num.items() if self.find(a) == root and n}

    def link(self, a: str, b: str) -> None:
        if not a or not b or a == b:
            return
        ra, rb = self.find(a), self.find(b)
        if ra == rb:
            return
        # Предохранитель считается по ГРУППАМ, а не по концам ребра. Иначе
        # общий адрес-посредник склеивает через себя двух чужих: 30.08 первый
        # прогон свёл client@ellyme.ru (карточка 4.82) и hr@sibdev.pro (1.55)
        # через mailer-daemon@corp.mail.ru, у которого своей карточки нет,
        # и проверка «у обоих концов свои карточки» промолчала.
        both = self.cards_of(a) | self.cards_of(b)
        if len(both) > 1:
            self.refused.append((a, b))
            return
        self.parent[rb] = ra

    def canonical(self) -> dict[str, str]:
        """адрес -> адрес досье. Выбор устойчивый, от порядка чтения не зависит:
        сначала адреса со своей карточкой, среди равных — лексикографически
        меньший. Пересборка с нуля даёт те же файлы."""
        groups: dict[str, list[str]] = {}
        for addr in self.parent:
            groups.setdefault(self.find(addr), []).append(addr)
        out: dict[str, str] = {}
        for members in groups.values():
            head = sorted(members, key=lambda a: (self.num.get(a) is None, a))[0]
            for a in members:
                out[a] = head
        return out


# --- чтение источников -------------------------------------------------------

def read_jsonl(path: Path) -> list[dict]:
    """Битая строка не должна ронять сборку: журнал важнее одной записи."""
    out: list[dict] = []
    if not path.exists():
        return out
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(rec, dict):
            out.append(rec)
    return out


def wave2_cards() -> dict[str, dict]:
    """Карточки второй волны: номер -> карточка. Нужны ради шапки досье
    (source_url, город, чем занимается). Поле body карточки в досье НЕ идёт
    (bl-091): текст письма живёт только в журнале отправки."""
    cards: dict[str, dict] = {}
    if not WAVE2.is_dir():
        return cards
    for path in sorted(WAVE2.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        if not isinstance(data, list):
            continue
        for card in data:
            if isinstance(card, dict) and card.get("id"):
                cards[str(card["id"])] = card
    return cards


def when(ts: str) -> datetime:
    """Общий ключ сортировки для двух журналов — в местном времени машины.

    В sent.jsonl время наивное и местное (`datetime.now()`), в replies.jsonl —
    из заголовка Date чужого сервера, со смещением («…+03:00»). Смещение
    отбрасывать нельзя: машина стоит на +10, и отлуп, пришедший через секунду
    после отправки, лёг бы в досье на семь часов раньше письма — ровно так
    и получилось на первом прогоне 25.08. astimezone() приводит и то и другое
    к местной шкале (наивное считается местным), дальше сравнение честное.
    Неразбираемая метка уходит в конец.
    """
    try:
        return datetime.fromisoformat(ts).astimezone().replace(tzinfo=None)
    except (TypeError, ValueError):
        return datetime.max


def human(ts: str) -> str:
    dt = when(ts)
    return "без даты" if dt == datetime.max else dt.strftime("%Y-%m-%d %H:%M")


# --- сборка досье ------------------------------------------------------------

def sent_addr(rec: dict) -> str:
    """Адресат записи об отправке. to пустой у неудачной отправки тоже
    (см. send.py): там настоящий адресат остался в real_to, и попытка в истории
    нужна — иначе «мы ему не писали» и «письмо не ушло» выглядят одинаково."""
    return (rec.get("to") or rec.get("real_to") or "").strip().lower()


def reply_addr(rec: dict) -> str:
    """Адрес, по которому группируется ответ: карточка, с которой его сопоставил
    inbox/read.py, иначе — настоящий отправитель."""
    return (rec.get("contact") or rec.get("from") or "").strip().lower()


def usable(sent: list[dict], replies: list[dict]) -> int:
    """Сколько записей журналов обязаны стать событиями досье.

    Мерило приёмки для bl-140: событий в досье ровно столько, сколько записей
    прошло отбор. Отбрасываются только пилоты (проверка на ящик Ильи, не
    переписка) и записи вовсе без адреса. Всё остальное обязано быть видно —
    склейка адресов события не съедает, а лишь переносит в соседнее досье.
    """
    return (sum(1 for r in sent if not r.get("pilot") and sent_addr(r))
            + sum(1 for r in replies if reply_addr(r)))


def aliases(sent: list[dict], replies: list[dict]) -> Aliases:
    """Собрать карту адресов одного собеседника. См. класс Aliases."""
    al = Aliases()
    for rec in sent:
        if rec.get("pilot"):
            continue
        al.own_card(sent_addr(rec), str(rec.get("num") or ""))
    for rec in replies:
        al.own_card((rec.get("contact") or "").strip().lower(),
                    str(rec.get("num") or ""))
    for rec in replies:
        # Уликой служит ТОЛЬКО живой ответ человека. Отлуп и автоответ приходят
        # от почтового робота (mailer-daemon, postmaster), и его адрес общий для
        # всех адресатов — склейка по нему связала бы через одного робота весь
        # список контактов в одно досье.
        if rec.get("kind", "reply") != "reply":
            continue
        al.link((rec.get("contact") or "").strip().lower(),
                (rec.get("from") or "").strip().lower())
    return al


def collect() -> dict[str, dict]:
    """адрес (lower) -> досье со списком событий, отсортированных по времени.

    Пилотные письма (pilot=true, to пустой) — это проверка на ящик Ильи, а не
    переписка с контактом: в досье они не попадают вовсе.

    Адреса одного собеседника сводятся в одно досье (bl-140, см. Aliases):
    ответ с личного ящика и наше письмо туда же ложатся в ту же хронологию,
    что и переписка с общим ящиком компании.
    """
    # Шаблоны нужны только для темы и карточки контакта. Текст письма из них
    # НЕ берётся (bl-091) — тело живёт в журнале отправки и больше нигде.
    subjects, cards = S.subjects(), wave2_cards()
    sent_recs, reply_recs = read_jsonl(SENT), read_jsonl(REPLIES)
    al = aliases(sent_recs, reply_recs)
    same = al.canonical()
    threads: dict[str, dict] = {}

    def thread(addr: str) -> dict:
        head = same.get(addr, addr)
        t = threads.setdefault(head, {"addr": head, "name": "", "num": "",
                                      "template": "", "events": [], "also": set()})
        if addr != head:
            t["also"].add(addr)
        return t

    for rec in sent_recs:
        if rec.get("pilot"):
            continue
        addr = sent_addr(rec)
        if not addr:
            continue
        t = thread(addr)
        t["name"] = rec.get("name") or t["name"]
        t["num"] = rec.get("num") or t["num"]
        t["template"] = rec.get("template") or t["template"]

        # Текст письма берём ТОЛЬКО из журнала. Сегодняшний шаблон сюда не
        # подставляется никогда (bl-091): тексты переписываются, и досье
        # соврало бы задним числом — а по досье строится второе касание.
        # Старым записям тело восстановлено из гита на момент отправки
        # (modules/outbox/backfill_bodies.py) и помечено body_exact: false.
        body = rec.get("body") or ""
        t["events"].append({
            "dir": "out", "ts": rec.get("ts", ""),
            "peer": addr,
            "subject": rec.get("subject") or subjects.get(str(rec.get("num") or ""), ""),
            "body": body.strip(),
            "restored": bool(body) and rec.get("body_exact") is False,
            "source": rec.get("body_source", ""),
            "lost": not body.strip(),
            "ok": rec.get("ok", True), "error": rec.get("error", ""),
            "kind": "", "from": "",
        })

    for rec in reply_recs:
        # contact — адрес карточки, с которой письмо сопоставил inbox/read.py.
        # from может отличаться: человек отвечает с личного ящика. Группируем
        # по карточке, а настоящего отправителя показываем в строке события.
        addr = reply_addr(rec)
        if not addr:
            continue
        t = thread(addr)
        # Личный ящик отвечающего тоже принадлежит этому досье — но только если
        # склейка состоялась: отказ предохранителя нового досье не заводит.
        frm = (rec.get("from") or "").strip().lower()
        if frm and same.get(frm, frm) == t["addr"] and frm != t["addr"]:
            t["also"].add(frm)
        t["name"] = t["name"] or rec.get("name") or ""
        t["num"] = t["num"] or rec.get("num") or ""
        t["template"] = t["template"] or rec.get("template") or ""
        t["events"].append({
            "dir": "in", "ts": rec.get("ts", ""),
            "subject": rec.get("subject", ""), "body": (rec.get("text") or "").strip(),
            "restored": False, "source": "", "lost": False, "ok": True, "error": "",
            "kind": rec.get("kind", "reply"), "from": rec.get("from", ""),
        })

    for t in threads.values():
        t["events"].sort(key=lambda e: (when(e["ts"]), e["dir"]))
        card = cards.get(str(t["num"]))
        t["card"] = card or {}
        kinds = {e["kind"] for e in t["events"] if e["dir"] == "in"}
        t["n_out"] = sum(1 for e in t["events"] if e["dir"] == "out")
        t["n_ok"] = sum(1 for e in t["events"] if e["dir"] == "out" and e["ok"])
        t["n_in"] = sum(1 for e in t["events"] if e["dir"] == "in")
        t["last"] = t["events"][-1]["ts"] if t["events"] else ""
        if "reply" in kinds:
            t["status"] = "ответил"
        elif "bounce" in kinds:
            t["status"] = "отлуп"
        elif "auto" in kinds:
            t["status"] = "автоответ"
        elif not t["n_ok"]:
            t["status"] = "не ушло"
        else:
            t["status"] = "молчит"
    return threads


def slugs(addrs: list[str]) -> dict[str, str]:
    """адрес -> имя файла. Детерминированно: адреса сортируются, совпавшие
    имена разводятся хвостом по порядку. Пересборка с нуля даст те же файлы."""
    out, taken = {}, {}
    for addr in sorted(addrs):
        base = re.sub(r"[^a-z0-9]+", "-", addr.lower()).strip("-") or "kontakt"
        taken[base] = taken.get(base, 0) + 1
        out[addr] = base if taken[base] == 1 else f"{base}-{taken[base]}"
    return out


# --- печать ------------------------------------------------------------------

def source_line(t: dict) -> str:
    card = t.get("card") or {}
    if card.get("source_url"):
        return card["source_url"]
    if t["num"]:
        return f"список T-835, карточка {t['num']}"
    return "—"


def dossier(t: dict) -> str:
    card = t.get("card") or {}
    name = t["name"] or card.get("name") or t["addr"]
    seg = card.get("segment") or (t["num"].split(".")[0] if t["num"] else "")
    lines = [f"# {name}", ""]
    lines.append(f"- Адрес: {t['addr']}")
    if t.get("also"):
        lines.append("- Пишет также с: " + ", ".join(sorted(t["also"]))
                     + " (тот же человек, склеено по журналу ответов)")
    if t["num"]:
        lines.append(f"- Карточка: {t['num']}" + (f", сегмент {seg}" if seg else ""))
    if card.get("city"):
        lines.append(f"- Город: {card['city']}")
    if card.get("what"):
        lines.append(f"- Чем занимается: {card['what']}")
    lines.append(f"- Каркас письма: {t['template'] or '—'}")
    lines.append(f"- Источник: {source_line(t)}")
    lines.append(f"- Статус: {t['status']}")
    lines.append(f"- Отправлено писем: {t['n_out']}"
                 + (f" (из них не ушло: {t['n_out'] - t['n_ok']})" if t["n_out"] != t["n_ok"] else "")
                 + f" · ответов: {t['n_in']}"
                 + (f" · последнее событие: {human(t['last'])}" if t["last"] else ""))
    lines += ["",
              "*Файл производный: собран из memory/outbox/sent.jsonl и replies.jsonl "
              "командой `python3 -m modules.outbox.history`. Правки руками "
              "пропадут при следующей пересборке.*", "", "---", ""]

    if not t["events"]:
        lines += ["Событий нет.", ""]
    for e in t["events"]:
        if e["dir"] == "out":
            head = f"## → {human(e['ts'])} · отправлено"
            if not e["ok"]:
                head += " — НЕ УШЛО"
            lines += [head, ""]
            if e.get("peer") and e["peer"] != t["addr"]:
                lines.append(f"- **Кому:** {e['peer']} (не адрес карточки)")
            lines.append(f"- **Тема:** {e['subject'] or '—'}")
            if e["error"]:
                lines.append(f"- **Ошибка:** {e['error']}")
            if e["restored"]:
                lines.append("- **Внимание:** send.py тогда текст не сохранял; "
                             "тело восстановлено из гита на момент отправки "
                             f"({e['source'] or 'источник не записан'}), без подписи. "
                             "Совпадает с ушедшим, если файл не правили до коммита.")
            if e["lost"]:
                lines.append("- **Внимание:** текст письма утерян — в журнале его "
                             "нет и в гите на тот момент не нашёлся. Цитировать "
                             "это письмо нельзя.")
        else:
            lines += [f"## ← {human(e['ts'])} · {KIND_RU.get(e['kind'], e['kind'])}", ""]
            frm = e["from"]
            if frm and frm.lower() != t["addr"]:
                lines.append(f"- **От:** {frm} (не адрес карточки)")
            elif frm:
                lines.append(f"- **От:** {frm}")
            lines.append(f"- **Тема:** {e['subject'] or '—'}")
        lines += ["", e["body"] or "*(текста нет)*", ""]
    return "\n".join(lines).rstrip() + "\n"


def index(threads: dict[str, dict], names: dict[str, str]) -> str:
    rows = sorted(threads.values(), key=lambda t: (when(t["last"]), t["addr"]),
                  reverse=True)
    total_out = sum(t["n_out"] for t in rows)
    total_in = sum(t["n_in"] for t in rows)
    lines = ["# Переписка по контактам", "",
             f"Контактов: {len(rows)} · писем отправлено: {total_out} · "
             f"ответов получено: {total_in}", "",
             "*Собрано из memory/outbox/sent.jsonl и memory/outbox/replies.jsonl. "
             "Пересобрать: `python3 -m modules.outbox.history`. Пилотные "
             "письма на ящик Ильи сюда не входят.*", "",
             "| Контакт | Адрес | Отправлено | Ответов | Последнее событие | Статус |",
             "| --- | --- | ---: | ---: | --- | --- |"]
    for t in rows:
        name = (t["name"] or t["addr"]).replace("|", "/")
        link = f"[{name}]({names[t['addr']]}.md)"
        lines.append(f"| {link} | {t['addr']} | {t['n_out']} | {t['n_in']} | "
                     f"{human(t['last']) if t['last'] else '—'} | {t['status']} |")
    if not rows:
        lines.append("| — | — | 0 | 0 | — | пусто |")
    return "\n".join(lines) + "\n"


# --- запуск ------------------------------------------------------------------

def build(contact: str | None = None) -> int:
    threads = collect()
    names = slugs(list(threads))
    OUT.mkdir(parents=True, exist_ok=True)

    if contact:
        addr = contact.strip().lower()
        t = threads.get(addr)
        if not t:
            print(f"Контакта {addr} нет ни в одном журнале. Есть: "
                  + (", ".join(sorted(threads)) or "никого"))
            return 1
        path = OUT / f"{names[addr]}.md"
        path.write_text(dossier(t), encoding="utf-8")
        print(f"{path}  —  писем {t['n_out']}, ответов {t['n_in']}, {t['status']}")
        return 0

    # Полная пересборка: сносим прошлые досье. Зона производная, уникального
    # здесь нет по построению, а файл контакта, исчезнувшего из журналов,
    # иначе остался бы врать.
    for old in OUT.glob("*.md"):
        old.unlink()
    for addr, t in threads.items():
        (OUT / f"{names[addr]}.md").write_text(dossier(t), encoding="utf-8")
    (OUT / "INDEX.md").write_text(index(threads, names), encoding="utf-8")

    answered = sum(1 for t in threads.values() if t["status"] == "ответил")
    print(f"Досье: {len(threads)} в {OUT}")
    print(f"Писем отправлено: {sum(t['n_out'] for t in threads.values())} · "
          f"ответов: {sum(t['n_in'] for t in threads.values())} · "
          f"ответили: {answered}")
    # Мерило bl-140: досье теряет события молча, и заметить это можно было
    # только глазами. Теперь расхождение печатается само.
    shown = sum(len(t["events"]) for t in threads.values())
    want = usable(read_jsonl(SENT), read_jsonl(REPLIES))
    if shown != want:
        print(f"! События потерялись: в досье {shown}, а в журналах {want} "
              f"записей с адресом. Это поломка сборщика, а не данных.")
    merged = {t["addr"]: sorted(t["also"]) for t in threads.values() if t.get("also")}
    for head, extra in sorted(merged.items()):
        print(f"i Склеено в одно досье: {head} + {', '.join(extra)}")
    al = aliases(read_jsonl(SENT), read_jsonl(REPLIES))
    for a, b in al.refused:
        print(f"! Не склеил {a} и {b}: у обоих своя карточка ({al.num.get(a)} "
              f"и {al.num.get(b)}). Похоже, inbox привязал ответ не к тому контакту.")
    restored = sum(1 for t in threads.values()
                   for e in t["events"] if e["dir"] == "out" and e["restored"])
    lost = sum(1 for t in threads.values()
               for e in t["events"] if e["dir"] == "out" and e["lost"])
    if restored:
        print(f"i У {restored} писем тело восстановлено из гита на момент "
              f"отправки (send.py тогда текст не сохранял), подписи в них нет.")
    if lost:
        print(f"! У {lost} писем текст утерян — цитировать их нельзя. "
              f"Попробовать восстановить: python3 -m modules.outbox.backfill_bodies")
    print(f"Сводка: {OUT / 'INDEX.md'}")
    return 0


def main() -> None:
    ap = argparse.ArgumentParser(description="История переписки по контактам")
    ap.add_argument("--contact", metavar="ADDR",
                    help="пересобрать досье одного адреса (без флага — все)")
    args = ap.parse_args()
    sys.exit(build(args.contact))


if __name__ == "__main__":
    main()
