"""Постоянные агенты-специалисты: чтение роли и сборка брифа.

Зачем это отдельно от modules/jobs. Задание подагенту (jobs) одноразово: бриф
написан под конкретную работу и исчезает вместе с ней. Роль — наоборот,
живёт долго: у неё своя область, свои накопленные знания, свой журнал и своя
зона записи. Роль не хранится в памяти главного агента и не едет в его промпт —
она поднимается тем же механизмом jobs, но бриф собирается из файлов роли.

Образец устройства — TrendFlowML/automation (факт trendflow-komanda-agentov):
каталог на роль, ROLE.yaml с зоной и лимитами, PROMPT.md с описанием, общение
через файлы, границы зоны держит код, а не уговоры в промпте.

Что взято, а что нет. Взято: каталог роли, ROLE.yaml, PROMPT.md, STATUS.md,
LESSONS.md, журнал, разделение режимов «спросить» и «работать». Не взято:
супервизор на 94 КБ, 16 ролей сразу, своя доска задач — очередь у нас одна
(memory/backlog.md), а исполняет её существующий диспетчер заданий.

Где что лежит:
  memory/agents/<role>/ROLE.yaml   — зона, модель, лимиты, план знаний
  memory/agents/<role>/PROMPT.md   — кто он и как работает (едет в бриф целиком)
  memory/agents/<role>/knowledge/  — то, что роль накопила (её собственность)
  memory/agents/<role>/journal/    — что делала и чем кончилось
  memory/agents/<role>/STATUS.md   — состояние роли одной страницей
  memory/agents/<role>/LESSONS.md  — уроки роли; растёт медленно, чистится руками

В memory/, а не в derived/, потому что знания роли ничем не пересоздаются
(правило v-derived-tolko-vosstanovimoe).
"""

from __future__ import annotations

import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from jarvis.core import config as C  # noqa: E402

AGENTS_DIR = C.ROOT / "memory" / "agents"

# Режимы. answer — спросили мнение: роль читает и отвечает, ничего не меняя.
# work — роль работает и пишет в свою зону. learn — набирает знания в
# knowledge/ по плану из ROLE.yaml.
MODES = ("answer", "work", "learn")


class RoleError(ValueError):
    pass


def role_dir(role: str) -> Path:
    return AGENTS_DIR / role


def exists(role: str) -> bool:
    return (role_dir(role) / "ROLE.yaml").exists()


def roles() -> list[str]:
    if not AGENTS_DIR.exists():
        return []
    return sorted(p.name for p in AGENTS_DIR.iterdir()
                  if (p / "ROLE.yaml").exists())


def load(role: str) -> dict:
    path = role_dir(role) / "ROLE.yaml"
    if not path.exists():
        raise RoleError(f"нет такой роли: {role}. Есть: {', '.join(roles()) or '—'}")
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    data["id"] = data.get("id", role)
    data["dir"] = role_dir(role)
    return data


def _read(path: Path, limit: int = 40000) -> str:
    if not path.exists():
        return ""
    text = path.read_text(encoding="utf-8")
    if len(text) > limit:
        text = text[:limit] + "\n\n[…текст обрезан при сборке брифа…]"
    return text


def knowledge_index(role: str) -> str:
    """Оглавление накопленных знаний: имя файла, первая строка, размер."""
    kdir = role_dir(role) / "knowledge"
    if not kdir.exists():
        return "— пусто, знания ещё не набраны."
    rows = []
    for p in sorted(kdir.glob("*.md")):
        if p.name == "README.md":
            continue
        head = ""
        for line in p.read_text(encoding="utf-8").splitlines():
            if line.strip() and not line.startswith("---"):
                head = line.lstrip("# ").strip()
                break
        rows.append(f"— {p.name} · {head} · {p.stat().st_size // 1024 or 1} КБ")
    return "\n".join(rows) or "— пусто, знания ещё не набраны."


def zone(role: str) -> list[str]:
    """Пути, куда роли МОЖНО писать. Относительно корня агента."""
    cfg = load(role)
    own = cfg.get("owns") or []
    return [str(p) for p in own]


def build_brief(role: str, task: str, mode: str = "answer") -> tuple[str, dict]:
    """Собрать бриф роли под конкретную задачу.

    Возвращает (текст брифа, параметры запуска). Бриф самодостаточен: роль
    видит ТОЛЬКО его и спросить Илью не может.
    """
    if mode not in MODES:
        raise RoleError(f"режим {mode}: бывают только {', '.join(MODES)}")
    cfg = load(role)
    rdir = cfg["dir"]
    rel = rdir.relative_to(C.ROOT)

    prompt = _read(rdir / "PROMPT.md")
    lessons = _read(rdir / "LESSONS.md", limit=12000)
    own = "\n".join(f"— {p}" for p in zone(role)) or "— (зоны нет: только чтение)"

    limits = cfg.get("limits") or {}
    mode_cfg = (limits.get(mode) or {})
    params = {
        "model": mode_cfg.get("model") or cfg.get("model") or "sonnet",
        "max_turns": mode_cfg.get("max_turns"),
        "timeout": mode_cfg.get("timeout"),
    }

    if mode == "answer":
        rights = (
            "Режим ОТВЕТ. Ничего не меняешь на диске вообще: ни в своей зоне,\n"
            "ни где-либо ещё. Твой продукт — текст ответа.")
    elif mode == "work":
        rights = (
            "Режим РАБОТА. Писать можно ТОЛЬКО в свою зону (список ниже).\n"
            "Любой файл вне зоны — чужая собственность: не трогать, даже если\n"
            "кажется, что там ошибка. Нашёл чужую ошибку — напиши о ней в\n"
            "результате, чинить не твоё дело.")
    else:
        rights = (
            "Режим УЧЁБА. Ищешь в интернете и складываешь найденное в\n"
            f"{rel}/knowledge/ одним файлом на тему. Ничего другого не трогаешь.\n"
            "Каждый факт — со ссылкой на источник и датой. Не уверен — так и\n"
            "пиши «не проверено», выдумывать нормы и статьи запрещено.")

    esc = cfg.get("escalate") or []
    escalate = ("\nОтдельно — это ты не решаешь сам, а приносишь мне текстом:\n"
                + "\n".join(f"— {e}" for e in esc)) if esc else ""
    done_when = (cfg.get("done_when") or
                 "Ответ применим к нашей ситуации, а не общая справка. Каждое "
                 "утверждение подкреплено источником и датой проверки. "
                 "Неизвестное названо неизвестным.").strip()

    brief = f"""{prompt}

## Твоя зона записи
{own}

## Права в этом вызове
{rights}

## Твои файлы
— {rel}/PROMPT.md — кто ты (этот текст)
— {rel}/knowledge/ — твои знания
— {rel}/journal/ — твой журнал
— {rel}/STATUS.md — твоё состояние
— {rel}/LESSONS.md — твои уроки

## Что у тебя уже есть в знаниях
{knowledge_index(role)}

## Уроки, записанные тобой раньше
{lessons or '— пока нет.'}

## Задача
{task}

## Где искать
Сначала свои знания ({rel}/knowledge/), потом файлы проекта, которые названы в
задаче, и только потом интернет. Российское право: проверять по действующей
редакции, а не по пересказу в блоге.

## Что можно самому
Читать что угодно внутри проекта. Искать в интернете. Писать в свою зону, если
режим это позволяет. Дописывать строку в свой журнал.

## Что только с моего согласия
Любая отправка наружу (письмо, сообщение, публикация). Любое действие от имени
Ильи. Правка файлов вне твоей зоны. Необратимое во внешнем мире. Тебе этого
делать нельзя вовсе — вместо действия верни в результате текст и объясни, что
нужно сделать.
{escalate}

## Формат результата
Сначала прямой ответ по существу задачи — без предисловий и без пересказа
задания. Дальше: на чём он основан (документ, статья, ссылка, дата проверки),
что осталось неизвестным, чем рискуем. Если работал в режиме работы или учёбы —
перечисли созданные и изменённые файлы путями. Пиши по-русски.

## Критерии приёмки
{done_when}
Файлы вне зоны не тронуты. Ничего наружу не отправлено.
"""
    return brief, params
