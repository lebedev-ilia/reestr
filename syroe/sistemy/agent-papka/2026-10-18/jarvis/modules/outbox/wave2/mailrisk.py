"""Второе сито по адресам пула: кому письмо уйдёт в пустоту.

Повод (bl-055). 25.08.2026 письмо на client@ellyme.ru отлупилось «550 No such
user» при живом MX домена: DNS-проверка (validate --mx, checkdns.py) видит
только домен, а существует ли за ним ящик — не видит вовсе. Слот дня сгорел, а
слотов пятнадцать в сутки, и каждый — это контакт, то есть деньги.

Что здесь проверяется — ровно то, что можно узнать, НЕ отправляя письма и не
стуча в чужой SMTP (проверка RCPT TO — разведка, за неё режут репутацию ящика,
а ящик у Ильи личный и невосстановимый):

  * пометки самих карточек: `dead` (был реальный отлуп) и `risk` (сборщик сам
    усомнился в адресе — «взят с агрегатора», «домен почты не совпадает с
    доменом сайта»);
  * безличная локальная часть (client@, no-reply@, all@ …) — за таким ящиком
    человека может не быть вовсе, а отлуп по нему самый частый;
  * живость домена по кешу сетевой пробы: есть ли MX/A и что отдаёт сайт —
    страницу компании, ошибку или заглушку регистратора («домен продаётся»).

Два уровня, и разница между ними принципиальная:

  МЁРТВЫЙ        — из плана дня выкидывается совсем. Только твёрдые признаки:
                   реальный отлуп, домен без MX и без A, сайт-парковка.
  ПОДОЗРИТЕЛЬНЫЙ — остаётся в пуле, но уходит в ХВОСТ своей партии. Выкидывать
                   info@ и mail@ нельзя: у российских студий это рабочие ящики,
                   и слепой запрет стоил бы больше, чем редкий отлуп.

Сеть спрятана за двумя функциями (`probe_dns`, `probe_site`) и вызывается
только по флагу --net; результат кладётся в derived (восстановимо, правило
v-derived-tolko-vosstanovimoe). Оценка пула сетью не пользуется никогда — она
читает кеш, поэтому и планировщик, и самотест работают офлайн.

    jarvis/.venv/bin/python -m jarvis.modules.outbox.wave2.mailrisk        отчёт
    jarvis/.venv/bin/python -m jarvis.modules.outbox.wave2.mailrisk --net  обновить кеш
    … --net --all   перепроверить и то, что уже в кеше
"""
from __future__ import annotations

import concurrent.futures as cf
import json
import socket
import sys
import urllib.error
import urllib.request
from datetime import date, timedelta

from jarvis.core import config as C

CACHE = C.DERIVED / "outbox" / "mailrisk.json"   # производное: пересобирается --net
FRESH_DAYS = 14           # старше — проба считается протухшей и обновляется
TIMEOUT = 8.0
UA = "Mozilla/5.0 (compatible; alverra-check/1.0)"

DEAD, SUSPECT, OK = "мёртвый", "подозрительный", "ok"

# Локальные части, за которыми человека может не быть. Это НЕ приговор: сами по
# себе они дают только «подозрительный». client@ здесь потому, что именно на нём
# случился отлуп 25.08.
#
# Чего здесь НЕТ и почему: info@, mail@, office@, sales@. Первая же прогонка по
# живому пулу дала 201 подозрительную карточку из 340 — то есть предупреждение
# обо всём, а значит ни о чём. У российских студий и заводов info@ и mail@ —
# основной рабочий ящик с сайта компании, письма туда доходят и на них отвечают
# (оба живых ответа на рассылку 25.08 пришли именно с таких адресов). Мерило,
# которое красит половину поля, лечит не адреса, а самооценку.
RISKY_LOCAL = {
    "client", "clients", "all", "admin", "root", "contact", "contacts",
    "robot", "bot", "reception", "secretary", "zakaz", "order",
}
# Из них — совсем безнадёжные: ящик заведомо не читает человек.
NEVER_READ = {"no-reply", "noreply", "donotreply", "do-not-reply", "test",
              "demo", "example", "mailer", "mailer-daemon", "postmaster",
              "abuse", "spam"}

PARKING = (
    "домен продается", "домен продаётся", "домен припаркован", "domain is for sale",
    "this domain is for sale", "buy this domain", "sedoparking", "hugedomains",
    "срок регистрации домена истек", "срок регистрации домена истёк",
    "домен не прикреплен", "домен не прикреплён", "parked domain",
    "здесь может быть ваш сайт", "заглушка хостинга", "сайт не настроен",
)


# ---------------------------------------------------------------------------
# Кеш проб
# ---------------------------------------------------------------------------

def cache_load() -> dict:
    try:
        return json.loads(CACHE.read_text(encoding="utf-8"))
    except Exception:                                        # noqa: BLE001
        return {}


def cache_save(data: dict) -> None:
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    CACHE.write_text(json.dumps(data, ensure_ascii=False, indent=1, sort_keys=True),
                     encoding="utf-8")


def stale(rec: dict, today: date | None = None) -> bool:
    """Проба протухла: нет даты либо старше FRESH_DAYS."""
    today = today or date.today()
    try:
        seen = date.fromisoformat(str(rec.get("ts", "")))
    except Exception:                                        # noqa: BLE001
        return True
    return (today - seen) > timedelta(days=FRESH_DAYS)


# ---------------------------------------------------------------------------
# Сеть. Обе функции возвращают данные, а не вердикт: толкует их verdict().
# ---------------------------------------------------------------------------

def ascii_host(dom: str) -> str:
    """Кириллический домен в punycode. Без этого urllib падает
    UnicodeEncodeError на «получи-права.рф», и живой домен читается как
    «сайт не отвечает» (первая же сетевая прогонка, 26.08.2026)."""
    try:
        return dom.encode("idna").decode("ascii")
    except Exception:                                        # noqa: BLE001
        return dom


def probe_dns(dom: str) -> tuple[bool, bool]:
    """(есть MX, есть A). Исключений не бросает: молчащий DNS — не приговор."""
    import dns.resolver

    host = ascii_host(dom)
    has_mx = has_a = False
    for _ in range(2):        # первая попытка нередко отваливается по таймауту
        try:
            dns.resolver.resolve(host, "MX", lifetime=TIMEOUT)
            has_mx = True
            break
        except Exception:                                    # noqa: BLE001
            continue
    for h in (host, "www." + host):
        try:
            dns.resolver.resolve(h, "A", lifetime=TIMEOUT)
            has_a = True
            break
        except Exception:                                    # noqa: BLE001
            continue
    return has_mx, has_a


def probe_site(dom: str) -> str:
    """Что отдаёт сайт домена: "ok" | "парковка" | "нет: причина".

    Обычный GET главной страницы, как у любого браузера: ни форм, ни авторизаций,
    ни обхода вглубь. Читаем первые 60 КБ — маркер заглушки всегда наверху.

    Пробуем и www: у части компаний A-запись висит только на www, и без второй
    попытки живой сайт читается как мёртвый.
    """
    host = ascii_host(dom)
    hosts = [host] if host.startswith("www.") else [host, "www." + host]
    last = "нет: не отвечает"
    for h in hosts:
        for scheme in ("https", "http"):
            req = urllib.request.Request(f"{scheme}://{h}/", headers={"User-Agent": UA})
            try:
                with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
                    body = r.read(60000).decode("utf-8", "replace").lower()
                    return "парковка" if any(m in body for m in PARKING) else "ok"
            except urllib.error.HTTPError as e:
                # 401/403/406/429 — защита от ботов на живом сайте, не смерть домена.
                if e.code in (401, 403, 405, 406, 429):
                    return "ok"
                last = f"нет: HTTP {e.code}"
            except (urllib.error.URLError, socket.timeout, OSError):
                continue
            except Exception as e:                           # noqa: BLE001
                last = f"нет: {type(e).__name__}"
    return last


def probe(dom: str, today: str = "") -> dict:
    has_mx, has_a = probe_dns(dom)
    site = probe_site(dom)
    return {"mx": has_mx, "a": has_a, "site": site,
            "ts": today or date.today().isoformat()}


def refresh(domains: list[str], cache: dict | None = None, force: bool = False,
            today: str = "", workers: int = 8) -> dict:
    """Обновить кеш проб по списку доменов. Единственное место с сетью."""
    cache = cache_load() if cache is None else cache
    todo = sorted({d for d in domains
                   if force or d not in cache or stale(cache[d])})
    if todo:
        with cf.ThreadPoolExecutor(workers) as ex:
            for dom, rec in zip(todo, ex.map(lambda d: probe(d, today), todo)):
                cache[dom] = rec
    return cache


# ---------------------------------------------------------------------------
# Вердикт. Чистая функция от карточки и кеша: ни сети, ни файлов.
# ---------------------------------------------------------------------------

def local_part(email: str) -> str:
    return (email or "").strip().lower().split("@")[0]


def domain(email: str) -> str:
    em = (email or "").strip().lower()
    return em.split("@")[-1] if "@" in em else ""


def verdict(card: dict, cache: dict | None = None,
            dom_count: dict[str, int] | None = None) -> tuple[str, list[str]]:
    """(уровень, причины). Уровень: DEAD | SUSPECT | OK.

    Причины возвращаются всегда — молча выкинутая карточка неотличима от
    кончившегося пула, а это ровно та ошибка, за которую в планировщике уже
    заведён вывод «кого не пустили и почему».
    """
    cache = cache or {}
    reasons: list[str] = []
    level = OK

    def worse(new: str) -> None:
        nonlocal level
        if new == DEAD or (new == SUSPECT and level == OK):
            level = new

    email = (card.get("email") or "").strip().lower()
    if not email or "@" not in email:
        return DEAD, ["адреса нет вовсе"]

    if card.get("dead"):
        worse(DEAD)
        reasons.append(f"отлуп: {card['dead']}")
    if card.get("risk"):
        worse(SUSPECT)
        reasons.append(f"пометка сборщика: {str(card['risk'])[:90]}")

    lp = local_part(email)
    if lp in NEVER_READ:
        worse(DEAD)
        reasons.append(f"ящик {lp}@ человек не читает по определению")
    elif lp in RISKY_LOCAL:
        worse(SUSPECT)
        reasons.append(f"безличный ящик {lp}@")

    dom = domain(email)
    rec = cache.get(dom)
    if rec:
        if not rec.get("mx") and not rec.get("a"):
            worse(DEAD)
            reasons.append(f"{dom}: ни MX, ни A — письмо не дойдёт")
        elif not rec.get("mx"):
            worse(SUSPECT)
            reasons.append(f"{dom}: MX нет, только A")
        site = str(rec.get("site", ""))
        if site == "парковка":
            worse(DEAD)
            reasons.append(f"{dom}: вместо сайта заглушка регистратора")
        elif site.startswith("нет"):
            worse(SUSPECT)
            reasons.append(f"{dom}: сайт не отвечает ({site[5:] or '?'})")

    if dom_count and dom_count.get(dom, 0) > 1:
        worse(SUSPECT)
        reasons.append(f"домен {dom} встречается в пуле {dom_count[dom]} раза")

    return level, reasons


def domain_counts(cards: list[tuple[str, dict]]) -> dict[str, int]:
    """Сколько раз домен встречается в пуле — БЕЗ публичных почтовиков: два
    разных ИП на yandex.ru не дубль, а норма (список общий с validate.py,
    чтобы правда о «публичном домене» жила в одном месте)."""
    from jarvis.modules.outbox.wave2.validate import PUBLIC_MAIL

    out: dict[str, int] = {}
    for _tag, c in cards:
        dom = domain(c.get("email", ""))
        if dom and dom not in PUBLIC_MAIL:
            out[dom] = out.get(dom, 0) + 1
    return out


def assess(cards: list[tuple[str, dict]], cache: dict | None = None) -> list[dict]:
    """Оценка всего пула: по строке на карточку, только не-ok."""
    cache = cache_load() if cache is None else cache
    counts = domain_counts(cards)
    out = []
    for tag, c in cards:
        level, why = verdict(c, cache, counts)
        if level != OK:
            out.append({"tag": tag, "id": str(c.get("id", "")),
                        "name": c.get("name", ""), "email": c.get("email", ""),
                        "level": level, "why": why})
    out.sort(key=lambda r: (r["level"] != DEAD, r["tag"], r["id"]))
    return out


def levels(cards: list[tuple[str, dict]],
           cache: dict | None = None) -> dict[tuple[str, str], str]:
    """{(партия, id): уровень} для всех не-ok карточек — то, чем пользуется
    планировщик: мёртвых выкидывает, подозрительных двигает в хвост партии."""
    return {(r["tag"], r["id"]): r["level"] for r in assess(cards, cache)}


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv: list[str]) -> int:
    from jarvis.modules.outbox.wave2 import dupcheck as D

    want_net = "--net" in argv
    force = "--all" in argv
    cards = D.load()
    doms = sorted({domain(c.get("email", "")) for _t, c in cards} - {""})
    print(f"карточек в пуле: {len(cards)}, доменов: {len(doms)}")

    cache = cache_load()
    if want_net:
        было = len(cache)
        cache = refresh(doms, cache, force=force)
        cache_save(cache)
        print(f"проб в кеше: было {было}, стало {len(cache)} → {CACHE}")
    else:
        нет = [d for d in doms if d not in cache]
        if нет:
            print(f"! доменов без сетевой пробы: {len(нет)} — прогони с --net, "
                  "иначе видны только пометки карточек и вид ящика")

    rows = assess(cards, cache)
    dead = [r for r in rows if r["level"] == DEAD]
    print(f"\nмёртвых (из плана вон): {len(dead)}, "
          f"подозрительных (в хвост партии): {len(rows) - len(dead)}\n")
    for r in rows:
        mark = "✗" if r["level"] == DEAD else "~"
        print(f"  {mark} {r['tag']} {r['id']} {r['email']}  {r['name']}")
        for w in r["why"]:
            print(f"      · {w}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
