"""Главный цикл на Claude Agent SDK.

Работает через подписку Claude (Max), API-ключ не нужен: SDK ходит через
локально установленный и залогиненный Claude Code.

Поведение задаётся конституцией, а не этим файлом. Здесь только механика:
классифицировать путь, собрать контекст, разрешить или запретить действие,
записать всё в лог.
"""

import ast
import asyncio
import hashlib
import math
import os
import re
import sys
import time
from datetime import timedelta
from pathlib import Path

from claude_agent_sdk import (
    query,
    ClaudeAgentOptions,
    HookMatcher,
    AssistantMessage,
    TextBlock,
    ToolUseBlock,
    ResultMessage,
    PermissionResultAllow,
    PermissionResultDeny,
)

from . import config as C
from . import heartbeat as HB
from . import interrupt as INT
from . import memory as M
from . import questions as Q
from . import tools as T


# --------------------------------------------------------------------------
# Видимость хода
# --------------------------------------------------------------------------
#
# Ход может идти минутами. Без обратной связи это неотличимо от зависания,
# поэтому шаги показываются по мере выполнения, а в паузах бьётся пульс.

DIM, OFF = ("\033[2m", "\033[0m") if sys.stdout.isatty() else ("", "")


def _elapsed(sec: float) -> str:
    m, s = divmod(int(sec), 60)
    return f"{m}м {s:02d}с" if m else f"{s}с"


class Progress:
    """Печатает шаги хода и пульс, если шагов давно не было."""

    def __init__(self):
        self.enabled = False
        self.paused = False
        self.started = 0.0
        self.last = 0.0
        self.steps = 0
        self._task: asyncio.Task | None = None

    def touch(self):
        self.last = time.monotonic()

    def start(self, enabled: bool):
        self.enabled = enabled
        self.paused = False
        self.steps = 0
        self.started = self.last = time.monotonic()
        if enabled:
            self._task = asyncio.create_task(self._beat())

    async def _beat(self):
        try:
            while True:
                await asyncio.sleep(1)
                if self.paused:
                    continue
                idle = time.monotonic() - self.last
                if idle >= C.PROGRESS_EVERY:
                    self.touch()
                    print(f"{DIM}  ⋯ думаю, {_elapsed(time.monotonic() - self.started)}{OFF}",
                          flush=True)
        except asyncio.CancelledError:
            pass

    def step(self, text: str):
        self.steps += 1
        if self.enabled and not self.paused:
            print(f"{DIM}  · {text}{OFF}", flush=True)
        self.touch()

    def note(self, text: str):
        if self.enabled and not self.paused:
            print(f"{DIM}  {text}{OFF}", flush=True)
        self.touch()

    def stop(self):
        if self._task:
            self._task.cancel()
            self._task = None
        if self.enabled:
            print(f"{DIM}  ✓ {_elapsed(time.monotonic() - self.started)}, "
                  f"шагов: {self.steps}{OFF}\n", flush=True)
        self.enabled = False


_progress = Progress()

# Итог последнего хода этого процесса: цена и число шагов, как их назвал SDK.
# Нужен процессу-подагенту (modules/jobs/run.py), который записывает стоимость
# задания в журнал: в ответ хода эти числа не попадают, а по логу их не собрать —
# рядом пишут другие ходы. Модульная переменная здесь безопасна ровно потому же,
# почему безопасны _progress и пульс: один ход на процесс.
LAST_RESULT: dict = {}


# --------------------------------------------------------------------------
# Цена хода
# --------------------------------------------------------------------------
#
# SDK кладёт в ResultMessage.total_cost_usd НАКОПЛЕННОЕ сессией, а не цену
# этого хода: внутри сессии ряд монотонно растёт и обнуляется только с новой
# сессией. Наивная сумма по логу за 25.08 давала 1767 долларов при 135
# реально накопленных — врала в тринадцать раз, и на этом стоял весь мой счёт
# собственной стоимости (bl-069). Поэтому в лог идёт дельта к прошлой записи
# ТОЙ ЖЕ сессии, а накопленное лежит рядом полем cumulative — по нему всегда
# можно пересобрать ряд, если дельта окажется врать.
_COST_SEEN: dict[str, float] = {}


def _cost_prev_from_log(session: str) -> float | None:
    """Последнее накопленное этой сессии — из лога.

    Память процесса между ходами не живёт, а сессия SDK живёт: соседние ходы
    приходят с одним session_id. Единственный источник, который переживает
    ход, — лог, поэтому предыдущее значение ищется в нём: два последних дня,
    с конца, первое совпадение по session.
    """
    import json as _json
    from datetime import timedelta

    day = M.today()
    for back in (0, 1):
        path = M.log_dir() / f"{(day - timedelta(days=back)).isoformat()}.jsonl"
        if not path.exists():
            continue
        try:
            lines = path.read_text(encoding="utf-8").splitlines()[-6000:]
        except OSError:
            continue
        for line in reversed(lines):
            if session not in line or "cumulative" not in line:
                continue                        # дешёвая отсечка до разбора
            try:
                body = _json.loads(_json.loads(line).get("content") or "{}")
            except Exception:
                continue
            if isinstance(body, dict) and body.get("session") == session \
                    and body.get("cumulative") is not None:
                return float(body["cumulative"])
    return None


def cost_delta(session: str | None, cumulative: float | None) -> float | None:
    """Цена именно этого хода: сколько накапало со времён прошлой записи."""
    if cumulative is None:
        return None
    key = session or "?"
    prev = _COST_SEEN.get(key)
    if prev is None:
        prev = _cost_prev_from_log(key)
    _COST_SEEN[key] = cumulative
    if prev is None or cumulative < prev:
        # Первый ход сессии либо счётчик сброшен: накопленное и есть цена.
        return round(cumulative, 6)
    return round(cumulative - prev, 6)


def _describe(tool: str, args: dict) -> str:
    """Короткая строка про вызов инструмента — путь или команда, без простыни."""
    if tool == "Bash":
        return f"Bash: {str(args.get('command', ''))[:90]}"
    for key in ("file_path", "path", "notebook_path", "url", "pattern", "query"):
        if args.get(key):
            val = str(args[key])
            if str(C.ROOT) in val:
                val = val.replace(str(C.ROOT) + "/", "")
            return f"{tool} {val[:90]}"
    if tool.startswith("mcp__memory__"):
        name = tool.removeprefix("mcp__memory__")
        return f"память: {name} {str(args.get('id', ''))[:40]}"
    return tool


# Слова для статуса в ВК. _describe годится журналу шагов, но не Илье: 24.08 он
# получил в чат десяток строк вида «Bash: cd ... && git commit -q -F - <<'MSGEOF'»
# и сказал прямо — код и команды мне не нужны, я их не читаю. Статус отвечает на
# «чем ты сейчас занят», по-русски и без синтаксиса (правила vsyo-po-russki,
# tekst-v-vk). Сама команда никуда не девается: она в логе и в derived/steps.
_VERBS = {"Read": "читаю", "Edit": "правлю", "Write": "пишу",
          "NotebookEdit": "правлю", "Grep": "ищу в файлах", "Glob": "ищу файлы",
          "WebSearch": "ищу в интернете", "WebFetch": "читаю страницу",
          "Agent": "отдал подагенту", "Task": "отдал подагенту"}


def _say(tool: str, args: dict) -> str:
    """Что сказать Илье про шаг: по-русски, без команд, путей и кода."""
    if tool == "Bash":
        what = str(args.get("description") or "").strip()
        return what[:120] if what else "работаю в терминале"
    if tool.startswith("mcp__memory__"):
        return {"remember_fact": "записываю факт",
                "remember_rule": "записываю правило",
                "remember_procedure": "записываю процедуру",
                "add_obligation": "завожу обязательство",
                "close_obligation": "закрываю обязательство",
                "update_now": "обновляю память",
                "recall": "ищу в памяти"}.get(
                    tool.removeprefix("mcp__memory__"), "работаю с памятью")
    verb = _VERBS.get(tool)
    if not verb:
        return "работаю"
    if tool in ("Agent", "Task"):
        what = str(args.get("description") or "").strip()
        return f"{verb}: {what[:100]}" if what else verb
    name = str(args.get("file_path") or args.get("path")
               or args.get("pattern") or args.get("query") or "").strip()
    name = name.rsplit("/", 1)[-1]
    return f"{verb} {name[:60]}".strip() if name else verb


# --------------------------------------------------------------------------
# Маршрутизация
# --------------------------------------------------------------------------

# Маршрутизатора («fast» отдельным вызовом haiku перед каждым ходом) больше
# нет — снят 26.08.2026 по bl-076. Данные за четыре дня: 117 ходов от Ильи,
# быстрый путь не выбран НИ РАЗУ, то есть механизм давал только лишний сетевой
# вызов и секунды задержки на каждом сообщении.
#
# Дело не в промпте классификатора: быстрый путь был устроен так, что не мог
# ответить даже на свой образцовый пример. Он собирал конституцию БЕЗ памяти,
# без инструментов и без шлюза — значит на «сколько время» модель не знала ни
# времени, ни часового пояса Ильи и ответить правильно не могла в принципе.
# Расширять примеры реальными фразами было бы починкой указателя на пустоту.
#
# Быстрый путь из раздела 4 конституции никуда не делся, он просто не механизм,
# а поведение: агент, увидев простой вопрос, отвечает сразу — не грузит лишнее,
# не рефлексирует, не планирует. Решает это сама модель внутри хода, а не
# классификатор снаружи.
#
# Вернуть дешёвый путь имеет смысл только вместе с ответом на вопрос «что он
# умеет без памяти и инструментов» — иначе получится то же самое.


# --------------------------------------------------------------------------
# Разрешения = раздел 5 конституции, «цена ошибки»
# --------------------------------------------------------------------------
#
# Политика целиком живёт в gate() — одно место, один набор правил.
# Логика ровно как в конституции: цена ошибки, а не сложность действия.
#
#   нулевая  — чтение, память, запись в свою папку (всё под гитом, откатывается)
#   средняя  — шелл, меняющий состояние; зависит от режима
#   высокая  — выход за пределы папки, необратимое, внешний мир; запрещено или спрашиваем

ZERO_COST_TOOLS = {
    # чтение и поиск
    "Read", "Glob", "Grep", "NotebookRead", "WebSearch", "WebFetch",
    # служебное: не трогает мир, только собственную работу агента.
    # Agent/Task — подагенты. Разрешены намеренно (Илья, 23.08.2026): изоляция
    # контекста предусмотрена конституцией, а до этого шлюз глушил их как
    # «неизвестное действие», и в unattended-ходах подагент умирал на старте.
    # Безопасно: каждый вызов инструмента ВНУТРИ подагента проходит этот же хук.
    "Agent", "TodoWrite", "Task", "Skill", "ToolSearch", "SlashCommand", "ExitPlanMode",
    "BashOutput", "KillShell", "ListMcpResources", "ReadMcpResource",
    # Управление уже запущенными подагентами (bl-071, 25.08.2026). Запускать
    # подагента шлюз разрешал, а править его работу — нет: SendMessage,
    # Monitor, TaskStop, TaskOutput, ListAgents падали в ветку «неизвестное
    # действие», в фоновом ходе спросить некого, и все 8 попыток за два дня
    # отбились по таймауту. Один раз подагент из-за этого доработал задание с
    # ошибкой, которую я уже знал, но не мог ему передать.
    # Цена ошибки нулевая: это переписка внутри моего же хода — сказать
    # своему подагенту, посмотреть его вывод, остановить его. Наружу ничего
    # не уходит: адресат — процесс, а не человек, и каждый вызов ВНУТРИ
    # подагента проходит этот же хук. Намеренно НЕ включены средства,
    # которые дотягиваются до внешнего мира или до будущего: PushNotification,
    # RemoteTrigger, CronCreate/CronDelete — они остаются на ask.
    "SendMessage", "Monitor", "TaskStop", "TaskOutput", "ListAgents",
}
WRITE_TOOLS = {"Write", "Edit", "MultiEdit", "NotebookEdit"}

# Чтение через шелл — цена нулевая в любом режиме.
SAFE_BASH = re.compile(
    r"^\s*(ls|pwd|cat|head|tail|wc|tree|find|grep|rg|which|file|stat|du|df|echo|date|"
    r"env|printenv|python3?\s+-m\s+(pytest|py_compile|venv)|pip\s+(list|show)|"
    r"git\s+(status|diff|log|show|branch|ls-files)|nproc|uname|free|"
    # Смотреть на живые процессы — чтение, а не изменение: ни одна из этих
    # команд ничего не трогает. Убивать процессы (kill, pkill, killall) сюда
    # НЕ добавляется никогда — это изменение состояния машины.
    r"ps|pgrep|pstree|pidof|uptime)\b")

# Запрещено навсегда, во всех режимах, включая yolo.
NEVER_BASH = re.compile(
    r"(\brm\s+-[a-z]*[rf]|\bsudo\b|\bsu\b|\bmkfs|\bdd\s+if=|\bshutdown\b|\breboot\b|"
    r"\bchmod\s+-R\s+777|\bcrontab\s+-r|\bgit\s+push\b|\bcurl[^|]*\|\s*(ba)?sh|"
    r"\bwget[^|]*\|\s*(ba)?sh|>\s*/dev/(sd|nvme)|:\(\)\{)")

# --------------------------------------------------------------------------
# Разбор командной строки. Запрет обязан ловить КОМАНДУ, а не подстроку.
#
# 24-25.08 запрет по подстроке трижды отбил безобидное (bl-043): домен
# «gmz.su» поймался как `su` и подагент молча потерял источник; текст про
# удаление внутри сообщения коммита поймался как удаление; запись в память,
# где опасная команда лишь УПОМЯНУТА, пришлось переписывать трижды. Ложный
# запрет учит обходить шлюз перефразированием — а обход входит в привычку и
# однажды сработает там, где запрет был по делу.
#
# Поэтому строка разбирается: кавычки склеиваются в одно слово, heredoc
# отделяется от команды, подстановки ($(…), `…`) считаются отдельными
# командами, и опасность ищется только в позиции команды и её аргументов.
# --------------------------------------------------------------------------

SHELLS = {"sh", "bash", "zsh", "dash", "ksh", "ash", "busybox"}
# Обёртки: настоящая команда стоит следующим словом.
WRAPPERS = {"env", "nohup", "time", "timeout", "nice", "ionice", "stdbuf",
            "command", "exec", "setsid", "unbuffer"}
KILL_HOST = {"shutdown", "reboot", "halt", "poweroff", "init", "telinit"}
ROOT_CMDS = {"sudo", "su", "doas", "pkexec", "runuser"}
# Питон в heredoc — это данные, пока он сам не зовёт шелл. Тогда тело
# проверяется старым способом: разбирать питон я не берусь.
PY_SHELL = re.compile(r"\b(os\.system|os\.popen|subprocess\.|os\.exec|pty\.spawn)\b")
FORK_BOMB = re.compile(r":\s*\(\s*\)\s*\{")
SEPS = {";", "&&", "||", "|", "|&", "&", "\n", "(", ")", ";;"}
REDIRECTS = {">", ">>", ">|"}
# Перезапись файла. Дозапись (>>) в неизменяемой зоне законна — лог только
# растёт, — а вот `>` и `>|` затирают уже записанное.
OVERWRITES = {">", ">|"}
# Команды, которые в неизменяемой зоне запрещены целиком: они убирают или
# подменяют уже записанное. Раздел 8 конституции, граница без исключений.
# Чтение (cat, grep, sed -n, tail) не трогается: лог для того и пишется.
LOG_ZONE_CMDS = {"rm", "unlink", "shred", "truncate", "mv", "rename",
                 "install", "dd", "cp"}


def _cut_heredocs(cmd: str) -> tuple[str, list[str]]:
    """Вынимает тела heredoc'ов, оставляя на их месте слово-маркер.

    Тело heredoc'а исполняется, только если его читает шелл. У `git commit -F -`
    и у `python - <<PY` это ТЕКСТ и КОД ПИТОНА, а не команды шелла.
    """
    lines, out, bodies = cmd.split("\n"), [], []
    i = 0
    while i < len(lines):
        line = lines[i]
        m = re.search(r"<<-?\s*(['\"]?)([A-Za-z_]\w*)\1", line)
        if not m:
            out.append(line)
            i += 1
            continue
        tag, body, j = m.group(2), [], i + 1
        while j < len(lines) and lines[j].strip() != tag:
            body.append(lines[j])
            j += 1
        out.append(f"{line[:m.start()]} __heredoc{len(bodies)}__ {line[m.end():]}")
        bodies.append("\n".join(body))
        i = j + 1
    return "\n".join(out), bodies


def _words(s: str) -> list[tuple[str, str]]:
    """Строка → список ('w', слово) и ('op', разделитель).

    Кавычки склеивают содержимое в ОДНО слово: `-m "rm -rf x"` — это флаг и
    текст, а не три команды. Подстановки становятся разделителем: то, что
    внутри них, — настоящая команда и проверяется отдельно.
    """
    toks: list[tuple[str, str]] = []
    buf, has = "", False

    def flush() -> None:
        nonlocal buf, has
        if has:
            toks.append(("w", buf))
        buf, has = "", False

    i, n = 0, len(s)
    dq = False        # внутри двойных кавычек: пробелы не делят слово
    dq_back = False   # подстановка открыта изнутри двойных кавычек
    while i < n:
        c = s[i]
        if dq:
            if c == '"':
                dq, has, i = False, True, i + 1
                continue
            if c == "\\" and i + 1 < n:
                buf, has, i = buf + s[i + 1], True, i + 2
                continue
            if c == "`" or s[i:i + 2] == "$(":  # подстановка исполняется
                flush()                          # и внутри двойных кавычек
                toks.append(("op", ";"))
                dq, dq_back = False, c != "`"
                i += 1 if c == "`" else 2
                continue
            buf, has, i = buf + c, True, i + 1
            continue
        if c == "'":                                  # одинарные — всё буквально
            j = s.find("'", i + 1)
            j = n if j < 0 else j
            buf, has, i = buf + s[i + 1:j], True, j + 1
            continue
        if c == '"':
            dq, has, i = True, True, i + 1
            continue
        if c == ")" and dq_back:                      # подстановка закрылась
            flush()
            toks.append(("op", ";"))
            dq, dq_back, i = True, False, i + 1
            continue
        if c == "\\" and i + 1 < n:
            buf, has, i = buf + s[i + 1], True, i + 2
            continue
        if c == "`" or s[i:i + 2] == "$(":
            flush()
            toks.append(("op", ";"))
            i += 1 if c == "`" else 2
            continue
        if c.isspace():
            flush()
            if c == "\n":
                toks.append(("op", "\n"))
            i += 1
            continue
        if s[i:i + 2] in ("&&", "||", ";;", "|&", "<<", ">>", ">|"):
            flush()
            toks.append(("op", s[i:i + 2]))
            i += 2
            continue
        if c in ";|&()<>":
            flush()
            toks.append(("op", c))
            i += 1
            continue
        buf, has, i = buf + c, True, i + 1
    flush()
    return toks


def _segments(toks: list[tuple[str, str]]) -> list[tuple[str, list[tuple[str, str]]]]:
    """Токены → куски вида (разделитель перед куском, токены куска)."""
    segs, cur, prev = [], [], ""
    for kind, t in toks:
        if kind == "op" and t in SEPS:
            segs.append((prev, cur))
            cur, prev = [], t
        else:
            cur.append((kind, t))
    segs.append((prev, cur))
    return [s for s in segs if s[1]]


# --------------------------------------------------------------------------
# Раскрытие фигурных скобок шелла: {a,b} и {1..5}
# --------------------------------------------------------------------------
#
# Шелл раскрывает скобки ПЕРВЫМ делом — раньше подстановок, раньше масок — и
# делает это ПОСЛЕ того, как шлюз посмотрел на текст команды. Значит запретной
# цели в тексте может не быть вовсе, а на диск команда всё равно сходит:
#     cat memory/{projects,X}/chuzhoy/dossier.md   → memory/projects/…
#     cat memory/{pro,}jects/chuzhoy/dossier.md    → скобки ВНУТРИ слова
#     cat .en{v,X}                                 → корневой .env
#     tee memory/{log,X}/2026-09-01.jsonl          → неизменяемая зона
#     {/etc,x}/passwd                              → выход за папку агента
# Живой взломщик (job-192, 01.09.2026) этим одним приёмом вычитал чужое досье,
# чужой backlog и корневой .env с VK_TOKEN, SMTP_PASS и NOTION_TOKEN.
#
# Чинится не перечислением приёмов, а тем же правилом, что и маски: слово
# раскрывается ЗДЕСЬ, до проверки, и каждая ветка судится как отдельный путь.
# Попадание хоть одной ветки в запретное отбивает всю команду — ровно так и
# ведёт себя шелл, он выполнит их все.
#
# Огульно резать всё со скобками нельзя: `find … -exec … {} \;` — законная
# работа, и `{}` без запятой шелл не раскрывает вовсе. Поэтому раскрывается
# только группа с запятой верхнего уровня или с диапазоном `..`.

_BRACE_LIMIT = 64                  # веток на слово: дальше считаем «не видно»
_BRACE_RANGE_NUM = re.compile(r"^(-?\d+)\.\.(-?\d+)(?:\.\.(-?\d+))?$")
_BRACE_RANGE_CHR = re.compile(r"^([A-Za-z])\.\.([A-Za-z])(?:\.\.(-?\d+))?$")


def _brace_range(body: str) -> list[str] | None:
    """`1..5`, `a..e`, `1..9..2` → список значений. Не диапазон — None."""
    if m := _BRACE_RANGE_NUM.match(body):
        lo, hi = int(m.group(1)), int(m.group(2))
        step = abs(int(m.group(3) or 1)) or 1
        if abs(hi - lo) // step >= _BRACE_LIMIT:
            return None
        rng = range(lo, hi + 1, step) if lo <= hi else range(lo, hi - 1, -step)
        return [str(v) for v in rng]
    if m := _BRACE_RANGE_CHR.match(body):
        lo, hi = ord(m.group(1)), ord(m.group(2))
        step = abs(int(m.group(3) or 1)) or 1
        if abs(hi - lo) // step >= _BRACE_LIMIT:
            return None
        rng = range(lo, hi + 1, step) if lo <= hi else range(lo, hi - 1, -step)
        return [chr(v) for v in rng]
    return None


def _brace_group(s: str) -> tuple[str, list[str], str] | None:
    """Первая РАСКРЫВАЕМАЯ группа: (что до неё, ветки, что после). Нет — None.

    Раскрываемая — с запятой верхнего уровня внутри (`{a,b}`) или диапазоном
    (`{1..5}`). `{}` и `{abc}` шелл оставляет как есть, и мы тоже: иначе
    поломался бы `find … -exec … {} \\;`.
    """
    depth, start, cur, parts = 0, -1, 0, []
    for i, ch in enumerate(s):
        if ch == "{":
            if depth == 0:
                start, cur, parts = i, i + 1, []
            depth += 1
        elif ch == "}" and depth > 0:
            depth -= 1
            if depth == 0:
                parts.append(s[cur:i])
                if len(parts) > 1:
                    return s[:start], parts, s[i + 1:]
                if (rng := _brace_range(parts[0])) is not None:
                    return s[:start], rng, s[i + 1:]
                start, parts = -1, []      # не группа — ищем следующую
        elif ch == "," and depth == 1:
            parts.append(s[cur:i])
            cur = i + 1
    return None


def _brace_expand(word: str, limit: int = _BRACE_LIMIT) -> list[str]:
    """Во что шелл превратит слово со скобками. Без скобок — само слово.

    Вложенность разбирается рекурсией через очередь. Потолок веток — не
    оптимизация: слово, раскрытие которого шлюз не досчитал, он и не судил,
    поэтому потолок отдаётся наружу как «сюда попало исходное слово», а
    вызывающая сторона обязана считать такой случай непрозрачным.
    """
    if "{" not in word:
        return [word]
    out: list[str] = []
    queue = [word]
    while queue and len(out) < limit:
        cur = queue.pop(0)
        got = _brace_group(cur)
        if got is None:
            out.append(cur)
            continue
        pre, parts, tail = got
        if len(queue) + len(out) + len(parts) > limit * 4:
            out.append(cur)                # раскрытие не влезло — судим как есть
            continue
        queue += [pre + p + tail for p in parts]
    return out or [word]


def _brace_candidates(word: str) -> list[str]:
    """Ветки раскрытия ПЛЮС само слово.

    Кавычки парсер снимает («cat 'file{a,b}'» и «cat file{a,b}» доходят сюда
    одинаково), а в кавычках шелл скобки не раскрывает. Судим и то и другое:
    лишняя ветка максимум даёт лишний отказ, потерянная — дыру.
    """
    if "{" not in word:
        return [word]
    out = _brace_expand(word)
    return out if word in out else out + [word]


def _path_under(p: str, root: Path, base: Path | None = None) -> bool:
    """Путь заведомо лежит внутри root? Неизвестность считается «нет».

    `base` — каталог, от которого считается относительный путь. По умолчанию
    папка агента; при разборе команды туда передаётся фактический каталог
    после `cd`, иначе `cd derived/scratch && rm -rf tmp` разбирается так,
    будто tmp лежит в корне папки.
    """
    if not p or p.startswith("~") or "$" in p:
        return False
    p = re.split(r"[*?\[]", p)[0]
    if not p:
        # Подстановка съела путь целиком (`rm *.jsonl`): каталога в маске нет,
        # значит цель — сам рабочий каталог. Без этого `cd memory/log &&
        # rm *.jsonl` проходил: строки «memory/log» в аргументе уже нет, а
        # путь считался непонятным. Неизвестный каталог остаётся «не знаю».
        if base is None:
            return False
        try:
            return base.resolve().is_relative_to(root)
        except Exception:
            return False
    try:
        q = Path(p) if p.startswith("/") else (base or C.ROOT) / p
        q = q.resolve()
        return q.is_relative_to(root)
    except Exception:
        return False


def _cd_target(cwd: Path | None, rest: list[str]) -> Path | None:
    """Куда уводит `cd`. None — каталог неизвестен или он вне папки агента.

    Неизвестность заразна: после `cd` в неизвестное относительные пути
    считать не от чего, и следующий относительный `cd` тоже неизвестен.
    """
    if not rest:                                  # `cd` без аргумента — домой
        return None
    t = rest[0]
    if t == "-" or "$" in t or t.startswith("~"):
        return None
    if not t.startswith("/") and cwd is None:
        return None
    try:
        q = (Path(t) if t.startswith("/") else cwd / t).resolve()
    except Exception:
        return None
    return q if q.is_relative_to(C.ROOT) else None


def _danger(cmd: str, depth: int = 0) -> str | None:
    """Причина запрета или None. Разбирает строку, а не ищет подстроку."""
    if depth > 3:
        return "слишком глубокая вложенность команд"
    if FORK_BOMB.search(cmd):
        return "форк-бомба"
    body, heredocs = _cut_heredocs(cmd)
    # Относительные пути считаются от ФАКТИЧЕСКОГО каталога: строка часто
    # начинается с `cd`, и дальше всё в ней относительно него. Если `cd` увёл
    # наружу или в неизвестное — считать не от чего (cwd = None), послабление
    # для derived не действует, а запреты по зоне лога держит страховка по
    # виду пути в _zone_path.
    cwd: Path | None = C.ROOT
    for sep, seg in _segments(_words(body)):
        words = [t for k, t in seg if k == "w"]
        args = list(words)
        while args and re.match(r"^[A-Za-z_]\w*=", args[0]):
            args.pop(0)
        if not args:
            continue
        base = args[0].rsplit("/", 1)[-1]
        rest = args[1:]
        while base in WRAPPERS and rest:                  # env / nohup / timeout
            while rest and (rest[0].startswith("-") or rest[0].isdigit()
                            or re.match(r"^[A-Za-z_]\w*=", rest[0])):
                rest.pop(0)
            if not rest:
                break
            base, rest = rest[0].rsplit("/", 1)[-1], rest[1:]

        if base == "cd":
            cwd = _cd_target(cwd, rest)
            continue
        if base in ROOT_CMDS:
            return f"права суперпользователя: {base}"
        if base in KILL_HOST:
            return f"выключение машины: {base}"
        if base.startswith("mkfs"):
            return "разметка файловой системы"
        if base == "dd" and any(a.startswith("if=") for a in rest):
            return "посекторная запись dd"
        if base == "crontab" and "-r" in rest:
            return "снос расписания crontab"
        if base == "chmod" and "-R" in rest and any("777" in a for a in rest):
            return "рекурсивные права 777"
        if base in SHELLS:
            if sep in ("|", "|&"):
                return "конвейер прямо в интерпретатор"
            for k, a in enumerate(rest):
                if a == "-c" and k + 1 < len(rest):
                    if r := _danger(rest[k + 1], depth + 1):
                        return r
            for a in rest:
                if m := re.fullmatch(r"__heredoc(\d+)__", a):
                    if r := _danger(heredocs[int(m.group(1))], depth + 1):
                        return r
        elif base == "eval":
            if r := _danger(" ".join(rest), depth + 1):
                return r
        else:
            # Не шелл: heredoc и `-c` — данные или код на чужом языке.
            # Разбирать питон не берусь, но если он сам зовёт шелл — проверяю
            # тело старым способом, по подстроке: там это уместно.
            texts = [heredocs[int(m.group(1))] for a in rest
                     if (m := re.fullmatch(r"__heredoc(\d+)__", a))]
            texts += [rest[k + 1] for k, a in enumerate(rest)
                      if a == "-c" and k + 1 < len(rest)]
            for t in texts:
                if PY_SHELL.search(t) and NEVER_BASH.search(t):
                    return "чужой язык зовёт запрещённую команду шелла"
                if GUARD_ASSIGN.search(t):
                    return "чужой язык снимает предохранитель шлюза"
                if SECRET_WRITE_PY.search(t):
                    return "чужой язык пишет в файл доступов (.env)"

        if base == "git" and rest:
            if rest[0] == "push":
                return "отправка репозитория наружу"
            if rest[0] == "rm" and "--cached" not in rest:
                if r := _rm_danger(rest[1:], cwd):
                    return "git rm: " + r
        if base == "rm":
            if r := _rm_danger(rest, cwd):
                return r
        if base == "git" and rest and rest[0] in ("rm", "clean", "checkout",
                                                  "restore", "reset"):
            if any(_zone_path(p, LOG_ZONE(), cwd) for p in rest[1:]
                   if not p.startswith("-")):
                return f"git {rest[0]} по неизменяемой зоне memory/log"
        if r := _log_zone_danger(base, rest, seg, cwd):
            return r
        if r := _access_danger(base, rest, seg, words):
            return r
        if r := _charter_bash_danger(base, rest, seg):
            return r
        for idx, (kind, t) in enumerate(seg):            # запись в устройство
            if kind == "op" and t in REDIRECTS and idx + 1 < len(seg):
                nxt = seg[idx + 1][1]
                if re.match(r"/dev/(sd|nvme|hd|disk|mmcblk)", nxt):
                    return "запись прямо в дисковое устройство"
    return None


def LOG_ZONE() -> Path:
    """Неизменяемая зона: memory/log/. Только дозапись, никогда не правка."""
    return C.ROOT / "memory" / "log"


def _zone_path(p: str, log: Path, base: Path | None = None) -> bool:
    """Путь целит в неизменяемую зону — по разбору или хотя бы по виду.

    `_path_under` честно отвечает «нет» на всё, где есть `~`, `$` или
    подстановка: посчитать такой путь оно не может. Для запретов этого мало —
    `rm $LOG/2026-08-25.jsonl` прошёл бы мимо. В позиции аргумента команды,
    которая уже признана убирающей или подменяющей файлы, дешевле считать
    подозрительным любой путь, где вообще упомянут лог: ложная тревога стоит
    одного переформулированного вызова, а промах — куска лога навсегда.

    Скобки раскрываются до суда: `rm memory/{log,X}/2026-09-01.jsonl` не
    содержит строки «memory/log», а сносит файл лога (класс job-192).
    """
    return any(_path_under(c, log, base) or bool(re.search(r"memory/log(/|$)", c))
               for c in _brace_candidates(p))


def _log_zone_danger(base: str, rest: list[str], seg,
                     cwd: Path | None = None) -> str | None:
    """Команда трогает memory/log/ иначе, чем дозаписью?

    Раздел 8 конституции запрещает удалять что-либо из неизменяемой зоны, но
    до 25.08.2026 этот запрет жил только текстом в промпте: шлюз ловил лишь
    рекурсивное удаление, а `rm memory/log/2026-08-25.jsonl` проходил как
    «шелл внутри своей папки». Граница, которую проверяет только моя
    дисциплина, — не граница.
    """
    log = LOG_ZONE()
    paths = [a for a in rest if not a.startswith("-") and a != "-"]

    if base in LOG_ZONE_CMDS:
        # cp безобиден, пока копирует ИЗ лога: опасна только цель. Цель — это
        # последний путь либо аргумент -t/--target-directory.
        targeted = any(a.startswith("-t") or a.startswith("--target-directory")
                       for a in rest)
        watched = paths[-1:] if (base == "cp" and not targeted) else paths
        if any(_zone_path(p, log, cwd) for p in watched):
            return f"{base} в неизменяемой зоне memory/log"
    if base == "sed" and any(a.startswith("-i") or a == "--in-place"
                             for a in rest):
        if any(_zone_path(p, log, cwd) for p in paths):
            return "правка лога на месте (sed -i)"
    if base == "tee" and not any(a in ("-a", "--append") for a in rest):
        if any(_zone_path(p, log, cwd) for p in paths):
            return "перезапись лога через tee"

    for idx, (kind, t) in enumerate(seg):        # `>` и `>|` затирают файл
        if kind == "op" and t in OVERWRITES and idx + 1 < len(seg):
            if _zone_path(seg[idx + 1][1], log, cwd):
                return "перезапись файла в неизменяемой зоне memory/log"
    return None


# --------------------------------------------------------------------------
# Граница раздела 8: «Не расширяешь себе доступы. Их выдаёт Илья».
#
# До 25.08.2026 её не проверяло ничто, кроме моей памяти. Расширить доступ
# можно было двумя движениями, и оба проходили шлюз как «своя папка»:
#   1. дописать себе ключ или логин в .env — файл вне гита, Илья правки
#      не увидит вовсе;
#   2. запустить что угодно с JARVIS_PERMISSION=yolo — в этом режиме шлюз
#      пропускает всё, кроме запрещённого навсегда.
# Оба — не «опасная команда», а тихая смена собственных полномочий, и
# заметить её постфактум нельзя. Читать .env и смотреть режим по-прежнему
# можно: запрещено ИЗМЕНЯТЬ, а не знать.
# --------------------------------------------------------------------------

# Файлы доступов: заполняет Илья, агент только читает (sekrety-ne-cherez-dialog).
# .env.example — образец без значений, он под гитом и правится свободно.
SECRET_NAMES = re.compile(r"^\.env(\.(local|prod|production|secret))?$")
# Команды, меняющие файл на месте или подменяющие его целиком.
WRITE_CMDS = {"rm", "unlink", "shred", "truncate", "mv", "rename", "install",
              "dd", "cp", "chmod", "chown", "ln"}
# Переменные, которыми снимаются предохранители самого шлюза.
GUARD_VARS = {"JARVIS_PERMISSION", "JARVIS_FIELD_LIMIT"}
# Та же подмена из чужого языка: os.environ["JARVIS_PERMISSION"] = "yolo".
# Ищется именно ПРИСВОЕНИЕ: чтение (`os.environ.get("JARVIS_PERMISSION")`)
# и упоминание в grep остаются законными.
GUARD_ASSIGN = re.compile(
    r"JARVIS_(?:PERMISSION|FIELD_LIMIT)\s*['\"]?\s*\]?\s*=(?!=)")
# Открытие .env на запись из питона в heredoc или `-c`.
SECRET_WRITE_PY = re.compile(
    r"open\s*\(\s*['\"][^'\"]*\.env(\.\w+)?['\"]\s*,\s*['\"][wa]")

# Гит-хук — второй предохранитель того же рода: он держит условие, на котором
# Илья разрешил править ядро (строка в memory/core_changes.md на каждую правку)
# и не пускает в историю секреты. Снять его можно тремя движениями, и все три
# выглядят как безобидная работа в своей папке: коммит с --no-verify, перевод
# core.hooksPath в пустое место, удаление или снятие бита с самого файла хука.
# Поэтому все три — deny. Читать настройку и править текст хука по-прежнему
# можно: запрещено ОТКЛЮЧАТЬ, а не знать и не улучшать.
#
# Взвод предохранителя — не его снятие. Прогон 2943 живых команд из лога
# 22–25.08 дал одно срабатывание, и оно оказалось ложным: команда, которой я
# хук СТАВИЛ (`chmod +x` плюс `git config core.hooksPath core/githooks`).
# Запретить взвод — значит запереть себя без права починки: Write бит
# исполнения не ставит. Поэтому включение хука разрешено, выключение нет.
HOOK_FILE = re.compile(r"(^|/)(core/)?githooks(/|$)|(^|/)\.git/hooks(/|$)")
# Значения git config, которыми хуки выключаются целиком.
HOOK_CONFIG = "core.hookspath"
# Каталог хуков агента: перевод настройки СЮДА — включение, а не обход.
HOOK_DIR_OK = re.compile(r"^(.*/)?core/githooks/?$")


def _chmod_arms(mode: str) -> bool:
    """Режим chmod ДАЁТ право исполнения владельцу, а не снимает его?

    `+x`, `u+x`, `755` — взвод. `-x`, `644`, `000` — снятие: такой chmod
    разоружает хук не хуже удаления, и его пропускать нельзя.
    """
    if re.fullmatch(r"[0-7]{3,4}", mode):
        return bool(int(mode, 8) & 0o100)
    return bool(re.fullmatch(r"[ugoa]*\+[rwxXst]*x[rwxXst]*", mode))


def _secret_path(p: str) -> bool:
    """Путь целит в файл доступов? По имени файла, а не по содержимому.

    Скобки раскрываются до суда: `tee -a .en{v,X}` дописывает себе доступ
    в .env, не называя его (класс job-192).
    """
    if not p:
        return False
    for c in _brace_candidates(p):
        name = c.rstrip("/").rsplit("/", 1)[-1]
        if SECRET_NAMES.match(name):
            return True
    return False


def _lead_assignments(words: list[str]) -> list[str]:
    """Присваивания в позиции переменной окружения, а не в тексте аргумента.

    `JARVIS_PERMISSION=yolo ./jarvis` — присваивание. `echo
    'JARVIS_PERMISSION=yolo'` — текст, и запрещать его нельзя: ровно на такой
    подстрочной ловле сгорели три безобидных вызова 24.08 (bl-043).
    """
    out, i = [], 0
    while i < len(words):
        w = words[i]
        if re.match(r"^[A-Za-z_]\w*=", w):
            out.append(w)
        elif w.rsplit("/", 1)[-1] in WRAPPERS | {"export", "set", "declare"}:
            pass                       # дальше снова может идти присваивание
        else:
            break
        i += 1
    return out


def _hook_danger(base: str, rest: list[str]) -> str | None:
    """Команда выключает гит-хук — предохранитель журнала ядра и секретов?"""
    if base == "git" and rest:
        if rest[0] == "commit" and any(
                a == "--no-verify" or (re.fullmatch(r"-[a-zA-Z]+", a) and "n" in a[1:])
                for a in rest[1:]):
            return ("коммит в обход гит-хука (--no-verify): хук держит условие "
                    "Ильи — правка ядра идёт строкой в memory/core_changes.md")
        if rest[0] == "config":
            # Чтение настройки законно: запрещено её ЗАДАВАТЬ.
            vals = [a for a in rest[1:] if not a.startswith("-")]
            reading = any(a in ("--get", "--get-all", "--list", "-l", "--get-regexp")
                          for a in rest[1:])
            if (vals and vals[0].lower() == HOOK_CONFIG
                    and len(vals) > 1 and not reading
                    and not HOOK_DIR_OK.match(vals[1].rstrip("/") or "/")):
                return "перевод core.hooksPath: так выключаются все гит-хуки разом"
            if any(a in ("--unset", "--unset-all") for a in rest[1:]) and any(
                    v.lower() == HOOK_CONFIG for v in vals):
                return "снятие core.hooksPath: хуки перестанут вызываться"
    paths = [p for p in rest if not p.startswith("-")]
    if base in WRITE_CMDS and any(HOOK_FILE.search(p) for p in paths):
        # Текст хука правится инструментами Write/Edit — они сюда не заходят.
        # Через шелл файл хука обычно не правят, а убирают или разоружают.
        if base == "chmod" and paths and _chmod_arms(paths[0]):
            return None                # взвод: chmod +x по хуку — это починка
        return f"{base} по файлу гит-хука: предохранитель снимается вместе с ним"
    return None


def _access_danger(base: str, rest: list[str], seg, words: list[str]) -> str | None:
    """Команда меняет мои собственные полномочия?"""
    for a in _lead_assignments(words):
        if a.split("=", 1)[0] in GUARD_VARS:
            return (f"смена предохранителя шлюза через окружение ({a}): "
                    "режим и лимиты выдаёт Илья, не я")
    if r := _hook_danger(base, rest):
        return r

    paths = [a for a in rest if not a.startswith("-") and a != "-"]
    if base in WRITE_CMDS and any(_secret_path(p) for p in paths):
        return f"{base} по файлу доступов (.env): его заполняет Илья"
    if base in ("sed", "perl", "awk") and any(
            a.startswith("-i") or a == "--in-place" for a in rest):
        if any(_secret_path(p) for p in paths):
            return "правка файла доступов на месте"
    if base == "tee" and any(_secret_path(p) for p in paths):
        return "перезапись файла доступов через tee"
    for idx, (kind, t) in enumerate(seg):        # `>` и `>>` в .env
        if kind == "op" and t in REDIRECTS and idx + 1 < len(seg):
            if _secret_path(seg[idx + 1][1]):
                return "дозапись доступа себе в .env"
    return None


# --------------------------------------------------------------------------
# Граница раздела 8: «Сами границы снимает и добавляет только Илья».
#
# Ядро я правлю сам — Илья снял запрет 23.08, — и конституция для шлюза тот же
# обычный файл в моей папке. Исключение внутри неё ровно одно: раздел 8, сам
# перечень границ. До 26.08.2026 это исключение держала одна дисциплина, то
# есть ничего: правило, которое мешает, снимается тем же Edit, что и опечатка,
# и постфактум разницы не видно.
# Проверка честная, без угадывания по словам: правка ПРИМЕНЯЕТСЯ насухо в
# памяти, из старого и нового текста вынимается раздел 8 и сравнивается. Меняй
# в конституции что угодно; тронул границы — deny. Перевёрстка абзацев
# границей не считается: раздел сводится к одной строке без лишних пробелов.
# --------------------------------------------------------------------------
CHARTER_NAME = "CONSTITUTION.md"
BORDERS = re.compile(r"^##\s*8\.[^\n]*\n.*?(?=^##\s|\Z)", re.S | re.M)


def _is_charter(p: str) -> bool:
    """Путь целит в конституцию? По имени файла, как и с .env."""
    return bool(p) and p.rstrip("/").rsplit("/", 1)[-1] == CHARTER_NAME


def _borders(text: str) -> str | None:
    """Раздел 8 одной строкой. None — раздела в тексте нет вовсе."""
    m = BORDERS.search(text or "")
    return re.sub(r"\s+", " ", m.group(0)).strip() if m else None


def _charter_write_danger(tool: str, inp: dict) -> str | None:
    """Правка конституции инструментом Write/Edit трогает раздел 8?"""
    path = str(inp.get("file_path") or inp.get("path") or "")
    if not _is_charter(path):
        return None
    src = Path(path)
    if not src.is_absolute():
        src = C.ROOT / path
    try:
        cur = src.read_text(encoding="utf-8")
    except OSError:
        return None                    # файла нет — это не правка границ
    old = _borders(cur)
    if old is None:
        return None                    # раздела нет — гадать не берусь

    if tool == "Write":
        after = str(inp.get("content") or "")
    elif tool in ("Edit", "MultiEdit"):
        after = cur
        for e in (inp.get("edits") or [inp]):
            o = str(e.get("old_string") or "")
            n = str(e.get("new_string") or "")
            if not o or o not in after:
                continue               # такая правка и сама не применится
            after = (after.replace(o, n) if e.get("replace_all")
                     else after.replace(o, n, 1))
    else:
        return None

    new = _borders(after)
    if new is None:
        return ("правка убирает раздел 8 из конституции целиком: границы "
                "снимает только Илья")
    if new != old:
        return ("правка меняет раздел 8 — сам перечень границ. Его снимает и "
                "добавляет только Илья (конституция п. 8, правило pravki-yadra): "
                "остальную конституцию правь свободно, эту — с его слова")
    return None


def _charter_bash_danger(base: str, rest: list[str], seg) -> str | None:
    """Правка конституции через шелл: какой раздел меняется — не разобрать."""
    why = ("через шелл не видно, трогает ли правка раздел 8 (границы): "
           "правь конституцию инструментом Edit, он проверяется точно")
    paths = [a for a in rest if not a.startswith("-") and a != "-"]
    if base in LOG_ZONE_CMDS:
        targeted = any(a.startswith("-t") or a.startswith("--target-directory")
                       for a in rest)
        # cp безопасен, пока копирует ИЗ конституции: опасна только цель. У mv
        # опасны обе стороны — уносом файл пропадает так же, как удалением.
        watched = paths[-1:] if (base == "cp" and not targeted) else paths
        if any(_is_charter(p) for p in watched):
            return f"{base} по конституции: {why}"
    if base in ("sed", "perl", "awk") and any(
            a.startswith("-i") or a == "--in-place" for a in rest):
        if any(_is_charter(p) for p in paths):
            return f"правка конституции на месте ({base} -i): {why}"
    if base == "tee" and any(_is_charter(p) for p in paths):
        return f"перезапись конституции через tee: {why}"
    for idx, (kind, t) in enumerate(seg):        # `>` и `>>` по конституции
        if kind == "op" and t in REDIRECTS and idx + 1 < len(seg):
            if _is_charter(seg[idx + 1][1]):
                return f"запись в конституцию из шелла: {why}"
    return None


# --------------------------------------------------------------------------
# Граница раздела 8: «Не действуешь от имени Ильи без явного разрешения»
#
# До 25.08.2026 её держала одна дисциплина (правило ot-imeni-ili-cherez-
# chernovik) плюс предохранители внутри самой рассылки: без `--send` ничего не
# уходит, потолок писем за запуск, адрес из журнала не берётся дважды. Все три
# снимаю я сам, одним словом в командной строке, и шлюз при этом не отличает
# боевую отправку от чтения файла: для него это «шелл внутри своей папки».
# В ночном фоновом ходе такая команда прошла бы молча.
#
# Теперь любое исходящее наружу — письмо, сообщение в мессенджер, отклик,
# заявка, комментарий — это ask: в ходе из ВК шлюз спросит Илью, в ходе без
# канала ask сам превратится в deny с подсказкой «запиши предложением».
# Черновики, сухие прогоны и чтение не трогаются: запрещено ОТПРАВИТЬ, а не
# подготовить. Мой собственный голос в ВК (modules/vk/notify) исключён —
# это моё сообщение Илье, а не письмо людям от его имени.
# --------------------------------------------------------------------------

# Почтовики командной строки: одна команда — и письмо ушло.
MAIL_CMDS = {"sendmail", "msmtp", "mailx", "mail", "swaks", "mutt", "neomutt",
             "s-nail", "sendemail", "ssmtp"}
# Сеть из шелла.
NET_CMDS = {"curl", "wget", "http", "https", "httpie", "aria2c"}
# Моя же рассылка. Боевой режим включает флаг --send, всё остальное — черновик.
OUTBOX_CLI = re.compile(r"modules[./]outbox[./]send\b")
# Мой голос в ВК: сообщение Илье от меня. Не «от его имени» — не спрашивать.
SELF_VOICE = re.compile(r"modules[./]vk[./]notify\b")
# Отправка письма из чужого языка.
MAIL_PY = re.compile(r"\b(smtplib|aiosmtplib|yagmail)\b|\.sendmail\s*\(|"
                     r"\.send_message\s*\(")
# Вызов, который сам по себе доставляет сообщение, каким бы методом ни шёл.
SEND_URL = re.compile(
    r"api\.telegram\.org/bot[^/\s]*/send|botapi\.max\.ru|"
    r"/method/(messages\.send|wall\.post|wall\.createComment|board\.createComment)"
    r"|\bmessages\.send\b|\bwall\.post\b", re.I)
# Площадки, где POST — это отклик, заявка или комментарий от имени Ильи.
# Чтение оттуда законно, поэтому нужен ещё и метод записи.
ACT_URL = re.compile(r"\bhh\.ru|\bfl\.ru|farpost\.ru|avito\.ru|"
                     r"api\.mail\.ru|notify\.max\.ru", re.I)
WRITE_METHODS = re.compile(
    r"-X\s*(POST|PUT|PATCH|DELETE)|--request[ =](POST|PUT|PATCH|DELETE)|"
    r"(^|\s)(-d|--data|--data-\w+|-F|--form|-T|--upload-file)([ =]|$)|"
    r"--post-data|--post-file")
NET_PY_POST = re.compile(
    r"\b(requests|httpx|session|client|s)\.(post|put|patch)\s*\(|"
    r"urlopen\s*\([^)]*data=")

# --------------------------------------------------------------------------
# job-049 (26.08.2026, находка критика): «написать отправляющий скрипт
# инструментом Write (не сканируется вовсе), затем запустить его отдельной
# командой по имени файла» проходило мимо _outbound целиком — в команде
# `.venv/bin/python derived/scratch/.../send_nologo.py --send` нет ни слова
# smtplib, ни .send_message(. Тот же класс дыры, что bl-111 (обход через
# алиас), только смысл спрятан не в переименовании, а в разнесении
# «написать/выполнить» по разным вызовам инструмента.
#
# Лечение: команда запускает *.py — файлом по пути или модулем через `-m` —
# читаем его и гоняем через ТЕ ЖЕ MAIL_PY/SEND_URL/(ACT_URL+NET_PY_POST), что
# и heredoc/`-c` ниже. Файла нет, путь не разобрать, модуль не найден на
# диске (стандартная библиотека, чужой пакет) — молчим: это не наш случай,
# а не повод падать или спрашивать.
#
# Свои же CLI с собственным ask и предохранителями (modules/outbox/send.py:
# без --send не отправляет, --send уже ловится OUTBOX_CLI выше) исключены
# явно — иначе обычный дневной прогон рассылки через `-m
# jarvis.modules.outbox.send` спрашивал бы на каждый дневной ход, потому что
# файл естественно содержит smtplib.
# --------------------------------------------------------------------------

PY_BASE = re.compile(r"^python3?(\.\d{1,2})?$")
PY_SCAN_LIMIT = 200_000            # байт: файл читается не целиком, не в цикл
# Свой же CLI с собственным ask (modules/outbox/send.py: без --send не
# отправляет, --send уже ловится OUTBOX_CLI выше по этой же функции).
SELF_GUARDED_SCRIPTS = {"modules/outbox/send.py"}
# Самотесты называются по одному соглашению во всём репозитории (run.py их
# так и находит — см. процедуру pravka-yadra, «grep -n selftest run.py»):
# `selftest.py` или `*_selftest.py`. Смысл самотеста — МОКАТЬ отправку, а не
# совершать её: modules/vk/selftest.py держит "messages.send" строкой в
# фиктивном fake_call, core/selftest.py — строкой "smtplib" в тестовых
# командах для classify(). Без этого исключения job-049 ломает ежедневный
# `python -m jarvis.core.selftest` (сотни живых вызовов 23-26.08) — самотест
# начал бы спрашивать сам на себя. Ту же дыру ловит samotest-ne-pishet-v-
# zhivuyu-pamyat: тексту самотеста доверяем, поведению — нет.
SELF_TEST_NAME = re.compile(r"(^|/)(selftest|\w+_selftest)\.py$")


def _py_target(rest: list[str]) -> tuple[str, str] | None:
    """Что запускает python: ('path', файл) или ('module', имя после -m).

    `-c` не разбираем здесь — код прямо в команде уже ловится ниже по
    тексту. Флаги (`-u`, `-O`, ...) пропускаем, не зная, берут ли они
    значение отдельным аргументом — редкий случай, честно недокрытый.
    """
    i = 0
    while i < len(rest):
        a = rest[i]
        if a == "-c":
            return None
        if a == "-m" and i + 1 < len(rest):
            return ("module", rest[i + 1])
        if a.startswith("-"):
            i += 1
            continue
        return ("path", a)
    return None


def _resolve_py_target(kind: str, val: str, cwd: Path | None) -> Path | None:
    """Файл на диске, который реально будет исполнен. None — не нашли."""
    base = cwd or C.ROOT
    try:
        if kind == "path":
            if not val.endswith(".py"):
                return None            # не .py — не наш случай (данные, конфиг)
            p = Path(val) if val.startswith("/") else base / val
            p = p.resolve()
            return p if p.is_file() else None
        rel = val.replace(".", "/") + ".py"
        candidates = [base / rel, C.ROOT / rel, C.ROOT.parent / rel]
        if rel.startswith("jarvis/"):
            candidates.append(C.ROOT / rel[len("jarvis/"):])
        for c in candidates:
            try:
                c = c.resolve()
            except Exception:
                continue
            if c.is_file():
                return c
        return None
    except Exception:
        return None


def _self_guarded_script(p: Path) -> bool:
    """Свой же модуль с уже встроенным ask, или самотест — второй раз не
    спрашивать и не путать мок с настоящей отправкой."""
    try:
        rel = p.resolve().relative_to(C.ROOT).as_posix()
    except Exception:
        return False
    return rel in SELF_GUARDED_SCRIPTS or bool(SELF_TEST_NAME.search(rel))


def _outbound(cmd: str) -> str | None:
    """Команда отправляет что-то наружу от имени Ильи? Тогда спросить его.

    Возвращает причину для ask (не для deny): отправка бывает нужна и
    разрешается словом Ильи, а не переписыванием команды.
    """
    body, heredocs = _cut_heredocs(cmd)
    # Относительные пути `python скрипт.py` считаются от фактического
    # каталога — команда часто начинается с `cd`, как и было 26.08 (job-049).
    cwd: Path | None = C.ROOT
    for sep, seg in _segments(_words(body)):
        words = [t for k, t in seg if k == "w"]
        args = list(words)
        while args and re.match(r"^[A-Za-z_]\w*=", args[0]):
            args.pop(0)
        if not args:
            continue
        base = args[0].rsplit("/", 1)[-1]
        rest = args[1:]
        while base in WRAPPERS and rest:
            while rest and (rest[0].startswith("-") or rest[0].isdigit()
                            or re.match(r"^[A-Za-z_]\w*=", rest[0])):
                rest.pop(0)
            if not rest:
                break
            base, rest = rest[0].rsplit("/", 1)[-1], rest[1:]

        if base == "cd":
            cwd = _cd_target(cwd, rest)
            continue
        line = " ".join(rest)

        if SELF_VOICE.search(line):
            continue
        if base in MAIL_CMDS:
            return f"письмо уходит командой {base}"
        if OUTBOX_CLI.search(line) and "--send" in rest:
            return "боевая отправка рассылки (--send): письма уйдут людям"
        if base in NET_CMDS and (SEND_URL.search(line)
                                 or (ACT_URL.search(line)
                                     and WRITE_METHODS.search(line))):
            return f"{base} шлёт данные на внешний сервис от имени Ильи"

        # job-049: файл, который команда запускает по имени, а не пишет в
        # своём тексте. Write(smtplib) в `send_nologo.py`, а следом просто
        # `python send_nologo.py --send` — ни слова о SMTP в самой команде.
        if PY_BASE.match(base):
            target = _py_target(rest)
            if target:
                script = _resolve_py_target(*target, cwd)
                if script and not _self_guarded_script(script):
                    try:
                        with script.open("r", encoding="utf-8", errors="ignore") as f:
                            content = f.read(PY_SCAN_LIMIT)
                    except OSError:
                        content = ""
                    if content and not SELF_VOICE.search(content):
                        if MAIL_PY.search(content):
                            return (f"письмо из кода: файл {script.name} со "
                                    "SMTP запускается по имени")
                        if SEND_URL.search(content):
                            return (f"сообщение в мессенджер из файла "
                                    f"{script.name}, запущенного по имени")
                        if ACT_URL.search(content) and NET_PY_POST.search(content):
                            return (f"отклик или заявка на площадке из файла "
                                    f"{script.name}, запущенного по имени")

        # Код на чужом языке: heredoc и `-c`. Разбирать его я не берусь,
        # но отправку в нём ищу — там она и живёт.
        texts = [heredocs[int(m.group(1))] for a in rest
                 if (m := re.fullmatch(r"__heredoc(\d+)__", a))]
        texts += [rest[k + 1] for k, a in enumerate(rest)
                  if a == "-c" and k + 1 < len(rest)]
        for t in texts:
            if SELF_VOICE.search(t):
                continue
            if MAIL_PY.search(t):
                return "письмо из кода: SMTP прямо в командной строке"
            if SEND_URL.search(t):
                return "сообщение в мессенджер из кода"
            if ACT_URL.search(t) and NET_PY_POST.search(t):
                return "отклик или заявка на площадке из кода"
    return None


# --------------------------------------------------------------------------
# Граница раздела 8: «Не делаешь необратимых действий во внешнем мире без
# подтверждения» — её сетевая часть (bl-096).
#
# До 26.08.2026 сеть из шелла держал только счётчик похода в поле, а он не
# отличает POST от чтения страницы: прочитать сайт и создать на нём запись
# стоило для шлюза одинаково. Пункт 1 с 25.08 закрывает знакомые площадки
# (hh, fl, farpost, avito, мессенджеры) — но ровно их и ничего сверх.
#
# Теперь: изменяющий запрос (POST/PUT/PATCH/DELETE, curl -d/-F/-T, requests.
# post и родня) на адрес ВНЕ короткого белого списка — ask. Отдельно и строже
# ловятся деньги: платёжный домен или платёжный путь в URL, платёжная
# библиотека в коде — ask на любом методе, потому что «прочитать» у платёжного
# API легко оказывается «создать платёж».
#
# Чтение не трогается вовсе: GET, curl -G, скачивание файла, любые заголовки.
# Белый список — то, где запись рутинна и обратима: свой компьютер, локальная
# сеть, Notion (рабочий стол Ильи, правка с журналом и откатом) и ВК (мой
# собственный канал; отправка людям всё равно ловится пунктом 1 раньше).
# --------------------------------------------------------------------------

NET_CLI = {"curl", "wget", "httpie", "http", "https", "aria2c", "xh"}
NET_PY_ANY = re.compile(r"\b(requests|httpx|aiohttp|urlopen|urllib)\b")
CURL_METHOD = re.compile(
    r"(-X|--request|--method)[ =]\s*['\"]?(POST|PUT|PATCH|DELETE)\b", re.I)
# Флаги разбираются С УЧЁТОМ регистра: у curl -d данные, а -D — заголовки в
# файл; -T заливка, -t телнет; -F форма, -f тихий отказ. re.I здесь дал два
# ложных срабатывания на живом логе (чтение FarPost с `-D -`).
CURL_WRITE_FLAG = re.compile(
    r"(^|\s)(-d|--data|--data-\w+|-F|--form|-T|--upload-file|--json)([ =]|$)|"
    r"--post-data|--post-file")
# `-G` превращает данные в строку запроса: это чтение, а не отправка.
CURL_READ = re.compile(
    r"(^|\s)(-G|--get)([ =]|$)|(-X|--request)[ =]\s*['\"]?(GET|HEAD)\b", re.I)
NET_PY_WRITE = re.compile(
    r"\b(requests|httpx|aiohttp|session|sess|client|cli)\.(post|put|patch|delete)\s*\(|"
    r"urlopen\s*\([^)]*\bdata\s*=|"
    r"\brequest\s*\(\s*['\"](POST|PUT|PATCH|DELETE)|"
    r"method\s*=\s*['\"](POST|PUT|PATCH|DELETE)", re.I)
# bl-111: NET_PY_WRITE знает только буквальные имена. Переименование модуля
# (import requests as req) или импорт функции напрямую (from requests import
# post) проходят мимо него целиком, хотя NET_PY_ANY уже видит сеть в тексте.
# Здесь такие имена вытаскиваются из кода и проверяются той же логикой:
# .post/.put/.patch/.delete/.request(МЕТОД) для объектов (модуль, сессия,
# клиент), голый ИМЯ(...) — только для функций, пришедших from-import.
NET_ALIAS_IMPORT = re.compile(
    r"\bimport\s+(requests|httpx|aiohttp|urllib(?:\.request)?)\s+as\s+([A-Za-z_]\w*)")
NET_ALIAS_FROM = re.compile(
    r"\bfrom\s+(requests|httpx|aiohttp|urllib(?:\.request)?)\s+import\s+([^\n#]+)")
NET_ALIAS_CTOR = re.compile(
    r"\b([A-Za-z_]\w*)\s*=\s*(?:requests|httpx|aiohttp)\.(?:Session|Client|AsyncClient)\s*\(")
NET_WRITE_VERBS = ("post", "put", "patch", "delete")


def _net_py_write_aliased(text: str) -> bool:
    """Изменяющий вызов через переименованный сетевой модуль/сессию (bl-111).

    Три обхода мимо NET_PY_WRITE: `import requests as req` → `req.post(...)`,
    `from requests import post` → голый `post(...)`, `X = requests.Session()`
    → `X.post(...)`. Заодно urllib: `import urllib.request as u` →
    `u.urlopen(..., data=...)`, `from urllib.request import urlopen as g` →
    `g(..., data=...)`.
    """
    objs: set[str] = set()
    bare: set[str] = set()
    urlopen_dot: set[str] = set()
    urlopen_bare: set[str] = set()

    for mod, alias in NET_ALIAS_IMPORT.findall(text):
        (urlopen_dot if mod.startswith("urllib") else objs).add(alias)
    objs.update(NET_ALIAS_CTOR.findall(text))
    for mod, names in NET_ALIAS_FROM.findall(text):
        for part in names.split(","):
            part = part.strip()
            if not part:
                continue
            pieces = re.split(r"\s+as\s+", part)
            orig, alias = pieces[0].strip(), pieces[-1].strip()
            if not re.fullmatch(r"[A-Za-z_]\w*", alias):
                continue
            if mod.startswith("urllib"):
                if orig == "urlopen":
                    urlopen_bare.add(alias)
                continue
            objs.add(alias)
            if orig.lower() in NET_WRITE_VERBS:
                bare.add(alias)

    if objs:
        names = "|".join(re.escape(a) for a in objs)
        if re.search(
                rf"\b(?:{names})\.(?:post|put|patch|delete)\s*\(|"
                rf"\b(?:{names})\.request\s*\(\s*['\"](?:POST|PUT|PATCH|DELETE)",
                text, re.I):
            return True
    if bare:
        names = "|".join(re.escape(a) for a in bare)
        if re.search(rf"\b(?:{names})\s*\(", text):
            return True
    if urlopen_dot:
        names = "|".join(re.escape(a) for a in urlopen_dot)
        if re.search(rf"\b(?:{names})\.urlopen\s*\([^)]*\bdata\s*=", text):
            return True
    if urlopen_bare:
        names = "|".join(re.escape(a) for a in urlopen_bare)
        if re.search(rf"\b(?:{names})\s*\([^)]*\bdata\s*=", text):
            return True
    return False
# Деньги: платёжные шлюзы, банковские API и денежные пути в адресе.
PAY_URL = re.compile(
    r"yookassa|yoomoney|cloudpayments|robokassa|payanyway|paykeeper|unitpay|"
    r"tinkoff|tbank|acquiring|securepayments|sberbank|alfabank|modulbank|"
    r"qiwi|stripe\.com|paypal|payeer|webmoney|"
    r"/pay(ment|out|)s?\b|/invoice|/checkout|/billing|/withdraw|/transfer", re.I)
PAY_LIB = re.compile(
    r"\b(yookassa|cloudpayments|robokassa|stripe|paypalrestsdk|paypalhttp|"
    r"tinkoff_acquiring|yoomoney)\b", re.I)
URL_IN_TEXT = re.compile(r"https?://[^\s'\"()<>]+")
LOCAL_HOST = re.compile(
    r"^(localhost|127\.\d+\.\d+\.\d+|0\.0\.0\.0|\[?::1\]?|"
    r"10\.\d+\.\d+\.\d+|192\.168\.\d+\.\d+|172\.(1[6-9]|2\d|3[01])\.\d+\.\d+)"
    r"(:\d+)?$", re.I)
KNOWN_HOSTS = ("api.notion.com", "api.vk.com", "vk.com", "userapi.com")


def _known_host(host: str) -> bool:
    """Адрес, где изменяющий запрос — рутина: свой компьютер, Notion, ВК."""
    host = host.split("@")[-1].lower().strip("[]")
    if LOCAL_HOST.match(host):
        return True
    bare = host.split(":")[0]
    return any(bare == k or bare.endswith("." + k) for k in KNOWN_HOSTS)


def _net_change(cmd: str) -> str | None:
    """Изменяющий сетевой запрос наружу или деньги? Тогда спросить Илью.

    Возвращает причину для ask (не для deny): такое действие бывает нужным,
    но решает его Илья, а не моя торопливость. В ходе без канала ask сам
    превратится в deny.
    """
    body, heredocs = _cut_heredocs(cmd)
    for _sep, seg in _segments(_words(body)):
        args = [t for k, t in seg if k == "w"]
        while args and re.match(r"^[A-Za-z_]\w*=", args[0]):
            args.pop(0)
        if not args:
            continue
        base = args[0].rsplit("/", 1)[-1]
        rest = args[1:]
        while base in WRAPPERS and rest:
            while rest and (rest[0].startswith("-") or rest[0].isdigit()
                            or re.match(r"^[A-Za-z_]\w*=", rest[0])):
                rest.pop(0)
            if not rest:
                break
            base, rest = rest[0].rsplit("/", 1)[-1], rest[1:]

        chunks: list[tuple[str, str]] = []
        if base in NET_CLI:
            chunks.append(("cli", " ".join(rest)))
        chunks += [("code", heredocs[int(m.group(1))]) for a in rest
                   if (m := re.fullmatch(r"__heredoc(\d+)__", a))]
        chunks += [("code", rest[k + 1]) for k, a in enumerate(rest)
                   if a in ("-c", "-e") and k + 1 < len(rest)]

        for kind, text in chunks:
            if kind == "cli":
                net = True
                writes = bool(CURL_METHOD.search(text)
                              or CURL_WRITE_FLAG.search(text)) \
                    and not CURL_READ.search(text)
            else:
                net = bool(NET_PY_ANY.search(text))
                writes = bool(NET_PY_WRITE.search(text)) \
                    or _net_py_write_aliased(text)
            urls = URL_IN_TEXT.findall(text)
            if ((kind == "code" and PAY_LIB.search(text))
                    or any(PAY_URL.search(u) for u in urls)
                    or (net and writes and PAY_URL.search(text))):
                return (f"деньги: платёжный признак в сетевом вызове ({base}). "
                        "Раздел 8 — необратимое во внешнем мире")
            if not writes:
                continue
            hosts = [re.sub(r"^https?://", "", u).split("/")[0] for u in urls]
            if hosts and all(_known_host(h) for h in hosts):
                continue
            where = hosts[0] if hosts else "адрес собран из переменной"
            return (f"изменяющий запрос наружу: {base} → {where}. "
                    "Раздел 8 — необратимое во внешнем мире")
    return None


def _rm_danger(rest: list[str], cwd: Path | None) -> str | None:
    """Рекурсивное удаление. Разрешено только внутри derived/ — он восстановим.

    Раздел 9 конституции: derived можно снести целиком и пересобрать. Всё
    остальное — включая мою же папку с кодом и памятью — под запретом, даже
    если сносить собирался я сам и «всего лишь временный каталог».
    """
    flags = [a for a in rest if a.startswith("-") and a != "-"]
    paths = [a for a in rest if not a.startswith("-")]
    recursive = any(
        (not f.startswith("--") and re.search(r"[rRf]", f))
        or f in ("--recursive", "--force", "-fr", "-rf")
        for f in flags)
    if not recursive:
        return None
    if cwd is None:
        return "рекурсивное удаление при неизвестном рабочем каталоге"
    if not paths:
        return None
    if all(_path_under(p, C.ROOT / "derived", cwd) for p in paths):
        return None
    return "рекурсивное или принудительное (-f) удаление вне derived"


# Дублирующая страховка на уровне CLI: правила deny перебивают любой режим.
NEVER = ["Bash(rm -rf *)", "Bash(sudo *)", "Bash(git push*)", "Bash(mkfs*)",
         "Bash(dd if=*)", "Bash(shutdown*)", "Bash(reboot*)"]

# allowed_tools намеренно пуст: запись туда автоодобряет вызов ДО шлюза,
# и правило «никогда не выходить за пределы папки» перестало бы работать.
# Единственный источник решений — gate().


def _inside(path: str | None) -> bool:
    """Путь лежит внутри папки агента — по границе каталога, а не по префиксу.

    Через startswith() соседняя папка «jarvis2» считалась своей: строка
    начинается с «…/jarvis». is_relative_to() сравнивает по сегментам пути.
    """
    if not path:
        return False
    try:
        return Path(path).expanduser().resolve().is_relative_to(C.ROOT)
    except Exception:
        return False


# --------------------------------------------------------------------------
# Граница раздела 9 для ГЛАВНОГО агента: «Запись вне папки агента запрещена
# ни одним путём». Write/Edit это уже держат (_inside выше). У Bash до
# 30.08.2026 держала одна строка — «шелл внутри своей папки» — и она врала:
# в режиме careful чтение (SAFE_BASH) отсекалось только по первому слову
# команды, а всё остальное в non-careful режиме проходило БЕЗ разбора цели.
# `echo x > /etc/hosts`, `cat a > ~/.bashrc`, `touch /tmp/x && cp secret
# /tmp/`, `bash -c "echo x > ~/y"`, `find / -delete` — все читались как
# «безопасно», потому что читалось только первое слово, а не вся команда.
#
# Разбит на два раздельных вопроса:
#   1. _shell_pure_read — команда точно НИЧЕГО не пишет? Тогда цена нулевая
#      и её можно читать как раньше (SAFE_BASH), но только если в ней нет
#      разделителя (;, &&, |, обратные кавычки и $(...) — парсер превращает
#      их в границу сегмента), перенаправления (>, >>) и разрушающих флагов
#      у find (-delete, -exec и родня). `python3 -c`/`bash -c` из безопасных
#      исключены отдельно — регуляркой SAFE_BASH, где `-c` больше не значится.
#   2. _root_write_danger — если команда всё же пишет, каждая её цель обязана
#      лежать внутри корня агента. Не разбирается (shell -c, python -c,
#      heredoc в интерпретатор) — считается «пишет неизвестно куда» и
#      отбивается: доверять произвольному коду по умолчанию нельзя, а
#      спросить в фоновом ходе некого (в контуре careful, где спросить
#      можно, до этой проверки дело не доходит — там ask раньше).
# --------------------------------------------------------------------------

# Разрушающие флаги find: без -delete/-exec find — обычное чтение (поиск по
# имени, размеру, времени), и это активно используется (карта дисков t-003).
FIND_WRITE_FLAGS = {"-delete", "-exec", "-execdir", "-ok", "-okdir",
                     "-fprint", "-fprint0", "-fprintf", "-fls"}

# Команды, у которых ЛЮБОЙ путь-аргумент (кроме флагов) — цель записи.
# Ровно список из брифа плюс очевидные синонимы rm (unlink/shred/truncate,
# уже соседствуют в LOG_ZONE_CMDS с тем же смыслом). tar/unzip/rsync/awk/perl
# сюда не включены: они в основном читают (список архива, фильтр текста), а
# универсальный запрет на все их пути-аргументы ломал бы легитимное чтение
# по всему диску (pc-dostup-ustroystvo) — не то, о чём просил Илья.
ROOT_WRITE_VERBS = {"mv", "touch", "mkdir", "rmdir", "rm", "tee", "dd",
                    "chmod", "chown", "unlink", "shred", "truncate"}
# У cp/install/ln источник — чтение (можно откуда угодно, как и в
# _log_zone_danger), опасна только цель: последний путь либо аргумент
# -t/--target-directory. mv сюда не входит: у mv источник ИСЧЕЗАЕТ, то есть
# меняется, а не только читается, — обе стороны идут в ROOT_WRITE_VERBS.
ROOT_WRITE_TARGET_ONLY = {"cp", "install", "ln"}
# git-подкоманды, которые пишут в репозиторий (текущий, если не указано иное).
GIT_WRITE_SUBCMDS = {"add", "commit", "checkout", "reset", "clean", "mv",
                     "rm", "apply", "stash", "merge", "rebase",
                     "cherry-pick", "worktree", "config", "init",
                     "commit-tree", "update-index", "am", "revert", "tag",
                     "restore", "switch", "gc", "reflog", "filter-branch"}


def _write_target_ok(p: str, root: Path, cwd: Path | None) -> bool:
    """Путь-цель записи заведомо лежит внутри root? Неизвестность — «нет».

    В отличие от _path_under (её дело — заметить попадание в опасную зону,
    и там неизвестный каталог считается «может, и есть» ради осторожности),
    здесь неизвестность работает в другую сторону: разрешение — это то, что
    нужно ЗАСЛУЖИТЬ, а не то, что выдаётся по умолчанию. `cd /tmp && cp x y`
    обязан остаться под запретом: относительный `y` после ухода в неизвестное
    (`cwd is None`) не пересчитывается обратно на root.
    """
    if not p or p == "-":
        return True
    # /dev/null и стандартные потоки — не файл ни в чьей папке: перенаправление
    # туда ничего не пишет на диск, и запрещать его нет смысла. До этой строки
    # обычный хвост «2>/dev/null» (подавить шум) отбивался как «перенаправление
    # вывода вне папки агента» — ложный отказ на любой команде с этим хвостом
    # (ob-083, живой случай 31.08/01.09).
    if p in ("/dev/null", "/dev/stdout", "/dev/stderr"):
        return True
    if p.startswith("~") or "$" in p:
        return False
    # Скобки шелл раскрывает раньше всего: `{/etc,x}/passwd` выглядит
    # относительным путём внутри папки, а ведёт наружу. Годится, только если
    # годится КАЖДАЯ ветка (класс job-192).
    if "{" in p:
        branches = [c for c in _brace_candidates(p) if c != p]
        if branches:            # нераскрываемые «{}» судятся обычным порядком
            return all(_write_target_ok(c, root, cwd) for c in branches)
    p2 = re.split(r"[*?\[]", p)[0]
    try:
        if not p2:
            return cwd is not None and cwd.resolve().is_relative_to(root)
        if p2.startswith("/"):
            q = Path(p2).resolve()
        else:
            if cwd is None:
                return False
            q = (cwd / p2).resolve()
        return q.is_relative_to(root)
    except Exception:
        return False


def _shell_pure_read(cmd: str) -> bool:
    """Команда гарантированно ничего не пишет — safe-read без оговорок?

    Один сегмент (никакого ;, &&, ||, |, \\n — парсер разбивает по ним, и
    обратные кавычки/$(...) тоже превращаются в границу сегмента), внутри
    сегмента нет операции перенаправления (>, >>, >|), а find не позван с
    разрушающим флагом. Это ЧАСТЬ условия «чтение через шелл» — вторая
    часть (первое слово входит в SAFE_BASH, а `python3 -c` там больше нет)
    проверяется отдельно в classify().
    """
    try:
        body, _ = _cut_heredocs(cmd)
        segs = _segments(_words(body))
        if len(segs) != 1 or segs[0][0]:
            return False
        seg = segs[0][1]
        if any(kind == "op" for kind, _ in seg):
            return False
        words = [t for k, t in seg if k == "w"]
        if not words:
            return False
        base = words[0].rsplit("/", 1)[-1]
        if base == "find" and any(a in FIND_WRITE_FLAGS for a in words[1:]):
            return False
        return True
    except Exception:
        return False


# Тело `python -c` у главного агента (без зоны и без проекта). До 31.08 любое
# `-c` отбивалось безусловно — «тело не разбирается». Разбирается: тем же ast,
# каким его читает роль с зоной (_zone_py_body ниже). Цена безусловного отказа
# оказалась выше цены разбора: единственный ЗАКОННЫЙ способ править очередь —
# `python -c "from jarvis.core import memory as M; M.backlog_update(…)"` — сам
# же шлюз и запрещал, а промпт при этом требовал им пользоваться (31.08, живой
# отказ на bl-148). Здесь белый список шире зонного: своё имя `jarvis`, `os` и
# `sys` открыты, потому что дело этой проверки — запись МИМО папки агента, а не
# «роль не должна считать». Опасное закрыто по узлам дерева: чужие модули
# (subprocess, shutil, socket, ctypes) не импортируются вовсе, а без импорта
# строка «os.system» вызовом не станет; запись и запуск закрыты по именам
# атрибутов, сбор вызова по буквам — по eval/exec/getattr/дандерам.
ROOT_PY_IMPORTS_OK = frozenset({"jarvis", "os", "sys"})
ROOT_PY_BAD_NAMES = frozenset((
    "eval", "exec", "compile", "__import__", "getattr", "setattr", "delattr",
    "globals", "locals", "vars", "breakpoint", "memoryview"))
# Атрибуты записи, удаления и запуска. Чтение (read_text, iterdir, glob,
# read) остаётся открытым — оно и так разрешено инструментом Read.
ROOT_PY_BAD_ATTRS = frozenset((
    "system", "popen", "fork", "forkpty", "kill", "killpg", "dup2",
    "spawn", "spawnl", "spawnv", "spawnvp", "spawnlp",
    "execv", "execve", "execl", "execlp", "execvp", "execvpe",
    "run", "Popen", "call", "check_call", "check_output",
    "getoutput", "getstatusoutput",
    "remove", "unlink", "rmdir", "removedirs", "rename", "renames",
    "replace", "rmtree", "move", "copy", "copy2", "copyfile", "copytree",
    "mkdir", "makedirs", "touch", "chmod", "chown", "chdir",
    "symlink", "symlink_to", "hardlink_to", "link", "mknod",
    "truncate", "ftruncate", "write_text", "write_bytes",
    "open", "fdopen", "mkfifo", "urlopen", "socket", "connect", "sendall"))


def _root_py_body_danger(code: str, root: Path, cwd: Path | None) -> str | None:
    """Причина отказа для тела `python -c` у главного агента. Иначе None.

    Судим по разбору, а не по подстроке: «не вижу» и «безопасно» — разное, но
    и «не смотрел» — не то же самое, что «нельзя». Тело не разбирается питоном
    — отказ, как и раньше.
    """
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return "тело не разбирается питоном"
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                if a.name.split(".")[0] not in (
                        ROOT_PY_IMPORTS_OK | ZONE_PY_IMPORTS_OK):
                    return f"импорт {a.name}"
        elif isinstance(node, ast.ImportFrom):
            head = (node.module or "").split(".")[0]
            if node.level or head not in (
                    ROOT_PY_IMPORTS_OK | ZONE_PY_IMPORTS_OK):
                return f"импорт из {node.module or '.'}"
            for a in node.names:
                if a.name in ROOT_PY_BAD_ATTRS or a.name in ROOT_PY_BAD_NAMES:
                    return f"импорт имени {a.name}"
        elif isinstance(node, ast.Attribute):
            if node.attr.startswith("__") or node.attr in ROOT_PY_BAD_ATTRS:
                return f"атрибут .{node.attr}"
        elif isinstance(node, ast.Name):
            if node.id in ROOT_PY_BAD_NAMES:
                return f"вызов {node.id}"
            if node.id.startswith("__"):
                return f"имя {node.id}"
        elif isinstance(node, ast.Call):
            f = node.func
            if isinstance(f, ast.Name) and f.id == "open":
                mode = node.args[1] if len(node.args) > 1 else None
                for kw in node.keywords:
                    if kw.arg == "mode":
                        mode = kw.value
                if mode is None:
                    continue                      # без режима — чтение
                if not (isinstance(mode, ast.Constant)
                        and isinstance(mode.value, str)):
                    return "open с непонятным режимом"
                if not ZONE_PY_WRITE_MODE.search(mode.value):
                    continue
                where = node.args[0] if node.args else None
                if not (isinstance(where, ast.Constant)
                        and isinstance(where.value, str)):
                    return "open на запись по пути, которого шлюзу не видно"
                if not _write_target_ok(where.value, root, cwd):
                    return f"open на запись вне папки агента: {where.value}"
    return None


def _root_write_danger(cmd: str, root: Path) -> str | None:
    """Причина отказа, если команда пишет мимо root. None — не пишет, либо
    все её цели проверенно внутри root.
    """
    try:
        body, heredocs = _cut_heredocs(cmd)
        cwd: Path | None = root
        for sep, seg in _segments(_words(body)):
            for idx, (kind, t) in enumerate(seg):     # > >> >| пишут всегда
                if kind == "op" and t in REDIRECTS and idx + 1 < len(seg):
                    target = seg[idx + 1][1]
                    if not _write_target_ok(target, root, cwd):
                        return (f"перенаправление вывода вне папки агента: "
                                f"{target}")

            words = [t for k, t in seg if k == "w"]
            args = list(words)
            while args and re.match(r"^[A-Za-z_]\w*=", args[0]):
                args.pop(0)
            if not args:
                continue
            base = args[0].rsplit("/", 1)[-1]
            rest = args[1:]
            while base in WRAPPERS and rest:
                while rest and (rest[0].startswith("-") or rest[0].isdigit()
                                or re.match(r"^[A-Za-z_]\w*=", rest[0])):
                    rest.pop(0)
                if not rest:
                    break
                base, rest = rest[0].rsplit("/", 1)[-1], rest[1:]

            if base == "cd":
                cwd = _cd_target(cwd, rest)
                continue

            if base in SHELLS or base == "eval":
                if any(a == "-c" for a in rest):
                    return ("shell -c: тело не разбирается, доверять по "
                            "умолчанию нельзя")
                if any(re.fullmatch(r"__heredoc(\d+)__", a) for a in rest):
                    return ("heredoc в интерпретатор: тело не разбирается, "
                            "доверять по умолчанию нельзя")
                continue
            if re.fullmatch(r"(python|python3|py|pypy)[0-9.]*", base):
                for k, a in enumerate(rest):
                    if a != "-c":
                        continue
                    if k + 1 >= len(rest):
                        return "python -c без тела"
                    code = rest[k + 1]
                    if re.fullmatch(r"__heredoc(\d+)__", code):
                        return ("python -c из heredoc: тело подставляет шелл, "
                                "разобрать его нечем")
                    # Подстановка ($(…), `…`, $VAR) собирает тело уже после
                    # проверки. Парсер режет $(…) на границе сегмента, и на
                    # месте тела остаётся пустая строка — пустое тело
                    # разбирается питоном как валидная пустая программа, и
                    # ровно так проверка пропускала `python3 -c "$(cat x.py)"`.
                    if not code.strip() or "$" in code or "`" in code:
                        return ("python -c: тело собирает сам шелл "
                                "(подстановка или переменная) — проверять нечего")
                    if why := _root_py_body_danger(code, root, cwd):
                        return f"python -c: {why}"
                    break
                continue

            if base == "find":
                if any(a in FIND_WRITE_FLAGS for a in rest):
                    search = []
                    for a in rest:
                        if a.startswith("-"):
                            break
                        search.append(a)
                    if not search:
                        search = ["."]
                    if any(not _write_target_ok(p, root, cwd) for p in search):
                        return (f"find с разрушающим флагом (-delete/-exec) "
                                f"по пути вне папки агента: {' '.join(search)}")
                continue

            if base == "git" and rest:
                elsewhere = []
                for i, a in enumerate(rest):
                    if a in ("-C", "--git-dir", "--work-tree") and i + 1 < len(rest):
                        elsewhere.append(rest[i + 1])
                    elif "=" in a and a.split("=", 1)[0] in (
                            "--git-dir", "--work-tree"):
                        elsewhere.append(a.split("=", 1)[1])
                if any(not _write_target_ok(p, root, cwd) for p in elsewhere):
                    return "git с --git-dir/--work-tree/-C вне папки агента"
                if rest[0] in GIT_WRITE_SUBCMDS and cwd is None:
                    return ("git-запись после cd в неизвестное или внешнее "
                            "место: куда пишет git — не разобрать")
                continue

            if base == "dd":
                for a in rest:
                    if a.startswith("of="):
                        if not _write_target_ok(a[3:], root, cwd):
                            return f"dd of= вне папки агента: {a[3:]}"
                continue

            if base in ("sed", "perl") and any(
                    a.startswith("-i") or a == "--in-place" for a in rest):
                paths = [a for a in rest if not a.startswith("-") and a != "-"]
                if any(not _write_target_ok(p, root, cwd) for p in paths):
                    return f"{base} -i (правка на месте) вне папки агента"
                continue

            if base in ROOT_WRITE_TARGET_ONLY:
                paths = [a for a in rest if not a.startswith("-") and a != "-"]
                if not paths:
                    continue
                targeted = any(a.startswith("-t") or a.startswith("--target-directory")
                               for a in rest)
                watched = paths if targeted else paths[-1:]
                bad = [p for p in watched if not _write_target_ok(p, root, cwd)]
                if bad:
                    return f"{base} — цель вне папки агента: {', '.join(bad)}"
                continue

            if base in ROOT_WRITE_VERBS:
                paths = [a for a in rest if not a.startswith("-") and a != "-"]
                if not paths:
                    continue
                bad = [p for p in paths if not _write_target_ok(p, root, cwd)]
                if bad:
                    return f"{base} по пути вне папки агента: {', '.join(bad)}"
    except Exception as e:
        return f"разбор команды на цели записи не удался ({e})"
    return None


# Работа в поле: то, что должен делать подагент, а не главный агент.
# Главный занят ровно в момент раздачи и сбора, всё остальное время он свободен
# для Ильи — иначе вопрос «сколько нашёл» ждёт конца получасовой работы.
FIELD_TOOLS = {"WebSearch", "WebFetch"}

# Поле бывает не только инструментом. 25.08 партия AF, упёршись в лимит,
# приняла отказ шлюза за подлог со страницы и дочитала полсотни сайтов через
# `curl` в Bash. Ущерба не было — она делала ровно свою работу, — но обход
# предохранителя входит в привычку и однажды сработает там, где запрет был по
# делу. Сеть из шелла считается полем наравне с WebFetch.
NET_BASH = re.compile(
    r"\b(curl|wget|aria2c|httpie|lynx|w3m|links2?|scrapy)\b"
    r"|\bpython[0-9.]*\b[^\n]*\b(requests|urllib|httpx|aiohttp|socket)\b")

# Та же проверка, но по разбору команды, а не по подстроке. NET_BASH выше
# ищет слова во ВСЕЙ строке — включая тело heredoc и текст, который я пишу в
# файл. 26.08 (bl-097) запись абзаца в memory/core_changes.md со словами curl
# и requests была отбита как поход в поле: ложный запрет учит обходить
# предохранитель перефразированием и тратит ход впустую. NET_BASH оставлен
# страховкой на случай, когда разбор упал: тогда закрываемся, а не открываемся.
NET_FIELD_CLI = NET_CLI | {"lynx", "w3m", "links", "links2", "elinks",
                           "scrapy", "youtube-dl", "yt-dlp"}
# Настоящий сетевой вызов в чужом коде: не упоминание слова, а обращение.
NET_PY_CALL = re.compile(
    r"\b(requests|httpx|aiohttp|urllib3|urllib\.request)\s*\.\s*[A-Za-z_]\w*\s*\(|"
    r"\burlopen\s*\(|"
    r"\bimport\s+(requests|httpx|aiohttp|urllib|urllib3)\b|"
    r"\bfrom\s+(requests|httpx|aiohttp|urllib|urllib3)[\w.]*\s+import\b|"
    r"\b(session|sess|client|cli)\s*\.\s*(get|post|put|patch|delete|head|request)\s*\(|"
    r"\bsocket\s*\.\s*(socket|create_connection)\s*\(")


def _net_field(cmd: str, depth: int = 0) -> str | None:
    """Команда правда ходит в сеть? Тогда это поле, и счётчик её считает.

    Полем считается только сегмент, где сетевая команда стоит в позиции
    команды (как в _outbound и _net_change), либо чужой код, который сам
    делает сетевой вызов. Слово `curl` внутри записываемого текста полем не
    является.
    """
    if depth > 3:
        return None
    body, heredocs = _cut_heredocs(cmd)
    for _sep, seg in _segments(_words(body)):
        args = [t for k, t in seg if k == "w"]
        while args and re.match(r"^[A-Za-z_]\w*=", args[0]):
            args.pop(0)
        if not args:
            continue
        base = args[0].rsplit("/", 1)[-1]
        rest = args[1:]
        while base in WRAPPERS and rest:
            while rest and (rest[0].startswith("-") or rest[0].isdigit()
                            or re.match(r"^[A-Za-z_]\w*=", rest[0])):
                rest.pop(0)
            if not rest:
                break
            base, rest = rest[0].rsplit("/", 1)[-1], rest[1:]
        if base in NET_FIELD_CLI:
            return base
        texts = [heredocs[int(m.group(1))] for a in rest
                 if (m := re.fullmatch(r"__heredoc(\d+)__", a))]
        texts += [rest[k + 1] for k, a in enumerate(rest)
                  if a in ("-c", "-e") and k + 1 < len(rest)]
        if base in SHELLS or base == "eval":
            # Шелл исполняет вложенный текст — разбираем его тем же способом.
            for t in texts + ([" ".join(rest)] if base == "eval" else []):
                if r := _net_field(t, depth + 1):
                    return r
            continue
        for t in texts:
            if NET_PY_CALL.search(t):
                return f"сетевой вызов в коде ({base})"
    return None


def _is_field_bash(cmd: str) -> bool:
    """Поход в поле из шелла. Разбор упал — считаем полем (закрываемся)."""
    try:
        return bool(_net_field(cmd))
    except Exception:
        return bool(NET_BASH.search(cmd))

# Чем работа отдаётся: задание отдельному процессу либо подагент внутри хода.
DELEGATE_TOOLS = {"mcp__memory__delegate", "Agent", "Task"}

# Счётчики поля в текущем ходе. Живут в памяти процесса ровно на один ход.
# _field_calls — походы главного потока, _field_budget — его нынешний потолок,
# _worker_calls — походы каждого подагента отдельно (ключ: agent_id или "worker").
#
# Раньше здесь стоял флаг _delegated: первое же делегирование снимало
# ограничитель до конца хода, и снять его было нечем — флаг не сбрасывался
# (bl-075). 25.08 из этого вышло 3635 обращений в поле при лимите 6.
# Теперь делегирование не выключает счётчик, а поднимает потолок на аванс,
# и неизрасходованный аванс сгорает, когда подагент вернулся.
_field_calls = 0
_field_budget = C.FIELD_WORK_LIMIT
_worker_calls: dict[str, int] = {}


def reset_field_counter() -> None:
    global _field_calls, _field_budget, _worker_calls
    _field_calls = 0
    _field_budget = C.FIELD_WORK_LIMIT
    _worker_calls = {}


def field_state() -> dict:
    """Снимок счётчиков поля — для самотеста и разбора хода."""
    return {"calls": _field_calls, "budget": _field_budget,
            "workers": dict(_worker_calls)}


def _grant_field(bonus: int, why: str) -> None:
    """Работа отдана — потолок поднят, но не снят."""
    global _field_budget
    was = _field_budget
    _field_budget = min(C.FIELD_BUDGET_MAX, _field_budget + bonus)
    if _field_budget != was:
        M.log("self", {"field_budget": _field_budget, "было": was, "повод": why})


def delegation_done(agent_id: str | None = None) -> None:
    """Подагент вернулся: неизрасходованный аванс сгорает.

    Вызывается PostToolUse-хуком на Agent/Task. Главному остаётся базовый
    запас на приёмку — ровно столько же, сколько было на разведку до брифа.
    """
    global _field_budget
    was = _field_budget
    _field_budget = min(C.FIELD_BUDGET_MAX,
                        min(_field_budget, _field_calls) + C.FIELD_WORK_LIMIT)
    if _field_budget != was:
        M.log("self", {"field_budget": _field_budget, "было": was,
                       "повод": f"подагент вернулся ({agent_id or '—'})"})


def is_worker() -> bool:
    """Этот процесс — сам подагент? Ему в поле работать и положено."""
    return os.environ.get("JARVIS_ROLE") == "worker"


# Инструменты записи. Их путь сверяется с зоной роли, если зона задана.
ZONE_WRITE_TOOLS = ("Write", "Edit", "MultiEdit", "NotebookEdit")

# MCP-инструменты памяти, которые ПИШУТ в общую память. Зоной они не ловились
# до 26.08 (bl-089): у них нет пути в аргументах, файл создаёт мой процесс, а
# не Write роли, — и юрист мог завести факт или обязательство мимо своей папки.
# Отказ здесь по имени инструмента, а не по пути: путь тут не при чём, писать в
# общую память роли нельзя вовсе.
ZONE_MEMORY_WRITE = frozenset((
    "remember_fact", "remember_rule", "remember_procedure",
    "add_obligation", "close_obligation", "update_now"))

# Шелл, который что-то меняет. Проверка грубая и намеренно строгая: команда
# «echo привет > memory/facts/x.md» начинается с безобидного echo и проходит
# SAFE_BASH, а пишет в чужую память. Разбирать шелл по-настоящему я не берусь,
# поэтому у роли с зоной любой признак записи — отказ, а не разбор цели.
ZONE_BASH_WRITE = re.compile(
    r"(^|[\s;&|])(>|>>|tee\b|sed\s+-i|rm\b|mv\b|cp\b|mkdir\b|rmdir\b|touch\b|"
    r"chmod\b|chown\b|ln\b|dd\b|truncate\b|install\b|tar\b|unzip\b|git\s+(add|commit|checkout|reset|clean|push))")

# Убийство процессов. Роли оно закрыто: смотреть на процессы — чтение,
# останавливать их — изменение состояния машины, и цена ошибки здесь чужая
# (упавший демон ВК, оборванная рассылка).
#
# 30.08 (bl-135, находка критика): запрет держался НА ОТСУТСТВИИ слова в
# SAFE_BASH, а не на активном отказе. Голый `kill -9 …` отбивался только
# потому, что не начинался с разрешённого слова, — а «ps aux; kill -9 <pid>»
# начинается с ps, проходит SAFE_BASH.match и в ZONE_BASH_WRITE слова kill
# нет. Через точку с запятой, через `| xargs kill` и через `pgrep; killall`
# зона проходилась насквозь.
#
# Ищем по РАЗБОРУ строки (тот же парсер, что у _danger), а не по подстроке:
# «grep kill core/agent.py» и коммит со словом kill в тексте — это чтение и
# данные, ложный запрет здесь учил бы обходить шлюз перефразированием (bl-043).
ZONE_KILL_CMDS = {"kill", "pkill", "killall", "killall5", "xkill", "skill"}
# xargs здесь наравне с обёртками: настоящая команда стоит следующим словом.
ZONE_KILL_WRAPPERS = WRAPPERS | {"xargs"}


def _zone_kill(cmd: str, depth: int = 0) -> str | None:
    """Имя команды, убивающей процессы, если она есть в строке. Иначе None."""
    if depth > 3:
        return "слишком глубокая вложенность команд"
    try:
        body, heredocs = _cut_heredocs(cmd)
        for _sep, seg in _segments(_words(body)):
            args = [t for k, t in seg if k == "w"]
            while args and re.match(r"^[A-Za-z_]\w*=", args[0]):
                args.pop(0)
            if not args:
                continue
            base, rest = args[0].rsplit("/", 1)[-1], args[1:]
            while base in ZONE_KILL_WRAPPERS and rest:
                while rest and (rest[0].startswith("-") or rest[0].isdigit()
                                or re.match(r"^[A-Za-z_]\w*=", rest[0])):
                    rest.pop(0)
                if not rest:
                    break
                base, rest = rest[0].rsplit("/", 1)[-1], rest[1:]
            if base in ZONE_KILL_CMDS:
                return base
            if base == "fuser" and any(a.startswith("-") and "k" in a for a in rest):
                return "fuser -k"
            # Шелл и heredoc: тело исполняется, значит проверяется тоже.
            texts = [heredocs[int(m.group(1))] for a in rest
                     if (m := re.fullmatch(r"__heredoc(\d+)__", a))]
            texts += [rest[k + 1] for k, a in enumerate(rest)
                      if a == "-c" and k + 1 < len(rest)]
            for t in texts:
                if (base in SHELLS or base == "eval") and (r := _zone_kill(t, depth + 1)):
                    return r
                if PY_SHELL.search(t) and re.search(
                        r"(^|[\s;&|'\"])(p?kill|killall)\b", t):
                    return "чужой язык зовёт kill"
                if re.search(r"\bos\.(kill|killpg)\b|\bsignal\.SIGKILL\b", t):
                    return "os.kill из чужого языка"
    except Exception:                       # разбор сломался — закрываемся
        return ("разбор строки не удался"
                if re.search(r"(^|[\s;&|])(p?kill|killall)\b", cmd) else None)
    return None


# --------------------------------------------------------------------------
# Питон в шелле у роли с зоной (bl-143, находка при починке bl-135).
#
# До 30.08.2026 SAFE_BASH считал `python3 -c …` чтением и пропускал ЛЮБОЕ
# тело (главному агенту с тех пор запрещено целиком, см. _root_write_danger).
# Для роли с зоной та же дыра шире всех остальных вместе взятых: тем же
# вызовом можно
# записать файл в чужую папку (open(…, "w")) и снять чужой процесс, собрав
# имя команды по буквам (os.system(chr(107)+…)) — подстрочный поиск обойти
# тривиально, и гонку с обфускацией регуляркой не выиграть.
#
# Поэтому тело разбирается НАСТОЯЩИМ разбором (ast), а не поиском слов:
# импорты — по белому списку читающих модулей, запись и вызов чужого кода —
# по узлам дерева. os, subprocess, shutil, socket, ctypes не импортируются
# вовсе, а без них строка «os.system» никак не превращается в вызов; eval,
# exec, compile, getattr и дандер-атрибуты закрыты, поэтому собрать вызов из
# букв тоже нечем. Тело не разбирается (синтаксическая ошибка) — отказ.
#
# Смысл границы прежний, что и у зоны записи: считать по логу и по файлам
# роли можно (критик просил это прямо, LESSONS 26.08), менять состояние
# машины — нельзя. Не разбирается только форма `-c`: скрипт файлом, `-`
# со стдина, heredoc в питон и `-m` чем угодно, кроме py_compile, роли
# закрыты целиком — их тело шлюзу не видно.
ZONE_PY_BIN = re.compile(r"^(python[0-9.]*|py|pypy[0-9.]*|ipython[0-9.]*)$")
# `python3 -c …` в позиции команды. 30.08 SAFE_BASH перестал считать `-c`
# чтением ВООБЩЕ (главному агенту он закрыт целиком, см. _root_write_danger) —
# а роль с зоной судит `-c` не по SAFE_BASH, а по-настоящему, разбором тела
# (_zone_py_body чуть выше). Раз тело уже прошло AST-проверку и _zone_python
# вернул None, второй раз через SAFE_BASH её не пересуживать — надо явно
# узнавать «это был python -c, и он уже одобрен» своим отдельным признаком.
ZONE_PY_C_HEAD = re.compile(r"^\s*(\S*/)?(python[0-9.]*|py|pypy[0-9.]*)\s+-c\b")
ZONE_PY_MODULES_OK = {"py_compile"}
# Пульт протокола задач — единственный канал, которым роль с зоной работает с
# задачами проекта (bl-145). Правка bl-143 закрыла роли `-m jarvis.*` целиком,
# и вместе с записью улетело чтение: тимлид vnutrennii 30.08 не смог позвать
# даже tasks.status и не завёл ни одной задачи этапа 1 (job-092).
#
# Здесь пускается не «питон с импортом jarvis», а ОДИН наш модуль с закрытым
# набором подкоманд. Разница принципиальная: тело `python -c` шлюзу невидимо,
# а тело cli.py — наш код под гитом, у него нет ни eval, ни шелла, и пишет он
# только в memory/projects/<проект>/tasks/**. Свою зону роль при этом не
# покидает: cli.py сам сверяет папку задач с JARVIS_ZONE и отказывает
# (modules/projects/cli.py, _zone_refusal) — граница держится в двух местах.
#
# branch-create/branch-merge (ob-087, 02.09.2026): открыты роли с зоной, но не
# как обход ZONE_BASH_WRITE — cli.py не зовёт git текстом шелла, а вызывает
# branches.py напрямую (subprocess с фиксированным списком аргументов, ни
# eval, ни шелла, репозиторий всегда C.ROOT). Свой ли это проект, решает не
# шлюз (он видит только имя подкоманды), а cli.py: _zone_refusal сверяет
# аргумент project с зоной роли, а cmd_branch_create/cmd_branch_merge
# дополнительно требуют, чтобы задача реально лежала в этом проекте на диске
# (T.require_task) и чтобы имя ветки при слиянии совпадало с тем, что
# branch-create записал в task.json того же проекта, — сверка по факту на
# диске, а не по тексту, который прислал вызывающий.
ZONE_PY_CLI_MODULES = frozenset(("jarvis.modules.projects.cli",
                                 "modules.projects.cli"))
ZONE_PY_CLI_CMDS = frozenset((
    "status", "overdue", "list", "check", "summary", "cost", "branch-list",
    "create", "plan", "submit", "accept", "branch-create", "branch-merge"))
# Роль зовёт пульт питоном ИЗ ВЕНВА, полным путём (jarvis/.venv/bin/python), а
# SAFE_BASH узнаёт только голое слово `python3` и три модуля. Без этой строки
# подкоманда проходила бы _zone_python и тут же ложилась на общем «шелл зону
# не соблюдает» — ровно то, обо что 30.08 разбился тимлид. Подкоманду здесь не
# судим второй раз: её уже отсудил _zone_python, до которого дело доходит раньше.
ZONE_PY_CLI_HEAD = re.compile(
    r"^\s*(\S*/)?(python[0-9.]*|py|pypy[0-9.]*)\s+-m\s+"
    r"(jarvis\.)?modules\.projects\.cli\b")
# `python -m <модуль>` вообще: годится ли этот модуль, решает не регулярка, а
# резолв имени в файл на диске (_project_selftest_module ниже).
ZONE_PY_M_HEAD = re.compile(
    r"^\s*(\S*/)?(python[0-9.]*|py|pypy[0-9.]*)\s+-m\s+(?P<mod>[\w.]+)")
# Модули, которые только читают и считают. os/sys.path здесь нет намеренно.
ZONE_PY_IMPORTS_OK = frozenset((
    "json", "re", "collections", "datetime", "math", "statistics", "itertools",
    "functools", "operator", "textwrap", "unicodedata", "hashlib", "base64",
    "csv", "pathlib", "string", "difflib", "decimal", "fractions", "heapq",
    "bisect", "typing", "dataclasses", "enum", "pprint", "copy", "uuid",
    "calendar", "zoneinfo", "html", "urllib.parse", "email", "codecs"))
# Имена, которыми выполняют чужой код или лезут мимо разбора.
ZONE_PY_BAD_NAMES = frozenset((
    "eval", "exec", "compile", "__import__", "getattr", "setattr", "delattr",
    "globals", "locals", "vars", "breakpoint", "input", "memoryview"))
# Атрибуты записи и запуска. Чтение (read, read_text, iterdir, glob) открыто.
ZONE_PY_BAD_ATTRS = frozenset((
    "write", "writelines", "write_text", "write_bytes", "truncate", "unlink",
    "mkdir", "makedirs", "rmdir", "rename", "touch", "chmod", "chown",
    "symlink_to", "hardlink_to", "system", "popen", "spawn", "fork", "kill",
    "killpg", "remove", "rmtree", "open", "run", "Popen", "check_output",
    "check_call", "urlopen", "socket", "connect", "sendall", "dup2"))
ZONE_PY_WRITE_MODE = re.compile(r"[wxa+]")


def _zone_py_body(code: str) -> str | None:
    """Причина отказа для тела `python -c` у роли с зоной. Иначе None."""
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return "тело не разбирается питоном"
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                if a.name.split(".")[0] not in ZONE_PY_IMPORTS_OK:
                    return f"импорт {a.name}"
        elif isinstance(node, ast.ImportFrom):
            root = (node.module or "").split(".")[0]
            if node.level or root not in ZONE_PY_IMPORTS_OK:
                return f"импорт из {node.module or '.'}"
        elif isinstance(node, ast.Attribute):
            if node.attr.startswith("__") or node.attr in ZONE_PY_BAD_ATTRS:
                return f"атрибут .{node.attr}"
        elif isinstance(node, ast.Name):
            if node.id in ZONE_PY_BAD_NAMES:
                return f"вызов {node.id}"
            if node.id.startswith("__"):
                return f"имя {node.id}"
        elif isinstance(node, ast.Call):
            f = node.func
            if isinstance(f, ast.Name) and f.id == "open":
                mode = None
                if len(node.args) > 1:
                    mode = node.args[1]
                for kw in node.keywords:
                    if kw.arg == "mode":
                        mode = kw.value
                if mode is None:
                    continue                      # без режима — чтение
                if not (isinstance(mode, ast.Constant)
                        and isinstance(mode.value, str)):
                    return "open с непонятным режимом"
                if ZONE_PY_WRITE_MODE.search(mode.value):
                    return f"open на запись ({mode.value})"
    return None


def _zone_python(cmd: str, depth: int = 0) -> str | None:
    """Причина отказа, если строка запускает питон мимо разрешённого. Иначе None."""
    if depth > 3:
        return "слишком глубокая вложенность команд"
    try:
        body, heredocs = _cut_heredocs(cmd)
        _parsed = list(_segments(_words(body)))
        # Смена рабочего каталога и подмена окружения важны только для `-m`:
        # с ними одно и то же имя модуля означает другой файл на диске.
        moved = any(w[0].rsplit("/", 1)[-1] == "cd" for _s, sg in _parsed
                    if (w := [t for k, t in sg if k == "w"]))
        hijack = any(k == "w" and re.match(r"^[A-Za-z_]\w*=", t)
                     and t.split("=", 1)[0] in PROJECT_ENV_HIJACK
                     for _s, sg in _parsed for k, t in sg)
        for _sep, seg in _parsed:
            args = [t for k, t in seg if k == "w"]
            while args and re.match(r"^[A-Za-z_]\w*=", args[0]):
                args.pop(0)
            if not args:
                continue
            base, rest = args[0].rsplit("/", 1)[-1], args[1:]
            while base in ZONE_KILL_WRAPPERS and rest:
                while rest and (rest[0].startswith("-") or rest[0].isdigit()
                                or re.match(r"^[A-Za-z_]\w*=", rest[0])):
                    rest.pop(0)
                if not rest:
                    break
                base, rest = rest[0].rsplit("/", 1)[-1], rest[1:]
            # Шелл и heredoc исполняются — их тело проверяется тем же способом.
            if base in SHELLS or base == "eval":
                texts = [heredocs[int(m.group(1))] for a in rest
                         if (m := re.fullmatch(r"__heredoc(\d+)__", a))]
                texts += [rest[k + 1] for k, a in enumerate(rest)
                          if a == "-c" and k + 1 < len(rest)]
                for t in texts:
                    if r := _zone_python(t, depth + 1):
                        return r
                continue
            if not ZONE_PY_BIN.match(base):
                continue
            i = 0
            while i < len(rest):
                a = rest[i]
                if a.startswith("--"):
                    i += 1
                    continue
                if a.startswith("-") and len(a) > 1:
                    if "c" in a[1:]:
                        if i + 1 >= len(rest):
                            return "питон с -c без тела"
                        code = rest[i + 1]
                        if re.fullmatch(r"__heredoc(\d+)__", code):
                            return "питон читает код из heredoc"
                        if r := _zone_py_body(code):
                            return f"питон -c: {r}"
                        break                     # дальше — аргументы программы
                    if "m" in a[1:]:
                        mod = rest[i + 1] if i + 1 < len(rest) else ""
                        # Самотест кода агента: роль обязана уметь проверить
                        # то, что сама написала, иначе критерии закрывает за
                        # неё кто-то другой (ob-111, ob-114). Судим по файлу
                        # на диске, а не по виду имени; `cd` и подмена
                        # PYTHONPATH запрещены отдельно (moved/hijack выше).
                        if _project_selftest_module(mod):
                            if hijack:
                                return ("самотест с подменой окружения "
                                        "(PYTHONPATH и подобное)")
                            if moved:
                                return ("самотест после `cd`: имя модуля "
                                        "возьмёт файл из другого дерева")
                            break
                        if mod in ZONE_PY_CLI_MODULES:
                            sub = next((x for x in rest[i + 2:]
                                        if not x.startswith("-")), "")
                            if sub not in ZONE_PY_CLI_CMDS:
                                return (f"пульт протокола задач: подкоманда "
                                        f"{sub or '?'} роли закрыта")
                            break                 # дальше — аргументы команды
                        if mod not in ZONE_PY_MODULES_OK:
                            return f"питон -m {mod or '?'}"
                        break                     # дальше — аргументы модуля
                    i += 1
                    continue
                return "питон запускает файл или читает код со стдина"
            # Голый `python3` без аргументов читает код со стдина.
            if not rest:
                return "питон без аргументов читает код со стдина"
    except Exception:                             # разбор сломался — закрываемся
        return ("разбор строки не удался"
                if re.search(r"\bpython", cmd) else None)
    return None


# --------------------------------------------------------------------------
# Bash у роли с зоной: форма цикла и адресная запись (ob-082, ob-083).
#
# До этой правки любая запись из Bash у роли с зоной («>`, `tee`, `sed -i`)
# отбивалась ЦЕЛИКОМ, независимо от цели — даже запись внутрь СОБСТВЕННОЙ
# зоны (LESSONS 30.08, job-104: единственный способ роли записать файл в
# worktree задачи — уйти в Write/Edit, а туда зона может не пускать вовсе).
# А «for c in …; do git show …; done» (только чтение, без единого `>`)
# отбивался как запись, потому что SAFE_BASH смотрит только на первое слово
# строки, а первое слово тут — «for» (LESSONS 30.08, критик подтвердил
# 31.08 — job-168, ob-083).
#
# Починка точечная — раздел не переписан целиком:
#   1. _zone_loop_of_reads узнаёт форму «for VAR in …; do ЧТЕНИЕ; done» и
#      родственные ей служебные слова (do/then/elif/else/done/fi/esac) как
#      синтаксис, а не как команду, и проверяет САМУ команду внутри как
#      обычное чтение (SAFE_BASH). Опасное по-прежнему ловит ZONE_BASH_WRITE
#      ДО вызова этой функции — она узнаёт только форму.
#   2. Если ZONE_BASH_WRITE всё же сработал, и сработал ТОЛЬКО на одной из
#      четырёх адресуемых форм записи (`>`, `>>`, `tee`, `sed -i`/`perl -i`,
#      heredoc — он превращается в `>` после _cut_heredocs), цель разбирается
#      по тем же токенам, что у _root_write_danger, и судится по зоне роли —
#      так же, как Write/Edit. Любой ДРУГОЙ признак записи (rm/mv/cp/mkdir/
#      chmod/chown/ln/dd/truncate/install/tar/unzip/git-запись) как отбивался
#      целиком, так и отбивается: там цель может быть не одна или не
#      сводиться к одному пути (`rm -rf *`, `git commit`).
# --------------------------------------------------------------------------

ZONE_LOOP_LEADERS = frozenset(("do", "then", "elif", "else"))
ZONE_LOOP_ENDERS = frozenset(("done", "fi", "esac"))


def _zone_loop_of_reads(cmd: str) -> bool:
    """Каждый сегмент — служебное слово цикла/условия либо целиком
    безопасное чтение (SAFE_BASH)? Разбор упал — не чтение (закрываемся).
    """
    try:
        body, _heredocs = _cut_heredocs(cmd)
        segs = _segments(_words(body))
        if not segs:
            return False
        for _sep, seg in segs:
            if any(kind == "op" for kind, _ in seg):
                return False
            words = [t for k, t in seg if k == "w"]
            if not words:
                return False
            if words[0] == "for" and "in" in words:
                continue                    # for VAR in СПИСОК — не команда
            if len(words) == 1 and words[0] in ZONE_LOOP_ENDERS:
                continue
            idx = 1 if words[0] in ZONE_LOOP_LEADERS else 0
            rest = words[idx:]
            if not rest:
                continue
            if not SAFE_BASH.match(" ".join(rest)):
                return False
            base = rest[0].rsplit("/", 1)[-1]
            if base == "find" and any(a in FIND_WRITE_FLAGS for a in rest[1:]):
                return False
        return True
    except Exception:
        return False


# Признаки записи, которые НЕ разбираются на одну цель адресно — как
# отбивались целиком, так и отбиваются. Список — ZONE_BASH_WRITE минус
# четыре адресуемые формы (`>`, `>>`, `tee`, `sed -i`/`perl -i`).
ZONE_WRITE_UNADDRESSED = re.compile(
    r"(^|[\s;&|])(rm\b|mv\b|cp\b|mkdir\b|rmdir\b|touch\b|chmod\b|chown\b|"
    r"ln\b|dd\b|truncate\b|install\b|tar\b|unzip\b|"
    r"git\s+(add|commit|checkout|reset|clean|push))")


def _zone_bash_write_targets(cmd: str) -> list[str] | None:
    """Цели записи, если она сводится ТОЛЬКО к `>`/`>>`/`tee`/`sed -i`/
    `perl -i` — иначе None (форма не разобрана, а не «целей нет»: смена
    каталога (`cd`) внутри команды тоже не разбирается — резолвим только
    от корня папки агента, как и Write/Edit).
    """
    try:
        body, _heredocs = _cut_heredocs(cmd)
        targets: list[str] = []
        found = False
        for _sep, seg in _segments(_words(body)):
            words = [t for k, t in seg if k == "w"]
            if words and words[0].rsplit("/", 1)[-1] == "cd":
                return None                 # cd меняет базу — не резолвим вслепую
            for idx, (kind, t) in enumerate(seg):
                if kind == "op" and t in REDIRECTS and idx + 1 < len(seg):
                    found = True
                    targets.append(seg[idx + 1][1])
            if not words:
                continue
            base = words[0].rsplit("/", 1)[-1]
            rest = words[1:]
            if base == "tee":
                found = True
                targets += [a for a in rest if not a.startswith("-")
                           and not re.fullmatch(r"__heredoc\d+__", a)]
            elif base in ("sed", "perl") and any(
                    a.startswith("-i") or a == "--in-place" for a in rest):
                found = True
                non_flags = [a for a in rest if not a.startswith("-") and a != "-"
                            and not re.fullmatch(r"__heredoc\d+__", a)]
                # Без -e/-f первый позиционный — сценарий (s/.../.../), не
                # файл: `sed -i 's/a/b/' file` иначе принял бы сценарий за
                # цель записи и отбил бы законную правку в своей же зоне.
                has_script_flag = any(a in ("-e", "-f") for a in rest)
                targets += non_flags if has_script_flag else non_flags[1:]
        # Скобки раскрывает шелл, и пишет он во ВСЕ ветки: `tee
        # memory/{projects,X}/x` создаёт два файла. Судим каждую (job-192).
        targets = [b for t in targets for b in _brace_candidates(t)]
        return targets if found else None
    except Exception:
        return None


def role_zone() -> list[str]:
    """Куда этому процессу можно писать. Пусто — ограничения нет.

    Зона приходит переменной JARVIS_ZONE от диспетчера заданий и существует
    только у постоянных ролей (modules/agents): у юриста своя папка, и его
    вывод не должен оказаться в чужой. Обычный подагент зоны не имеет — его
    держат общие правила шлюза.
    """
    raw = os.environ.get("JARVIS_ZONE", "").strip()
    return [p for p in raw.split(os.pathsep) if p.strip()]


def outside_zone(tool_name: str, tool_input: dict) -> str | None:
    """Причина отказа, если вызов пишет вне зоны роли. Иначе None.

    Границу держит код, а не уговоры в промпте, — это главный урок чужой
    команды агентов (факт trendflow-komanda-agentov): роль, которой зона
    только описана словами, рано или поздно пишет мимо неё.
    """
    zone = role_zone()
    if not zone:
        return None
    zone_text = ", ".join(zone)

    # Инструмент без пути в аргументах пишет не туда, куда указано, а туда,
    # куда решит его код. Поэтому такие вызовы судятся по имени.
    short = re.sub(r"^mcp__[a-z0-9_]+__", "", tool_name)
    if short in ZONE_MEMORY_WRITE:
        return (f"Общая память вне твоей зоны ({zone_text}): {short} пишет в "
                f"memory/facts, rules, procedures, obligations.md и now.md, "
                f"мимо твоей папки. Знания роли живут в её knowledge/ — заведи "
                f"там файл через Write. Что-то обязано попасть в общую память "
                f"или в обязательства — напиши это словами в результате "
                f"задания, запишу я.")

    # Задание уходит ОТДЕЛЬНЫМ процессом, и зоны у него нет: делегированием
    # роль обошла бы границу целиком, ничего при этом не нарушая формально.
    if tool_name in DELEGATE_TOOLS:
        return (f"Роль не раздаёт работу дальше: подагент пойдёт без твоей "
                f"зоны ({zone_text}) и запишет что угодно куда угодно. Нужна "
                f"чужая работа — опиши её словами в результате, поставлю сам.")

    if tool_name == "Bash":
        cmd = str(tool_input.get("command", ""))
        # «cd куда-то && git log» — обычный способ позвать чтение, и SAFE_BASH
        # его не узнаёт, потому что смотрит на первое слово. Первый же заход
        # критика 25.08 отбился трижды подряд на своей же задаче «посмотри
        # git log». Ведущий cd снимаем и судим по настоящей команде.
        # Убийство процессов судится по всей строке до разбора на «чтение»:
        # «ps aux; kill -9 <pid>» начинается с разрешённого ps (bl-135).
        if victim := _zone_kill(cmd):
            return (f"Останавливать процессы тебе нельзя ({victim}): у тебя "
                    f"зона записи ({zone_text}), а kill меняет состояние всей "
                    f"машины — под руку попадают демон ВК и рассылка. Смотреть "
                    f"на процессы можно (ps, pgrep, pstree). Нужно что-то "
                    f"снять или перезапустить — напиши это словами в "
                    f"результате задания, сделаю сам.")
        # Питон судится до разбора на «чтение»: SAFE_BASH считает `python3 -c`
        # чтением с любым телом, а телом можно и записать файл в чужую папку,
        # и снять процесс обфускацией (bl-143).
        if why := _zone_python(cmd):
            return (f"Так питон тебе запускать нельзя ({why}): у тебя зона "
                    f"записи ({zone_text}), а произвольный код обходит её "
                    f"целиком — он и файл запишет, и процесс снимет. Считать "
                    f"по логу и по файлам можно: `python3 -c` с чтением "
                    f"(json, re, collections, datetime, open на чтение) "
                    f"проходит. Задачи проекта — только пультом: "
                    f"`python -m jarvis.modules.projects.cli "
                    f"<status|overdue|list|check|summary|cost|create|plan|"
                    f"submit|accept> …`, он же проверит твою зону. Нужен "
                    f"прогон тестов, ветки или свой скрипт — опиши это "
                    f"словами в результате задания, сделаю сам.")
        head = re.sub(r"^\s*cd\s+(\"[^\"]*\"|'[^']*'|[^&;|]+?)\s*&&\s*", "", cmd)
        if ZONE_PY_CLI_HEAD.match(head) and not ZONE_BASH_WRITE.search(head):
            return None
        if ZONE_PY_C_HEAD.match(head):
            return None            # _zone_python выше уже одобрил тело (AST)
        # Самотест своего кода: SAFE_BASH знает только три модуля и голое
        # слово python3, а роль зовёт питон из венва полным путём. Годность
        # модуля решил _zone_python выше — резолвом имени в файл на диске.
        if ((m := ZONE_PY_M_HEAD.match(head))
                and _project_selftest_module(m.group("mod"))
                and not ZONE_BASH_WRITE.search(head)):
            return None
        # Признак записи, который не сводится к одной цели (rm/mv/cp/mkdir/
        # git-запись и родня) — как отбивался целиком, так и отбивается.
        if not ZONE_WRITE_UNADDRESSED.search(head):
            if ZONE_BASH_WRITE.search(head):
                # Похоже на `>`/`>>`/`tee`/`sed -i` — цель судится адресно,
                # той же зоной, что и Write/Edit (ob-082, п. «б»). Проект
                # даёт доступ к worktrees/ целиком: чужой проект внутри неё
                # уже отбит выше (outside_project бежит первым и на этот же
                # вызов), здесь остаётся судить только свою зону.
                targets = _zone_bash_write_targets(head)
                if targets is not None:
                    roots = []
                    for item in zone:
                        p = Path(item).expanduser()
                        roots.append((p if p.is_absolute() else C.ROOT / p).resolve())
                    roots.append(Path("/tmp").resolve())
                    if project_scope():
                        roots.append((C.ROOT / "worktrees").resolve())
                    bad = [t for t in targets
                           if not any(_write_target_ok(t, r, C.ROOT) for r in roots)]
                    if not bad:
                        return None
                    return (f"У тебя есть своя зона записи ({zone_text}): цель "
                            f"«{', '.join(bad)}» вне неё. Пиши строго в зону; "
                            f"вне неё — через Write/Edit туда, куда можно, или "
                            f"словами в результате задания.")
            # Не запись вовсе — либо форма чтения, которую SAFE_BASH не узнаёт
            # по одному первому слову: «for c in …; do git show …; done»
            # начинается с «for» и мимо него не проходит, хотя внутри ни
            # одной пишущей команды нет (LESSONS 30.08, критик 31.08, ob-083).
            if SAFE_BASH.match(head) or _zone_loop_of_reads(head):
                return None
        return ("У тебя есть своя зона записи, и шелл её не соблюдает: "
                "создавай и правь файлы инструментами Write и Edit, "
                "а Bash оставь для чтения (ls, cat, grep, find) и для записи "
                "строго в свою зону (>, >>, tee, sed -i).")

    if tool_name not in ZONE_WRITE_TOOLS:
        return None

    raw = (tool_input.get("file_path") or tool_input.get("notebook_path")
           or tool_input.get("path") or "")
    if not raw:
        return None
    try:
        target = Path(str(raw)).expanduser()
        target = (target if target.is_absolute() else C.ROOT / target).resolve()
    except (OSError, RuntimeError):
        return "путь не разбирается — писать по нему нельзя"

    allowed = []
    for item in zone:
        p = Path(item).expanduser()
        allowed.append((p if p.is_absolute() else C.ROOT / p).resolve())
    allowed.append(Path("/tmp").resolve())
    # Ветки задач проекта — worktrees/<ветка> (ob-082): роль, работающая на
    # проекте (JARVIS_PROJECT задан), может писать туда, даже если её
    # собственная зона (owns) этого не перечисляет. Границу проекта уже
    # проверил outside_project — он бежит первым для того же самого вызова,
    # и чужой проект внутри worktrees/ (там полная копия репозитория) уже
    # отбит; здесь остаётся только «в зоне роли или нет».
    if project_scope():
        allowed.append((C.ROOT / "worktrees").resolve())

    for base in allowed:
        if target == base or base in target.parents:
            return None

    return (f"Путь {target} вне твоей зоны ({zone_text}). Это граница, а не "
            f"ошибка: чужие файлы правит их владелец. Нужно изменить что-то "
            f"вне зоны — опиши это словами в результате, я сделаю сам.")


# --------------------------------------------------------------------------
# Изоляция по проекту (раздел 6.2 документа «Джарвис — команда и проекты»)
# --------------------------------------------------------------------------
#
# Зона роли (outside_zone выше) режет запись по папке роли и существует у
# постоянных агентов. Проект — другое измерение: несколько ролей и заданий
# могут работать на один проект, и ни одна из них не должна видеть материалы
# ДРУГОГО проекта — ни читать, ни писать, — а также не должна дотянуться до
# чужого или общего .env. Работает только когда задан JARVIS_PROJECT: без
# него поведение шлюза не отличается от того, что было до этой правки —
# 30.08.2026 у юриста и критика проекта нет, и они обязаны продолжать
# работать точно так же (правило обратной совместимости из брифа).

def project_scope() -> str | None:
    """На какой проект работает этот процесс. Не задано — None.

    Переменная приходит от диспетчера заданий (core/jobs.py), как JARVIS_ZONE
    у зоны роли. Пусто или отсутствует — изоляция по проекту выключена целиком,
    а не «разрешено всё»: outside_project в этом случае даже не смотрит на вызов.
    """
    raw = os.environ.get("JARVIS_PROJECT", "").strip()
    return raw or None


# Путь в чужой проект — по виду, тем же приёмом, что и _zone_path у
# неизменяемой зоны лога: разбирать каждый инструмент до конца дороже, чем
# держать наготове регулярку и считать подозрительным всё похожее.
# Имя проекта — один сегмент пути: без пробелов, переносов и кавычек. Жадное
# [^/\\]+ хватало продолжение строки («…/vnutrennii && cat x») и объявляло свой
# же проект чужим — ложный отказ на законной работе (job-117).
_PROJECT_DIR_RE = re.compile(r"memory[/\\]projects[/\\]([^/\\\s'\"`;|&)]+)")
# Тот же корень БЕЗ имени проекта: «grep -r ключ memory/projects» не называет
# ни одного чужого проекта и потому проходил мимо проверки выше, вычитывая при
# этом их все (bl-148, пункт 4 приёмки этапа 0).
_PROJECTS_ROOT_RE = re.compile(r"memory[/\\]projects\b")

# Команды, которые читают СОДЕРЖИМОЕ файлов рекурсивно, вглубь дерева. Имена
# файлов (ls, find, glob) намеренно не режем: чужой материал утекает текстом, а
# не списком путей, и запрет на find сломал бы половину законной работы.
PROJECT_RECURSIVE_READERS = frozenset((
    "grep", "egrep", "fgrep", "rg", "ripgrep", "ag", "ack", "ack-grep"))
# Корни, обход которых заведомо накрывает memory/projects целиком.
PROJECT_WIDE_ROOTS = frozenset((".", "./", "*", "memory", "memory/", "./memory",
                                "./memory/", "memory/*", "~", "$HOME"))
# Рекурсия у греп-подобных: без неё grep читает только названные файлы.
PROJECT_RECURSIVE_FLAG = re.compile(r"^-[A-Za-z]*[rR]")

# Инструменты, у которых путь к файлу лежит в аргументах и по которым можно
# ПРОЧИТАТЬ чужое, а не только записать. В отличие от зоны роли, здесь режется
# и чтение: иначе роль проекта А просто прочитает досье проекта Б инструментом
# Read и перескажет его своими словами — обхода достаточно, чтобы изоляция
# была фикцией.
PROJECT_PATH_TOOLS = ("Read", "Write", "Edit", "MultiEdit", "NotebookEdit",
                      "Glob", "Grep")


def _project_wide_root(raw: str) -> bool:
    """Правда, если путь накрывает memory/projects целиком.

    Пустой путь считается широким: у Grep он значит «весь репозиторий», и
    именно так чужой проект вычитывался, ни разу не будучи названным.
    """
    text = (raw or "").strip()
    if not text or text in PROJECT_WIDE_ROOTS:
        return True
    try:
        target = Path(text).expanduser()
        target = (target if target.is_absolute() else C.ROOT / target).resolve()
    except (OSError, RuntimeError):
        return False
    wide = {C.ROOT.resolve(), (C.ROOT / "memory").resolve(),
            (C.ROOT / "memory" / "projects").resolve()}
    return target in wide


def _bash_reads_all_projects(cmd: str) -> bool:
    """Правда, если шелл-команда рекурсивно вычитывает содержимое от корня.

    Судим по словам команды: греп-подобная база + флаг рекурсии + либо широкий
    корень в аргументах, либо вовсе ни одного пути (тогда корень — текущий
    каталог, то есть папка агента).
    """
    try:
        body, _heredocs = _cut_heredocs(cmd)
        segments = list(_segments(_words(body)))
    except Exception:                                  # разбор не удался —
        return False                                   # судить не по чему
    for _sep, seg in segments:
        args = [t for k, t in seg if k == "w"]
        while args and re.match(r"^[A-Za-z_]\w*=", args[0]):
            args.pop(0)
        if not args:
            continue
        base = args[0].rsplit("/", 1)[-1]
        if base not in PROJECT_RECURSIVE_READERS:
            continue
        rest = args[1:]
        if not any(PROJECT_RECURSIVE_FLAG.match(a) for a in rest):
            continue
        paths = [a for a in rest if not a.startswith("-")]
        # Первый непарный аргумент грепа — образец, а не путь.
        paths = paths[1:] if paths else []
        if not paths or any(_project_wide_root(p) for p in paths):
            return True
    return False


# Интерпретаторы, которым можно скормить код файлом или строкой. Список
# намеренно шире, чем у зоны роли: там питон, здесь всё, чем 30.08 обходили
# изоляцию (job-116 достал корневой .env через `python3 script.py` и через
# `perl -e` с перевёрнутой строкой пути).
PROJECT_INTERPRETERS = re.compile(
    r"^(python[0-9.]*|py|pypy[0-9.]*|perl|ruby|node|nodejs|php|lua|tclsh|"
    r"awk|gawk|mawk|sh|bash|zsh|ksh|dash|deno|bun|Rscript|julia)$")
# Флаги, после которых идёт КОД строкой.
PROJECT_CODE_FLAGS = ("-c", "-e", "-E", "--eval", "-f", "--file")
# Приёмы, которыми путь прячут от текстовой проверки, и обходы ссылками.
PROJECT_CODE_TRICKS = re.compile(
    r"(b64decode|base64|codecs\.decode|unhexlify|fromhex|\[::-1\]|"
    r"\breverse\b|\brev\b|os\.link|os\.symlink|symlink_to|hardlink_to|"
    r"chr\(\s*\d|\\x[0-9a-f]{2})", re.I)


# Флаги, которые берут СПИСОК путей из файла: содержимое файла шлюзу не видно,
# а читается по нему что угодно (job-117).
PROJECT_LIST_FLAGS = re.compile(
    r"(^|\s)(-a\b|-T\b|--files-from\b|--files-from=|-f\b(?=\s*\S)|"
    r"--include-from\b|--exclude-from\b|--target-directory=)")
# Эскейпы, которыми путь собирают средствами самого шелла: в тексте команды
# запретных слов нет вовсе.
PROJECT_ESCAPES = re.compile(r"(\$'|\\x[0-9a-fA-F]{2}|\\0[0-7]{2}|\\u[0-9a-fA-F]{4})")
# Единственный законный способ выполнить наш код при заданном проекте — пульт
# протокола: его тело под гитом, у него нет ни eval, ни шелла.
PROJECT_CLI_OK = re.compile(r"-m\s+(jarvis\.)?modules\.projects\.(cli|selftest)\b")
# Переменные, которыми подменяют, ОТКУДА возьмётся модуль: с ними имя
# `modules.status.selftest` перестаёт означать файл в папке агента.
PROJECT_ENV_HIJACK = ("PYTHONPATH", "PYTHONHOME", "PYTHONSTARTUP", "PATH")

# Дыра job-169 (01.09.2026): _project_exec_danger и _project_code_danger брали
# ТОЛЬКО первое слово сегмента и, если оно не входило ни в SHELLS, ни в
# PROJECT_INTERPRETERS, делали continue — весь разбор тела кода пропускался
# целиком. Слова `env`, `command`, `nice -n0` перед `python3 -c`/`perl -e`
# спокойно прятали интерпретатор: путь к чужому файлу собирался конкатенацией
# строк внутри тела, а текстовый поиск по самой команде тоже не срабатывал,
# потому что подстрока была разбита кавычками и `+`. Список обёрток тот же,
# что уже снимает _project_bash_verdict (глобальный WRAPPERS) — там же ошибки
# не было, потому что путь резолвится по СЛОВАМ команды независимо от их
# позиции. Плюс xargs: он передаёт команду первым аргументом (та же позиция,
# что у обёртки), и `ZONE_KILL_WRAPPERS` уже добавляет его к WRAPPERS по той
# же причине для зоны лога.
PROJECT_EXEC_WRAPPERS = WRAPPERS | {"xargs"}


def _strip_project_wrappers(args: list[str]) -> tuple[str | None, list[str], bool]:
    """Снимает известные обёртки (env/nice/timeout/xargs/…) с головы `args`.

    Возвращает (реальную команду, её аргументы, была ли снята хоть одна
    обёртка). Список обёрток — РАЗРЕШЁННЫЙ (PROJECT_EXEC_WRAPPERS), а не
    запрещённый: слово, которого в нём нет, обёрткой не считается, и снятие
    останавливается на нём же — решает вызывающий код
    (_project_unknown_wrapper_hides_interpreter).
    """
    if not args:
        return None, [], False
    base = args[0].rsplit("/", 1)[-1]
    rest = args[1:]
    wrapped = False
    while base in PROJECT_EXEC_WRAPPERS and rest:
        wrapped = True
        while rest and (rest[0].startswith("-") or rest[0].isdigit()
                        or re.match(r"^[A-Za-z_]\w*=", rest[0])):
            rest.pop(0)
        if not rest:
            return base, rest, wrapped
        base, rest = rest[0].rsplit("/", 1)[-1], rest[1:]
    return base, rest, wrapped


def _project_unknown_wrapper_hides_interpreter(rest: list[str]) -> bool:
    """Правда, если сразу за неопознанной обёрткой открывается интерпретатор
    с флагом кода (-c/-e/…).

    Список PROJECT_EXEC_WRAPPERS конечен, а способов обернуть команду — нет
    (`granicy-razdela-8-chem-zashchishcheny`: «слова кончаются раньше, чем
    каналы»). Поэтому обёртка, которой в списке нет, не пропускается молча —
    берём только флаго- и цифро-подобные слова (та же граница, что и у
    известных обёрток) и смотрим, не открывается ли сразу за ними
    интерпретатор с флагом кода. Совпадение БЕЗ флага кода не считается —
    иначе `grep -c python3 file.txt` (тот же `-c`, что и у python, но это
    счётчик грепа) ловилось бы ложно.
    """
    probe = list(rest)
    while probe and (probe[0].startswith("-") or probe[0].isdigit()
                     or re.match(r"^[A-Za-z_]\w*=", probe[0])):
        probe.pop(0)
    if not probe:
        return False
    name = probe[0].rsplit("/", 1)[-1]
    if not (PROJECT_INTERPRETERS.match(name) or name in SHELLS or name == "eval"):
        return False
    return len(probe) > 1 and probe[1] in PROJECT_CODE_FLAGS


def _project_selftest_module(mod: str) -> Path | None:
    """Файл на диске, если `mod` — самотест кода агента. Иначе None.

    Резолв по РЕАЛЬНОМУ пути, а не по виду имени: точки в дотированном имени
    превращаются в каталоги, и требуется, чтобы получившийся файл лежал под
    `<корень>/modules/**` и НЕ под `memory/` (там worktrees задач: туда роль
    пишет сама, и запуск оттуда означал бы исполнение своего же кода мимо
    проверки). Ничего не импортируется — только путь.
    """
    name = (mod or "").strip()
    if not name.endswith(".selftest"):
        return None
    parts = name.split(".")
    if parts and parts[0] == "jarvis":
        parts = parts[1:]
    if not parts or parts[0] != "modules" or any(
            (not p.isidentifier()) for p in parts):
        return None
    try:
        spot = C.ROOT.joinpath(*parts).with_suffix(".py").resolve()
        base = (C.ROOT / "modules").resolve()
    except (OSError, RuntimeError):
        return None
    if not spot.is_file() or not spot.is_relative_to(base):
        return None
    return spot


def _project_exec_danger(cmd: str, own: str, depth: int = 0) -> str | None:
    """Отказ на исполнение произвольного кода и на приёмы сокрытия пути.

    Третий живой заход (job-117) показал, что проверять ТЕЛО кода недостаточно:
    команда `cat > x.py <<EOF … EOF; python3 x.py` создаёт скрипт и запускает
    его одним вызовом — на момент решения файла ещё нет, шлюз видит «файла нет»
    и пропускает (TOCTOU). Читать файл, который появится через миллисекунду,
    невозможно в принципе, поэтому при заданном проекте исполнение произвольного
    кода закрыто целиком: остаётся только пульт протокола. Там же закрыты
    эскейпы `$'\\x2e'` (путь собирает сам шелл) и флаги вида `xargs -a`,
    `tar -T`, `rsync --files-from`, берущие список путей из файла.
    """
    if PROJECT_ESCAPES.search(cmd):
        return ("Команда собирает строку эскейпами ($'\\x..'): так прячут путь "
                "от проверки, поэтому при работе на проекте это запрещено.")
    if PROJECT_LIST_FLAGS.search(cmd) and not PROJECT_CLI_OK.search(cmd):
        return ("Команда берёт список путей из файла (-a/-T/--files-from): "
                "содержимое такого списка шлюзу не видно, а читается по нему "
                "что угодно.")
    try:
        body, heredocs = _cut_heredocs(cmd)
        segments = list(_segments(_words(body)))
    except Exception:
        return None
    # Самотест своего кода можно запускать только из корня папки агента: после
    # `cd` тот же `-m modules.status.selftest` возьмёт файл из другого дерева
    # (например из worktree задачи, куда роль пишет сама), а имя останется тем
    # же. Проще и честнее — не пускать самотест там, где рабочий каталог сменён.
    moved = False
    for _sep, seg in segments:
        first = [t for k, t in seg if k == "w"]
        if first and first[0].rsplit("/", 1)[-1] == "cd":
            moved = True

    for _sep, seg in segments:
        args = [t for k, t in seg if k == "w"]
        hijack = False
        while args and re.match(r"^[A-Za-z_]\w*=", args[0]):
            if args[0].split("=", 1)[0] in PROJECT_ENV_HIJACK:
                hijack = True
            args.pop(0)
        if not args:
            continue
        # Обёртки (env/nice/timeout/command/xargs/…) снимаются ПЕРЕД тем, как
        # решать «это интерпретатор или нет» — иначе `env python3 -c …` или
        # `nice -n0 python3 -c …` прячут интерпретатор целиком (job-169).
        base, rest, _wrapped = _strip_project_wrappers(args)
        if base in SHELLS or base == "eval":
            # Шелл внутри шелла прячет вызов на уровень глубже.
            if depth >= 3:
                continue
            inner = [rest[k + 1] for k, a in enumerate(rest)
                     if a in ("-c", "-lc") and k + 1 < len(rest)]
            inner += [heredocs[int(m.group(1))] for a in rest
                      if (m := re.fullmatch(r"__heredoc(\d+)__", a))]
            for t in inner:
                if r := _project_exec_danger(t, own, depth + 1):
                    return r
            continue
        if not PROJECT_INTERPRETERS.match(base):
            # Список PROJECT_EXEC_WRAPPERS конечен — обёртка, которой в нём
            # нет, не считается прозрачной заранее (разрешённый список, а не
            # запрещённый). Если сразу за ней всё-таки открывается
            # интерпретатор с флагом кода — это та же дыра под новым именем.
            if _project_unknown_wrapper_hides_interpreter(rest):
                return (f"Слово «{base}» перед интерпретатором не входит в "
                        f"список разрешённых обёрток при работе на проекте "
                        f"«{own}»: за необъявленной обёрткой прячется "
                        f"исполнение кода, а это запрещено так же, как и "
                        f"голое.")
            continue
        if PROJECT_CLI_OK.search(" ".join(args)):
            continue                      # пульт протокола — первое исключение
        # Второе исключение: самотест кода агента. Без него исполнитель с
        # меткой проекта физически не может проверить то, что сам написал, —
        # и критерии за него дважды закрывал главный агент (ob-111, ob-114).
        # Пускается не «питон с -m», а модуль, который РЕЗОЛВИТСЯ НА ДИСКЕ в
        # файл под <корень>/modules/**.selftest.py: имя тут ничего не решает.
        mod = next((args[k + 1] for k, a in enumerate(args)
                    if a == "-m" and k + 1 < len(args)), "")
        if mod and _project_selftest_module(mod):
            if hijack:
                return (f"Самотест запускается с подменой окружения "
                        f"(PYTHONPATH и подобное): тогда имя модуля перестаёт "
                        f"означать файл в папке агента. Позови его без "
                        f"переменных перед командой.")
            if moved:
                return (f"Самотест своего кода запускается из корня папки "
                        f"агента: после `cd` то же имя модуля возьмёт файл из "
                        f"другого дерева (например из worktree задачи). Убери "
                        f"`cd` из команды.")
            if any(k == "op" for k, _ in seg):        # редирект внутри сегмента
                return (f"Самотест разрешён отдельной командой, без "
                        f"перенаправлений и конвейеров: разбирать, куда "
                        f"уходит его вывод, шлюз не берётся.")
            continue
        return (f"Исполнение кода ({base}) при работе на проекте «{own}» "
                f"закрыто: тело кода может появиться на диске уже после "
                f"проверки, и тогда изоляция становится фикцией. Работай "
                f"инструментами (Read, Grep, Write, Edit) и пультом "
                f"протокола `python3 -m jarvis.modules.projects.cli`.")
    return None


def _project_code_danger(cmd: str, own: str, depth: int = 0) -> str | None:
    """Причина отказа, если команда исполняет код, тела которого шлюз не видит.

    Дыра, найденная живой проверкой 30.08 (job-116): путь к чужому файлу лежал
    не в самой команде, а внутри скрипта на диске — `python3 skript.py`. Шлюз
    судит строку команды, в строке чисто, а скрипт открытым текстом читал
    корневой .env с живыми токенами ВК, SMTP и Notion. Обфускация даже не
    понадобилась.

    Поэтому при заданном проекте тело исполняемого кода читается и судится теми
    же мерками, что и путь. Не прочиталось — отказ: невидимое тело и есть дыра.
    """
    try:
        body, heredocs = _cut_heredocs(cmd)
        segments = list(_segments(_words(body)))
    except Exception:
        return None
    for _sep, seg in segments:
        args = [t for k, t in seg if k == "w"]
        while args and re.match(r"^[A-Za-z_]\w*=", args[0]):
            args.pop(0)
        if not args:
            continue
        # Те же обёртки, что и в _project_exec_danger — иначе `env python3
        # script.py`/`nice -n0 perl script.pl` прячут скрипт от разбора тела
        # (job-169, тот же класс, что и голая команда).
        base, rest, _wrapped = _strip_project_wrappers(args)
        if not PROJECT_INTERPRETERS.match(base):
            continue
        # Шелл внутри шелла: `sh -c "python3 skript.py"` прячет вызов на
        # уровень глубже — разбираем тело тем же способом (job-116).
        if base in SHELLS or base == "eval":
            inner = [rest[k + 1] for k, a in enumerate(rest)
                     if a in ("-c", "-lc") and k + 1 < len(rest)]
            inner += [heredocs[int(m.group(1))] for a in rest
                      if (m := re.fullmatch(r"__heredoc(\d+)__", a))]
            if depth < 3:
                for t in inner:
                    if r := _project_code_danger(t, own, depth + 1):
                        return r
        texts: list[tuple[str, str]] = []
        i = 0
        while i < len(rest):
            a = rest[i]
            if a in PROJECT_CODE_FLAGS and i + 1 < len(rest):
                texts.append(("строка кода", rest[i + 1]))
                i += 2
                continue
            if (m := re.fullmatch(r"__heredoc(\d+)__", a)):
                texts.append(("heredoc", heredocs[int(m.group(1))]))
                i += 1
                continue
            # `-m модуль`: дальше идут аргументы МОДУЛЯ, а не файлы скриптов.
            # 31.08 из-за этого `… -m …projects.cli submit … --files
            # modules/status/build.py` читался как «запуск build.py», шлюз лез
            # в тело файла и отбивал сдачу собственной работы исполнителя —
            # потому что в докстринге build.py стоит строка «memory/projects/»
            # (находка критика, job-160). Сам модуль судит _project_exec_danger
            # по РЕАЛЬНОМУ пути, а не по содержимому.
            if a == "-m":
                break
            if not a.startswith("-"):
                # Первый непарный аргумент интерпретатора — файл скрипта, всё
                # после него — argv программы, а не новые скрипты.
                p = Path(a).expanduser()
                p = p if p.is_absolute() else C.ROOT / p
                try:
                    if p.is_file():
                        texts.append((f"скрипт {a}",
                                      p.read_text(encoding="utf-8",
                                                  errors="replace")[:200_000]))
                except OSError:
                    return (f"Тело скрипта «{a}» шлюзу не прочитать, а значит "
                            f"и не проверить: при работе на проекте «{own}» "
                            f"исполнять невидимый код нельзя.")
                break
            i += 1
        for what, text in texts:
            flat = text.replace("\\", "/")
            for m2 in _PROJECT_DIR_RE.finditer(flat):
                if m2.group(1) != own:
                    return (f"{what.capitalize()} трогает чужой проект "
                            f"memory/projects/{m2.group(1)}, а этот процесс "
                            f"работает на «{own}».")
            if ".env" in flat and f"memory/projects/{own}/.env" not in flat:
                return (f"{what.capitalize()} читает .env — чужой или общий "
                        f"секрет агента.")
            if PROJECT_CODE_TRICKS.search(text):
                return (f"{what.capitalize()} собирает путь по частям или "
                        f"заводит ссылку (base64, разворот строки, os.link) — "
                        f"так изоляцию уже обходили, поэтому запрещено целиком.")
    return None


def _bash_symlink_target(cmd: str, own: str) -> str | None:
    """Причина отказа, если аргумент команды ВЕДЁТ в чужой проект по факту.

    Проверка по тексту команды ловит только названное вслух. 30.08 подагент на
    живой проверке (job-115) обошёл её за минуту: положил в своей папке симлинк
    на чужой dossier.md (имя цели собрал из base64, чтобы слова «чужой» в
    команде не было) и спокойно прочитал файл `cat`, `xargs`, `cp`, `wc`,
    `zip`. Тем же приёмом достал корневой .env с живым токеном ВК. Поэтому
    каждый аргумент, похожий на путь, резолвится по диску — симлинк
    разворачивается — и уже цель судится теми же мерками, что и Read.
    """
    try:
        body, _heredocs = _cut_heredocs(cmd)
        words = [t for k, t in _words(body) if k == "w"]
    except Exception:
        return None
    # Ссылки роль проекта не заводит вовсе. Жёсткую ссылку резолвом не поймать
    # (у неё нет цели, она сама файл), а имя источника легко спрятать за
    # подстановку — поэтому режем сам инструмент, а не его аргументы.
    if any(w.rsplit("/", 1)[-1] == "ln" for w in words):
        return ("Ссылки (ln) роли проекта не заводят: ссылка — известный обход "
                "изоляции, целью можно сделать чужой файл, не называя его.")
    for w in words:
        if not w or w.startswith("-"):
            continue
        if "/" not in w and not w.startswith("."):
            continue                       # не путь, а слово команды или флаг
        try:
            p = Path(w).expanduser()
            p = (p if p.is_absolute() else C.ROOT / p)
            if not p.exists() and not p.is_symlink():
                continue
            target = p.resolve()
        except (OSError, RuntimeError):
            continue
        text = target.as_posix()
        m = _PROJECT_DIR_RE.search(text)
        if m and m.group(1) != own:
            return (f"Путь «{w}» ведёт (через ссылку или ..) в чужой проект "
                    f"memory/projects/{m.group(1)}, а этот процесс работает "
                    f"на «{own}».")
        if _looks_like_env(target.name):
            own_env = (C.ROOT / "memory" / "projects" / own / ".env").resolve()
            if text != own_env.as_posix():
                return (f"Путь «{w}» ведёт (через ссылку или ..) в файл "
                        f"доступов {target.name}, который этому процессу "
                        f"закрыт.")
    return None


def _looks_like_env(raw: str) -> bool:
    """Похоже на .env-файл: сам .env или .env.что-нибудь рядом с ним."""
    name = Path(raw).name
    return name == ".env" or name.startswith(".env.")


# --------------------------------------------------------------------------
# Судим команду по ДИСКУ, а не по её тексту (job-122, пункт 4 приёмки этапа 0)
# --------------------------------------------------------------------------
#
# Всё, что написано выше, ищет запретное в ТЕКСТЕ команды: слитное
# «memory/projects», слово «.env», путь в аргументе. Шестой заход подагента-
# взломщика (job-122, 30.08 — единственный, шедший реально с JARVIS_PROJECT)
# прочитал и чужое досье, и корневой .env с живым токеном ВК, и все четыре его
# приёма — один класс: путь расщеплён, в тексте его нет.
#     cd memory && find projects -name dossier.md -exec cat {} \;
#     find . -maxdepth 1 -iname '*env*' -exec cat {} \;
#     d=memory/project; cat "${d}s/chuzhoy/dossier.md"
#     cd memory && find . -name dossier.md | xargs cat
# Латать по одному приёму бесполезно: следующий заход найдёт пятый. Поэтому
# ниже другое правило, и оно одно: команда судится по тому, ВО ЧТО ЕЁ СЛОВА
# ПРЕВРАЩАЮТСЯ НА ДИСКЕ — с учётом `cd`, масок и ссылок, — а всё, чей набор
# читаемых файлов шлюзу увидеть нечем (подстановка, переменная шелла, xargs,
# find -exec), закрыто целиком, потому что «не вижу» и «безопасно» — разное.

# find сам выбирает файлы и сам же запускает по ним команду: что он прочитает,
# из текста не следует.
FIND_CONTENT_FLAGS = frozenset((
    "-exec", "-execdir", "-ok", "-okdir", "-delete", "-fprint", "-fprint0",
    "-fprintf", "-fls", "-files0-from"))
# Читают файлы, названные не в команде, а на входе.
PROJECT_LIST_PIPES = frozenset(("xargs", "parallel"))
# Рекурсивны САМИ ПО СЕБЕ, без флага -r: `rg токен` без единого пути читает
# всё дерево от текущего каталога. Старая проверка требовала флага и потому
# этих не видела вовсе.
PROJECT_ALWAYS_RECURSIVE = frozenset(("rg", "ripgrep", "ag", "ack", "ack-grep"))
# Забирают целое дерево, а не названный файл: судятся по дереву.
PROJECT_TREE_TAKERS = frozenset((
    "cp", "rsync", "tar", "zip", "7z", "7za", "cpio", "pax", "scp", "sftp",
    "unzip", "gzip", "bzip2", "xz", "zstd", "install", "dd"))
_GLOB_CHARS = "*?["
# Потолок обхода дерева: не столько защита от медленного диска, сколько
# честность — дерево, которое шлюз не осмотрел целиком, он считает опасным.
_PROJECT_WALK_BUDGET = 60_000


def _project_cd(cwd: Path | None, rest: list[str]) -> Path | None:
    """Куда уводит `cd` при работе на проекте. None — каталог неизвестен.

    В отличие от _cd_target выше, каталог ВНЕ папки агента здесь остаётся
    известным: пути резолвятся по диску, и знать реальный каталог полезнее,
    чем считать выход наружу послаблением.
    """
    if not rest:
        return Path.home()
    t = rest[0]
    if t == "-" or "$" in t:
        return None
    p = Path(t).expanduser()
    if not p.is_absolute():
        if cwd is None:
            return None
        p = cwd / t
    try:
        p = p.resolve()
    except (OSError, RuntimeError):
        return None
    return p if p.is_dir() else None


def _project_paths_of_word(word: str, cwd: Path | None) -> list[Path]:
    """Во что слово команды превращается на диске. Пусто — слово не путь.

    Скобки раскрываются ПЕРЕД маской (порядок шелла), каждая ветка резолвится
    отдельно, результаты складываются: `cat memory/{projects,X}/…` сходит на
    диск по обеим и обязана быть осуждена по обеим.
    """
    w = (word or "").strip()
    if not w or w.startswith("-") or w in ("{}", ";", "\\;", "+"):
        return []
    if "{" in w:
        out: list[Path] = []
        for branch in _brace_candidates(w):
            out += _project_paths_of_plain(branch, cwd)
            if len(out) >= 500:
                break
        return out[:500]
    return _project_paths_of_plain(w, cwd)


def _project_paths_of_plain(word: str, cwd: Path | None) -> list[Path]:
    """То же самое для слова БЕЗ фигурных скобок.

    Маска раскрывается ЗДЕСЬ, а не оставляется шеллу: `cat .e*` не называет
    .env, но читает его. Несуществующее пропускается: прочитать нельзя то,
    чего нет, а запись судит своя проверка.
    """
    w = (word or "").strip()
    if not w or w.startswith("-") or w in ("{}", ";", "\\;", "+"):
        return []
    if any(ch in w for ch in _GLOB_CHARS):
        base = cwd or C.ROOT
        if w.startswith("/") or w.startswith("~"):
            return []                    # абсолютные маски закрыты отдельно
        out: list[Path] = []
        try:
            for hit in base.glob(w):
                out.append(hit)
                if len(out) >= 500:
                    break
        except (OSError, ValueError, IndexError, RuntimeError):
            return []
        return out
    p = Path(w).expanduser()
    if not p.is_absolute():
        if cwd is None:
            return []
        p = cwd / w
    try:
        return [p] if (p.exists() or p.is_symlink()) else []
    except OSError:
        return []


def _own_env_path(own: str) -> str:
    return (C.ROOT / "memory" / "projects" / own / ".env").resolve().as_posix()


def _project_path_verdict(target: Path, own: str, word: str) -> str | None:
    """Тот же суд, что у Read, но по уже резолвнутому пути."""
    try:
        text = target.resolve().as_posix()
        name = target.resolve().name
    except (OSError, RuntimeError):
        return (f"Путь «{word}» шлюзу не разобрать, а невидимое при работе "
                f"на проекте «{own}» запрещено.")
    if text == "/proc" or text.startswith("/proc/"):
        return (f"Путь «{word}» ведёт в /proc: через окружение чужого процесса "
                f"читается корневой .env целиком.")
    m = _PROJECT_DIR_RE.search(text)
    if m and m.group(1) != own:
        return (f"Путь «{word}» ведёт на диске в чужой проект "
                f"memory/projects/{m.group(1)}, а этот процесс работает "
                f"на «{own}».")
    if _looks_like_env(name) and text != _own_env_path(own):
        return (f"Путь «{word}» ведёт на диске в файл доступов {name} — "
                f"это либо чужой проект, либо общий секрет агента.")
    return None


def _project_tree_verdict(root: Path, own: str, word: str) -> str | None:
    """Что закрытого лежит ПОД этим каталогом: чужой проект, .env, история гита.

    Нужно там, где команда забирает дерево целиком (grep -r, cp -r, tar): имя
    каталога при этом чистое, а внутри лежит всё. Обход не идёт по ссылкам, но
    каждый подкаталог резолвится: ссылка на чужой проект — тот же обход.
    """
    if verdict := _project_path_verdict(root, own, word):
        return verdict
    try:
        root = root.resolve()
    except (OSError, RuntimeError):
        return f"Каталог «{word}» шлюзу не разобрать."
    if not root.is_dir():
        return None
    if root.name == ".git":
        return (f"«{word}» — история репозитория: в ней лежат все проекты "
                f"целиком, включая чужие.")
    own_env, seen = _own_env_path(own), 0
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        here = Path(dirpath)
        for d in dirnames:
            if d == ".git":
                return (f"Под «{word}» лежит история репозитория (.git): в ней "
                        f"все проекты целиком, включая чужие.")
            try:
                text = (here / d).resolve().as_posix()
            except (OSError, RuntimeError):
                continue
            m = _PROJECT_DIR_RE.search(text)
            if m and m.group(1) != own:
                return (f"Под «{word}» лежит чужой проект "
                        f"memory/projects/{m.group(1)}, а этот процесс "
                        f"работает на «{own}»: сузь путь.")
        for f in filenames:
            spot = here / f
            # Ссылка на файл под безобидным именем ведёт куда угодно — судим
            # цель, а не имя (тот же приём, что job-115 применил к cat).
            try:
                linked = spot.is_symlink()
            except OSError:
                linked = False
            if linked and (verdict := _project_path_verdict(spot, own, word)):
                return verdict
            if not _looks_like_env(f):
                continue
            try:
                text = spot.resolve().as_posix()
            except (OSError, RuntimeError):
                text = spot.as_posix()
            if text != own_env:
                return (f"Под «{word}» лежит файл доступов {f}: забрать дерево "
                        f"целиком — значит забрать и его.")
        seen += len(dirnames) + len(filenames)
        if seen > _PROJECT_WALK_BUDGET:
            return (f"Дерево «{word}» слишком велико, чтобы шлюз успел за него "
                    f"поручиться: назови каталог поуже.")
    return None


def _project_brace_verdict(cmd: str, own: str) -> str | None:
    """Судит ВЕТКИ раскрытия скобок тем же текстовым мерилом, что и команду.

    Нужна отдельно от суда по диску: тот видит только существующие файлы, а
    запись создаёт новый — `tee memory/{projects,X}/chuzhoy/новый.md` на диске
    не резолвится ни во что и прошла бы насквозь. Здесь же судится текст
    ветки, а он у записи и у чтения одинаков.
    """
    if "{" not in cmd:
        return None
    try:
        body, _heredocs = _cut_heredocs(cmd)
        words = [t for k, t in _words(body) if k == "w"]
    except Exception:
        return (f"Шлюз не смог разобрать команду со скобками, а при работе на "
                f"проекте «{own}» неразобранное считается опасным.")
    own_env = f"memory/projects/{own}/.env"
    for w in words:
        if "{" not in w:
            continue
        for branch in _brace_expand(w):
            if branch == w:
                continue                      # исходное слово судят проверки выше
            text = branch.replace("\\", "/")
            m = _PROJECT_DIR_RE.search(text)
            if m and m.group(1) != own:
                return (f"Скобки в «{w}» раскрываются шеллом в "
                        f"«{branch}» — это чужой проект "
                        f"memory/projects/{m.group(1)}, а этот процесс "
                        f"работает на «{own}».")
            if _ENV_WORD_RE.search(text) and own_env not in text:
                return (f"Скобки в «{w}» раскрываются шеллом в «{branch}» — "
                        f"это файл доступов: либо чужой проект, либо общий "
                        f"секрет агента.")
            if _PROJECTS_ROOT_RE.search(text) and f"memory/projects/{own}" not in text:
                return (f"Скобки в «{w}» раскрываются шеллом в «{branch}» — "
                        f"это корень memory/projects целиком, а этот процесс "
                        f"работает на «{own}».")
    return None


def _project_bash_verdict(cmd: str, own: str) -> str | None:
    """Главная проверка Bash при работе на проекте: суд по диску.

    Порядок: сначала закрываем то, чего шлюз в принципе не видит (подстановка
    команды, переменная шелла, xargs, find -exec, абсолютная маска), потом
    считаем фактический рабочий каталог по цепочке `cd` и судим каждое слово
    по тому, во что оно превращается на диске. Дерево целиком (grep -r, cp -r,
    tar) судится обходом дерева, а не именем его корня.
    """
    if "$(" in cmd or "`" in cmd:
        return ("Команда собирает аргументы подстановкой ($(…) или `…`): что "
                "именно будет прочитано, шлюзу не видно, а при работе на "
                f"проекте «{own}» невидимое запрещено.")
    try:
        body, _heredocs = _cut_heredocs(cmd)
        segments = list(_segments(_words(body)))
    except Exception:
        return (f"Шлюз не смог разобрать команду, а при работе на проекте "
                f"«{own}» неразобранное считается опасным.")

    cwd: Path | None = C.ROOT
    for _sep, seg in segments:
        words = [t for k, t in seg if k == "w"]
        args = list(words)
        while args and re.match(r"^[A-Za-z_]\w*=", args[0]):
            args.pop(0)
        if not args:
            continue
        base = args[0].rsplit("/", 1)[-1]
        rest = args[1:]
        while base in WRAPPERS and rest:
            while rest and (rest[0].startswith("-") or rest[0].isdigit()
                            or re.match(r"^[A-Za-z_]\w*=", rest[0])):
                rest.pop(0)
            if not rest:
                break
            base, rest = rest[0].rsplit("/", 1)[-1], rest[1:]

        if base == "cd":
            target = _project_cd(cwd, rest)
            if target is None:
                return ("После этого `cd` шлюз не знает рабочего каталога, а "
                        "значит не может сказать, что читают следующие "
                        f"команды: при работе на проекте «{own}» так нельзя.")
            if verdict := _project_path_verdict(target, own,
                                                rest[0] if rest else "~"):
                return verdict
            cwd = target
            continue

        if base in PROJECT_LIST_PIPES:
            return (f"«{base}» читает файлы, названные не в команде, а на её "
                    f"входе: что именно будет прочитано, шлюзу не видно. "
                    f"Назови пути прямо.")
        if base == "find" and any(a in FIND_CONTENT_FLAGS for a in rest):
            return ("`find` с -exec/-delete запускает команду над файлами, "
                    "которые выберет сам: их список шлюзу не виден. Ищи имена "
                    "без -exec, а читай названным путём.")

        for w in words:
            if "$" in w:
                return (f"Слово «{w}» собрано подстановкой переменной шелла: "
                        f"путь в тексте команды не назван, и судить его шлюзу "
                        f"не по чему.")
            if ((w.startswith("/") or w.startswith("~"))
                    and any(ch in w for ch in _GLOB_CHARS)):
                return (f"Маска по абсолютному пути («{w}») при работе на "
                        f"проекте «{own}» закрыта: раскрыть её шлюз не берётся.")

        roots: list[str] = []
        if base in PROJECT_TREE_TAKERS:
            roots = [a for a in rest if not a.startswith("-")]
        elif (base in PROJECT_ALWAYS_RECURSIVE
                or (base in PROJECT_RECURSIVE_READERS
                    and any(PROJECT_RECURSIVE_FLAG.match(a) for a in rest))):
            paths = [a for a in rest if not a.startswith("-")]
            roots = paths[1:] if len(paths) > 1 else ["."]   # первый — образец
        for r in roots:
            for p in _project_paths_of_word(r, cwd):
                if verdict := _project_tree_verdict(p, own, r):
                    return verdict

        for w in words:
            for p in _project_paths_of_word(w, cwd):
                if verdict := _project_path_verdict(p, own, w):
                    return verdict
    return None


# «.env» как отдельное имя, а не как подстрока. Наивное `".env" in cmd` ловило
# `os.environ` и запрещало роли проекта обычный разбор кода (ложный отказ,
# job-119): перед точкой стоит буква, значит это не имя файла.
_ENV_WORD_RE = re.compile(r"(?<![\w-])\.env(?![\w])")

# Файловая система процессов. Через /proc/<чужой-pid>/environ читается всё
# окружение работающего демона — то есть корневой .env открытым текстом, и в
# команде при этом нет ни слова «.env», ни имени проекта (job-119, доказано
# живьём: утекли VK_TOKEN, SMTP_PASS, NOTION_TOKEN). Законной нужды в procfs у
# роли проекта нет, поэтому закрыт весь /proc, а не только environ: через
# /proc/<pid>/root, /proc/<pid>/cwd и /proc/<pid>/fd/* достаётся то же самое.
_PROCFS_RE = re.compile(r"(^|[\s'\"`=(<>|;&])/proc(/|\b)")

# Гит печатает содержимое файлов, которых нет в команде: истории, патчи, поиск
# по всему дереву. Ни одна текстовая проверка пути тут не помогает (job-119).
GIT_CONTENT_ALWAYS = frozenset((
    "grep", "cat-file", "format-patch", "archive", "bundle", "stash"))
GIT_NEEDS_PATH = frozenset(("show", "diff", "blame", "log"))
GIT_PATCH_FLAGS = re.compile(
    r"^(-p|-u|--patch|--full-diff|--word-diff|-[SG]|--pickaxe.*)$")


def _project_git_danger(cmd: str, own: str) -> str | None:
    """Отказ на гит-команды, вычитывающие содержимое мимо названных путей.

    Разрешено то, чем роль проекта работает: status, add, commit, rev-parse,
    branch, а также log/show/diff/blame С ЯВНЫМ путём — путь дальше судится
    обычными проверками (чужой проект, .env). Запрещено то, что печатает
    содержимое всего репозитория и его истории: git grep, cat-file, archive,
    голый `git show`/`git diff`, `git log -p` и поиск по содержимому `-S/-G`.
    """
    try:
        body, _heredocs = _cut_heredocs(cmd)
        segments = list(_segments(_words(body)))
    except Exception:
        return None
    for _sep, seg in segments:
        args = [t for k, t in seg if k == "w"]
        while args and re.match(r"^[A-Za-z_]\w*=", args[0]):
            args.pop(0)
        if not args or args[0].rsplit("/", 1)[-1] != "git":
            continue
        rest = args[1:]
        sub = next((a for a in rest if not a.startswith("-")), "")
        if sub in GIT_CONTENT_ALWAYS:
            return (f"«git {sub}» читает содержимое файлов всего репозитория и "
                    f"его истории, включая чужие проекты и старые секреты, а "
                    f"этот процесс работает на «{own}».")
        if sub not in GIT_NEEDS_PATH:
            continue
        tail = rest[rest.index(sub) + 1:]
        patch = any(GIT_PATCH_FLAGS.match(a) for a in tail)
        if sub == "log" and not patch:
            continue                       # заголовки коммитов, не содержимое
        looks_path = any(("/" in a or "." in a) and not a.startswith("-")
                         for a in tail)
        if not looks_path:
            return (f"«git {sub}» без явного пути печатает содержимое файлов "
                    f"всего репозитория, а этот процесс работает на «{own}»: "
                    f"назови путь.")
    return None


def outside_project(tool_name: str, tool_input: dict) -> str | None:
    """Причина отказа, если вызов трогает чужой проект или чужой .env. Иначе
    None. Действует только при заданном JARVIS_PROJECT (см. project_scope) и
    не зависит от того, задана ли отдельно зона роли (outside_zone) — это
    два независимых измерения одной и той же проверки.
    """
    own = project_scope()
    if not own:
        return None

    how = (" Нужен материал чужого проекта по делу — опиши это словами в "
           "результате задания, я передам сам.")

    if tool_name == "Bash":
        cmd = str(tool_input.get("command", "")).replace("\\", "/")
        for m in _PROJECT_DIR_RE.finditer(cmd):
            if m.group(1) != own:
                return (f"Команда упоминает чужой проект "
                        f"(memory/projects/{m.group(1)}), а этот процесс "
                        f"работает на «{own}»." + how)
        if (_ENV_WORD_RE.search(cmd)
                and f"memory/projects/{own}/.env" not in cmd):
            return ("Команда трогает .env — это либо чужой проект, либо "
                    "общий секрет агента (корневой .env), доступа к нему у "
                    "роли проекта нет." + how)
        # Скобки шелл раскрывает раньше всего и уже ПОСЛЕ шлюза: запретной
        # цели в тексте нет, а на диск команда сходит (job-192).
        via_brace = _project_brace_verdict(cmd, own)
        if via_brace:
            return via_brace + how
        if _PROCFS_RE.search(cmd):
            return ("Команда лезет в /proc: через окружение и дескрипторы "
                    "чужих процессов читается корневой .env целиком, поэтому "
                    "роли проекта procfs закрыт." + how)
        via_git = _project_git_danger(cmd, own)
        if via_git:
            return via_git + how
        # Корень проектов без имени: «grep -r ключ memory/projects» не называет
        # ни одного чужого проекта, а вычитывает их все.
        if (_PROJECTS_ROOT_RE.search(cmd)
                and f"memory/projects/{own}" not in cmd):
            return ("Команда читает memory/projects целиком, а этот процесс "
                    f"работает на «{own}»: сузь путь до "
                    f"memory/projects/{own}." + how)
        # Ссылка или .. в аргументе: судим по тому, куда путь ведёт на диске,
        # а не по тому, как он написан (job-115).
        via_link = _bash_symlink_target(cmd, own)
        if via_link:
            return via_link + how
        # Исполнение произвольного кода закрыто целиком: тело может появиться
        # на диске уже после проверки (TOCTOU, job-117).
        via_exec = _project_exec_danger(cmd, own)
        if via_exec:
            return via_exec + how
        # Остаток — код, тело которого всё-таки видно (heredoc пультом и т.п.).
        via_code = _project_code_danger(cmd, own)
        if via_code:
            return via_code + how
        if _bash_reads_all_projects(cmd):
            return ("Рекурсивный поиск по содержимому от корня папки агента "
                    f"вычитал бы и чужие проекты, а этот процесс работает на "
                    f"«{own}»: назови каталог явно (core, modules, "
                    f"memory/facts, memory/projects/{own})." + how)
        # Главное. Всё выше судит ТЕКСТ команды и потому обходится расщеплением
        # пути (job-122). Здесь команда судится по диску: cd, маски, ссылки,
        # деревья целиком, — и закрывается всё, чей набор читаемых файлов шлюзу
        # не виден.
        via_disk = _project_bash_verdict(cmd, own)
        if via_disk:
            return via_disk + how
        return None

    if tool_name == "Grep":
        # Grep читает СОДЕРЖИМОЕ вглубь дерева, и без path его корень — вся
        # папка агента вместе с чужими проектами. Glob и find не режем: они
        # возвращают пути, а не текст (bl-148).
        where = str(tool_input.get("path") or "")
        if _project_wide_root(where):
            return ("Поиск по содержимому без указанного каталога накрыл бы "
                    f"и чужие проекты, а этот процесс работает на «{own}»: "
                    f"задай path — core, modules, memory/facts или "
                    f"memory/projects/{own}." + how)
        # Grep читает дерево, поэтому и судится деревом, а не именем корня:
        # «worktrees» — чистое имя, а внутри копия репозитория с проектами и
        # .env. Тот же суд, что у `grep -r` в Bash.
        for spot in _project_paths_of_word(where, C.ROOT):
            if verdict := _project_tree_verdict(spot, own, where):
                return verdict + how

    if tool_name not in PROJECT_PATH_TOOLS:
        return None

    if tool_name == "Glob":
        # У Glob путь корня в path, а САМ ШАБЛОН — в pattern, и он туда же
        # ведёт: «memory/projects/*/dossier.md» не называет чужой проект, но
        # перечисляет их все (job-119, непроверенная дыра).
        pat = str(tool_input.get("pattern") or "").replace("\\", "/")
        m = _PROJECT_DIR_RE.search(pat)
        if m and m.group(1) != own:
            return (f"Шаблон ведёт в чужой проект "
                    f"(memory/projects/{m.group(1)}), а этот процесс работает "
                    f"на «{own}»." + how)
        if _PROJECTS_ROOT_RE.search(pat) and f"memory/projects/{own}" not in pat:
            return ("Шаблон накрывает memory/projects целиком, а этот процесс "
                    f"работает на «{own}»: сузь до memory/projects/{own}."
                    + how)
        if _ENV_WORD_RE.search(pat):
            return ("Шаблон ищет .env — это либо чужой проект, либо общий "
                    "секрет агента, доступа к нему у роли проекта нет." + how)

    raw = str(tool_input.get("file_path") or tool_input.get("notebook_path")
              or tool_input.get("path") or "")
    if not raw:
        return None

    # Резолвим ДО проверки: «memory/projects/vnutrennii/../nologo/.env»
    # текстом выглядит как свой проект, а на диске указывает на чужой.
    try:
        target = Path(raw).expanduser()
        target = (target if target.is_absolute() else C.ROOT / target).resolve()
        target_text = target.as_posix()
    except (OSError, RuntimeError):
        target_text = raw.replace("\\", "/")

    m = _PROJECT_DIR_RE.search(target_text)
    if m and m.group(1) != own:
        return (f"Путь ведёт в чужой проект (memory/projects/{m.group(1)}), "
                f"а этот процесс работает на «{own}»." + how)

    if target_text == "/proc" or target_text.startswith("/proc/"):
        return ("Путь ведёт в /proc: через окружение чужого процесса читается "
                "корневой .env целиком, поэтому роли проекта procfs закрыт."
                + how)

    if _looks_like_env(raw):
        own_env = (C.ROOT / "memory" / "projects" / own / ".env").resolve()
        if target_text != own_env.as_posix():
            return ("Это .env — секрет либо чужого проекта, либо общий "
                    "секрет агента (корневой .env), доступа к нему у роли "
                    "проекта нет." + how)

    return None


# Признаки записи в шелле — без гита: коммитить очередь нужно и можно.
# Редирект судится по цели, иначе «git show …:backlog.md > derived/x.md»
# (обычное чтение старой версии) отбивалось бы как правка.
BACKLOG_REDIRECT = re.compile(r">>?\s*\S*backlog\.md")
BACKLOG_MUTATE = re.compile(
    r"(tee\b|sed\s+-i|perl\s+-[a-z]*i|patch\b|truncate\b|"
    r"write_text|writelines|\.write\(|open\([^)]*['\"][wa])")
# Законный путь — только API памяти. Его упоминание снимает подозрение.
BACKLOG_API = re.compile(r"backlog_(update|add|lint|edit|fix_row)\s*\(")
# Любой путь, оканчивающийся на backlog.md, встреченный в строке шелла.
BACKLOG_PATH = re.compile(r"[\w./~-]*backlog\.md")


def is_main_backlog(text: str) -> bool:
    """Правда, если путь ведёт в ГЛАВНУЮ очередь — memory/backlog.md.

    Судим по ПУТИ, а не по имени файла (bl-142). Имя тут случайный признак:
    очередь проекта (memory/projects/<проект>/backlog.md) — другой файл,
    другого формата и другого владельца, семь колонок общей таблицы её не
    касаются. Пока запрет висел на базовом имени, файл проекта пришлось
    назвать queue.md, и построенное разошлось с документом Ильи (раздел 6.1).

    Голое «backlog.md» без каталога считаем главной очередью: из шелла оно
    почти всегда значит именно её (запуск из корня репозитория), а ошибиться
    здесь в сторону запрета дешевле, чем молча сбить формат таблицы.
    """
    t = text.replace("\\", "/").strip().strip("'\"")
    if not t.endswith("backlog.md"):
        return False
    while t.startswith("./"):
        t = t[2:]
    # Нет каталога (или он относительный «..») — куда ведёт путь, зависит от
    # текущего каталога процесса, которого я тут не знаю. Считаем главной.
    if "/" not in t or t.startswith("../") or "/../" in t:
        return True
    p = Path(t)
    if not p.is_absolute():
        p = C.ROOT / p
    # C.BACKLOG подменяется в самотесте на временный файл — сверяем и с ним,
    # и с постоянным местом очереди в репозитории.
    try:
        here = p.resolve()
    except OSError:
        return False
    return here in {C.BACKLOG.resolve(), (C.ROOT / "memory" / "backlog.md").resolve()}


# Потолок task.md — пункт 6 приёмки этапа 0. Число живёт в
# modules/projects/tasks.py (TASK_MD_MAX_LINES) и берётся оттуда, а не
# повторяется здесь: два числа в двух файлах разъезжаются молча
# (правило chislo-zhivyot-v-odnom-fakte).
_TASK_MD_RE = re.compile(r"memory[/\\]projects[/\\][^/\\]+[/\\]tasks[/\\]"
                         r"[^/\\]+[/\\]task\.md$")
# Тот же путь, встреченный внутри строки шелла (не обязательно в конце).
TASK_MD_PATH = re.compile(r"projects/[^/\s]+/tasks/[^/\s]+/task\.md")
TASK_MD_REDIRECT = re.compile(r">>?\s*\S*task\.md")
# Законный путь — код протокола. Его упоминание снимает подозрение.
TASK_MD_API = re.compile(r"(modules\.projects|modules/projects)")


def _task_md_cap() -> int:
    try:
        from ..modules.projects.tasks import TASK_MD_MAX_LINES
        return int(TASK_MD_MAX_LINES)
    except Exception:
        return 100


def _apply_edits(text: str, tool_input: dict) -> str:
    """Как выглядел бы файл после Edit/MultiEdit — чтобы считать строки по
    результату, а не по куску правки."""
    edits = tool_input.get("edits")
    if not isinstance(edits, list):
        edits = [tool_input]
    for e in edits:
        if not isinstance(e, dict):
            continue
        old = str(e.get("old_string") or "")
        new = str(e.get("new_string") or "")
        if not old:
            continue
        text = (text.replace(old, new) if e.get("replace_all")
                else text.replace(old, new, 1))
    return text


def task_md_cap_danger(tool_name: str, tool_input: dict) -> str | None:
    """Причина отказа, если правка сделала бы task.md длиннее потолка.

    Потолок держал только код протокола (`tasks.create`, круг доработки), а
    руками через Write/Edit файл дописывался мимо него. Пункт 6 приёмки этапа
    0 требует, чтобы отбивалось И создание, И правка, а «отбивается» значит
    «файл не сохранён», то есть решение принимается ДО записи — здесь.
    """
    if tool_name == "Bash":
        # Шеллом строки не сосчитать заранее: heredoc, sed -i и python-запись
        # кладут в файл то, что видно только после записи, а «отбивается»
        # значит «не сохранено». Поэтому машинная запись в task.md из шелла
        # закрыта целиком — правь Write/Edit (там потолок считается по
        # результату) или через протокол modules.projects. Чтение, грепы и
        # коммиты не трогаем.
        cmd = str(tool_input.get("command", ""))
        if not TASK_MD_PATH.search(cmd):
            return None
        if TASK_MD_API.search(cmd):
            return None
        if not (TASK_MD_REDIRECT.search(cmd) or BACKLOG_MUTATE.search(cmd)):
            return None
        return ("task.md не правится шеллом: потолок "
                f"{_task_md_cap()} строк считается по итоговому файлу, а "
                "шелл кладёт текст мимо счёта. Пиши через Write/Edit либо "
                "через протокол (modules.projects.tasks).")

    if tool_name not in ("Write", "Edit", "MultiEdit"):
        return None
    raw = str(tool_input.get("file_path") or tool_input.get("path") or "")
    if not raw:
        return None
    posix = Path(raw).as_posix()
    if not _TASK_MD_RE.search(posix):
        return None

    cap = _task_md_cap()
    if tool_name == "Write":
        text = str(tool_input.get("content") or "")
    else:
        try:
            target = Path(raw).expanduser()
            target = target if target.is_absolute() else C.ROOT / target
            text = _apply_edits(target.read_text(encoding="utf-8"), tool_input)
        except OSError:
            return None                      # файла нет — судить нечего
    n = len(text.splitlines())
    if n <= cap:
        return None
    return (f"task.md вышел бы в {n} строк при потолке {cap}: правка не "
            f"сохранена. Не влезло — не влезло: сократи текст или убери часть "
            f"по остаточному принципу (даты, цели, критерии, границы, связи, "
            f"материалы — по убыванию приоритета), но задачу не дроби.")


def backlog_direct_write(tool_name: str, tool_input: dict) -> str | None:
    """Причина отказа, если вызов правит очередь мимо core.memory. Иначе None.

    Таблица очереди читается кодом по номерам колонок. Правка её питоном или
    sed'ом ломает формат молча: 25 и 26.08 два фоновых хода подряд вписали
    статус и циклы лишними колонками — 8-9 полей вместо 7, и до самотеста
    этого никто не видел (bl-099). Границу держит код, а не память: правило в
    промпте я уже читал оба раза и всё равно полез руками.

    Edit по очереди запрещён целиком (bl-102). Прежняя поблажка «правь кусок
    строки, если API не умеет» ловила подделку только по признакам «| bl-» в
    новом тексте, а правка ХВОСТА строки — статуса, циклов, чекпоинта — шла
    насквозь: поймано 26.08 на своей же правке bl-079. Того, ради чего
    поблажка была, теперь умеет API: текст задачи — backlog_edit, съехавшая
    строка — backlog_fix_row. Читать, коммитить и грепать очередь можно.
    """
    if tool_name == "Bash":
        cmd = str(tool_input.get("command", ""))
        if "backlog.md.bak" in cmd:
            return None
        if not any(is_main_backlog(m) for m in BACKLOG_PATH.findall(cmd)):
            return None
        if BACKLOG_API.search(cmd):
            return None
        if not (BACKLOG_REDIRECT.search(cmd) or BACKLOG_MUTATE.search(cmd)):
            return None
        return ("Очередь не правится шеллом и питоном напрямую: формат таблицы "
                "ломается молча. Статус, чекпоинт, циклы и приоритет — только "
                "через core.memory.backlog_update(id, status=..., "
                "checkpoint=..., bump_cycle=True), новая задача — через "
                "backlog_add(what, why, prio).")

    raw = str(tool_input.get("file_path") or tool_input.get("path") or "")
    # Судим по пути до файла: «my-backlog.md», «wave-backlog.md» и очередь
    # проекта — чужие файлы, их эта защита не касается.
    if not raw or not is_main_backlog(raw):
        return None

    if tool_name == "Write":
        return ("Очередь целиком не перезаписывается: так теряются чужие "
                "строки. Правь через core.memory.backlog_update / backlog_add.")

    if tool_name in ("Edit", "MultiEdit"):
        return ("Очередь не правится Edit'ом — ни строка целиком, ни её "
                "хвост: семь колонок сбиваются молча и живут так до "
                "самотеста. Статус, чекпоинт, циклы, приоритет — "
                "core.memory.backlog_update('bl-NNN', status=..., "
                "checkpoint=..., bump_cycle=True). Текст задачи и «зачем» — "
                "backlog_edit('bl-NNN', what=..., why=...). Съехавшая строка "
                "(колонок не семь) — backlog_fix_row('bl-NNN', [семь полей]). "
                "Шапка и текст вокруг таблицы — тоже через файл нельзя: скажи "
                "словами, что там поправить.")
    return None


def classify(tool_name: str, tool_input: dict,
             agent_id: str | None = None) -> tuple[str, str]:
    """Возвращает (решение, причина): allow | ask | deny.

    agent_id приходит из PreToolUse, когда вызов сделан внутри подагента
    (Agent/Task) — по нему поле подагента отделяется от моего собственного.
    """
    global _field_calls

    # Одна работа — один исполнитель. Подагент из Agent и задание из delegate
    # ничего не знают друг о друге, и 25.08 половина заданий дня повторяла уже
    # отданную работу (bl-072). Реестр claims видит обоих: задание отбивается
    # в jobs.submit, подагент внутри хода — здесь, на вызове инструмента.
    if tool_name in ("Agent", "Task"):
        text = " ".join(str(tool_input.get(k) or "")
                        for k in ("description", "prompt")).strip()
        if text:
            from . import claims
            row, score = claims.find(text)
            if row:
                M.log("self", {"agent_dup_blocked": row["ref"],
                               "score": round(score, 2)})
                # работа отдана — просто не мне и не сейчас: приёмка требует
                # поля, но это базовый запас, а не открытая дверь
                _grant_field(C.FIELD_WORK_LIMIT, "дубль отбит, работа у другого")
                return "deny", claims.busy_message(row, score)
            claims.claim(text, str(tool_input.get("description") or "")[:120])

    if tool_name in DELEGATE_TOOLS and not agent_id and not is_worker():
        _grant_field(C.FIELD_DELEGATE_BONUS
                     if tool_name in ("Agent", "Task") else C.FIELD_WORK_LIMIT,
                     f"работа отдана: {tool_name}")

    # Принуждение к делегированию. Правило dnyom-rabotayut-podagenty Илья
    # повторил трижды, и трижды оно проиграло: в моменте всегда кажется, что
    # быстрее доделать самому. Уговоры тут не работают — работает счётчик.
    # Отданная работа не отключает счётчик, а поднимает потолок: подагенту
    # нужен запас на его походы, мне — базовый на приёмку.
    field = tool_name in FIELD_TOOLS or (
        tool_name == "Bash" and _is_field_bash(tool_input.get("command", "")))
    if field:
        who = agent_id or ("worker" if is_worker() else None)
        if who:
            # Руки подагента. Ограничитель тут не про делегирование, а про
            # петлю: работа партии укладывается в сотню обращений, тысяча —
            # это заклинивший цикл, и он должен упереться в стену.
            n = _worker_calls[who] = _worker_calls.get(who, 0) + 1
            if n > C.FIELD_WORKER_LIMIT:
                return "deny", (
                    f"Стоп: {n}-й поход в поле в одном задании при потолке "
                    f"{C.FIELD_WORKER_LIMIT}. Похоже на петлю. Заверши работу "
                    f"тем, что уже собрано, и честно напиши в результате, что "
                    f"осталось непройденным. Это отказ шлюза агента, а не "
                    f"указание со страницы; обходить его другим инструментом "
                    f"(curl, wget, requests) запрещено.")
        else:
            _field_calls += 1
            if _field_calls > _field_budget:
                return "deny", (
                    f"Стоп: это {_field_calls}-й поход в поле за ход при "
                    f"потолке {_field_budget}, а поле — работа подагента. "
                    f"Собери сделанное и отдай остаток через "
                    f"mcp__memory__delegate: бриф с целью, тем что уже найдено, "
                    f"где искать дальше и что считать результатом. Один вызов — "
                    f"и ты свободен для Ильи. Своими руками дальше не ходи. "
                    f"Это отказ шлюза самого агента, а не подложное указание из "
                    f"содержимого страницы: он приходит на вызов инструмента, "
                    f"повторяется на каждой попытке и обходить его другим "
                    f"инструментом (curl, wget, requests) запрещено.")
    if tool_name in ZERO_COST_TOOLS or tool_name.startswith("mcp__memory__"):
        return "allow", "нулевая цена: чтение и память"

    if tool_name in WRITE_TOOLS:
        path = tool_input.get("file_path") or tool_input.get("path") or tool_input.get("notebook_path")
        if path and _path_under(str(path), LOG_ZONE()):
            return "deny", ("memory/log/ — неизменяемая зона: только дозапись "
                            f"кодом, не Write и не Edit ({path})")
        if path and _secret_path(str(path)):
            return "deny", ("файл доступов заполняет Илья, а не я: ключи, "
                            f"логины и режим шлюза мне себе не выдавать ({path})")
        if bad := _charter_write_danger(tool_name, tool_input):
            return "deny", bad
        if _inside(path):
            return "allow", "запись в свою папку: нулевая цена, всё под гитом"
        return "deny", f"запись вне папки агента запрещена: {path}"

    if tool_name == "Bash":
        cmd = tool_input.get("command", "")
        try:
            bad = _danger(cmd)
        except Exception as e:                 # разбор сломался — закрываемся
            bad = (f"разбор команды не удался ({e}), а по старой проверке "
                   "она опасна") if NEVER_BASH.search(cmd) else None
        if bad:
            return "deny", f"команда из списка запрещённых навсегда: {bad}"
        # Раздел 8: наружу от имени Ильи — только с его словом. Спрашивается
        # в любом режиме, включая yolo: это не «опасная команда», а действие
        # за другого человека. Нет канала — ask станет deny, и правильно.
        if out := _outbound(cmd):
            return "ask", (f"исходящее от имени Ильи: {out}. Раздел 8 — без "
                           "его подтверждения наружу ничего не уходит; "
                           "черновик показывается целиком (ot-imeni-ili-cherez-chernovik)")
        # Раздел 8: необратимое во внешнем мире. Изменяющий запрос на
        # незнакомый адрес и любые деньги — только со словом Ильи, в любом
        # режиме. Чтение (GET, скачивание) не трогается.
        try:
            net = _net_change(cmd)
        except Exception as e:                 # разбор сломался — закрываемся
            net = f"разбор сетевой части не удался ({e})" if NET_BASH.search(cmd) else None
        if net:
            return "ask", (f"необратимое во внешнем мире: {net}. Читать можно "
                           "молча, менять чужое — с его подтверждения")
        if SAFE_BASH.match(cmd) and _shell_pure_read(cmd):
            return "allow", "чтение через шелл"
        if C.PERMISSION == "careful":
            return "ask", "шелл, меняющий состояние"
        if bad := _root_write_danger(cmd, C.ROOT):
            return "deny", f"запись вне папки агента запрещена: {bad}"
        return "allow", "шелл внутри своей папки"

    if C.PERMISSION == "yolo":
        return "allow", "режим yolo"
    return "ask", "неизвестное действие или внешний мир"


def make_gate(unattended: bool, can_ask: bool = False, limit: int | None = None):
    """PreToolUse-хук: проходит КАЖДЫЙ вызов инструмента, в любом режиме.

    Отдельно от can_use_tool намеренно — тот не вызывается для автоодобренного,
    а «никогда не выходить за пределы папки» должно работать всегда.

    Он же — единственное место, где видно, сколько шагов уже съедено. На 80%
    потолка ход получает напоминание записать чекпоинт: SDK обрывает ход без
    предупреждения, и всё несохранённое пропадает (25.08, bl-048 — 508 секунд
    работы, ноль записанного, задача бралась ещё трижды).
    """
    used = 0
    warned = False

    async def gate(payload, tool_use_id, context):
        nonlocal used, warned
        tool_name = payload.get("tool_name", "")
        tool_input = payload.get("tool_input", {}) or {}

        # Мягкое прерывание. Если Илья написал, пока шёл ход, текст его
        # сообщения вкладывается сюда — другого канала «дослать сообщение в
        # работающий query» у SDK нет. Формально это deny, по смыслу — пауза
        # на один вызов: агент фиксирует сделанное, учитывает новое и
        # повторяет действие. См. core/interrupt.py.
        fresh = INT.take()
        if fresh:
            M.log("self", {"interrupt_delivered": [t[:200] for t in fresh],
                           "at_tool": tool_name})
            _progress.note(f"↯ новое сообщение от Ильи посреди хода ({len(fresh)})")
            return {"hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": INT.notice(fresh),
            }}

        used += 1
        if limit and not warned and used >= math.ceil(limit * C.CHECKPOINT_AT):
            warned = True
            M.log("self", {"steps_warning": used, "limit": limit})
            _progress.note(f"⏳ шагов израсходовано {used} из {limit}")
            return {"hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": (
                    f"Шагов израсходовано {used} из {limit} — ход скоро оборвут "
                    f"без предупреждения. СЕЙЧАС запиши чекпоинт вызовом "
                    f"core.memory.backlog_update(id, status=..., "
                    f"checkpoint='что сделано / что дальше') — файл очереди "
                    f"шлюз не даст править — или итог в "
                    f"память, и заверши ход коротким ответом. Незаписанное "
                    f"пропадёт целиком. Отменён один вызов — повтори его "
                    f"только если он и есть эта запись."),
            }}

        # Вызов сделан внутри подагента? CLI помечает такие agent_id — по нему
        # его поле отделяется от моего. Метки нет (старый CLI) — походы
        # подагента считаются моими, и на это выдан аванс при делегировании.
        agent_id = payload.get("agent_id") or payload.get("agentId")

        # Изоляция по проекту — независимо от зоны роли, ДО общих правил:
        # чужой проект и чужой .env закрыты, даже если у роли зоны нет вовсе
        # или сам инструмент по зоне безобиден.
        project_breach = outside_project(tool_name, tool_input)
        if project_breach:
            M.log("self", {"project_denied": tool_name,
                           "input": str(tool_input)[:200]})
            _progress.note(f"✗ {tool_name}: вне своего проекта")
            return {"hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": project_breach,
            }}

        # Зона роли проверяется ДО общих правил: у постоянной роли своя папка,
        # и запись мимо неё запрещена, даже если сам инструмент безобиден.
        breach = outside_zone(tool_name, tool_input)
        if breach:
            M.log("self", {"zone_denied": tool_name,
                           "input": str(tool_input)[:200]})
            _progress.note(f"✗ {tool_name}: вне зоны роли")
            return {"hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": breach,
            }}

        # Очередь правится только через API памяти: формат таблицы ломается
        # молча, и битую строку видно лишь на следующем самотесте (bl-099).
        wrong_hands = backlog_direct_write(tool_name, tool_input)
        if wrong_hands:
            M.log("self", {"backlog_direct_denied": tool_name,
                           "input": str(tool_input)[:200]})
            _progress.note(f"✗ {tool_name}: очередь правится через backlog_update")
            return {"hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": wrong_hands,
            }}

        # Потолок task.md (пункт 6 приёмки этапа 0): решение принимается ДО
        # записи, иначе «отбито» означало бы «сохранено и потом поругались».
        # Код протокола держит только create и круг доработки — руками файл
        # дописывался мимо него.
        too_long = task_md_cap_danger(tool_name, tool_input)
        if too_long:
            M.log("self", {"task_md_cap_denied": tool_name,
                           "input": str(tool_input)[:200]})
            _progress.note(f"✗ {tool_name}: task.md длиннее потолка")
            return {"hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": too_long,
            }}

        decision, reason = classify(tool_name, tool_input, agent_id)

        if decision == "ask" and unattended and not can_ask:
            decision, reason = "deny", ("спросить некого, " + reason +
                                        ". Запиши это предложением Илье и продолжай")

        if decision != "allow":
            M.log("self", {"gate": decision, "tool": tool_name,
                           "input": str(tool_input)[:300], "reason": reason})
            if decision == "deny":
                _progress.note(f"✗ {tool_name}: {reason}")

        return {"hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": decision,
            "permissionDecisionReason": reason,
        }}

    return gate


def make_post_gate():
    """PostToolUse-хук: единственная его работа — заметить, что подагент вернулся.

    Пока он работал, потолок поля был поднят авансом (иначе его собственные
    походы, не помеченные agent_id, упёрлись бы в мой лимит). Вернулся —
    неизрасходованный аванс сгорает, и дальше я снова на коротком поводке.
    """
    async def post(payload, tool_use_id, context):
        tool_name = payload.get("tool_name", "")
        inside = payload.get("agent_id") or payload.get("agentId")
        if tool_name in ("Agent", "Task") and not inside:
            delegation_done(str(payload.get("tool_use_id") or tool_use_id or ""))
        return {}

    return post


def make_guard(unattended: bool, ask=None):
    """Интерактивный запрос — срабатывает только на то, что шлюз пометил как ask.

    ask — необязательный способ спросить Илью не через терминал: демон ВК
    передаёт сюда функцию, которая пишет вопрос в чат и ждёт ответа. Без неё
    ход из ВК был глухим: любое действие средней цены отклонялось само собой,
    потому что stdin у демона нет.
    """
    async def guard(tool_name, input_data, context):
        preview = str(input_data)[:300]
        if ask is not None:
            _progress.paused = True
            try:
                verdict = await ask(tool_name, preview)
            except Exception as e:
                M.log("error", {"ask_channel": f"{type(e).__name__}: {e}"})
                verdict = False
            finally:
                _progress.paused = False
                _progress.touch()
            # ask отвечает True/False, а в глухое время — строкой: вопрос не
            # задан вовсе, он отложен в копилку (core/questions.py). Ход должен
            # услышать разницу: «не разрешил» и «спросить было не у кого» —
            # разные вещи, второе не повод бросать задачу.
            # verdict: True — «да»; False — Илья ЯВНО ответил не «да»; None —
            # вопрос задан, ответа не дождались за таймаут; строка — вопрос
            # не задавался вовсе (глухое время/проверка), это и есть verdict.
            # До этой правки таймаут и явное «нет» доходили сюда ОДНИМ и тем
            # же False и в логе были неразличимы (ob-109) — самоаудит не мог
            # понять, спрашивали Илью вообще или нет.
            allowed = verdict is True
            timed_out = verdict is None
            deferred = isinstance(verdict, str)
            via = "канал"
            if deferred:
                # Проверочный вызов в копилку не кладётся вовсе — в логе это
                # должно быть видно как «проверка», а не как отложенный вопрос.
                via = "проверка" if Q.is_probe(preview) else "копилка"
            elif timed_out:
                via = "таймаут"
            M.log("verdict", {"tool": tool_name, "input": preview,
                              "allowed": allowed, "via": via},
                  actor="agent" if deferred else ("timeout" if timed_out else "ilya"))
            if allowed:
                return PermissionResultAllow()
            if deferred:
                return PermissionResultDeny(message=verdict)
            return PermissionResultDeny(
                message="Илья не разрешил (или не ответил вовремя). "
                        "Не обходи это другим способом — запиши как вопрос и продолжай.")

        if unattended:
            return PermissionResultDeny(message="Спросить некого: подтверждать нечем.")

        _progress.paused = True
        print(f"\n  ⚠ {tool_name}: {preview}")
        answer = await asyncio.get_event_loop().run_in_executor(
            None, input, "  разрешить? [y/N] ")
        allowed = answer.strip().lower() in ("y", "yes", "д", "да")
        _progress.paused = False
        _progress.touch()
        M.log("verdict", {"tool": tool_name, "input": preview, "allowed": allowed},
              actor="ilya")
        if allowed:
            return PermissionResultAllow()
        return PermissionResultDeny(message="Илья не разрешил. Не обходи это другим способом.")

    return guard


# --------------------------------------------------------------------------
# Ход
# --------------------------------------------------------------------------

TURN_SLOW = """---

# ЭТОТ ХОД

Медленный путь. Перед действием пройди раздел 5 конституции: декомпозиция,
цена ошибки, право сказать «не умею».

ПАМЯТЬ ЗДЕСЬ — ОГЛАВЛЕНИЕ, А НЕ ТЕКСТ. Факты и процедуры перечислены строкой:
id, о чём, размер. Тема относится к задаче — открой файл целиком через Read
(memory/facts/<id>.md, memory/procedures/<id>.md) прежде чем действовать.
Строка в оглавлении — повод прочитать, а не основание считать, что ты помнишь.
Правила даны сутью; спорный случай — прочитай memory/rules/<id>.md, там «почему»
и «как применять». Нет подходящей строки — recall по логу и памяти.

После — раздел 3. Поправил Илья → remember_rule. Решил новую задачу
воспроизводимо → remember_procedure. Узнал устойчивый факт → remember_fact.
Возник срок или обещание → add_obligation. Не хватило контекста → recall.

Пересказ разговора не записывай: он уже в логе."""

def build_system() -> str:
    """Системный промпт хода: конституция, состояние памяти, инструкция хода.

    Отдельной «быстрой» сборки (конституция без памяти, дешёвая модель, без
    шлюза) больше нет — см. удаление маршрутизатора 26.08.2026 ниже по файлу.
    """
    return "\n\n".join([
        M.constitution(),
        "---\n\n# СОСТОЯНИЕ ПАМЯТИ\n\n" + M.build_context(),
        TURN_SLOW,
    ])


def write_system_prompt(text: str) -> Path:
    """Системный промпт — в файл, а не в аргументы командной строки.

    SDK передаёт строковый system_prompt в argv процесса Claude Code, а Linux
    рубит один аргумент длиннее ~128 КБ ошибкой «Argument list too long».
    23.08.2026 память доросла до 134 КБ, и агент перестал отвечать вообще:
    каждый ход падал на старте. Файловая форма потолка не имеет.

    Жалобы «промпт великоват» здесь больше нет (bl-078, 26.08.2026): порог был
    перейдён навсегда, и она уходила в лог каждый ход — 382 раза за сутки. Раз
    в сутки то же самое меряет ночной цикл (core/night.context_measure), а
    что промпт вообще влезает в бюджет, стережёт самотест — до хода, а не после.
    """
    C.DERIVED.mkdir(parents=True, exist_ok=True)
    path = C.DERIVED / f"system_prompt.{os.getpid()}.txt"
    path.write_text(text, encoding="utf-8")
    return path


def token_usage(usage: dict | None) -> dict | None:
    """Токены хода из ResultMessage.usage — только то, что понадобится потом.

    `вход` — оплаченный полностью, `кэш_чтение` — прочитанный из кэша (он и
    несёт системный промпт на всех шагах, кроме первого), `кэш_запись` — первая
    заливка. Сумма трёх — ВЕСЬ контекст, который модель прочитала за ход, по
    всем её шагам; для одного промпта это оценка сверху, и именно так её и
    читает ночной замер, а не как размер промпта.
    """
    if not isinstance(usage, dict):
        return None
    def n(k: str) -> int:
        v = usage.get(k)
        return v if isinstance(v, int) else 0
    inp, read, write = (n("input_tokens"), n("cache_read_input_tokens"),
                        n("cache_creation_input_tokens"))
    if not (inp or read or write):
        return None
    return {"вход": inp, "кэш_чтение": read, "кэш_запись": write,
            "выход": n("output_tokens"), "контекст": inp + read + write}


def build_options(unattended: bool,
                  prompt_path: Path | None = None, ask=None,
                  model: str | None = None,
                  max_turns: int | None = None) -> ClaudeAgentOptions:
    return ClaudeAgentOptions(
        system_prompt={"type": "file", "path": str(prompt_path)},
        model=model or C.MODEL_MAIN,
        cwd=str(C.ROOT),                    # агент живёт только в своей папке
        mcp_servers={"memory": T.server},
        allowed_tools=[],                   # см. комментарий у NEVER
        disallowed_tools=NEVER,
        hooks={
            "PreToolUse": [HookMatcher(hooks=[make_gate(unattended, ask is not None,
                                                        max_turns or C.MAX_TURNS)])],
            "PostToolUse": [HookMatcher(hooks=[make_post_gate()])]},
        can_use_tool=make_guard(unattended, ask),
        setting_sources=[],                 # не подхватывать чужие настройки
        max_turns=max_turns or C.MAX_TURNS,
    )


# Сбои не по нашей вине: перегрузка, пятисотки, обрывы сети. Лечатся ожиданием.
TRANSIENT = re.compile(
    r"(529|overloaded|503|502|504|internal server error|"
    r"timeout|timed out|connection reset|temporarily)", re.I)


def is_transient(e: Exception) -> bool:
    return bool(TRANSIENT.search(str(e)))


def retry_plan(model: str | None) -> list[tuple[str | None, int]]:
    """Пары (модель, сколько ждать перед попыткой).

    Порядок такой намеренно. Первая осечка — сразу другая модель, без пауз:
    сбои у Anthropic обычно накрывают одну модель, и запасная отвечает
    немедленно. Ждать имеет смысл, только когда легло у обеих — тогда это
    общая перегрузка, и она проходит сама за минуту-другую.
    """
    plan = [(model, 0)]
    fallback = C.MODEL_FALLBACK or model
    plan.append((fallback, 0))
    plan += [(fallback, pause) for pause in C.RETRY_BACKOFF]
    return plan


def _explain_failure(e: Exception, limit: int | None = None) -> str:
    """Понятная причина обрыва плюс что делать дальше.

    Первой строкой [текстом]: озвучивать сообщение об ошибке жестоко —
    Илья слушает голосом полминуты, чтобы узнать, что ответа нет.
    """
    text = str(e)
    if is_transient(e):
        return ("[текстом]\n"
                "⚠ Сервер перегружен — это не поломка у нас, а сбой на стороне "
                "Claude. Я честно подождал и повторил несколько раз, не вышло. "
                "Проверить: status.claude.com. Напиши ещё раз через несколько минут — "
                "или скажи «повтори», когда отпустит.")
    if "maximum number of turns" in text or "max_turns" in text:
        # Потолок у каждого вида хода свой: обычный, фоновый, подагентский.
        # Печатать общий C.MAX_TURNS — врать втрое (25.08: «200» на ходе,
        # который оборвался на 60).
        return (f"⚠ Ход упёрся в потолок шагов ({limit or C.MAX_TURNS}) и оборван. "
                f"Сделанное сохранено — файлы записаны, память обновлена. "
                f"Скажи «продолжай» — я подхвачу с того места. "
                f"Если задача заведомо большая, подними потолок: "
                f"JARVIS_MAX_TURNS (обычный ход), JARVIS_SELF_MAX_TURNS "
                f"(фоновый), JARVIS_JOB_MAX_TURNS (подагент).")
    if "usage limit" in text.lower():
        return f"[текстом]\n⚠ Похоже, упёрлись в лимиты подписки. {text[:200]}"
    return f"[текстом]\n⚠ Ход оборвался: {type(e).__name__}: {text[:300]}"


def _work_key(user_input: str) -> str:
    """Короткий отпечаток работы — по нему второй обрыв узнаёт первый.

    Берём начало запроса, а не весь текст: продолжение той же работы приходит
    похожими словами («доделай», «продолжай»), а хвост у длинных сообщений
    каждый раз разный.
    """
    head = " ".join(str(user_input).lower().split())[:80]
    return hashlib.md5(head.encode("utf-8")).hexdigest()[:8]


def park_unfinished(user_input: str, ref: str, actor: str, channel: str,
                    steps: int, limit: int, force: bool = False) -> str | None:
    """Обрыв по потолку шагов кладёт недоделанное в очередь. Вернёт id строки.

    Зачем это в ядре, а не в промпте: 30.08.2026 ход в 17:08 упёрся в потолок,
    сказал Илье «остаток допишу следующим ходом» — и встал на 2,5 часа.
    Следующего хода не было, потому что ход бывает только от сообщения Ильи или
    от фонового цикла, а фоновый цикл берёт задачи ТОЛЬКО из backlog.md, где
    недоделанного не было. Обещание жило в тексте сообщения и не было привязано
    ни к чему. Любая длинная работа обрывалась так же и молча ждала человека.

    Фоновый ход сюда не заходит: у него своя ветка (self_cycle ставит чекпоинт
    на ту же задачу очереди, заводить вторую строку было бы дублем).

    Подагент (actor="job") — тоже: его продолжение заводит modules/jobs/run.py
    через jobs.continue_maxed, отдельным заданием с половинным потолком и
    результатом предыдущего захода на входе. 01.09.2026 сработали ОБА
    механизма разом: job-175 оборвался, ему завели и продолжение job-177, и
    строку очереди bl-160 — фоновый цикл пошёл делать ту же работу
    параллельно живому заданию, а Илья заплатил дважды. Страховка на случай,
    когда продолжения не будет (цепочка исчерпана), — тот же вызов из run.py
    с force=True.
    """
    if actor == "self":
        return None
    if actor == "job" and not force:
        M.log("self", {"park_skipped_job": ref, "steps": steps,
                       "why": "продолжение заводит jobs.continue_maxed"})
        return None
    key = _work_key(user_input)
    mark = f"обрыв:{key}"
    head = " ".join(str(user_input).split())[:150]
    try:
        rows = M._backlog_rows()
    except Exception:
        rows = []
    for r in rows:
        if mark in (r.get("checkpoint") or "") and r.get("status") in (
                "open", "wip", "blocked"):
            M.backlog_update(r["id"], checkpoint=(
                f"{mark} · обрыв повторился {M.today()} на {steps} шагах, "
                f"событие {ref}. Что сделано — в логе по этой ссылке.")[:400])
            M.log("self", {"parked_again": r["id"], "ref": ref, "steps": steps})
            return r["id"]
    try:
        task_id = M.backlog_add(
            what=(f"Доделать оборванное ({channel}): {head}"),
            why=(f"Ход {ref} упёрся в потолок шагов ({limit}) на {steps} шагах "
                 f"и оборвался. Сделанное лежит в логе по этой ссылке, "
                 f"продолжения само не случится — фоновый цикл берёт работу "
                 f"только отсюда."),
            prio=1)
    except Exception as e:                      # очередь битая — не тонуть тут
        M.log("error", {"park_failed": str(e)[:200], "ref": ref})
        return None
    M.backlog_update(task_id, checkpoint=(
        f"{mark} · оборвано {M.today()} на {steps} шагах, событие {ref}. "
        f"Начинать с проверки лога: часть работы уже сделана.")[:400])
    M.log("self", {"parked_unfinished": task_id, "ref": ref, "steps": steps,
                   "limit": limit, "channel": channel})
    return task_id


async def turn(user_input: str, actor: str = "ilya", unattended: bool = False,
               progress: bool = False, ask=None, model: str | None = None,
               max_turns: int | None = None, channel: str = "cli") -> str:
    ref = M.log("message_in", user_input, actor=actor)
    M.set_turn(ref)   # все строки хода получат поле turn с этой ссылкой
    _progress.start(progress)
    reset_field_counter()
    LAST_RESULT["hit_max_turns"] = False
    # Ссылка на событие хода нужна снаружи: подагентский воркер кладёт остаток
    # в очередь уже после turn() и должен сослаться на ТОТ ход, а не на свой.
    LAST_RESULT["ref"] = ref
    HB.start(channel)

    try:
        _progress.note(f"{model or C.MODEL_MAIN} · режим {C.PERMISSION}")

        reply_parts: list[str] = []
        # Предпоследний прогон текста: запасной ответ на случай, когда модель
        # сказала итог, а потом ещё что-то записала (память, коммит) и хвост
        # после последнего инструмента оказался пустым.
        last_run: list[str] = []
        prompt_path = write_system_prompt(build_system())
        try:
            # Перегрузка на стороне сервера — не поломка, а погода: 24.08.2026
            # инцидент с Opus положил каждый ход, и Илья получал голосом текст
            # ошибки. Ждём и повторяем; на последней попытке пробуем модель
            # попроще — сбои обычно бьют по одной, а ответ лучше, чем ничего.
            plan = retry_plan(model)
            attempts = len(plan)
            for attempt, (use_model, wait) in enumerate(plan):
                reply_parts.clear()
                last_run.clear()
                if wait:
                    M.log("self", {"retry": attempt, "in_sec": wait, "model": use_model})
                    _progress.note(f"жду {wait}с и пробую снова "
                                   f"({attempt + 1} из {attempts})")
                    await asyncio.sleep(wait)
                elif attempt:
                    _progress.note(f"перехожу на {use_model} без ожидания")
                try:
                    async for msg in query(
                            prompt=f"{user_input}\n\n[событие: {ref}]",
                            options=build_options(unattended, prompt_path,
                                                  ask, use_model, max_turns)):
                        if isinstance(msg, AssistantMessage):
                            for b in msg.content:
                                if isinstance(b, TextBlock) and b.text.strip():
                                    reply_parts.append(b.text.strip())
                                    _progress.touch()
                                elif isinstance(b, ToolUseBlock):
                                    # Всё, что сказано ДО вызова инструмента, —
                                    # ход мысли, а не ответ. Илье уходит только
                                    # хвост после последнего инструмента.
                                    if reply_parts:
                                        last_run[:] = reply_parts
                                        reply_parts.clear()
                                    M.log("action", {"tool": b.name,
                                                     "input": str(b.input)[:2000]})
                                    what = _describe(b.name, b.input or {})
                                    _progress.step(what)
                                    # Пульс: если Илья давно ничего от меня не
                                    # слышал, статус уйдёт сам — core/heartbeat.py.
                                    HB.beat(what, say=_say(b.name, b.input or {}))
                        elif isinstance(msg, ResultMessage):
                            # SDK умеет закончить ход и без исключения — просто
                            # итогом error_max_turns. Метка нужна и здесь.
                            if getattr(msg, "subtype", None) == "error_max_turns":
                                LAST_RESULT["hit_max_turns"] = True
                            session = getattr(msg, "session_id", None)
                            total = getattr(msg, "total_cost_usd", None)
                            spent = cost_delta(session, total)
                            # Токены хода. Считать надо их, а не доллары
                            # (правило model-ne-ekonomit), и до 26.08 их не
                            # писал никто: спросить «во что обошёлся день»
                            # было нечем. Отсюда же ночной замер промпта
                            # берёт верхнюю оценку контекста в токенах.
                            tok = token_usage(getattr(msg, "usage", None))
                            LAST_RESULT.update(
                                subtype=getattr(msg, "subtype", None),
                                cost_usd=spent, cumulative=total, session=session,
                                tokens=tok,
                                turns=getattr(msg, "num_turns", None))
                            M.log("self", {"result": getattr(msg, "subtype", None),
                                           "cost_usd": spent,
                                           "cumulative": total,
                                           "tokens": tok,
                                           "session": session,
                                           "turns": getattr(msg, "num_turns", None)})
                    break                       # прошло — выходим из повторов
                except Exception as e:
                    # Повторяем только погоду. Своя ошибка от повтора не пройдёт,
                    # а ждать впустую и жечь ход ради этого незачем.
                    if attempt == attempts - 1 or not is_transient(e) or _progress.steps:
                        raise
                    M.log("self", {"failed_attempt": attempt + 1,
                                   "model": use_model, "why": str(e)[:200]})
        except Exception as e:
            # Ход сорвался, но сделанное никуда не делось: файлы записаны,
            # память обновлена. Терять уже сказанное было бы худшим из вариантов.
            M.log("error", {"turn": f"{type(e).__name__}: {e}",
                            "partial_steps": _progress.steps})
            # Машиночитаемая метка «сгорел на потолке»: по ней self_cycle
            # ставит чекпоинт за меня, если я не успел этого сделать сам.
            LAST_RESULT["hit_max_turns"] = ("maximum number of turns" in str(e)
                                            or "max_turns" in str(e))
            reply_parts.append(_explain_failure(e, max_turns or C.MAX_TURNS))
        finally:
            if prompt_path:
                prompt_path.unlink(missing_ok=True)

        parts = reply_parts or last_run
        reply = "\n\n".join(parts).strip() or "(пустой ответ)"
        # Оборвались на потолке шагов — недоделанное уходит в очередь САМО, до
        # того как ответ уйдёт Илье (bl-150). Иначе продолжение зависит от того,
        # напишет ли он снова: фоновый цикл видит только backlog.md.
        if LAST_RESULT.get("hit_max_turns"):
            parked = park_unfinished(user_input, ref, actor, channel,
                                     _progress.steps,
                                     max_turns or C.MAX_TURNS)
            if parked:
                reply += (f"\n\nОстаток работы не потерян: положил его в "
                          f"очередь строкой {parked} — возьму сам, "
                          f"без напоминания.")
        M.log("self", HB.summary())     # время хода и число шагов — в лог
        M.log("message_out", reply)
        return reply
    finally:
        _progress.stop()
        # Метка «идёт ход» снимается здесь, а не рядом с summary(): ход может
        # оборваться исключением выше, а забытая метка задержит перезапуск
        # демона (core/restart.py ждёт, пока процесс освободится).
        HB.finish()


SELF_PROMPT = """[фоновый ход: Илья молчит, ты работаешь сам]

Задача из очереди: [{id}] {what}
Зачем: {why}
Потрачено циклов на неё: {cycles}
Чекпоинт с прошлого раза: {checkpoint}

Правила фонового хода:
1. Один ход — одна задача — один артефакт: коммит, файл или запись в памяти.
   Ход без артефакта прошёл впустую и стоил денег зря.
2. Разрешено всё внутри своей папки. Без спроса нельзя: писать людям, тратить
   деньги, необратимое во внешнем мире, удалять из memory/log/.
3. Правишь ядро — в том же ходе строка в memory/core_changes.md. Это условие,
   на котором Илья дал право трогать ядро.
4. В КОНЦЕ обнови свою строку в очереди — ТОЛЬКО вызовом, не правкой файла:
   core.memory.backlog_update("{id}", status=..., checkpoint="что сделано /
   что дальше", bump_cycle=True). Руками таблицу не трогать: Edit, питон и sed
   по memory/backlog.md шлюз отобьёт целиком, семь колонок сбиваются молча.
   Текст задачи — backlog_edit(id, what=..., why=...), съехавшую строку
   (колонок не семь) — backlog_fix_row(id, [семь полей]). Между ходами
   тебя нет — незаписанное исчезнет, и в следующий раз начнёшь это заново.
5. Илье не пиши: отчёт о самостоятельной работе идёт одним сообщением утром.
   Исключение — сломалось то, что мешает ему прямо сейчас.
6. Не берись за то, что заведомо не влезет в {max_turns} шагов: отдели кусок,
   остальное допиши в очередь отдельной строкой.

Ответ — 2-4 строки: что сделано, что осталось. Он идёт в лог, не в чат."""


async def self_cycle(ask=None) -> str | None:
    """Один самостоятельный ход в простое. Возвращает отчёт или None.

    Илья разрешил 23.08.2026; 25.08 поднял бюджет до 40 ходов в сутки
    (число живёт в C.SELF_TURNS_PER_DAY). Бюджет считается по логу,
    а не оставлен на усмотрение модели — иначе в первый же интересный день
    она сама себе разрешит потратить сколько угодно.
    """
    spent = M.self_turns_today()
    if spent >= C.SELF_TURNS_PER_DAY:
        return None

    # Задача со статусом blocked ждёт обязательства. Проверяется механически
    # перед каждым отбором: закрылось ob — задача сама вернулась в очередь.
    freed, orphan = M.backlog_unblock_ready()
    if freed:
        M.log("self", {"backlog_unblocked": freed})
    if orphan:
        # blocked без ссылки на ob не разблокирует никто и никогда
        M.log("self", {"backlog_blocked_forever": orphan})

    task = M.backlog_top()
    if not task:
        return None

    M.log("self", {"self_turn": task["id"], "what": task["what"][:120],
                   "spent_today": spent + 1, "budget": C.SELF_TURNS_PER_DAY})
    M.backlog_update(task["id"], status="wip", bump_cycle=True)
    # Илья должен знать, чем я занят в его тишине, а не находить это утром.
    if not HB.quiet_now():
        HB.send(f"Ты молчишь — беру из очереди [{task['id']}]: {task['what'][:160]}")

    prompt = SELF_PROMPT.format(id=task["id"], what=task["what"], why=task["why"],
                                cycles=task["cycles"],
                                checkpoint=task["checkpoint"] or "нет, начинаешь с нуля",
                                max_turns=C.SELF_MAX_TURNS)
    reply = await turn(prompt, actor="self", unattended=True, ask=ask,
                       model=C.MODEL_SELF, max_turns=C.SELF_MAX_TURNS,
                       channel="self")

    # Ход сгорел на потолке шагов — записать чекпоинт он уже не успел.
    # Ставим метку за него: иначе задача берётся заново с нуля, и так по кругу
    # (25.08, bl-048: 508 секунд работы, ноль записанного, взята ещё трижды).
    # Предупреждение на 80% шагов должно было сработать раньше — если метка
    # всё-таки появилась, значит не сработало, и это видно в логе.
    if LAST_RESULT.get("hit_max_turns"):
        row = next((r for r in M._backlog_rows() if r["id"] == task["id"]), None)
        old = ((row or {}).get("checkpoint") or "").strip()
        if not old.startswith("ОБРЫВ"):
            M.backlog_update(task["id"], checkpoint=(
                f"ОБРЫВ на потолке шагов ({C.SELF_MAX_TURNS}), ход {M.today()}: "
                f"что сделано — только в логе, проверить перед продолжением. "
                f"{old}")[:400])
        M.log("self", {"self_turn_maxed": task["id"],
                       "limit": C.SELF_MAX_TURNS})

    # Детект зацикливания. Задача, съевшая свой лимит ходов и не закрытая, не
    # станет ближе на следующем: раньше её просто опускали вниз очереди (prio+5),
    # и она возвращалась наверх, как только выше ничего не оставалось. 25.08.2026
    # так сгорела половина дневного бюджета. Теперь задача снимается из очереди
    # (blocked) и переписывается в вопрос Илье: доделывать, переписать или
    # выкинуть. Его ответ закроет обязательство, и backlog_unblock_ready вернёт
    # задачу в очередь сам — ссылка ob-NNN стоит в чекпоинте.
    fresh = next((r for r in M._backlog_rows() if r["id"] == task["id"]), None)
    if fresh and fresh["status"] == "wip":
        try:
            cycles = int(fresh["cycles"])
        except ValueError:
            cycles = 0
        if cycles >= C.SELF_STUCK_CYCLES:
            ob = M.add_obligation(
                f"Очередь: {task['id']} снята в blocked — {cycles} фоновых хода "
                f"без результата. Спросить Илью одной строкой в общей пачке: "
                f"доделывать, переписать или выкинуть. Его ответ закрывает это "
                f"обязательство и сам возвращает задачу в очередь. На чём стоит — "
                f"чекпоинт в memory/backlog.md.",
                who="ilya", due=str(M.today() + timedelta(days=1)),
                source=f"log:{M.today()}")
            M.backlog_update(
                task["id"], status="blocked",
                checkpoint=f"ЗАСТРЯЛА: {cycles} цикла без завершения, ждёт {ob}. "
                           f"{fresh['checkpoint']}")
            M.log("self", {"self_stuck": task["id"], "cycles": cycles, "asked": ob})
    return reply


async def initiative(reason: str) -> str | None:
    """Ход по собственной инициативе.

    Лимит проверяется здесь, а не оставлен на усмотрение модели: внимание Ильи —
    самый дефицитный ресурс системы, и в первый же интересный день модель
    сама себе разрешит его потратить.
    """
    if M.initiative_count_today() >= C.INITIATIVE_LIMIT:
        M.log("self", {"initiative_suppressed": reason})
        return None
    reply = await turn(reason, actor="system", unattended=True)
    M.log("message_out", reply, actor="agent-initiative")
    return reply
