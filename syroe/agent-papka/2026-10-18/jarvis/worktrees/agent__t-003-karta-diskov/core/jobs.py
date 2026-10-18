"""Задания подагентам, которые переживают ход.

Проблема, ради которой этот модуль существует. Ход агента — одноразовый вызов
SDK: процесс живёт ровно один ход и умирает на результате. Подагент, поднятый
инструментом Agent, живёт ВНУТРИ этого процесса и умирает вместе с ним, а между
ходами агента нет вовсе (факт agent-sobytiyniy). Значит принять результат
подагента после конца хода некому — если только результат не лежит в файле, а
исполнитель не является отдельным процессом операционной системы.

Как устроено:

  · главный ход зовёт submit() — тот кладёт бриф на диск и поднимает
    ОТДЕЛЬНЫЙ процесс (`python -m jarvis.modules.jobs.run <id>`,
    start_new_session=True). Процесс отвязан от группы хода и переживает его
    конец;
  · воркер в своём процессе зовёт обычный agent.turn(): свежие модульные
    переменные (agent/heartbeat/interrupt держат состояние на процесс), тот же
    шлюз разрешений, тот же лог. Один ход на процесс — правило не нарушено;
  · воркер пишет результат в memory/jobs/<id>/result.md и статус в job.json;
  · демон ВК на каждом такте зовёт tick() (дёшево: чтение нескольких мелких
    файлов) и подкладывает готовые результаты в промпт СЛЕДУЮЩЕГО хода либо,
    если Илья молчит, поднимает ход-доклад сам.

Почему журнал и результаты в memory/, а не в derived/, как предполагал факт
podagenty-arhitektura: снесу derived — результат работы подагента не вернётся
ничем. Правило v-derived-tolko-vosstanovimoe запрещает уникальное в derived.
В derived остаётся только сырой вывод процесса-воркера (derived/jobs/<id>.log) —
он одноразовый и восстановлению не подлежит по определению.

Параллельность считается по железу, а не по деньгам: рабочий стол Ильи днём
съедает почти все ядра, и три подагента на четырёхъядерной машине превратят
разговор в ВК в ожидание.
"""

from __future__ import annotations

import fcntl
import json
import os
import re
import signal
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from . import config as C
from . import memory as M

# Живые статусы. queued — ждёт свободного слота, running — идёт процесс,
# done/failed/empty — закончено, результат лежит в файле. empty — процесс
# дошёл до конца и вернул пустышку: работы в результате нет (см. empty_reason).
LIVE = ("queued", "running")
DONE = ("done", "failed", "empty")

ID_RE = re.compile(r"^job-(\d+)$")


# --------------------------------------------------------------------------
# Файлы задания
# --------------------------------------------------------------------------

def job_dir(job_id: str) -> Path:
    return C.JOBS / job_id


def brief_path(job_id: str) -> Path:
    return job_dir(job_id) / "brief.md"


def claim_path(job_id: str) -> Path:
    """Чем задание представляется реестру занятой работы. Может не быть."""
    return job_dir(job_id) / "claim.txt"


def result_path(job_id: str) -> Path:
    return job_dir(job_id) / "result.md"


def meta_path(job_id: str) -> Path:
    return job_dir(job_id) / "job.json"


def worker_log(job_id: str) -> Path:
    """Сырой вывод процесса-воркера. Производное: одноразовое, сносимо."""
    return C.JOBS_LOGS / f"{job_id}.log"


def rel_path(path: Path) -> str:
    """Путь для журнала и промпта — относительный, если он внутри папки агента.

    Относительный читается человеком и открывается инструментом Read как есть.
    Абсолютный остаётся абсолютным (так бывает только в самотесте, где журнал
    подменён временным каталогом) — лучше некрасиво, чем с исключением.
    """
    try:
        return str(path.relative_to(C.ROOT))
    except ValueError:
        return str(path)


def _now() -> str:
    return datetime.now(ZoneInfo(C.TZ)).isoformat(timespec="seconds")


def read(job_id: str) -> dict | None:
    try:
        return json.loads(meta_path(job_id).read_text(encoding="utf-8"))
    except Exception:
        return None


def _write(meta: dict) -> None:
    """Атомарная запись: сначала во временный файл, потом переименование.

    Журнал читает демон на каждом такте; половинчатый json в этот момент
    выглядел бы как пропавшее задание.
    """
    path = meta_path(meta["id"])
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, path)


def _update(job_id: str, **fields) -> dict | None:
    """Правка журнала под замком: пишут и диспетчер, и сам воркер.

    Без замка гонка «диспетчер помечает провалом ⇄ воркер пишет результат»
    молча теряет одну из двух записей, а это ровно тот случай, ради которого
    механизм затевался.
    """
    d = job_dir(job_id)
    d.mkdir(parents=True, exist_ok=True)
    with open(d / ".lock", "a+", encoding="utf-8") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        try:
            meta = read(job_id)
            if meta is None:
                return None
            meta.update(fields)
            _write(meta)
            return meta
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)


def all_jobs() -> list[dict]:
    """Весь журнал, по возрастанию id."""
    if not C.JOBS.exists():
        return []
    out = []
    for d in sorted(C.JOBS.iterdir()):
        if d.is_dir() and ID_RE.match(d.name):
            meta = read(d.name)
            if meta:
                out.append(meta)
    return out


def by_status(*statuses: str) -> list[dict]:
    return [m for m in all_jobs() if m.get("status") in statuses]


# --------------------------------------------------------------------------
# Сколько подагентов тянет машина
# --------------------------------------------------------------------------

def cpu_slots() -> int:
    """Ядра минус одно: последнее остаётся демону, иначе разговор встанет."""
    try:
        n = len(os.sched_getaffinity(0))
    except (AttributeError, OSError):
        n = os.cpu_count() or 2
    return max(1, n - 1)


def mem_available_mb() -> int:
    """MemAvailable, а не free: кэш отдаётся под нагрузкой и свободным считается."""
    try:
        for line in Path("/proc/meminfo").read_text(encoding="utf-8").splitlines():
            if line.startswith("MemAvailable:"):
                return int(line.split()[1]) // 1024
    except Exception:
        pass
    return C.JOB_MEM_MB          # не знаем — считаем, что хватает ровно на одного


def capacity() -> int:
    """Сколько заданий можно держать одновременно ПРЯМО СЕЙЧАС.

    Считается в момент запуска, а не константой: днём браузер и базы Ильи уже
    съели ядра, ночью машина свободна. Деньги в расчёт не входят намеренно —
    ограничение поставлено по железу (правило dnyom-rabotayut-podagenty).
    Ноль в JOB_MAX_PARALLEL выключает механизм без правки кода: задания
    останутся в очереди, ничего не запустится.
    """
    if C.JOB_MAX_PARALLEL <= 0:
        return 0
    return max(1, min(cpu_slots(),
                      mem_available_mb() // C.JOB_MEM_MB,
                      C.JOB_MAX_PARALLEL))


# --------------------------------------------------------------------------
# Постановка задания
# --------------------------------------------------------------------------

# Разделы брифа. BRIEF_MUST — жёсткие: без них задание не ставится вовсе.
# До 25.08.2026 проверка была мягкой (строка в лог) и за 15 заданий не
# остановила ни одного: job-009 ушёл в работу без критериев приёмки, потратил
# 1.6 доллара и закрылся как done со словами «сдал 0 карточек из 20».
# Подагент видит только бриф и спросить некого — не сказано, что считать
# результатом, значит результата не будет, а деньги уйдут.
# BRIEF_ADVISED — мягкие: их отсутствие по-прежнему только пишется в лог,
# потому что «где искать» бывает очевидно из самой задачи.
BRIEF_MUST = ("задача", "формат результата", "критерии приёмки")
BRIEF_ADVISED = ("где искать", "что можно самому", "что только с моего согласия")
BRIEF_REQUIRED = BRIEF_MUST + BRIEF_ADVISED

# Сколько значащих знаков должно стоять после заголовка раздела. Заголовок без
# текста — это не раздел: «Критерии приёмки: —» проходило бы проверку по
# подстроке и не значило бы ничего (правило merilo-schitaetsya-po-vsemu-polyu).
BRIEF_SECTION_MIN = 10


# Заголовок раздела — только с начала строки, с любой разметкой («## Задача»,
# «**Задача**», «Задача:») и необязательным двоеточием. Поиск голой подстрокой
# был неверен в обе стороны: бриф постоянной роли, где слово «задача» стоит в
# тексте промпта ВЫШЕ настоящего раздела, отбивался как пустой (режим learn у
# юриста, 26.08), а случайное упоминание с длинным абзацем следом, наоборот,
# сходило за заполненный раздел.
_HEAD_RE = {h: re.compile(
    r"^[ \t]*(?:#{1,6}[ \t]*)?(?:\*\*|__)?" + re.escape(h)
    + r"(?:\*\*|__)?[ \t]*(?::|—|-|$)", re.MULTILINE)
    for h in BRIEF_REQUIRED}

# Любой markdown-заголовок закрывает раздел: за «## Задача» в брифе роли идёт
# «## Где искать», но может идти и раздел, которого нет в списке обязательных.
_ANY_HEAD_RE = re.compile(r"^[ \t]*#{1,6}[ \t]+\S", re.MULTILINE)


def _section_body(low: str, head: str) -> str | None:
    """Текст раздела: от заголовка до ближайшего следующего заголовка."""
    m = _HEAD_RE[head].search(low)
    if m is None:
        return None
    start = m.end()
    ends = [o.start() for h, r in _HEAD_RE.items() if h != head
            and (o := r.search(low, start))]
    if (o := _ANY_HEAD_RE.search(low, start)):
        ends.append(o.start())
    end = min([e for e in ends if e > start] or [len(low)])
    return low[start:end]


def brief_gaps(brief: str) -> tuple[list[str], list[str]]:
    """Пустые разделы брифа: (жёсткие, мягкие). Пустой = нет или без текста."""
    low = (brief or "").lower()

    def thin(head: str) -> bool:
        body = _section_body(low, head)
        if body is None:
            return True
        return len(re.sub(r"\W+", "", body, flags=re.UNICODE)) < BRIEF_SECTION_MIN

    return ([h for h in BRIEF_MUST if thin(h)],
            [h for h in BRIEF_ADVISED if thin(h)])


# Что писать под каждым заголовком. Нужно не валидатору, а тому, кто получил
# отказ: до 27.08 отказ называл пустые разделы, но не показывал, как выглядит
# заполненный бриф, — и после отказа приходилось лезть grep'ом в этот файл за
# точными заголовками (трижды за 26.08). Образец отдаётся вместе с отказом.
BRIEF_HINT = {
    "задача": "что именно сделать — самодостаточно, без отсылок к разговору",
    "формат результата": "чем отчитаться: файл, список строк, число",
    "критерии приёмки": "по чему видно, что сделано, а не изображено",
    "где искать": "файлы, каталоги, команды — откуда начинать",
    "что можно самому": "что исполнитель делает без спроса",
    "что только с моего согласия": "что он обязан показать мне до того, как делать",
}


def brief_skeleton() -> str:
    """Готовый скелет брифа с точными заголовками. Собирается из BRIEF_REQUIRED,
    чтобы образец не разошёлся с тем, что на самом деле проверяет валидатор."""
    return "\n\n".join(
        f"## {h[0].upper() + h[1:]}\n{BRIEF_HINT.get(h, '')}"
        + ("" if h in BRIEF_MUST else "   (необязательно)")
        for h in BRIEF_REQUIRED)


class ThinBrief(ValueError):
    """Бриф без жёстких разделов. Задание не создаётся."""

    def __init__(self, missing: list[str]):
        self.missing = list(missing)
        super().__init__(
            "бриф не принят — пусто в разделах: "
            + ", ".join(f"«{m}»" for m in self.missing)
            + ". Подагент видит ТОЛЬКО бриф и спросить меня не может: без "
              "критериев приёмки и формата результата он вернёт «ничего не "
              "нашёл» за те же деньги.\n\nВот скелет — заголовки дословные, "
              "первые три обязательны:\n\n" + brief_skeleton()
            + f"\n\nЗаголовок считается ТОЛЬКО с начала строки (можно «## "
              f"Задача», «**Задача**», «Задача:»); то же слово посреди абзаца "
              f"разделом не станет. Под заголовком нужен настоящий текст: "
              f"меньше {BRIEF_SECTION_MIN} значащих знаков или прочерк — это "
              f"пустой раздел. Допиши и позови снова.")


# Признаки того, что задание отработало вхолостую: процесс дошёл до конца, а
# работы в результате нет. Проверяется на закрытии, потому что done не должен
# означать «отработал» — только «процесс не упал».
#
# Судится ПО ДЕЛУ, а не по словам (правило priznak-ishchetsya-po-delu). Первая
# редакция искала «ни одного», «0 из N», «ничего не нашёл» где угодно в тексте —
# и 27.08 выяснилось, что за сутки она заклеймила все восемь отчётов, которые
# смотрела, включая четыре с настоящими находками, не поймав при этом ни одной
# настоящей пустышки: отказная фраза попадалась внутри ФОРМУЛИРОВКИ находки
# («ни одной настоящей пустышки не поймано»). Поэтому теперь:
#   · главное — артефакт: файл на диске или коммит в гите, названные в отчёте;
#   · слова смотрятся, только когда предъявить нечего, и только на КРАЮ отчёта
#     (первые/последние строки) — там подагент подводит итог, а не описывает
#     находку.
EMPTY_MIN = 30              # значащих знаков; подагенту велено сдавать 3-5 строк
EDGE_LINES = 2              # сколько строк с каждого края считать итогом
ZERO_RE = re.compile(
    r"\b0\s*(?:из|/)\s*\d+"
    r"|\bсдал[аио]?\s+0\b"
    r"|\bни\s+одн(?:ого|ой)\b"
    r"|\bничего\s+не\s+(?:наш|сдел|получ|удал|собр|вышл)")
PATH_RE = re.compile(r"\b(?:derived|memory|modules|core)/[\w./-]+\.\w{1,5}")
# Хеш коммита — только когда рядом сказано, что это коммит: голое семизначное
# шестнадцатеричное слово слишком часто оказывается чем-то другим.
SHA_RE = re.compile(r"(?:коммит\w*|commit)\s+([0-9a-f]{7,40})\b", re.IGNORECASE)


def _commit_exists(sha: str) -> bool:
    """Есть ли такой коммит в репозитории агента."""
    try:
        return subprocess.run(
            ["git", "cat-file", "-e", f"{sha}^{{commit}}"],
            cwd=C.ROOT, timeout=10,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def artifacts(text: str) -> list[str]:
    """След работы, существование которого можно проверить машиной.

    Ссылки на задачи и обязательства (bl-, ob-) артефактом НЕ считаются: они
    приезжают к подагенту в брифе, и переписать их в отчёт можно, не сделав
    ничего.
    """
    found = [p for p in sorted(set(PATH_RE.findall(text)))
             if (C.ROOT / p).exists()]
    if not found:
        found = [s for s in sorted(set(m.group(1).lower()
                                       for m in SHA_RE.finditer(text)))
                 if _commit_exists(s)]
    return found


def empty_reason(text: str | None) -> str | None:
    """Почему этот результат — пустышка. None, если работа настоящая."""
    body = (text or "").strip()
    if len(re.sub(r"\W+", "", body, flags=re.UNICODE)) < EMPTY_MIN:
        return "результата нет: пусто или пара слов"
    if artifacts(body):
        return None                      # есть что предъявить — работа настоящая
    paths = sorted(set(PATH_RE.findall(body)))
    if paths:
        return "обещанных файлов нет на диске: " + ", ".join(paths[:3])
    lines = [ln for ln in body.splitlines() if ln.strip()]
    edge = " ".join(lines[:EDGE_LINES] + lines[-EDGE_LINES:]).lower()
    if ZERO_RE.search(edge):
        return "в итоге отчёта сказано, что сдано ноль"
    return None


def new_id() -> str:
    """Следующий свободный id. Каталог создаётся тут же — это и есть замок:
    два процесса не получат одинаковый номер."""
    C.JOBS.mkdir(parents=True, exist_ok=True)
    nums = [int(m.group(1)) for d in C.JOBS.iterdir()
            if (m := ID_RE.match(d.name))]
    n = (max(nums) + 1) if nums else 1
    while True:
        job_id = f"job-{n:03d}"
        try:
            job_dir(job_id).mkdir()
            return job_id
        except FileExistsError:
            n += 1


class DuplicateWork(Exception):
    """Эту работу уже делает другой исполнитель (см. core/claims)."""

    def __init__(self, message: str, ref: str):
        super().__init__(message)
        self.ref = ref


def submit(brief: str, title: str = "", model: str | None = None,
           max_turns: int | None = None, timeout: int | None = None,
           origin: str = "vk", autostart: bool = True,
           dedupe: bool = True, claim_text: str | None = None,
           zone: list[str] | None = None,
           project: str | None = None) -> str:
    """Поставить задание. Возвращает id. Не блокирует: ход идёт дальше.

    brief — самодостаточный текст: подагент видит ТОЛЬКО его и не может
    спросить Илью сам. Разделы BRIEF_MUST обязательны — без них задание не
    создаётся (ThinBrief); разделы BRIEF_ADVISED только пишутся в лог.

    claim_text — чем задание представляется реестру занятой работы. По
    умолчанию это весь бриф, и для разовых заданий так и правильно. Но у
    постоянной роли (modules/agents) бриф на девять десятых состоит из
    описания самой роли: два РАЗНЫХ вопроса юристу совпадают по мере Жаккара
    выше порога и второй отбивается как дубль первого. Поэтому роль передаёт
    сюда только суть работы, а не весь текст, который увидит исполнитель.

    project — идентификатор проекта (см. modules/projects), едет в процесс
    переменной JARVIS_PROJECT так же, как zone едет в JARVIS_ZONE. Не задан —
    переменная в дочернем процессе не ставится вовсе, и шлюз (core.agent.
    outside_project) для него молчит: поведение обычного задания без проекта
    не меняется.
    """
    brief = (brief or "").strip()
    if not brief:
        raise ValueError("бриф пустой: подагент видит только его")

    # Тощий бриф отбивается ДО создания каталога: незачем плодить job-NNN,
    # который всё равно нельзя запускать.
    must, advised = brief_gaps(brief)
    if must:
        M.log("self", {"job_brief_rejected": (title or brief[:60])[:80],
                       "missing": must})
        raise ThinBrief(must)

    # Эту работу уже кто-то делает? Реестр видит и задания, и подагентов
    # внутри ходов (bl-072). Отбой — до создания каталога задания.
    claim_text = (claim_text or "").strip() or brief
    if dedupe:
        from . import claims
        row, score = claims.find(claim_text)
        if row:
            M.log("self", {"job_dup_blocked": row["ref"],
                           "score": round(score, 2), "title": title[:80]})
            raise DuplicateWork(claims.busy_message(row, score), row["ref"])

    job_id = new_id()
    title = (title or brief.splitlines()[0])[:120]
    brief_path(job_id).write_text(brief, encoding="utf-8")
    if claim_text != brief:
        claim_path(job_id).write_text(claim_text, encoding="utf-8")

    if advised:
        M.log("self", {"job_brief_thin": job_id, "missing": advised})

    _write({
        "id": job_id, "title": title, "status": "queued",
        "model": model or C.JOB_MODEL,
        "max_turns": int(max_turns or C.JOB_MAX_TURNS),
        "timeout": int(timeout or C.JOB_TIMEOUT),
        "origin": origin,
        "zone": list(zone or []),
        "project": (project or "").strip(),
        "created": _now(), "started": None, "finished": None, "sec": None,
        "pid": None, "cost_usd": None, "turns": None,
        "result": None, "error": None,
        "delivered": False, "delivered_at": None,
    })
    M.log("self", {"job_submit": job_id, "title": title,
                   "model": model or C.JOB_MODEL})
    if autostart:
        pump()
    return job_id


# --------------------------------------------------------------------------
# Запуск и присмотр
# --------------------------------------------------------------------------

def _python() -> str:
    return str(C.PYTHON) if C.PYTHON.exists() else sys.executable


def _spawn(job_id: str) -> int:
    """Отдельный процесс ОС. start_new_session — чтобы он пережил конец хода.

    Без новой сессии процесс остаётся в группе хода, и когда SDK убивает
    группу на выходе, подагент умирает вместе с ней — то есть ровно то, от
    чего этот модуль лечит.
    """
    C.JOBS_LOGS.mkdir(parents=True, exist_ok=True)
    out = worker_log(job_id).open("a", encoding="utf-8")
    # Зона записи роли (modules/agents) едет в процесс переменной окружения:
    # шлюз в core/agent.py отбивает по ней запись мимо своей папки. У обычного
    # задания зоны нет — пусто, и проверка не включается вовсе.
    meta = read(job_id) or {}
    # live_env(): без унаследованных JARVIS_SELFTEST/JARVIS_LIVE — воркер
    # подагента живёт дольше вызвавшего его хода и обязан сам объявить себя
    # живым через M.mark_live(), а не получить чужой статус по наследству
    # через окружение (bl-121, тот же класс дыры, что у демона).
    env = C.live_env({"JARVIS_ROLE": "worker"})
    zone = meta.get("zone") or []
    if zone:
        env["JARVIS_ZONE"] = os.pathsep.join(zone)
    else:
        env.pop("JARVIS_ZONE", None)
    # Проект — тем же способом: параметр задания задан → передаём переменной,
    # не задан → не ставим вовсе (пустая JARVIS_PROJECT — это не «без
    # проекта», см. core.agent.project_scope), чтобы обычные задания без
    # проекта не отличались от сегодняшнего поведения.
    project = (meta.get("project") or "").strip()
    if project:
        env["JARVIS_PROJECT"] = project
    else:
        env.pop("JARVIS_PROJECT", None)
    try:
        proc = subprocess.Popen(
            [_python(), "-m", "jarvis.modules.jobs.run", job_id],
            cwd=str(C.ROOT.parent), stdin=subprocess.DEVNULL,
            stdout=out, stderr=subprocess.STDOUT,
            start_new_session=True, env=env,
        )
    finally:
        out.close()
    return proc.pid


def alive(pid: int | None) -> bool:
    if not pid:
        return False
    try:
        os.kill(int(pid), 0)
    except (OSError, ValueError):
        return False
    return True


def _terminate(pid) -> None:
    """Снять зависшее задание. Отдельной функцией — чтобы самотест мог её
    подменить: настоящий killpg по выдуманному pid убил бы сам самотест."""
    try:
        os.killpg(os.getpgid(int(pid)), signal.SIGTERM)
    except (OSError, ValueError):
        pass


def pump() -> list[str]:
    """Запустить столько ждущих заданий, сколько влезает в машину."""
    started = []
    free = capacity() - len(by_status("running"))
    if free <= 0:
        return started
    for meta in by_status("queued"):
        if free <= 0:
            break
        pid = _spawn(meta["id"])
        _update(meta["id"], status="running", pid=pid, started=_now())
        M.log("self", {"job_start": meta["id"], "pid": pid,
                       "model": meta.get("model")})
        started.append(meta["id"])
        free -= 1
    return started


def _elapsed(meta: dict) -> float:
    try:
        started = datetime.fromisoformat(meta["started"])
    except Exception:
        return 0.0
    return (datetime.now(ZoneInfo(C.TZ)) - started).total_seconds()


def reap() -> list[str]:
    """Прибрать за умершими и просроченными. Возвращает id, которых коснулись.

    Осиротевший процесс — отдельная беда: без этой проверки статус «в работе»
    врал бы вечно, а слот параллельности не освобождался бы никогда (например
    после перезапуска машины).
    """
    touched = []
    for meta in by_status("running"):
        job_id = meta["id"]
        if alive(meta.get("pid")):
            if meta.get("timeout") and _elapsed(meta) > meta["timeout"]:
                _terminate(meta["pid"])
                finish(job_id, "failed",
                       f"Задание оборвано по таймауту ({meta['timeout']} с).")
                M.log("self", {"job_timeout": job_id, "sec": round(_elapsed(meta))})
                touched.append(job_id)
            continue
        # процесса нет
        if result_path(job_id).exists():
            # воркер успел записать результат, но не статус — доверяем файлу
            finish(job_id, "done", None)
        else:
            finish(job_id, "failed",
                   "Процесс подагента исчез, не оставив результата. "
                   f"Сырой вывод: {worker_log(job_id)}")
            M.log("self", {"job_orphan": job_id, "pid": meta.get("pid")})
        touched.append(job_id)
    return touched


def finish(job_id: str, status: str, result: str | None,
           cost_usd: float | None = None, turns: int | None = None) -> None:
    """Закрыть задание. Зовётся и воркером, и диспетчером при уборке.

    Результат пишется в файл ДО смены статуса: статус done без результата
    выглядел бы как потерянная работа.

    done означает только «процесс не упал». Если работы в результате нет —
    пусто, «сдал 0 из 20», обещанные файлы не появились на диске — статус
    становится empty, а причина ложится в error. Иначе холостое задание
    неотличимо от сделанного, и это уже стоило 1.6 доллара (job-009).
    """
    if result is not None:
        result_path(job_id).write_text(result.strip() + "\n", encoding="utf-8")
    meta = read(job_id) or {}
    reason = None
    if status == "done":
        body = result
        if body is None:
            try:
                body = result_path(job_id).read_text(encoding="utf-8")
            except OSError:
                body = ""
        reason = empty_reason(body)
        if reason:
            status = "empty"
            M.log("self", {"job_empty": job_id, "why": reason})
    fields = {
        "status": status, "finished": _now(),
        "result": (rel_path(result_path(job_id))
                   if result_path(job_id).exists() else None),
    }
    if meta.get("started"):
        fields["sec"] = round(_elapsed(meta), 1)
    if cost_usd is not None:
        fields["cost_usd"] = cost_usd
    if turns is not None:
        fields["turns"] = turns
    if reason:
        fields["error"] = reason
    elif status == "failed" and result:
        fields["error"] = result[:400]
    _update(job_id, **fields)
    M.log("self", {"job_done": job_id, "status": status,
                   "sec": fields.get("sec"), "cost_usd": cost_usd})


def tick() -> dict:
    """Такт диспетчера: прибрать за умершими, запустить ждущих.

    Дёшево (чтение нескольких мелких файлов) — поэтому зовётся демоном на
    каждом опросе long-poll, как ночной цикл.
    """
    return {"reaped": reap(), "started": pump()}


# --------------------------------------------------------------------------
# Приёмка: результат доезжает до следующего хода
# --------------------------------------------------------------------------

RESULT_HEAD = 1500          # сколько результата класть в промпт на задание
BLOCK_LIMIT = 6000          # потолок всего блока: промпт и так на бюджете


def ready_reports() -> list[dict]:
    """Законченные задания, о которых Илье ещё не докладывали."""
    return [m for m in by_status(*DONE) if not m.get("delivered")]


def mark_delivered(ids) -> None:
    for job_id in ids:
        _update(job_id, delivered=True, delivered_at=_now())
    if ids:
        M.log("self", {"job_delivered": list(ids)})


def _one(meta: dict) -> str:
    head = f"— [{meta['id']}] {meta['title']} · {meta['status']}"
    if meta["status"] == "empty":
        head += f" · ВХОЛОСТУЮ: {meta.get('error') or 'работы в результате нет'}"
    if meta.get("sec"):
        head += f" · {meta['sec']} с"
    if meta.get("model"):
        head += f" · {meta['model']}"
    if meta.get("cost_usd"):
        head += f" · ${meta['cost_usd']:.2f}"
    if meta.get("result"):
        head += f"\n  результат целиком: {meta['result']} (открой Read, если нужно больше)"
    body = ""
    try:
        if meta.get("result"):
            body = (C.ROOT / meta["result"]).read_text(encoding="utf-8").strip()
    except OSError:
        body = ""
    if not body:
        body = meta.get("error") or "(пусто)"
    if len(body) > RESULT_HEAD:
        body = body[:RESULT_HEAD] + " …(обрезано, дальше в файле)"
    return head + "\n" + body


def format_block(metas: list[dict], header: str) -> str:
    if not metas:
        return ""
    parts = [header]
    for meta in metas:
        parts.append(_one(meta))
        if sum(len(p) for p in parts) > BLOCK_LIMIT:
            parts.append("…(остальные задания смотри в memory/jobs/)")
            break
    return "\n\n".join(parts)


INBOX_HEADER = (
    "[задания подагентов закончились, пока шёл разговор. Прими их: проверь, "
    "что критерии приёмки выполнены, и доложи Илье СВОЕЙ выжимкой в 1-2 строки "
    "на задание (правило kratkost). Сырой отчёт подагента не пересылается. "
    "Работа не сделана или сделана не так — заведи задание заново, а не "
    "переписывай за подагента молча.]")

REPORT_HEADER = (
    "[задание подагента закончилось, Илья молчит — доложи сам, не дожидаясь "
    "вопроса. Ответ уходит ему в ВК: 1-2 строки итога на задание, дальше "
    "спроси, скидывать ли подробности. Сырой отчёт не пересылается.]")


def take_inbox() -> str:
    """Блок для промпта хода, начатого сообщением Ильи. Помечает доставленным."""
    ready = ready_reports()
    if not ready:
        return ""
    block = format_block(ready, INBOX_HEADER)
    mark_delivered([m["id"] for m in ready])
    return block


def take_report() -> str:
    """Промпт хода-доклада в тишине. Пусто — докладывать нечего."""
    ready = ready_reports()
    if not ready:
        return ""
    block = format_block(ready, REPORT_HEADER)
    mark_delivered([m["id"] for m in ready])
    return block


def should_report(quiet: bool, idle_sec: float,
                  talk_window: int | None = None) -> bool:
    """Пора ли докладывать самому.

    Три условия сразу: есть что докладывать, сейчас не ночь и разговор уже
    кончился. Ночная тишина (правило nochnaya-tishina) — не отсрочка отчёта в
    никуда: доклад копится, и первый же такт после 08:00 его отдаст.
    Идёт разговор — не лезем отдельным сообщением: результат подложится в
    промпт следующего же хода Ильи (take_inbox).
    """
    if quiet:
        return False
    window = C.SELF_IDLE_SECONDS if talk_window is None else talk_window
    return idle_sec >= window and bool(ready_reports())


if __name__ == "__main__":
    print(json.dumps({
        "capacity": capacity(), "cpu_slots": cpu_slots(),
        "mem_available_mb": mem_available_mb(),
        "jobs": [{k: m.get(k) for k in ("id", "status", "title", "sec", "cost_usd")}
                 for m in all_jobs()],
    }, ensure_ascii=False, indent=1))
