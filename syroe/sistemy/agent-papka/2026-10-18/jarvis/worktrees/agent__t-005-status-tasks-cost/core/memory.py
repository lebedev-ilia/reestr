"""Память: запись в лог и сборка контекста.

Единственное место, которое знает форматы из MEMORY.md. Если формат меняется —
меняется этот файл, и больше ничего. Всё остальное работает через эти функции.
"""

import contextlib
import fcntl
import json
import os
import re
import sys
from datetime import datetime, date, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from . import config as C
from . import morph
from . import questions


def now_ts() -> str:
    return datetime.now(ZoneInfo(C.TZ)).isoformat(timespec="seconds")


def today() -> date:
    return datetime.now(ZoneInfo(C.TZ)).date()


# --------------------------------------------------------------------------
# Эпизодический слой: только дозапись, никогда не правится
# --------------------------------------------------------------------------

_SECRETS: tuple | None = None
_SECRETS_MTIME: float | None = None


def _secrets() -> list[tuple[str, str]]:
    """Значения из .env, которым нельзя лежать в логе.

    Секретом считается значение от 20 знаков, а у переменной с говорящим
    именем (PASS, TOKEN, SECRET, KEY) — от 12: пароль почты оказался ровно
    двадцатизначным и в порог «длиннее 20» не попадал. Ровно то же правило
    зашито в core/githooks/pre-commit — если правишь здесь, правь и там.

    Отдаёт пары (имя, что искать): для каждого значения ищем и сырой вид, и
    json-экранированный — в лог оно попадает уже прогнанным через json.dumps.
    Порядок — от длинного к короткому, чтобы длинное не осталось обрубком.
    """
    global _SECRETS, _SECRETS_MTIME
    env = C.ROOT / ".env"
    mtime = env.stat().st_mtime if env.exists() else None
    if _SECRETS is not None and mtime == _SECRETS_MTIME:
        return list(_SECRETS)
    pairs: list[tuple[str, str]] = []
    if env.exists():
        for line in env.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            name, value = line.split("=", 1)
            name, value = name.strip(), value.strip().strip('"').strip("'")
            speaking = any(w in name.upper() for w in ("PASS", "TOKEN", "SECRET", "KEY", "PWD"))
            if len(value) < (12 if speaking else 20):
                continue
            pairs.append((name, value))
            escaped = json.dumps(value, ensure_ascii=False)[1:-1]
            if escaped != value:
                pairs.append((name, escaped))
    pairs.sort(key=lambda p: -len(p[1]))
    _SECRETS, _SECRETS_MTIME = tuple(pairs), mtime
    return pairs


def mask_secrets(text: str) -> str:
    """Заменяет значения секретов на <СЕКРЕТ ИМЯ>. Дёшево: обычно 0 совпадений."""
    if not text:
        return text
    for name, needle in _secrets():
        if needle in text:
            text = text.replace(needle, f"<СЕКРЕТ {name}>")
    return text


_LIVE_WARNED = False


def mark_live() -> None:
    """Объявляет ЭТОТ процесс живым: его записи идут в настоящий memory/log.

    Зовётся точкой входа настоящей работы: демон ВК, run.py (в нём же ночной
    цикл), воркер подагента, отправка сообщения Илье. Ход агента и сервер
    памяти живут внутри демона одним процессом, поэтому им отдельной отметки
    не нужно.

    Намеренно НЕ уходит в окружение. Разовый скрипт из Bash — правнук демона
    и унаследовал бы право писать вместе с переменной; именно так 25.08 в
    неизменяемую зону натекли 210 строк gate-фикстуры (bl-087). Право
    берётся вызовом в коде, а не происхождением процесса.
    """
    C.LIVE = True


def enforce_live_no_selftest() -> bool:
    """Предохранитель для точки входа заведомо живой работы (демон ВК):
    унаследованный флаг самотеста здесь — ВСЕГДА ошибка окружения, а не
    тихая особенность (bl-121). subprocess.Popen без явного env копирует
    окружение родителя целиком; если родитель когда-то (сам, или через цепь
    более ранних перезапусков — см. core/config.py:live_env) нёс
    JARVIS_SELFTEST=1, демон рождается уже «внутри самотеста» и все его
    записи 26.08 полтора часа уезжали в derived/selftest/log вместо
    неизменяемого memory/log.

    Сброс — раньше первого обращения к логу (иначе даже эта error-строка
    уедет мимо живого лога и случай останется незамеченным). Возвращает
    True, если пришлось сбрасывать — вызывающий код при желании может это
    учесть, тесты используют это как признак утечки.
    """
    if not (C.SELFTEST or os.environ.get("JARVIS_SELFTEST")):
        return False
    C.SELFTEST = False
    os.environ.pop("JARVIS_SELFTEST", None)
    log("error", {
        "vk_daemon_born_tainted":
            "JARVIS_SELFTEST=1 достался живому процессу по наследству через "
            "окружение — сброшен на старте, до первой записи в лог",
    })
    return True


def log_dir() -> Path:
    """Куда писать событие. Живой memory/log — только для живого хода.

    Три случая, по убыванию строгости:
      * самотест — в SELFTEST_LOG (правило samotest-ne-pishet-v-zhivuyu-pamyat);
      * процесс не объявлял себя живым (разовый скрипт из Bash) — в MANUAL_LOG,
        с предупреждением в stderr: запись не теряется, но не врёт в реестре
        и в самоаудите навсегда (bl-087);
      * живой процесс — в C.LOG_DIR.
    Подменяется только настоящий memory/log: тест, который сам подставил
    C.LOG_DIR во временную папку, продолжает писать туда, куда просил.
    """
    global _LIVE_WARNED
    if C.LOG_DIR != C.MEM / "log":
        return C.LOG_DIR
    if C.SELFTEST:
        return C.SELFTEST_LOG
    if not C.LIVE:
        if not _LIVE_WARNED:
            _LIVE_WARNED = True
            print("memory.log: процесс не живой (нет JARVIS_LIVE) — пишу в "
                  f"{C.MANUAL_LOG}, а не в memory/log", file=sys.stderr)
        return C.MANUAL_LOG
    return C.LOG_DIR


_TURN: str = ""
# путь файла -> (сколько байт уже просмотрено, сколько строк в них).
# Считать строки заново после каждой записи — 29 млн лишних прочитанных строк
# в сутки; здесь досматривается только хвост, дописанный чужими процессами.
_LINES: dict[str, tuple[int, int]] = {}


def set_turn(ref: str) -> None:
    """Отмечает, каким событием начался текущий ход этого процесса.

    Ссылка на первое событие хода (message_in) становится полем turn у всех
    следующих строк: по ней ход собирается из лога целиком, не гадая по времени.
    """
    global _TURN
    _TURN = ref or ""


def current_turn() -> str:
    return _TURN


def log(event_type: str, content, actor: str = "agent", refs=None) -> str:
    """Пишет событие в лог. Возвращает ссылку вида log:2026-08-22#7.

    Запись и подсчёт номера строки идут под flock: в один файл пишут демон ВК,
    подагенты и разовые команды, а строка длиннее PIPE_BUF без замка рвётся.
    Номер строки известен ДО записи, поэтому событие несёт собственную ссылку
    в поле ref — grep по логу сразу даёт готовый log:дата#N, без пересчёта.
    """
    d = log_dir()
    d.mkdir(parents=True, exist_ok=True)
    day = today().isoformat()
    path = d / f"{day}.jsonl"
    key = str(path)
    body = mask_secrets(
        content if isinstance(content, str) else json.dumps(content, ensure_ascii=False)
    )
    with path.open("a+b") as f:
        fcntl.flock(f.fileno(), fcntl.LOCK_EX)
        try:
            seen, lines = _LINES.get(key, (0, 0))
            size = os.fstat(f.fileno()).st_size
            if size < seen:          # файл подменили или урезали — считаем заново
                seen, lines = 0, 0
            if size > seen:          # дописали другие процессы — досматриваем хвост
                f.seek(seen)
                lines += f.read(size - seen).count(b"\n")
                seen = size
            line_no = lines + 1
            event = {
                "ts": now_ts(),
                "type": event_type,
                "actor": actor,
                "ref": f"log:{day}#{line_no}",
                "pid": os.getpid(),
                "turn": _TURN,
                "content": body,
                # refs — только явные связи от вызывающего: ссылку на начало
                # хода несёт turn, дублировать её здесь значит платить лишними
                # тридцатью байтами на каждой строке ради второго имени тому же.
                "refs": list(refs) if refs else [],
            }
            blob = (json.dumps(event, ensure_ascii=False) + "\n").encode("utf-8")
            f.write(blob)
            f.flush()
            _LINES[key] = (seen + len(blob), line_no)
        finally:
            fcntl.flock(f.fileno(), fcntl.LOCK_UN)
    return f"log:{day}#{line_no}"


@contextlib.contextmanager
def selftest_guard(markers: tuple[str, ...] = ()):
    """Держит самотест подальше от живого лога и доказывает это.

    Внутри блока log() уводит записи в derived (см. log_dir). На выходе
    живой лог просматривается за время прогона: если хоть одна фикстура
    (markers) там всё же оказалась — подмена отвалилась, и тест обязан
    упасть, а не промолчать. Смотрим по меткам, а не по размеру файла:
    рядом работает демон ВК, он пишет в тот же файл законно.
    """
    state: dict = {"ok": None, "leaked": []}
    start = now_ts()
    saved, saved_env = C.SELFTEST, os.environ.get("JARVIS_SELFTEST")
    saved_live, saved_live_env = C.LIVE, os.environ.get("JARVIS_LIVE")
    C.SELFTEST = True
    os.environ["JARVIS_SELFTEST"] = "1"
    try:
        yield state
    finally:
        C.LIVE = saved_live
        if saved_live_env is None:
            os.environ.pop("JARVIS_LIVE", None)
        else:
            os.environ["JARVIS_LIVE"] = saved_live_env
        C.SELFTEST = saved
        if saved_env is None:
            os.environ.pop("JARVIS_SELFTEST", None)
        else:
            os.environ["JARVIS_SELFTEST"] = saved_env
        live = C.MEM / "log" / f"{today().isoformat()}.jsonl"
        fresh = []
        if live.exists():
            for line in live.read_text(encoding="utf-8").splitlines():
                try:
                    e = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if e.get("ts", "") >= start:
                    fresh.append(e.get("content", ""))
        state["leaked"] = [m for m in markers
                           if any(m in c for c in fresh)]
        state["ok"] = not state["leaked"]


def read_log_day(d: date) -> list[dict]:
    path = C.LOG_DIR / f"{d.isoformat()}.jsonl"
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return out


def read_log_range(days: int) -> list[dict]:
    end = today()
    events = []
    for i in range(days - 1, -1, -1):
        events.extend(read_log_day(end - timedelta(days=i)))
    return events


# Служебный шум: след есть в логе, но в разговор он не входит. Пульс шлёт
# Илье строки вида «читаю страницу» — 25.08 они составили 83% всех записей
# типа message_out за день (bl-067). Пока они лежали тем же типом, что и
# ответы, окно лога в промпте забивалось ими, ночной цикл считал их
# разговором, а поиск по памяти выдавал их вместо содержания. С 26.08 у них
# свой тип, и отсюда — единственное место, где решается, что считать шумом.
NOISE_TYPES = frozenset({"status"})


def drop_noise(events: list[dict]) -> list[dict]:
    """События без служебного шума: для окна лога, ночного разбора и поиска."""
    return [e for e in events if e.get("type") not in NOISE_TYPES]


# --------------------------------------------------------------------------
# Производные слои: пишутся только через эти функции, чтобы формат не поплыл
# --------------------------------------------------------------------------

def _slug(s: str) -> str:
    s = s.lower().strip()
    s = re.sub(r"[^\w\s-]", "", s, flags=re.UNICODE)
    return re.sub(r"[\s_]+", "-", s)[:60]


def _frontmatter(fields: dict, body: str) -> str:
    lines = ["---"]
    for k, v in fields.items():
        if isinstance(v, list):
            v = "[" + ", ".join(str(x) for x in v) + "]"
        lines.append(f"{k}: {v}")
    lines += ["---", "", body.strip(), ""]
    return "\n".join(lines)


def parse_frontmatter(text: str) -> tuple[dict, str]:
    if not text.startswith("---"):
        return {}, text
    parts = text.split("---", 2)
    if len(parts) < 3:
        return {}, text
    meta = {}
    for line in parts[1].strip().splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            meta[k.strip()] = v.strip()
    return meta, parts[2].strip()


def _parse_list_field(value: str) -> list[str]:
    """Разбирает «[a, b, c]» из шапки в список строк. Пусто/нет скобок — []."""
    v = (value or "").strip()
    if v.startswith("[") and v.endswith("]"):
        v = v[1:-1]
    if not v.strip():
        return []
    return [p.strip() for p in v.split(",") if p.strip()]


def _merge_lists(old: list[str], new: list[str]) -> list[str]:
    """Старые значения первыми, новые добавляются, дубликаты схлопываются."""
    out = list(old)
    for item in new:
        if item not in out:
            out.append(item)
    return out


def write_fact(fact_id: str, ftype: str, name: str, body: str,
               source: str = "", links=None, status: str = "active") -> str:
    """Записать факт; повторный вызов обновляет шапку, а не затирает её.

    Прежняя версия при обновлении существующего факта переписывала sources и
    links с нуля значениями текущего вызова — источники и связи прошлой
    версии терялись молча, даже если новый вызов передавал только source, а
    links вовсе не передавал. Поймано 26.08 на memory/facts/ilya.md: пропали
    2 источника из 3 и все 5 ссылок (bl-112). Тот же класс бага уже чинился в
    write_rule (bl-031) — здесь то же лечение: старое читается и сливается с
    новым, а не заменяется им.

    Сохраняются: created — из старой шапки. sources и links — объединение
    старого списка с новым: старое первым, новое дописывается, повторы
    схлопываются, пустой новый source или отсутствующие links ничего не
    стирают. updated — всегда сегодняшняя дата.
    """
    C.FACTS.mkdir(parents=True, exist_ok=True)
    fact_id = _slug(fact_id)
    path = C.FACTS / f"{fact_id}.md"
    created = today().isoformat()
    old_sources: list[str] = []
    old_links: list[str] = []
    if path.exists():
        old_meta, _ = parse_frontmatter(path.read_text(encoding="utf-8"))
        created = old_meta.get("created", created)
        old_sources = _parse_list_field(old_meta.get("sources", ""))
        old_links = _parse_list_field(old_meta.get("links", ""))
    sources = _merge_lists(old_sources, [source] if source else [])
    all_links = _merge_lists(old_links, links or [])
    path.write_text(_frontmatter({
        "id": fact_id, "type": ftype, "name": name, "status": status,
        "created": created, "updated": today().isoformat(),
        "sources": sources, "links": all_links,
    }, body), encoding="utf-8")
    return str(path.relative_to(C.ROOT))


def _replace_section(body: str, label: str, value: str) -> str:
    """Заменить содержимое «**Метка:** …», не тронув соседей.

    Раздел кончается там, где начинается следующий: строка, открывающаяся
    «**» или «#», либо конец текста. Без этой границы замена «Как применять»
    съела бы всё, что дописано в файл после неё.

    Замена идёт через lambda: в новом тексте попадается «\\1» и прочие
    последовательности с обратной косой, и обычный sub истолковал бы их как
    ссылки на группы.
    """
    pat = re.compile(rf"^\*\*{re.escape(label)}:\*\*.*?(?=\n\*\*|\n#|\Z)",
                     flags=re.S | re.M)
    return pat.sub(lambda _m: f"**{label}:** {value}", body, count=1)


def write_rule(rule_id: str, rule: str, why: str, scope: str,
               how: str = "", source: str = "") -> str:
    """Записать правило; повторный вызов обновляет, а не затирает файл.

    Правила живут долго и правятся: сужается область, дописывается причина.
    Прежняя версия при обновлении писала файл с нуля — и молча теряла две
    вещи: дату рождения (created вставала сегодняшней, история правила
    исчезала) и всё, что не укладывается в три раздела. У правила
    v-derived-tolko-vosstanovimoe перед «Правило» стоит преамбула о том, что
    повод устранён, — она бы пропала. Из-за этого правки шли руками, а
    инструмент простаивал (bl-031).

    Теперь при обновлении сохраняются: created, status (снятое с действия
    правило не воскресает само), source и всё тело файла помимо трёх
    ведомых кодом разделов. Пустой how на обновлении означает «не трогай», а
    не «сотри»: удаление раздела — правка руками, чтобы случайный вызов не
    стирал написанное.
    """
    C.RULES.mkdir(parents=True, exist_ok=True)
    rule_id = _slug(rule_id)
    path = C.RULES / f"{rule_id}.md"
    created, status, old_body = today().isoformat(), "active", ""
    if path.exists():
        old_meta, old_body = parse_frontmatter(path.read_text(encoding="utf-8"))
        created = old_meta.get("created", created)
        status = old_meta.get("status", status)
        source = source or old_meta.get("source", "")
    sections = {"Правило": rule, "Почему": why}
    if how:
        sections["Как применять"] = how
    body = old_body
    for label, value in sections.items():
        if re.search(rf"^\*\*{re.escape(label)}:\*\*", body, flags=re.M):
            body = _replace_section(body, label, value)
        else:
            body = (body.rstrip() + "\n" if body else "") + f"**{label}:** {value}\n"
    path.write_text(_frontmatter({
        "id": rule_id, "scope": f'"{scope}"', "created": created,
        "updated": today().isoformat(), "source": source, "status": status,
    }, body), encoding="utf-8")
    return str(path.relative_to(C.ROOT))


def write_procedure(proc_id: str, trigger: str, steps: str,
                    preconditions: str = "", pitfalls: str = "",
                    cost: str = "slow", links=None) -> str:
    """Записать процедуру; повторный вызов обновляет шапку, а не затирает её.

    Тот же класс бага, что чинился в write_rule (bl-031) и write_fact
    (bl-112), жил и здесь. Из старой шапки поднимались только runs, failures
    и created, а остальное писалось с нуля значениями текущего вызова:

    - status затирался словом «experimental» всегда. Процедура, доросшая до
      «working», после первого же повторного remember_procedure молча
      разжаловалась обратно в испытуемые. На 27.08 таких файлов был один —
      perezapusk-demona-vk (5 запусков, статус working);
    - links не писались вовсе, то есть строка связей просто исчезала из
      файла. У того же perezapusk-demona-vk их пять.

    Сохраняются: created, runs (+1), failures, status и links — старое
    сливается с новым, а не заменяется им. Обновляются: trigger, cost,
    last_used и тело (тело процедуры и задумано переписываемым — оно целиком
    складывается из аргументов вызова).
    """
    C.PROCEDURES.mkdir(parents=True, exist_ok=True)
    proc_id = _slug(proc_id)
    path = C.PROCEDURES / f"{proc_id}.md"
    runs, failures, created = 1, 0, today().isoformat()
    status = "experimental"
    old_links: list[str] = []
    if path.exists():
        meta, _ = parse_frontmatter(path.read_text(encoding="utf-8"))
        runs = int(meta.get("runs", 0)) + 1
        failures = int(meta.get("failures", 0))
        created = meta.get("created", created)
        status = meta.get("status", status) or status
        old_links = _parse_list_field(meta.get("links", ""))
    all_links = _merge_lists(old_links, links or [])
    body = ""
    if preconditions:
        body += f"## Предусловия\n{preconditions}\n\n"
    body += f"## Шаги\n{steps}\n"
    if pitfalls:
        body += f"\n## Известные грабли\n{pitfalls}\n"
    head = {
        "id": proc_id, "trigger": f'"{trigger}"', "cost": cost,
        "created": created, "last_used": today().isoformat(),
        "runs": runs, "failures": failures, "status": status,
    }
    # Пустой список не пишем: у новых процедур связей нет, и лишняя строка
    # «links: []» в шапке только мусорит.
    if all_links:
        head["links"] = all_links
    path.write_text(_frontmatter(head, body), encoding="utf-8")
    return str(path.relative_to(C.ROOT))


def _next_id(text: str, prefix: str) -> str:
    """Следующий свободный id — по максимуму существующих, а не по их числу.

    Считать количеством нельзя: удалили или переставили строку — и новый id
    совпадёт с чьим-то живым. А правятся строки по префиксу «| id », то есть
    один update молча переписал бы сразу две задачи.
    """
    nums = [int(n) for n in re.findall(rf"^\|\s*{prefix}-(\d+)", text, flags=re.M)]
    return f"{prefix}-{(max(nums) + 1) if nums else 1:03d}"


# Обязательство — это «что сделать, к какому сроку, чего нельзя», а не разбор.
# Весь список открытых уезжает в системный промпт КАЖДЫЙ ход: 24.08 семнадцать
# записей весили 15 КБ, половина — в трёх эссе. Длинное вредно и по существу:
# главное условие тонет в объяснениях. Порог мягкий — не режем и не отказываем
# (обрезка молча потеряла бы условие вроде «квартиру до 27.08 не поднимать»),
# а жалуемся в лог. Правило: obyazatelstvo-ne-razbor.
OB_WHAT_LIMIT = 400


def add_obligation(what: str, who: str, due: str, source: str = "") -> str:
    """Обязательство. Проверяется механически, не поиском."""
    text = C.OBLIGATIONS.read_text(encoding="utf-8")
    next_id = _next_id(text, "ob")
    if len(what) > OB_WHAT_LIMIT:
        log("obligation_long",
            {"id": next_id, "chars": len(what), "limit": OB_WHAT_LIMIT,
             "hint": "перепиши в 2-3 строки, разбор — в факт или лог",
             "head": what[:120]})
    row = f"| {next_id} | {what} | {who} | {due} | open | {source} | 0 |\n"
    C.OBLIGATIONS.write_text(text.rstrip("\n") + "\n" + row, encoding="utf-8")
    return next_id


def set_obligation_status(ob_id: str, status: str,
                          due: str | None = None) -> dict:
    """Статус обязательства и, при переносе, новый срок.

    Перенос — это «open» поверх open ИЛИ overdue, а также смена срока у живой
    строки. Считать его только из состояния open было ошибкой: 26.08 три
    просроченных обязательства «перенесли» вызовом инструмента, и в файле
    остались старый срок и переносов=0 — наутро они снова стали overdue, а
    сигнал о повторном переносе потерялся; сроки пришлось править руками.
    Поэтому перенос просроченного без нового срока код не принимает вовсе:
    молча оставить срок в прошлом хуже, чем отказать.

    Возвращает словарь: ok, reason (почему отказ), was, due, deferrals,
    deferred. Раньше возвращался bool — причина отказа никуда не доходила.
    """
    text = C.OBLIGATIONS.read_text(encoding="utf-8")
    out, res = [], {"ok": False, "reason": f"нет такого обязательства: {ob_id}"}
    for line in text.splitlines():
        if line.startswith(f"| {ob_id} ") and not res["ok"]:
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            if len(cells) >= 7:
                was, was_due = cells[4], cells[3]
                if due is not None:
                    try:
                        new_due = date.fromisoformat(due)
                    except ValueError:
                        return {"ok": False, "reason":
                                f"срок не в формате ГГГГ-ММ-ДД: {due}"}
                    if status == "open" and new_due < today():
                        return {"ok": False, "reason":
                                f"новый срок {due} уже в прошлом — "
                                "перенос в прошлое смысла не имеет"}
                if status == "open" and was == "overdue" and due is None:
                    return {"ok": False, "reason":
                            f"{ob_id} просрочен (срок {was_due}): перенос без "
                            "нового срока не принимается — назови due=ГГГГ-ММ-ДД, "
                            "иначе строка снова станет overdue завтра"}
                deferred = was in ("open", "overdue") and (
                    status == "open" or (due is not None and due != was_due))
                if deferred:
                    cells[6] = str(int(cells[6] or 0) + 1)
                if due is not None:
                    cells[3] = due
                cells[4] = status
                line = "| " + " | ".join(cells) + " |"
                res = {"ok": True, "reason": "", "was": was, "due": cells[3],
                       "due_was": was_due, "status": status,
                       "deferred": deferred, "deferrals": int(cells[6] or 0)}
        out.append(line)
    if res["ok"]:
        C.OBLIGATIONS.write_text("\n".join(out) + "\n", encoding="utf-8")
        if res["deferred"]:
            log("obligation_deferred",
                {"id": ob_id, "was": res["was"], "due_was": res["due_was"],
                 "due": res["due"], "deferrals": res["deferrals"]})
    return res


def open_obligations() -> list[dict]:
    rows = []
    for line in C.OBLIGATIONS.read_text(encoding="utf-8").splitlines():
        if not line.startswith("| ob-"):
            continue
        c = [x.strip() for x in line.strip().strip("|").split("|")]
        if len(c) >= 7 and c[4] in ("open", "overdue"):
            rows.append({"id": c[0], "what": c[1], "who": c[2], "due": c[3],
                         "status": c[4], "source": c[5], "deferrals": c[6]})
    return rows


def due_or_overdue(on: date | None = None) -> list[dict]:
    """Обязательства, срок которых наступил: сегодня или раньше."""
    on = on or today()
    rows = []
    for o in open_obligations():
        try:
            due = date.fromisoformat(o["due"])
        except ValueError:
            continue
        if due <= on:
            rows.append(o)
    return rows


def morning_review_done_today() -> bool:
    """Был ли сегодня утренний разбор сроков. Считается по логу — его нельзя
    подделать и он переживает перезапуск: между ходами меня не существует."""
    return any("morning_review" in e["content"] for e in read_log_day(today())
               if e["type"] == "self")


def mark_morning_review(decisions: str) -> dict:
    """Отметить утренний разбор сроков сделанным.

    Разбор считается сделанным, только если в решениях назван КАЖДЫЙ id со
    сроком «сегодня или раньше». Иначе отметки нет и блок висит в промпте
    дальше: 25.08 ob-012 и ob-017 просрочились молча ровно потому, что
    «посмотрел реестр» и «решил по каждому» — разные вещи, и первое не
    проверялось ничем.
    """
    pending = due_or_overdue()
    missing = [o["id"] for o in pending if o["id"] not in decisions]
    if missing:
        return {"ok": False, "missing": missing, "total": len(pending)}
    log("self", {"morning_review": _cut_bytes(decisions, 3000),
                 "ids": [o["id"] for o in pending]})
    return {"ok": True, "missing": [], "total": len(pending)}


def _morning_block(hour: int | None = None) -> str:
    """Блок промпта «разбор сроков». Пуст всё время, кроме окна 08:00–23:00
    того дня, в котором разбора ещё не было. Пустой блок в промпт не едет.

    `hour` — только для самотеста: подменить время суток, не трогая часы машины.
    """
    if hour is None:
        hour = datetime.now(ZoneInfo(C.TZ)).hour
    if not (C.MORNING_HOUR <= hour < C.NIGHT_SILENCE_HOUR):
        return ""
    if morning_review_done_today():
        return ""
    rows = due_or_overdue()
    if not rows:
        return ""
    head = ("Разбора сроков сегодня ещё не было. Он делается ДО остальной\n"
            "работы: по каждой строке ниже — решение вслух Илье одной фразой\n"
            "(делаю сегодня / переношу, причина / снимаю, причина), в том же\n"
            "ходе close_obligation на снятые и перенесённые. Потом вызвать\n"
            "mcp__memory__morning_review с этими решениями — иначе блок\n"
            "останется висеть здесь весь день.\n\n")

    # Не влезаем в потолок — ужимаем ФОРМУЛИРОВКИ, а не список: обязательство,
    # которого не видно, ничем не отличается от незаписанного, и именно от
    # этого блок и заведён. Полный текст лежит в memory/obligations.md.
    limit = C.BLOCK_MAX_BYTES["morning"]
    for what_bytes in (160, 100, 60, 30):
        body = head + "\n".join(
            f"- [{o['id']}] срок {o['due']} · {o['status']} · {o['who']} — "
            f"{_cut_bytes(o['what'], what_bytes)}" for o in rows)
        if len(body.encode()) <= limit:
            break
    return body


def refresh_obligation_statuses() -> int:
    """Механическая проверка просрочек. Не полагается на интеллект."""
    changed = 0
    for ob in open_obligations():
        try:
            due = date.fromisoformat(ob["due"])
        except ValueError:
            continue
        if due < today() and ob["status"] == "open":
            if set_obligation_status(ob["id"], "overdue")["ok"]:
                changed += 1
    return changed


# --------------------------------------------------------------------------
# Сборка контекста
# --------------------------------------------------------------------------

def _read(path: Path, default: str = "") -> str:
    return path.read_text(encoding="utf-8") if path.exists() else default


def _first_line(body: str, limit: int = 130) -> str:
    """Первая осмысленная строка тела — аннотация для оглавления."""
    for line in body.splitlines():
        line = line.strip().lstrip("#").strip()
        if line and not line.startswith("---"):
            return line[:limit] + (" …" if len(line) > limit else "")
    return ""


def index_entries(d: Path, annotate: str = "name") -> list[dict]:
    """Оглавление каталога памяти: одна строка на запись, без содержимого.

    Ключевое решение архитектуры: в постоянный контекст едет КАРТА памяти
    (id, о чём, где лежит), а territory — сам текст — читается инструментом
    Read по указанному пути, когда он понадобился под конкретную задачу.
    Иначе объём промпта растёт линейно с объёмом памяти и однажды съедает всё.
    """
    if not d.exists():
        return []
    out = []
    for p in sorted(d.glob("*.md")):
        if p.name == "README.md":
            continue
        text = p.read_text(encoding="utf-8")
        meta, body = parse_frontmatter(text)
        if meta.get("status") in ("refuted", "retired", "deprecated"):
            continue
        note = meta.get(annotate, "").strip().strip('"') or _first_line(body)
        out.append({
            "id": meta.get("id", p.stem),
            "path": str(p.relative_to(C.ROOT)),
            "note": note,
            "extra": _first_line(body) if annotate != "name" else "",
            "kb": max(1, len(text.encode()) // 1024),
        })
    return out


def memory_index(note_bytes: int | None = None) -> str:
    """Оглавление памяти: строка на запись, тело — через Read.

    `note_bytes` режет ПОДПИСЬ записи, а не выкидывает саму запись. Когда
    оглавление упирается в свой потолок (C.BLOCK_MAX_BYTES["index"]), ужимать
    надо именно так: файл, которого нет в оглавлении, для меня не существует
    вовсе — я о нём не вспомню и его не открою, — а файл с укороченной
    подписью я всё равно найду по id и теме. Поэтому потеря подписи дешёвая,
    а потеря строки необратимая.
    """
    facts = index_entries(C.FACTS, "name")
    procs = index_entries(C.PROCEDURES, "trigger")

    def note(e: dict) -> str:
        return _cut_bytes(e["note"], note_bytes) if note_bytes else e["note"]

    lines = []
    if facts:
        lines.append("## Факты — memory/facts/<id>.md")
        lines += [f"- {e['id']} — {note(e)} [{e['kb']} КБ]" for e in facts]
    if procs:
        lines.append("\n## Процедуры — memory/procedures/<id>.md")
        lines += [f"- {e['id']} — когда: {note(e)} [{e['kb']} КБ]" for e in procs]
    return "\n".join(lines)


def _index_block(limit: int) -> str:
    """Оглавление, ужатое подписями до потолка блока.

    Сначала пробуем как есть, дальше подрезаем подпись всё короче. Ниже 40 байт
    не опускаемся: от подписи должно остаться хоть что-то узнаваемое, иначе
    оглавление превращается в список id и перестаёт работать как карта. Если и
    на 40 не влезло — значит записей стало столько, что пора не подписи резать,
    а полоть память; тогда сработает общая обрезка _fit и скажет об этом вслух.
    """
    for nb in (None, 110, 90, 70, 55, 40):
        text = memory_index(nb)
        if len(text.encode()) <= limit:
            return text
    return memory_index(40)


def _rule_text(body: str) -> str:
    """Формулировка правила одной строкой — то, что едет в промпт.

    «Почему» и «как применять» остаются в файле: они нужны, только когда
    правило применяется к спорному случаю.
    """
    m = re.search(r"\*\*Правило:\*\*\s*(.+?)(?=\n\*\*|\Z)", body, flags=re.S)
    return " ".join((m.group(1) if m else _first_line(body)).split())


def _when(scope: str, rule: str, limit: int = 100) -> str:
    """«Когда включается» одной фразой — для свёрнутого правила.

    Берётся первое предложение области: автор правила пишет его как «Любая
    правка X», «Все исходящие Y» — то есть ровно как признак входа в область.
    Остальные предложения области — это обычно исключения («не касается Z»),
    и они нужны уже после того, как файл открыт.

    Дальше отрезается перечисление примеров после двоеточия («Любая приёмка по
    числу, которое считает мой код: доля своего текста в письмах, покрытие
    проверками, дубли …»): вход в область распознаётся по голове фразы, а
    примеры до открытия файла ничего не решают и стоят половину строки. Голову
    короче 45 знаков не режем — там двоеточие обычно и есть весь смысл
    («Запись и правка обязательств: add_obligation …»).

    Остаток режется по границе слова: строка-указатель не должна стоить как
    само правило. Замер 25.08: 22 свёрнутых правила — 5123 байта до, 3705
    после, при том что примеры ушли только у самодостаточных формулировок.
    """
    first = re.split(r"(?<=[а-яa-z0-9)»/*])\.\s+", scope.strip())[0].strip()
    if not first:
        first = rule
    head = first.split(": ", 1)[0]
    if len(head) >= 45:
        first = head
    if len(first) > limit:
        first = first[:limit].rsplit(" ", 1)[0] + " …"
    return first


# Шапка свёрнутого эшелона. Отдельной константой, чтобы проверка в самотесте
# сверялась с тем же текстом, который видит модель.
FOLDED_HEADER = (
    "## Свёрнутые правила: узкая область, в промпт едет только признак входа.\n"
    "Работа попала в описанную область — ДО первого действия открой текст "
    "правила: Read memory/rules/<id>.md. Не помнишь правило наизусть — значит "
    "ты его ещё не прочитал, а не «оно не про это»."
)


def rules_digest(when_bytes: int = 100) -> str:
    """Правила в контексте двумя эшелонами (bl-036).

    Ежеходные (C.EVERY_TURN_RULES) — как раньше: формулировка плюс область.
    Это то, что применяется почти в каждом ходе или запрещает действие насмерть;
    такое обязано быть перед глазами, потому что момента «пойти прочитать» у
    него нет. Узкие — строкой «id + когда включается»: у них есть отчётливый
    вход в область, на котором открыть файл через Read естественно.

    Зачем: блок правил рос линейно с их числом и на 28 правилах весил 32,2 КБ —
    больше всех в промпте, — а применялась в конкретном ходе от силы половина.
    Файлы правил при этом не тронуты: правило остаётся полным на диске,
    меняется только его подача.

    `when_bytes` — потолок ПРИЗНАКА ВХОДА свёрнутого правила; им блок и
    ужимается, когда упирается в свой потолок (см. _rules_block).
    """
    full, folded = [], []
    for p in sorted(C.RULES.glob("*.md")) if C.RULES.exists() else []:
        if p.name == "README.md":
            continue
        text = p.read_text(encoding="utf-8")
        meta, body = parse_frontmatter(text)
        if meta.get("status") != "active":
            continue
        rid = meta.get("id", p.stem)
        rule = _rule_text(body)
        scope = meta.get("scope", "").strip().strip('"')
        if rid in C.EVERY_TURN_RULES:
            full.append(f"- [{rid}] {rule}\n  область: {scope}")
        else:
            folded.append(f"- [{rid}] когда: {_when(scope, rule, when_bytes)}")
    out = "\n".join(full)
    if folded:
        out += ("\n\n" if out else "") + FOLDED_HEADER + "\n" + "\n".join(folded)
    return out


def _rules_block(limit: int) -> str:
    """Блок правил, ужатый ПРИЗНАКАМИ ВХОДА до потолка блока (bl-125).

    До этого блок правил не ужимал себя вовсе: перерос потолок — и общая
    обрезка _fit резала его с хвоста, то есть выбрасывала свёрнутые строки
    целиком, по алфавиту с конца. Правило, которого нет в промпте даже
    строкой, для меня не существует: я не вспомню, что оно есть, и никогда не
    открою файл — а действовать оно при этом не перестало. Пометка «обрезано»
    об этом не спасает: она говорит, что хвост есть, но не какой.

    Поэтому ужимаемся тем же приёмом, что и оглавление памяти (_index_block):
    сначала режем ПОДПИСЬ, а строку сохраняем. Ежеходные правила не трогаются
    никогда — они и есть то, ради чего блок существует. Ниже 40 знаков признак
    входа не опускаем (в _when потолок считается в знаках, не в байтах): от
    него должно остаться узнаваемое начало фразы, иначе
    свёрнутый эшелон превращается в список id. Не влезло и на 40 — значит
    полоть надо не подписи, а состав ежеходного эшелона; тогда сработает _fit
    и скажет об этом вслух.
    """
    for nb in (100, 75, 55, 40):
        text = rules_digest(nb)
        if len(text.encode()) <= limit:
            return text
    return rules_digest(40)


def registry_digest() -> str:
    """Реестр в контексте — таблицей, а не целиком.

    Постоянно нужна одна вещь: список способностей со статусом и счётом, чтобы
    на вопрос «что ты умеешь» отвечать реестром, а не самоощущением (§10
    конституции). Разборы «что стоит за строками» и правила машинного счёта —
    это 10 КБ прозы, которая нужна раз в неделю: когда выносится вердикт по
    испытательному сроку или чинится счётчик. Они читаются файлом по надобности.
    """
    text = _read(C.REGISTRY)
    head, sep, _ = text.partition("## Что стоит за строками")
    if not sep:
        return text
    return (head.rstrip()
            + "\n\nЗа таблицей — разборы каждой способности (испытательные сроки, "
              "чем считается вызов и провал, чего числа не значат) и правила "
              "машинного счёта. В промпт они не едут: выносишь вердикт по "
              "способности или чинишь счётчик — открой memory/registry.md через Read.")


def _cut_bytes(text: str, limit: int) -> str:
    """Обрезать по БАЙТАМ, не порвав кириллицу, и честно пометить обрезку."""
    raw = text.encode("utf-8")
    if len(raw) <= limit:
        return text
    return raw[:limit].decode("utf-8", "ignore").rstrip() + " …"


def _event_limit(event_type: str) -> int:
    """Сколько байт события пускать в промпт — по типу записи.

    Замер 25.08 (bl-049): окно лога весило 20 КБ и вмещало 54 ПОСЛЕДНИХ
    события из 14 973 за неделю. Средняя запись действия — 514 байт, и почти
    весь её вес — полный текст команды или аргументов инструмента: heredoc с
    письмом, тело правила, git commit на полторы тысячи знаков. В следующем
    ходе от этой записи нужно одно: чем я работал и над чем. Полный текст
    никуда не девается — он в файле дня и достаётся через recall.
    Слова людей режутся мягче: что сказал Илья и что я ему ответил — это
    содержание, а не след инструмента.
    """
    if event_type == "action":
        return C.LOG_ACTION_BYTES
    if event_type in ("message_in", "message_out"):
        return C.LOG_MESSAGE_BYTES
    return C.LOG_EVENT_BYTES


def _format_log(events: list[dict]) -> str:
    lines = []
    for e in events:
        content = _cut_bytes(e["content"], _event_limit(e["type"]))
        lines.append(f"[{e['ts'][:16]}] {e['type']} ({e['actor']}): {content}")
    text = "\n".join(lines)
    # Считаем БАЙТЫ, а не символы: ограничение, в которое мы упираемся,
    # системное (MAX_ARG_STRLEN = 128 КБ на аргумент запуска), и оно в байтах.
    # На кириллице символ занимает два байта, поэтому потолок «в символах»
    # давал вдвое больший промпт, чем ожидалось, — и ходы падали с E2BIG.
    raw = text.encode("utf-8")
    if len(raw) > C.RAW_LOG_BYTES:
        raw = raw[-C.RAW_LOG_BYTES:]
        text = ("… (ранние события дня опущены, доступны поиском) …\n"
                + raw.decode("utf-8", "ignore"))
    return text


def _rollup_digest(path: Path) -> str:
    """Суть старой свёртки: заголовок и «одна фраза для памяти», не всё тело.

    Свёртка пишется по шаблону (core/night.py): заголовок «# Неделя …» и где-то
    в первых абзацах — предложение «Одна фраза для памяти: …», которое сам же
    свёрточный ход считает главным выводом периода. Берём ровно его — это уже
    сжатие, сделанное автором свёртки, грех его не использовать повторно.
    Шаблон не задан жёстко нигде, кроме промпта ночного хода, поэтому если
    фразы нет (старая свёртка, ручная правка), тихо падаем на первую
    содержательную строку тела — не роняем сборку контекста из-за формата.

    Дайджест ограничен C.ROLLUP_DIGEST_BYTES (31.08.2026, ob-100). До этого
    запасной путь был ограничен 300 знаками, а основной — ничем: «одна фраза для
    памяти» на деле бывает абзацем в пять строк (у 2026-W35 — 600 байт). Разница
    незаметна на одной старой неделе и решает всё на пятнадцати: дайджестов в
    блоке до 15 (3 недели и 12 месяцев), и неограниченный дайджест делает рост
    блока линейным по возрасту агента. Ограничение — не потеря: полный текст
    свёртки лежит файлом и читается по имени, о чём говорит хвост блока.
    """
    text = path.read_text(encoding="utf-8").strip()
    lines = text.splitlines()
    title = lines[0].lstrip("#").strip() if lines and lines[0].startswith("#") else path.stem
    m = re.search(r"Одна фраза для памяти:\s*(.+?)(?=\n\n|\Z)", text, flags=re.S)
    gist = " ".join(m.group(1).split()) if m else _first_line("\n".join(lines[1:]), limit=300)
    raw = gist.encode("utf-8")
    if len(raw) > C.ROLLUP_DIGEST_BYTES:
        gist = raw[:C.ROLLUP_DIGEST_BYTES].decode("utf-8", "ignore").rstrip() + "…"
    return f"# {title}\n{gist}"


def _rollup_head(path: Path) -> str:
    """Свежая недельная свёртка в промпте: голова по разделам, хвост — по имени.

    До 31.08.2026 свежая неделя ехала в промпт целиком и была его крупнейшей
    частью после правил и оглавления (13,5 КБ из 99,7). Замер цены хода
    (bl-155) показал, чего это стоит: промпт перечитывается на каждом шаге, при
    ходе в 20–25 шагов он даёт 80–85% всех токенов хода, то есть килобайт
    промпта — это двадцать оплаченных килобайт. Свёртка при этом история, а не
    действующее правило: её отсутствие в промпте не меняет поведения, только
    подробность памяти о прошлой неделе.

    Режем ЦЕЛЫМИ разделами (граница «\\n## »): обрывок абзаца посреди фразы
    хуже, чем честная строка «этот раздел лежит в файле». Имена выпавших
    разделов остаются в промпте вместе с путём — я знаю, что они есть, и могу
    поднять их через Read. Раздел, который не влез, пропускается, а следующий
    (если меньше) — берётся: порядок файла сохраняется, упаковка плотнее.
    Потолок — C.WEEK_ROLLUP_PROMPT_BYTES.
    """
    text = path.read_text(encoding="utf-8").strip()
    limit = C.WEEK_ROLLUP_PROMPT_BYTES
    if len(text.encode("utf-8")) <= limit:
        return text
    parts = re.split(r"\n(?=## )", text)
    head: list[str] = []
    dropped: list[str] = []
    used = 0
    for i, part in enumerate(parts):
        size = len(part.encode("utf-8")) + 1
        if head and used + size > limit:
            m = re.match(r"##\s*(.+)", part)
            dropped.append(m.group(1).strip() if m else f"часть {i + 1}")
            continue
        if not head and size > limit:
            # Шапка свёртки сама больше потолка — обрезаем по байтам, иначе
            # блок вылезет за свой потолок и его срежет общая обрезка _fit.
            part = part.encode("utf-8")[:limit].decode("utf-8", "ignore").rstrip() + "…"
            size = len(part.encode("utf-8")) + 1
        head.append(part)
        used += size
    if dropped:
        head.append("Разделы «" + "», «".join(dropped) + "» в промпт не едут — "
                    f"Read derived/rollups/weeks/{path.name}")
    return "\n".join(head)


def _rollups() -> str:
    """Свёртки в контексте: свежая неделя целиком, остальное — сутью.

    Свёртка — это история, а не действующее поведение агента (в отличие от
    правил): её ужатие не меняет то, как агент себя ведёт сейчас, только то,
    сколько подробностей о прошлом он держит в голове без чтения файла. Замер
    24.08 (bl-036): этот блок весил 12,5 КБ уже на одной-единственной неделе
    жизни агента — целиком грузились последние 4 недели и 12 месяцев, и это
    растёт линейно с возрастом агента. Текущая (самая свежая) неделя нужна
    подробно — на неё чаще всего опираются в разговоре о «на этой неделе»;
    более старые нужны редко и только сутью: что произошло, каким выводом
    кончилось. Полные тексты — в derived/rollups/weeks/ и
    derived/rollups/months/, поднимаются инструментом Read по имени файла,
    когда понадобилась именно та неделя или месяц.
    """
    weeks = sorted((C.ROLLUPS / "weeks").glob("*.md")) if (C.ROLLUPS / "weeks").exists() else []
    months = sorted((C.ROLLUPS / "months").glob("*.md")) if (C.ROLLUPS / "months").exists() else []
    if not weeks and not months:
        return ""
    parts = [_rollup_digest(p) for p in weeks[-4:-1]]
    parts += [_rollup_digest(p) for p in months[-12:]]
    if weeks:
        parts.append(_rollup_head(weeks[-1]))
    parts.append("Полные свёртки — derived/rollups/weeks/ и derived/rollups/months/, "
                  "читаются через Read по имени файла (номер недели или YYYY-MM).")
    return "\n\n".join(parts)


def _fit(body: str, limit: int, hint: str) -> str:
    """Уложить блок промпта в его потолок, не соврав о том, что не поместилось.

    Общий ограничитель для всех блоков (bl-095). До него потолок был только у
    правил и у лога, а остальные шесть блоков росли свободно: за сутки промпт
    прибавлял ~5 КБ и дважды за трое суток подходил к порогу, после чего
    приходилось убираться руками. Уборка возвращается через двое суток;
    ограничитель — нет.

    Режем с ХВОСТА и по границе строки: у блоков промпта голова важнее хвоста
    (свежее «сейчас», ближние сроки, начало таблицы). Обрезка всегда помечается
    строкой с подсказкой, где лежит целое, — блок, который молча стал короче,
    хуже блока, которого нет: он выглядит полным.
    """
    raw = body.encode("utf-8")
    if len(raw) <= limit:
        return body
    mark = f"\n… обрезано по потолку блока. Целиком: {hint}"
    room = max(0, limit - len(mark.encode("utf-8")))
    cut = raw[:room].decode("utf-8", "ignore")
    if "\n" in cut:
        cut = cut[:cut.rindex("\n")]
    return cut.rstrip() + mark


def _obligations_block() -> str:
    """Обязательства строками, формулировка каждой — до C.OBLIGATION_WHAT_BYTES.

    Правило obyazatelstvo-ne-razbor («обязательство — не разбор») существовало
    только в памяти и потому не соблюдалось: на 26.08 средняя формулировка 415
    байт при норме 350, рекорд 1089. Границу проверяет код — здесь он её и
    проверяет, на выходе в промпт. Файл memory/obligations.md не трогается:
    хвост остаётся на диске и достаётся чтением, а «…» в промпте показывает,
    что он там есть.

    Блок УЖИМАЕТ ФОРМУЛИРОВКИ, а не список (bl-146, 30.08.2026). Раньше он
    рос свободно и на 29 обязательствах перевалил потолок на 29 байт — общий
    ограничитель отрезал ему хвост, и последние строки (ближние сроки — они
    в конце, если сортировать по дате) в промпт не доезжали вовсе. Пропавшее
    обязательство ничем не отличается от незаписанного, ради чего блок и
    заведён. Поэтому теперь: сначала теснее пишутся ДАЛЬНИЕ (срок ещё не
    наступил), потом все подряд, и только если не помогло и это — режется
    хвост. Порядок строк — по сроку: горит первым, читается первым.
    """
    rows = sorted(open_obligations(), key=lambda o: (o["due"], o["id"]))
    if not rows:
        return "нет открытых"
    limit = C.BLOCK_MAX_BYTES["obligations"]
    now = today()

    def _urgent(o: dict) -> bool:
        try:
            return date.fromisoformat(o["due"]) <= now
        except ValueError:
            return True  # битая дата — считаем срочной, разберусь глазами

    def _line(o: dict, width: int) -> str:
        return (f"- [{o['id']}] {_cut_bytes(o['what'], width)} — {o['who']} — "
                f"срок {o['due']} — {o['status']}, переносов: {o['deferrals']}")

    full = C.OBLIGATION_WHAT_BYTES
    foot = ("\n(формулировки ужаты под потолок блока — "
            "полный текст в memory/obligations.md)")
    body = ""
    # Пары «сколько байт срочным / сколько дальним». Первая — как было.
    for near, far in ((full, full), (full, 200), (full, 100), (250, 80),
                      (150, 60), (80, 40), (40, 20)):
        tight = (near, far) != (full, full)
        body = "\n".join(_line(o, near if _urgent(o) else far) for o in rows)
        if tight:
            body += foot
        if len(body.encode()) <= limit:
            break
    return body


# Блоки промпта: ключ (он же ключ потолка в C.BLOCK_MAX_BYTES), заголовок,
# как собрать, куда идти за обрезанным хвостом. Список — единственный источник
# правды о составе промпта: по нему и собирается контекст, и считается бюджет
# в самотесте, чтобы «что в промпте» и «что мы меряем» не разъезжались.
BLOCKS: tuple[tuple[str, str, str], ...] = (
    ("morning", "РАЗБОР СРОКОВ — первое дело дня, до всего остального",
     "memory/obligations.md"),
    ("now", "СЕЙЧАС (memory/now.md)", "memory/now.md"),
    ("obligations", "ОБЯЗАТЕЛЬСТВА — проверять механически, не искать",
     "memory/obligations.md"),
    ("questions",
     "КОПИЛКА ВОПРОСОВ (memory/questions.md) — отложены, потому что были\n"
     "заданы в глухое время. Илья на связи — задать их пачкой одним\n"
     "сообщением и пометить: .venv/bin/python -m core.questions --asked",
     "memory/questions.md"),
    ("rules",
     "ПРАВИЛА. Действуют все до единого. Сверху — те, что применяются\n"
     "почти в каждом ходе: формулировка и область. Ниже, под заголовком\n"
     "«Свёрнутые», — правила узкой области: дан только id и признак входа,\n"
     "текст в промпт не едет. Работа попала в такую область — сначала\n"
     "Read memory/rules/<id>.md, потом действие. Разбор спорного случая\n"
     "(«почему» и «как применять») читается из файла для любого правила.",
     "memory/rules/"),
    ("registry", "РЕЕСТР СПОСОБНОСТЕЙ — отвечая «что умею», брать отсюда",
     "memory/registry.md"),
    ("index",
     "ОГЛАВЛЕНИЕ ПАМЯТИ — не содержимое, а карта.\n"
     "Тема касается задачи — открой файл через Read, не гадай по строке.\n"
     "Не нашёл нужного в оглавлении — recall по логу и фактам.",
     "ls memory/facts/ и memory/procedures/"),
    ("log", "ЛОГ ЗА ПОСЛЕДНИЕ ДНИ (хвост; раньше — recall)", "recall по логу"),
    ("rollups", "СВЁРТКИ (недели и месяцы)", "derived/rollups/"),
)


# Блоки со СКОЛЬЗЯЩИМ окном: они упираются в свой потолок каждый день по
# устройству, а не от запущенности. Окно лога — это последние N байт событий;
# «перерос» для него нормальное состояние, и полоть там нечего. Такие блоки не
# попадают в сигнал «пора полоть», иначе он звенел бы всегда и его перестали бы
# слышать — а тогда он не отличит рабочий день от настоящей проблемы.
SLIDING_BLOCKS = frozenset({"log"})


def _block_body(key: str, raw: bool = False) -> str:
    """Тело блока. `raw=True` — до собственного ужатия блока: нужно, чтобы
    видеть, что блок ПЕРЕРОС потолок, а не только что он в него уложен."""
    # raw — «без потолка вовсе»: не число из конфига, а заведомо недостижимое,
    # иначе сырой размер сам оказался бы обрезан и врал бы про перерост.
    limit = 10 ** 9 if raw else C.BLOCK_MAX_BYTES[key]
    if key == "morning":
        return _morning_block()
    if key == "now":
        return _read(C.NOW)
    if key == "obligations":
        return _obligations_block()
    if key == "questions":
        return questions.digest()
    if key == "rules":
        return _rules_block(limit)
    if key == "registry":
        return registry_digest()
    if key == "index":
        return _index_block(limit)
    if key == "log":
        return _format_log(drop_noise(read_log_range(C.RAW_LOG_DAYS)))
    if key == "rollups":
        return _rollups()
    raise KeyError(key)


def context_blocks() -> list[tuple[str, str, str]]:
    """Готовые блоки промпта: (ключ, заголовок, тело в пределах потолка)."""
    out = []
    for key, title, hint in BLOCKS:
        body = _block_body(key).strip()
        if not body:
            continue
        out.append((key, title, _fit(body, C.BLOCK_MAX_BYTES[key], hint)))
    return out


def context_budget() -> dict:
    """Разбивка промпта по блокам и худший случай — для самотеста и отчётов.

    `worst` — сколько промпт займёт, если каждый блок разом упрётся в свой
    потолок. Именно это число и надо держать ниже бюджета: тогда рост памяти
    физически не может отъесть у хода место, а самотест краснеет не от рабочего
    дня, а от неправильно расписанного бюджета.

    Бюджет с 26.08.2026 (bl-078) считается от ОКНА МОДЕЛИ в токенах, а не от
    MAX_ARG_STRLEN: потолка argv нет с тех пор, как промпт уехал в файл. Байты
    здесь остались единицей измерения — их я мерю сам и мгновенно, — а перевод
    в токены идёт через C.BYTES_PER_TOKEN, число заведомо заниженное (то есть
    строгое) и уточняемое ночным замером.
    """
    blocks = context_blocks()
    now_size = {k: len(b.encode()) for k, _, b in blocks}
    # Сырой размер — до ужатия. Меряться с потолком должен именно он: тело в
    # `blocks` уже урезано ограничителем и потому влезает ВСЕГДА, и проверка
    # «блок в потолке» по нему была бы зелёной по построению, то есть врала бы.
    raw_size = {k: len(_block_body(k, raw=True).strip().encode()) for k, _, _ in blocks}
    frame = sum(len(f"# {t}\n\n\n".encode()) for _, t, _ in blocks)
    const = len(constitution().encode())
    worst = sum(C.BLOCK_MAX_BYTES.values()) + const + frame
    current = sum(now_size.values()) + const + frame
    return {
        "blocks": now_size,
        "raw": raw_size,
        "limits": dict(C.BLOCK_MAX_BYTES),
        "over": [k for k, v in raw_size.items()
                 if v > C.BLOCK_MAX_BYTES[k] and k not in SLIDING_BLOCKS],
        "constitution": const,
        "frame": frame,
        "current": current,
        "worst": worst,
        "budget": C.PROMPT_BUDGET_BYTES,
        # То же самое в токенах — в них считает модель, и в них же расписан
        # бюджет. Байты остаются рабочей единицей, токены — смыслом.
        "current_tokens": int(current / C.BYTES_PER_TOKEN),
        "worst_tokens": int(worst / C.BYTES_PER_TOKEN),
        "budget_tokens": C.PROMPT_TOKENS_BUDGET,
        "window_tokens": C.MODEL_CONTEXT_TOKENS,
        "bytes_per_token": C.BYTES_PER_TOKEN,
    }


def constitution() -> str:
    return _read(C.CONSTITUTION)


def build_context(fast: bool = False) -> str:
    """Раздел 10 MEMORY.md: постоянный объём + оглавление вместо содержимого.

    В промпт едет только то, что растёт медленно: снимок момента, обязательства,
    суть правил, реестр способностей, окно сырого лога, свёртки. Факты и
    процедуры — оглавлением: строка на запись и путь к файлу. Тело записи
    поднимается инструментом Read под конкретную задачу.

    До 23.08.2026 сюда сваливалось всё содержимое памяти целиком, и промпт
    за два дня жизни дошёл до 135 КБ при системном потолке 131 КБ — ходы
    перестали начинаться вовсе. Рост памяти не должен упираться в размер окна.

    Состав блоков и потолок каждого — BLOCKS здесь и C.BLOCK_MAX_BYTES.
    Блок, переросший потолок, режется с честной пометкой (bl-095): рост памяти
    больше не может дотолкать промпт до системного лимита.
    """
    if fast:
        return ""  # быстрый путь: только конституция, она передаётся отдельно

    refresh_obligation_statuses()
    return "\n\n".join(f"# {title}\n{body}" for _, title, body in context_blocks())


def search(query: str, days: int = 400, limit: int = 60) -> list[str]:
    """Поиск по логу и фактам — длинный хвост, когда контекста не хватило.

    Пока это честный полнотекстовый перебор: на объёмах первого года он быстрее
    и отлаживается лучше, чем индекс. Когда станет медленно — сюда встанет
    SQLite FTS5, а сигнатура не изменится.

    Сравниваются не подстроки, а основы слов (core/morph.py): запрос «письмо»
    находит «письма» и «письмам». Порядок выдачи — по трём вещам: сколько слов
    запроса нашлось (главное), насколько свежая запись и что это за источник.
    Записи памяти идут выше лога: факт остаётся верным, событие устаревает.
    """
    q = morph.prepare(query)
    if not (q[0] or q[1]):
        return []
    hits: list[tuple[float, str]] = []
    today_ord = today().toordinal()

    for e in drop_noise(read_log_range(days)):
        # Ищем ровно по тому куску, который потом покажем. Запись лога — это
        # след, а не память: у action туда попадает весь input инструмента,
        # включая тело записываемого файла. Из-за этого каждый факт лежал в
        # логе вторым экземпляром и вытеснял из выдачи сам файл, ради которого
        # искали. Совпадение, невидимое в показанных 400 знаках, ответом не
        # является — оно только сдвигает вниз то, что является.
        s = morph.score(f"{e['type']} {e['content'][:400]}", q)
        if not s:
            continue
        # Свежесть — только довесок к полноте совпадения, а не замена ей:
        # сегодняшняя запись получает +2, полугодовая — почти ноль.
        try:
            age = today_ord - date.fromisoformat(e["ts"][:10]).toordinal()
        except ValueError:
            age = 180
        s += 2.0 * max(0.0, 1.0 - age / 180.0)
        hits.append((s, f"[{e['ts'][:10]}] {e['type']}: {e['content'][:400]}"))

    # Записи памяти ищутся вместе с логом и возвращают ПУТЬ: контекст теперь
    # держит оглавление, а не содержимое, и попадание должно говорить,
    # какой файл открыть целиком.
    for d, kind in ((C.FACTS, "факт"), (C.PROCEDURES, "процедура"), (C.RULES, "правило")):
        for p in sorted(d.glob("*.md")) if d.exists() else []:
            if p.name == "README.md":
                continue
            text = p.read_text(encoding="utf-8")
            meta, body = parse_frontmatter(text)
            rel = p.relative_to(C.ROOT)
            title = meta.get("name") or meta.get("trigger", "").strip('"') or p.stem
            s = morph.score(text, q)
            if not s:
                continue
            # Попадание в заголовок весит больше, чем где-то в теле: файл
            # называется так не случайно.
            s += 3.0 + 0.3 * morph.score(f"{title} {p.stem.replace('-', ' ')}", q)
            hits.append((s, f"[{kind} → Read {rel}] {title}: {_first_line(body, 200)}"))

    hits.sort(key=lambda x: -x[0])
    # Хвост, где нашлось одно слово из трёх, — это не ответ, а шум: он
    # вытесняет из выдачи то, ради чего искали. Порог относительный: если
    # ничего сильного не нашлось, слабое всё равно покажется.
    if hits:
        floor = hits[0][0] * 0.6
        hits = [h for h in hits if h[0] >= floor]
    return [h[1] for h in hits[:limit]]


# --------------------------------------------------------------------------
# Очередь собственных задач: чем агент занят, когда Илья молчит
# --------------------------------------------------------------------------
#
# Формат такой же табличный, как у обязательств, и по той же причине: очередь
# должна перечитываться механически, а не находиться поиском.
#
# | id | задача | зачем | приоритет | статус | циклов | чекпоинт |
#
# приоритет: число, меньше — раньше.
# статус: open | wip | blocked | done | dropped | sleep:ГГГГ-ММ-ДД.
# blocked — ждёт обязательства (ob-NNN названо в чекпоинте), очередь её не
# берёт до его закрытия. sleep:ДАТА — задача сделана на сегодня и должна
# вернуться в очередь не раньше названного дня (ежедневные дела: разбор
# входящих, пересборка истории). До этого дня очередь её не видит.
# «циклов» — сколько фоновых ходов уже потрачено. Три цикла без артефакта
# означают, что задача не по зубам в этой форме: она уходит вниз очереди.

BACKLOG_HEADER = (
    "| id | задача | зачем | приоритет | статус | циклов | чекпоинт |\n"
    "|----|--------|-------|-----------|--------|--------|----------|\n")

# Восьмая колонка — метка проекта (раздел 18 документа «Джарвис — команда и
# проекты», job-090). НЕОБЯЗАТЕЛЬНАЯ и не в шапке: старые строки (7 колонок)
# остаются как есть и значат «без проекта», ничего не меняется в их
# поведении. Строка получает метку только через backlog_set_project() —
# тогда у неё становится 8 колонок. Это тот же приём, что уже используют
# core/jobs.py (поле "project" в job.json) и core.agent.project_scope
# (переменная JARVIS_PROJECT): не задано — ведёт себя как раньше.
_PROJECT_RE = re.compile(r"^[a-z][a-z0-9_-]*$")

BACKLOG_STATUSES = ("open", "wip", "blocked", "done", "dropped")

_SLEEP_RE = re.compile(r"^sleep:(\d{4}-\d{2}-\d{2})$")


def sleep_until(status: str) -> date | None:
    """Дата пробуждения задачи со статусом sleep:ГГГГ-ММ-ДД, иначе None.

    Ежедневное дело (разбор входящих, пересборка истории) не имеет конца:
    статус done для него — ложь, а wip держит его в очереди весь день, и
    фоновый ход берёт его снова через десять минут после прогона (27.08:
    два хода подряд на bl-054, второй — дубль первого). sleep:ДАТА говорит
    «на сегодня сделано, вернись такого-то числа» и переносится машинно,
    а не памятью агента, которой между ходами нет.
    """
    m = _SLEEP_RE.match((status or "").strip())
    if not m:
        return None
    try:
        return date.fromisoformat(m.group(1))
    except ValueError:
        return None


def _cell(text: str, limit: int = 1200) -> str:
    """Текст, безопасный для клетки таблицы.

    Литеральный «|» внутри задачи или «зачем» съезжает всю строку: парсер
    режет её по этому знаку, и в поле приоритета оказывается чужой текст.
    Экранирование не спасёт — строка режется до всякого разбора.
    """
    return text.replace("|", "/").replace("\n", " ").strip()[:limit]


def backlog_lint(path=None) -> list[str]:
    """Проверка формата очереди. Пустой список — таблица цела.

    Очередь читается кодом, а правится руками, поэтому формат — не косметика:
    съехавшая строка означает мусор в приоритете или статусе, и фоновый ход
    берёт не ту задачу либо не берёт никакую.
    """
    p = path or C.BACKLOG
    if not p.exists():
        return ["файла очереди нет"]
    problems: list[str] = []
    seen: set[str] = set()
    header = False
    for n, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
        if line.startswith("| id |"):
            header = True
            continue
        if not line.startswith("| bl-"):
            continue
        c = [x.strip() for x in line.strip().strip("|").split("|")]
        tid = c[0]
        if len(c) not in (7, 8):
            problems.append(
                f"строка {n} ({tid}): колонок {len(c)}, а нужно 7 (или 8 с "
                "меткой проекта) — скорее всего «|» внутри текста")
            continue
        if not re.fullmatch(r"bl-\d{3,}", tid):
            problems.append(f"строка {n}: id «{tid}» не вида bl-NNN")
        if not re.fullmatch(r"\d+", c[3]):
            problems.append(f"строка {n} ({tid}): приоритет «{c[3][:40]}» — не число")
        if c[4] not in BACKLOG_STATUSES and sleep_until(c[4]) is None:
            if c[4].startswith("sleep:"):
                problems.append(
                    f"строка {n} ({tid}): дата пробуждения «{c[4][:40]}» — "
                    "нужно sleep:ГГГГ-ММ-ДД")
            else:
                problems.append(
                    f"строка {n} ({tid}): статус «{c[4][:40]}» вне списка")
        if not re.fullmatch(r"\d+", c[5]):
            problems.append(f"строка {n} ({tid}): циклов «{c[5][:40]}» — не число")
        if len(c) == 8 and c[7] and not _PROJECT_RE.fullmatch(c[7]):
            problems.append(
                f"строка {n} ({tid}): метка проекта «{c[7][:40]}» — "
                "нужны строчные латинские буквы, цифры, - и _")
        if tid in seen:
            problems.append(f"строка {n}: id {tid} повторяется")
        seen.add(tid)
    if not header:
        problems.append("нет шапки таблицы")
    return problems


def _backlog_rows() -> list[dict]:
    if not C.BACKLOG.exists():
        return []
    rows = []
    for line in C.BACKLOG.read_text(encoding="utf-8").splitlines():
        if not line.startswith("| bl-"):
            continue
        c = [x.strip() for x in line.strip().strip("|").split("|")]
        if len(c) >= 7:
            rows.append({"id": c[0], "what": c[1], "why": c[2], "prio": c[3],
                         "status": c[4], "cycles": c[5], "checkpoint": c[6],
                         "project": c[7] if len(c) >= 8 else ""})
    return rows


def backlog_open() -> list[dict]:
    def key(r):
        try:
            return (int(r["prio"]), int(r["cycles"]))
        except ValueError:
            return (99, 0)
    now = today()
    live = []
    for r in _backlog_rows():
        if r["status"] in ("open", "wip"):
            live.append(r)
            continue
        wake = sleep_until(r["status"])
        if wake is not None and wake <= now:
            live.append(r)
    return sorted(live, key=key)


def backlog_unblock_ready() -> tuple[list[str], list[str]]:
    """Разбор статуса blocked. Возвращает (освобождённые, беспризорные).

    blocked — задача, которую нельзя делать, пока не закрыто обязательство:
    она ждёт не меня, а Илью или внешнее событие. Очередь её не берёт, потому
    что backlog_open() пускает только open и wip.

    Чтобы blocked не стал тихим кладбищем, условие называется машиночитаемо —
    ссылкой ob-NNN в чекпоинте или в тексте задачи. Все названные обязательства
    закрыты — задача сама возвращается в open. Ни одной ссылки нет — задачу
    никто не разблокирует никогда, и об этом надо сказать вслух, а не молчать.
    """
    open_ids = {o["id"] for o in open_obligations()}
    freed, orphan = [], []
    for r in _backlog_rows():
        if r["status"] != "blocked":
            continue
        waits = set(re.findall(r"\bob-\d+\b", f"{r['checkpoint']} {r['what']}"))
        if not waits:
            orphan.append(r["id"])
        elif not (waits & open_ids):
            try:
                backlog_update(r["id"], status="open")
            except ValueError:
                # Строка съехала — освободить её нельзя, но и весь разбор
                # blocked из-за одной битой строки ронять нельзя. Такая задача
                # тоже никем не разблокируется, и это должно быть слышно.
                orphan.append(r["id"])
                continue
            freed.append(r["id"])
    return freed, orphan


def backlog_top() -> dict | None:
    rows = backlog_open()
    return rows[0] if rows else None


def _backlog_write(text: str) -> None:
    """Запись очереди с самопроверкой и откатом.

    Битая строка не видна в момент правки: она живёт до следующего самотеста,
    а до тех пор статус и чекпоинт читаются не из тех полей и задача молча
    возвращается в очередь либо теряется (bl-099, два хода подряд 25–26.08).
    Поэтому после каждой записи таблица проверяется линтером, и если претензий
    стало больше, чем было, файл возвращается как был, а вызов падает.
    """
    before = backlog_lint() if C.BACKLOG.exists() else []
    was = C.BACKLOG.read_text(encoding="utf-8") if C.BACKLOG.exists() else None
    C.BACKLOG.write_text(text, encoding="utf-8")
    after = backlog_lint()
    fresh = [p for p in after if p not in before]
    if fresh:
        if was is None:
            C.BACKLOG.unlink()
        else:
            C.BACKLOG.write_text(was, encoding="utf-8")
        raise ValueError("правка ломает очередь, откатано: " + "; ".join(fresh))


def backlog_intro_set(text: str) -> bool:
    """Заменить пояснение НАД таблицей очереди, не касаясь ни одной строки.

    Edit по backlog.md отбит целиком (bl-102) — и вместе со строками таблицы
    стал недоступен текст вокруг неё, то есть инструкция, которую я же и
    читаю перед правкой очереди. 27.08 понадобилось описать в ней новый
    статус sleep — и выяснилось, что записать это некуда: шлюз отбивает файл,
    а API умеет только строки. Здесь таблица не пересобирается, а переносится
    как есть; проверка _backlog_write всё равно откатит правку, если строки
    пострадают.
    """
    if not C.BACKLOG.exists():
        return False
    lines = C.BACKLOG.read_text(encoding="utf-8").splitlines()
    idx = next((i for i, ln in enumerate(lines) if ln.startswith("| id |")), None)
    if idx is None:
        return False
    _backlog_write(text.rstrip("\n") + "\n\n" + "\n".join(lines[idx:]) + "\n")
    return True


def backlog_add(what: str, why: str, prio: int = 5, project: str = "") -> str:
    """project — необязательная метка (см. комментарий у BACKLOG_HEADER):
    не задана — строка из семи колонок, как раньше; задана — из восьми."""
    C.BACKLOG.parent.mkdir(parents=True, exist_ok=True)
    text = C.BACKLOG.read_text(encoding="utf-8") if C.BACKLOG.exists() else BACKLOG_HEADER
    next_id = _next_id(text, "bl")
    project = (project or "").strip()
    if project and not _PROJECT_RE.fullmatch(project):
        raise ValueError(
            f"метка проекта «{project}» — нужны строчные латинские буквы, "
            "цифры, - и _")
    row = (f"| {next_id} | {_cell(what)} | {_cell(why)} | {int(prio)} | open | 0 | — |"
           if not project else
           f"| {next_id} | {_cell(what)} | {_cell(why)} | {int(prio)} | open | 0 | — | {project} |")
    _backlog_write(text.rstrip("\n") + "\n" + row + "\n")
    return next_id


def backlog_update(task_id: str, status: str | None = None,
                   checkpoint: str | None = None, bump_cycle: bool = False,
                   prio: int | None = None) -> bool:
    if not C.BACKLOG.exists():
        return False
    # Незнакомый статус не отбивался ничем: он молча ложился в клетку, и
    # backlog_open переставал видеть задачу — та исчезала из очереди, не
    # будучи ни сделанной, ни брошенной. Лучше упасть здесь.
    if status is not None and status not in BACKLOG_STATUSES \
            and sleep_until(status) is None:
        raise ValueError(
            f"статус «{status}» не из списка: {', '.join(BACKLOG_STATUSES)} "
            "или sleep:ГГГГ-ММ-ДД (ежедневное дело, сделанное на сегодня)")
    out, found = [], False
    for line in C.BACKLOG.read_text(encoding="utf-8").splitlines():
        if line.startswith(f"| {task_id} "):
            c = [x.strip() for x in line.strip().strip("|").split("|")]
            # Семь колонок — обычная строка, восемь — с меткой проекта
            # (8-я = project, необязательная). Другое число — строка уже
            # съехала, и её поля значат не то, что подписано в шапке.
            # Править её по индексам нельзя: статус ляжет в текст задачи, а
            # чекпоинт — в никуда. Гадать, какая колонка лишняя, тоже нельзя —
            # только сказать вслух.
            if len(c) not in (7, 8):
                raise ValueError(
                    f"строка {task_id} битая: колонок {len(c)}, а нужно 7 "
                    "(или 8 с меткой проекта). Сначала собери её заново: "
                    "core.memory.backlog_fix_row("
                    f"'{task_id}', [id, что, зачем, приоритет, статус, циклы, "
                    "чекпоинт]), потом обновляй.")
            if len(c) >= 7:
                if prio is not None:
                    c[3] = str(prio)
                if status:
                    c[4] = status
                if bump_cycle:
                    c[5] = str(int(c[5] or 0) + 1)
                if checkpoint:
                    c[6] = _cell(checkpoint, 400)
                line = "| " + " | ".join(c) + " |"
                found = True
        out.append(line)
    _backlog_write("\n".join(out) + "\n")
    return found


def backlog_edit(task_id: str, what: str | None = None,
                 why: str | None = None) -> bool:
    """Правка текста задачи и «зачем» — то, чего backlog_update не умеет.

    Раньше это делалось Edit'ом по файлу, и вместе с текстом в ту же правку
    заезжали статус и чекпоинт — семь колонок сбивались молча (bl-099, bl-102).
    Здесь колонки не собираются вручную: меняются ровно два поля, остальные
    переносятся как есть, а «|» внутри текста обезвреживается _cell.
    """
    if not C.BACKLOG.exists() or (what is None and why is None):
        return False
    out, found = [], False
    for line in C.BACKLOG.read_text(encoding="utf-8").splitlines():
        if line.startswith(f"| {task_id} "):
            c = [x.strip() for x in line.strip().strip("|").split("|")]
            if len(c) not in (7, 8):
                raise ValueError(
                    f"строка {task_id} битая: колонок {len(c)}, а нужно 7 "
                    "(или 8 с меткой проекта). Сначала собери её заново: "
                    "core.memory.backlog_fix_row(...)")
            if what is not None:
                c[1] = _cell(what)
            if why is not None:
                c[2] = _cell(why)
            line = "| " + " | ".join(c) + " |"
            found = True
        out.append(line)
    _backlog_write("\n".join(out) + "\n")
    return found


def backlog_fix_row(task_id: str, cells: list[str]) -> bool:
    """Аварийная пересборка съехавшей строки: ровно семь готовых полей.

    Единственный законный способ починить строку, у которой колонок не семь.
    Ручной Edit по файлу для этого больше не годится: он же и был причиной
    поломки. Здесь число полей проверяется до записи, каждое чистится _cell,
    а _backlog_write откатит правку, если после неё линтер станет ругаться
    сильнее прежнего.
    """
    if not C.BACKLOG.exists():
        return False
    if len(cells) not in (7, 8):
        raise ValueError(f"нужно 7 полей (или 8 с меткой проекта), дано "
                         f"{len(cells)}: id, что, зачем, приоритет, статус, "
                         "циклы, чекпоинт[, проект]")
    if str(cells[0]).strip() != task_id:
        raise ValueError(f"первое поле — id задачи: ждал {task_id}, "
                         f"дано «{str(cells[0])[:40]}»")
    row = "| " + " | ".join(_cell(str(x)) for x in cells) + " |"
    out, found = [], False
    for line in C.BACKLOG.read_text(encoding="utf-8").splitlines():
        if line.startswith(f"| {task_id} ") or line.startswith(f"|{task_id} "):
            line, found = row, True
        out.append(line)
    if not found:
        return False
    _backlog_write("\n".join(out) + "\n")
    return True


def backlog_set_project(task_id: str, project: str) -> bool:
    """Проставить (или снять, project="") метку проекта на существующей
    строке. Единственный законный способ добавить восьмую колонку задним
    числом — руками правится только текст вокруг таблицы (backlog_intro_set),
    строки таблицы правит только код (см. шапку файла)."""
    project = (project or "").strip()
    if project and not _PROJECT_RE.fullmatch(project):
        raise ValueError(
            f"метка проекта «{project}» — нужны строчные латинские буквы, "
            "цифры, - и _")
    if not C.BACKLOG.exists():
        return False
    out, found = [], False
    for line in C.BACKLOG.read_text(encoding="utf-8").splitlines():
        if line.startswith(f"| {task_id} "):
            c = [x.strip() for x in line.strip().strip("|").split("|")]
            if len(c) not in (7, 8):
                raise ValueError(
                    f"строка {task_id} битая: колонок {len(c)}, а нужно 7 "
                    "(или 8 с меткой проекта). Сначала собери её заново: "
                    "core.memory.backlog_fix_row(...)")
            c = c[:7]                # без старой метки, если была
            if project:
                c.append(project)
            line = "| " + " | ".join(c) + " |"
            found = True
        out.append(line)
    if not found:
        return False
    _backlog_write("\n".join(out) + "\n")
    return True


def backlog_by_project(project: str, open_only: bool = False) -> list[dict]:
    """Задачи одного проекта. open_only=True — только то, что реально видит
    очередь (open/wip/проснувшийся sleep), как backlog_open()."""
    rows = backlog_open() if open_only else _backlog_rows()
    return [r for r in rows if r.get("project") == project]


def self_turns_today() -> int:
    """Сколько фоновых ходов уже потрачено сегодня. Бюджет — не на усмотрение
    модели: он считается по логу, который подделать нельзя."""
    return sum(1 for e in read_log_day(today())
               if e["type"] == "self" and "self_turn" in e["content"])


def initiative_count_today() -> int:
    return sum(1 for e in read_log_day(today())
               if e["type"] == "message_out" and e.get("actor") == "agent-initiative")
