"""Ветки и коммиты команды агентов (раздел 14 документа «Джарвис — команда и
проекты», job-090, 30.08.2026).

Правило из документа, разложенное в код:
  · исполнитель коммитит часто, только в свою ветку `agent/<id задачи>-
    <коротко>`; в основную ветку исполнитель не коммитит никогда;
  · тимлид сливает ветку после приёмки — это и есть коммит, который
    считается результатом; сообщение слияния обязано содержать id задачи,
    иначе связь артефакта с задачей держится памятью, а не машиной;
  · слияние делает только роль тимлида — тем же приёмом, что accept() в
    tasks.py: роль передаётся явно строкой (actor=...), модуль не знает и не
    доверяет тому, кто его позвал, разные роли — разные ветки одной функции.

Изоляция без переключения общего рабочего дерева. Раздел 8 конституции и
брифы, ставящие эту работу, отдельно запрещают переключать ветку в РАБОЧЕМ
РЕПОЗИТОРИИ АГЕНТА без согласия Ильи — общее дерево читают демон и другие
процессы, дёрнуть под ними чужой checkout нельзя (тот же класс риска, что и
git stash, см. правило git-stash-zapreshchen). Поэтому ветка задачи заводится
через `git worktree add`: отдельный каталог со своим HEAD, соседний с
репозиторием, а не смена ветки в основном дереве. Исполнитель работает и
коммитит внутри этого каталога. Слияние тимлидом — обычный `git merge` в
ветке, которая уже открыта(current) в основном дереве (сама merge-операция
и есть предписанное действие раздела 14, не обход правила).

Все функции принимают путь к репозиторию параметром — модуль ничего не знает
о том, где лежит репозиторий агента, и не открывает его сам. Проверки этого
модуля (branches_selftest.py) все до одной идут на одноразовых репозиториях
в derived/scratch/ (правило odnorazovye-proverki-v-derived); ни create_task_
branch, ни merge_task_branch НИГДЕ в этой работе не вызваны с путём на
рабочий репозиторий агента.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

from .tasks import PermissionRole, ProjectsError

BRANCH_PREFIX = "agent/"

# Транслитерация для короткого куска имени ветки: задачи и заголовки — по-
# русски, а git-ветка должна быть безопасной ASCII-строкой.
_TRANSLIT = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "e",
    "ж": "zh", "з": "z", "и": "i", "й": "y", "к": "k", "л": "l", "м": "m",
    "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t", "у": "u",
    "ф": "f", "х": "h", "ц": "c", "ч": "ch", "ш": "sh", "щ": "sch", "ъ": "",
    "ы": "y", "ь": "", "э": "e", "ю": "yu", "я": "ya",
}


class GitError(ProjectsError):
    """Git-команда упала. Текст — стандартный вывод git, без перевода."""


class BranchMessageMissingTaskId(ProjectsError):
    def __init__(self, task_id: str, message: str):
        self.task_id = task_id
        self.message = message
        super().__init__(
            f"сообщение слияния не содержит id задачи «{task_id}»: "
            f"«{(message or '')[:120]}». Раздел 14: связь артефакта с "
            "задачей должна читаться машиной, а не по памяти — впиши id в "
            "текст сообщения слияния.")


def slugify(text: str, limit: int = 30) -> str:
    """Русский заголовок → безопасный кусок имени ветки: транслит, нижний
    регистр, всё не-ASCII-буквенно-цифровое — дефис, дефисы не дублируются."""
    text = (text or "").strip().lower()
    text = "".join(_TRANSLIT.get(ch, ch) for ch in text)
    text = re.sub(r"[^a-z0-9]+", "-", text).strip("-")
    text = re.sub(r"-{2,}", "-", text)
    return text[:limit].strip("-") or "task"


def branch_name(task_id: str, short: str) -> str:
    return f"{BRANCH_PREFIX}{task_id}-{slugify(short)}"


def _git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    res = subprocess.run(["git", "-C", str(repo), *args],
                         capture_output=True, text=True, timeout=30)
    if check and res.returncode != 0:
        raise GitError(
            f"git {' '.join(args)} упал: "
            f"{(res.stderr or res.stdout).strip()[:500]}")
    return res


def current_branch(repo: Path) -> str:
    return _git(repo, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip()


def list_task_branches(repo: Path) -> list[str]:
    """Ветки задач по именам, без пометок git.

    Читается `for-each-ref`, а не `branch --list`: у последнего перед именем
    стоит пометка текущей ветки (`*`) ИЛИ занятости другим рабочим деревом
    (`+`), и старый разбор `lstrip("* ")` плюс не снимал. Ветку задачи всегда
    держит worktree, то есть она всегда с плюсом — и список молча возвращал
    имена вида «+ agent/t-777-…». Из-за этого `merge_task_branch()` не находил
    собственную только что заведённую ветку («нет такой ветки задачи»), и
    раздел 14 не работал вовсе (поймано живой обкаткой 30.08, ob-074).
    """
    res = _git(repo, "for-each-ref", "--format=%(refname:short)",
               f"refs/heads/{BRANCH_PREFIX}*")
    return [ln.strip() for ln in res.stdout.splitlines() if ln.strip()]


def is_merged(repo: Path, branch: str, base: str | None = None) -> bool:
    """Влита ли ветка в базовую. Имена читаются `for-each-ref` по той же
    причине, что и в list_task_branches: `branch --merged` печатает пометки
    `*` и `+` перед именем, и ветка, занятая рабочим деревом, не узнавалась."""
    base = base or current_branch(repo)
    res = _git(repo, "for-each-ref", "--format=%(refname:short)",
               "--merged", base, "refs/heads/")
    names = {ln.strip() for ln in res.stdout.splitlines() if ln.strip()}
    return branch in names


def ensure_not_base(repo: Path, base: str) -> None:
    """Исполнитель не коммитит в основную ветку — проверка кодом, а не
    доверием. Полезно и для ручных/нестандартных потоков, хотя обычный путь
    через create_task_branch (worktree) на основной ветке и так не окажется:
    у отдельного каталога свой HEAD."""
    cur = current_branch(repo)
    if cur == base:
        raise ProjectsError(
            f"нельзя коммитить прямо в основную ветку «{base}» — заведи "
            "ветку задачи через create_task_branch()")


def create_task_branch(repo: Path | str, task_id: str, short: str, *,
                       base: str | None = None,
                       worktree_root: Path | str | None = None) -> dict:
    """Завести ветку `agent/<id>-<коротко>` отдельным `git worktree` —
    общий HEAD репозитория не трогается. Возвращает {branch, path, base}.

    worktree_root по умолчанию — каталог `worktrees` ВНУТРИ репозитория.
    Снаружи он лежать не может: с 30.08 шлюз не пускает запись за пределы
    папки агента ни одним путём (пункт 3 приёмки этапа 0), и работа в ветке
    была бы возможна только через ту самую дыру, которую пункт 2 закрыл.
    От `git status` каталог скрыт строкой в `.gitignore`, а не тем, что он
    лежит в стороне.
    """
    repo = Path(repo)
    if not (repo / ".git").exists():
        raise GitError(f"не репозиторий git: {repo}")
    base = base or current_branch(repo)
    name = branch_name(task_id, short)
    root = Path(worktree_root) if worktree_root else (repo / "worktrees")
    root.mkdir(parents=True, exist_ok=True)
    wt_path = root / name.replace("/", "__")
    if wt_path.exists():
        raise GitError(f"каталог worktree уже существует: {wt_path}")
    if name in list_task_branches(repo):
        raise GitError(f"ветка уже существует: {name}")
    _git(repo, "worktree", "add", str(wt_path), "-b", name, base)
    return {"branch": name, "path": str(wt_path), "base": base}


def _worktree_path_for_branch(repo: Path, branch: str) -> str | None:
    res = _git(repo, "worktree", "list", "--porcelain", check=False)
    path = None
    for block in res.stdout.split("\n\n"):
        lines = block.splitlines()
        p = next((ln[len("worktree "):] for ln in lines
                  if ln.startswith("worktree ")), None)
        b = next((ln[len("branch "):] for ln in lines
                  if ln.startswith("branch ")), None)
        if b == f"refs/heads/{branch}":
            path = p
            break
    return path


def remove_task_worktree(repo: Path | str, branch: str) -> bool:
    """Убрать рабочий каталог ветки (после слияния или отказа от задачи).
    Саму ветку (ref) не трогает — невлитая ветка обязана остаться видимой
    (раздел 14: «проваленная задача остаётся видна как невлитая ветка»)."""
    repo = Path(repo)
    path = _worktree_path_for_branch(repo, branch)
    if not path:
        return False
    _git(repo, "worktree", "remove", "--force", path, check=False)
    return True


def merge_task_branch(repo: Path | str, task_id: str, branch: str, *,
                      message: str, actor: str = "executor",
                      remove_worktree: bool = True) -> dict:
    """Слить ветку задачи в текущую (основную) ветку репозитория.

    Только тимлид: `actor` проверяется кодом, как `accept()` в tasks.py —
    вызов не из-под роли teamlead падает PermissionRole независимо от того,
    кто на самом деле запустил процесс. Сообщение обязано содержать
    task_id — иначе BranchMessageMissingTaskId, до самого git merge.
    """
    if actor != "teamlead":
        raise PermissionRole("сливает ветку только тимлид")
    if task_id not in (message or ""):
        raise BranchMessageMissingTaskId(task_id, message)
    repo = Path(repo)
    if branch not in list_task_branches(repo):
        raise GitError(f"нет такой ветки задачи: {branch}")
    res = _git(repo, "merge", "--no-ff", branch, "-m", message, check=False)
    if res.returncode != 0:
        raise GitError(
            f"слияние {branch} упало: {(res.stderr or res.stdout).strip()[:500]}")
    sha = _git(repo, "rev-parse", "HEAD").stdout.strip()
    if remove_worktree:
        remove_task_worktree(repo, branch)
    return {"branch": branch, "commit": sha, "task_id": task_id}
