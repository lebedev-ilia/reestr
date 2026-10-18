#!/usr/bin/env python3
"""Проверка почтовых доменов из списка контактов: есть ли вообще куда слать.

Это самая дешёвая правда о качестве списка. Адрес на домене без MX-записи —
гарантированный отлуп: письмо не дойдёт никогда, сколько ни шли. Отсеять такие
ДО рассылки важно вдвойне: каждый bounce портит репутацию отправляющего ящика,
а у нас ящик личный, и его репутация — расходник, который не восстановить.

Проверяется только домен, не адрес: существует ли конкретный ящик, DNS не знает.

    python -m jarvis.modules.outbox.checkdns [файл.md]
"""
import concurrent.futures as cf
import sys
from pathlib import Path

import dns.resolver

from .contacts import parse

TIMEOUT = 5.0


def mx(domain: str) -> tuple[str, str]:
    """(домен, вердикт). Исключений не бросает — падение одного домена
    не должно ронять проверку остальных."""
    try:
        answers = dns.resolver.resolve(domain, "MX", lifetime=TIMEOUT)
        return domain, f"ok: {sorted(str(r.exchange).rstrip('.') for r in answers)[0]}"
    except dns.resolver.NoAnswer:
        # Нет MX, но есть A — по RFC почта пойдёт на сам хост. Работает не везде.
        try:
            dns.resolver.resolve(domain, "A", lifetime=TIMEOUT)
            return domain, "спорно: MX нет, только A-запись"
        except Exception:
            return domain, "МЁРТВЫЙ: ни MX, ни A"
    except dns.resolver.NXDOMAIN:
        return domain, "МЁРТВЫЙ: домена не существует"
    except Exception as e:
        return domain, f"не проверен: {type(e).__name__}"


def main() -> None:
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else (
        Path.home() / "Рабочий стол/TrendFlowML/docs/marketing/T-835_60_KONTAKTOV.md")
    cards = parse(path)
    by_domain: dict[str, list[str]] = {}
    for c in cards:
        for e in c["channels"]["email"]:
            by_domain.setdefault(e.split("@")[1].lower(), []).append(
                f"{c['num']} {c['name']}")

    print(f"доменов к проверке: {len(by_domain)}")
    with cf.ThreadPoolExecutor(max_workers=8) as pool:
        results = dict(pool.map(mx, by_domain))

    bad = {d: v for d, v in results.items() if not v.startswith("ok")}
    print(f"чистых: {len(results) - len(bad)}, к разбору: {len(bad)}\n")
    for d, verdict in sorted(bad.items(), key=lambda x: x[1]):
        print(f"{verdict:<40} {d}   ← {', '.join(by_domain[d])}")


if __name__ == "__main__":
    main()
