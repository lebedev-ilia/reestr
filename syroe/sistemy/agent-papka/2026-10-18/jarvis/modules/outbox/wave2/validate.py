#!/usr/bin/env python3
"""Приёмка партии карточек второй волны: что принёс агент-сборщик.

Проверяет ровно то, что можно проверить машиной, и не притворяется, что
проверяет остальное. Персональность зацепки, честность адреса и «не выдумал ли
он кейс» машина не видит — это читается глазами выборочно.

    .venv/bin/python -m modules.outbox.wave2.validate <файл.json> [ещё файлы]
    .venv/bin/python -m modules.outbox.wave2.validate --all

Код возврата 1, если есть хоть одна ошибка (не предупреждение).
"""
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
HERE = ROOT / "modules" / "outbox" / "wave2"

# Пояс города нужен приёмке, а не только планировщику: карточка без города
# или с городом, который не резолвится (было «Россия», bl-058), уедет по
# Москве молча — и обещание «письмо в 07:00 по адресату» перестанет быть
# правдой ровно там, где этого никто не заметит.
try:  # -m modules.outbox.wave2.validate и -m jarvis.modules...
    from . import geo as GEO
except ImportError:  # прямой запуск файлом
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import geo as GEO  # type: ignore

REQUIRED = {"id", "segment", "template", "name", "city", "what", "email",
            "source_url", "checked", "why", "hook", "subject", "body"}
# Публичные почтовики: совпадение домена у двух карточек тут НЕ дубль компании.
PUBLIC_MAIL = {"yandex.ru", "mail.ru", "gmail.com", "bk.ru", "list.ru",
               "inbox.ru", "ya.ru", "rambler.ru", "internet.ru", "icloud.com"}
# Пусто с 26.08.2026. Раньше здесь требовался абзац про наш проект
# («75 компонентов за 11 дней») — Илья велел убрать упоминание проекта из
# всех писем, и требование превратилось в запрет (см. BANNED ниже).
MUST_CONTAIN: dict[str, str] = {}
# Проверяем СМЫСЛ, а не букву. Два обязательных куска письма: просьба о
# коротком разговоре и довод, ради чего адресату этот разговор. Форма любая.
#
# 27.08.2026 (bl-133) требования переписаны второй раз. Первая редакция
# («15 минут» и корень «окуп») требовала БУКВЫ — и сама плодила лекало: во
# всех 340 письмах пула обязаны были стоять одни и те же слова, почтовый
# фильтр видел ровно это. Пять агентов-сборщиков из двенадцати независимо
# наступили на одно и то же: «дайте двадцать минут» и «поговорим полчаса»
# валились с ошибкой, хотя по смыслу верны. Каркас (templates.json) к тому
# моменту уже разрешал «полчаса / короткий созвон» — валидатор запрещал.
#
# Чего эти проверки НЕ делают. Они ловят письмо, где просьбы или довода нет
# вовсе, а не оценивают формулировку: слово «обсудим» в середине письма
# засчитается за просьбу (на живом пуле так проходит 95 писем из 340, если
# вырезать у них настоящую просьбу). Качество CTA читается глазами и
# счётчиком повторов dupcheck, машина здесь — предохранитель от пропуска.
ASK_RE = (
    # мера времени: «15 минут», «двадцать минут», «полчаса», «четверть часа»
    r"(?:\d{1,2}|пятнадцат\w*|двадцат\w*|десят\w*|тридцат\w*|сорок|пят\w*|"
    r"полчас\w*)\s*минут|полчаса|получас\w*|четверт\w*\s+часа"
    # либо разговор как таковой: созвон, звонок, обсудим, встретимся
    r"|разговор\w*|поговор\w*|созвон\w*|созвонимся|звонк\w*|позвон\w*|"
    r"обсуд\w*|встрет\w*|встреч\w*|переговор\w*|пообща\w*|спис(?:аться|ыва\w*)"
)
BENEFIT_RE = (
    r"окуп\w*|эконом\w*|сэконом\w*|высвобод\w*|освобод\w*|верн\w*тся|"
    r"верн\w*лись|отобь\w*|перестан\w*|не придётся|не потребу\w*|избав\w*|"
    r"разгруз\w*|выгод\w*|польз\w*|сним(?:ет|ается|аются)\b"
)
MUST_MATCH = {
    ASK_RE: "нет просьбы о коротком разговоре",
    BENEFIT_RE: "нет довода, ради чего этот разговор (польза для адресата)",
}
BANNED = {
    "75 компонент": "упоминание нашего проекта (запрещено Ильёй 26.08.2026)",
    "11 дней": "упоминание нашего проекта (запрещено Ильёй 26.08.2026)",
    "TrendFlow": "старый бренд",
    "трендфлоу": "старый бренд",
    "Alverra": "имя компании в теле письма (в первой волне его там нет)",
}
BODY_MIN, BODY_MAX = 600, 1400
SUBJ_MIN, SUBJ_MAX = 25, 60
EMAIL_RE = re.compile(r"[^@\s]+@[^@\s]+\.[a-zа-я]{2,}", re.I)


def known_contacts() -> tuple[set[str], set[str]]:
    """Домены и нормализованные имена первой волны — чтобы ловить дубли."""
    doms: set[str] = set()
    names: set[str] = set()
    src = Path.home() / "Рабочий стол" / "TrendFlowML" / "docs" / "marketing" / "T-835_60_KONTAKTOV.md"
    if src.exists():
        text = src.read_text(encoding="utf-8", errors="replace")
        for m in EMAIL_RE.finditer(text):
            doms.add(m.group(0).split("@")[-1].lower().rstrip("."))
        for m in re.finditer(r"^### \d+\.\d+ (.+)$", text, re.M):
            names.add(norm_name(m.group(1)))
    return doms - PUBLIC_MAIL, names


def norm_name(s: str) -> str:
    s = re.sub(r"\(.*?\)", " ", s)
    s = re.sub(r"[«»\"'.,\-–—/]", " ", s.lower())
    for w in ("веб студия", "студия", "digital agency", "агентство", "ооо", "гк", "тд"):
        s = s.replace(w, " ")
    return " ".join(s.split())


def check_file(path: Path, seen_ids: dict, seen_doms: dict, seen_names: dict,
               old_doms: set, old_names: set) -> tuple[list, list]:
    errors: list[str] = []
    warns: list[str] = []
    try:
        cards = json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        return [f"{path.name}: файл не читается как JSON — {e}"], []
    if not isinstance(cards, list) or not cards:
        return [f"{path.name}: ожидался непустой список карточек"], []

    for i, c in enumerate(cards):
        cid = c.get("id", f"<без id, {i}-я>")
        tag = f"{path.name} {cid}"
        miss = REQUIRED - set(c)
        if miss:
            errors.append(f"{tag}: нет полей {sorted(miss)}")
            continue
        if not str(c["id"]).strip():
            errors.append(f"{tag}: пустой id")
        if c["id"] in seen_ids:
            errors.append(f"{tag}: id уже занят в {seen_ids[c['id']]}")
        seen_ids[c["id"]] = path.name

        email = c["email"].strip()
        if not EMAIL_RE.fullmatch(email):
            errors.append(f"{tag}: адрес не похож на адрес — {email!r}")
            dom = ""
        else:
            dom = email.split("@")[-1].lower()
        if dom and dom not in PUBLIC_MAIL:
            if dom in old_doms:
                errors.append(f"{tag}: домен {dom} уже есть в первой волне")
            elif dom in seen_doms:
                errors.append(f"{tag}: домен {dom} дублирует {seen_doms[dom]}")
            seen_doms[dom] = tag

        city = str(c["city"]).strip()
        if not city:
            errors.append(f"{tag}: пустой город — письмо уедет по Москве")
        else:
            _off, ok = GEO.offset_for(city)
            if not ok:
                errors.append(
                    f"{tag}: город «{city}» не резолвится в часовой пояс — "
                    "письмо уедет по Москве. Настоящий город в карточку либо "
                    "строку в geo.CITY_TZ")

        nm = norm_name(c["name"])
        if nm in old_names:
            errors.append(f"{tag}: название «{c['name']}» уже есть в первой волне")
        elif nm in seen_names:
            errors.append(f"{tag}: название «{c['name']}» дублирует {seen_names[nm]}")
        seen_names[nm] = tag

        body = c["body"]
        if re.search(r"[\[\]]", body):
            errors.append(f"{tag}: в теле остались скобки из скелета каркаса")
        for bad, why in BANNED.items():
            if bad.lower() in body.lower():
                errors.append(f"{tag}: {why} — «{bad}»")
        for need, why in MUST_CONTAIN.items():
            if need not in body:
                errors.append(f"{tag}: {why}")
        for pat, why in MUST_MATCH.items():
            if not re.search(pat, body, re.IGNORECASE):
                errors.append(f"{tag}: {why}")
        if not BODY_MIN <= len(body) <= BODY_MAX:
            warns.append(f"{tag}: тело {len(body)} знаков (ждём {BODY_MIN}-{BODY_MAX})")

        subj = c["subject"].strip()
        if not SUBJ_MIN <= len(subj) <= SUBJ_MAX:
            warns.append(f"{tag}: тема {len(subj)} знаков (ждём {SUBJ_MIN}-{SUBJ_MAX})")
        if not re.search(r"[а-яё]", subj, re.I):
            errors.append(f"{tag}: тема не по-русски — {subj!r}")
        if re.search(r"[*_#`]|!{1,}$", subj):
            warns.append(f"{tag}: в теме разметка или восклицательный знак")

        if not str(c["source_url"]).startswith("http"):
            errors.append(f"{tag}: source_url не ссылка")
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(c["checked"])):
            warns.append(f"{tag}: checked не в виде ГГГГ-ММ-ДД")
        if len(str(c["hook"]).split()) < 4:
            warns.append(f"{tag}: зацепка подозрительно короткая")

    # штампованность: первый абзац после приветствия должен быть у всех разный
    firsts = []
    for c in cards:
        parts = [p for p in str(c.get("body", "")).split("\n\n") if p.strip()]
        firsts.append(parts[1][:80] if len(parts) > 1 else "")
    if len(set(firsts)) != len(firsts):
        errors.append(f"{path.name}: одинаковые первые абзацы — зацепка не персональная")
    return errors, warns


def check_mx(files: list) -> list[str]:
    """Домены без MX — гарантированный отлуп. Сетевая проверка, потому отдельным флагом.

    Домен без MX, но с A-записью почту принять теоретически может (RFC-фоллбэк), и
    всё же это слабый адрес: помечаем, а не молчим.
    """
    import concurrent.futures as cf

    import dns.resolver

    doms: dict[str, str] = {}
    for f in files:
        try:
            for c in json.loads(f.read_text(encoding="utf-8")):
                em = str(c.get("email", ""))
                if EMAIL_RE.fullmatch(em):
                    doms.setdefault(em.split("@")[-1].lower(), f"{f.name} {c.get('id')}")
        except Exception:
            continue

    def one(dom: str) -> tuple[str, str]:
        last = ""
        for _ in range(2):  # первая попытка нередко отваливается по таймауту
            try:
                dns.resolver.resolve(dom, "MX", lifetime=10)
                return dom, ""
            except Exception as e:
                last = type(e).__name__
        try:
            dns.resolver.resolve(dom, "A", lifetime=10)
            return dom, f"нет MX ({last}), но есть A — почта под вопросом"
        except Exception:
            return dom, f"нет ни MX, ни A ({last}) — письмо не дойдёт"

    out = []
    with cf.ThreadPoolExecutor(10) as ex:
        for dom, verdict in ex.map(one, sorted(doms)):
            if verdict:
                out.append(f"{doms[dom]}: {dom} — {verdict}")
    print(f"MX: проверено доменов {len(doms)}, проблемных {len(out)}")
    return out


def main(argv: list[str]) -> int:
    want_mx = "--mx" in argv
    argv = [a for a in argv if a != "--mx"]
    if argv and argv[0] == "--all":
        files = sorted(HERE.glob("*.json"))
    else:
        files = [Path(a) for a in argv]
    if not files:
        print("нечего проверять: укажи файлы или --all")
        return 1
    old_doms, old_names = known_contacts()
    if not old_doms:
        print("! первая волна не прочиталась — дубли с ней не ловятся")
    seen_ids: dict = {}
    seen_doms: dict = {}
    seen_names: dict = {}
    all_err: list[str] = []
    all_warn: list[str] = []
    for f in files:
        e, w = check_file(f, seen_ids, seen_doms, seen_names, old_doms, old_names)
        n = 0
        try:
            n = len(json.loads(f.read_text(encoding="utf-8")))
        except Exception:
            pass
        print(f"{f.name}: карточек {n}, ошибок {len(e)}, предупреждений {len(w)}")
        all_err += e
        all_warn += w
    if want_mx:
        all_warn += check_mx(files)
    for w in all_warn:
        print("  ~", w)
    for e in all_err:
        print("  ✗", e)
    print(f"\nвсего карточек: {len(seen_ids)}, ошибок: {len(all_err)}, предупреждений: {len(all_warn)}")
    return 1 if all_err else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
