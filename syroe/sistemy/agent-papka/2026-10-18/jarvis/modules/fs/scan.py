"""Обход файловой системы: где границы, что считается шумом, что личное.

Содержимое файлов здесь не открывается НИКОГДА. Весь модуль работает через
`os.scandir` и `stat` — имя, размер, время изменения, тип. Ни одного `open()`,
ни одного чтения байт. Это проверяется самотестом (`selftest.no_reads`): на
время сборки `builtins.open` подменяется взрывающейся заглушкой.

Три независимых списка, их нельзя путать:

* `HARD_PRUNE` — абсолютные каталоги ядра и виртуальных ФС. Отсекаются по
  ТОЧНОМУ совпадению пути, а не по префиксу: смонтированный диск живёт в
  `/run/media/...`, и префиксный отсев `/run` выкинул бы его вместе с tmpfs.
  Второй диск обходится отдельным корнем, поэтому точного совпадения хватает.
* `NOISE_DIRS` — имена каталогов-шума на любой глубине: кэши, node_modules,
  .git, окружения. Отсев по содержимому, а не по важности: Илья сказал
  «в индекс включать всё без исключений», и личные каталоги здесь не при чём.
* `SECRET_*` — не отсев, а метка. Такие пути в индекс попадают (имя и путь
  нужны, чтобы ответить «где лежит ключ»), но помечаются, и выдача поиска
  показывает их со знаком «!»: содержимое не открывать ни при каких условиях.
"""

import os
import re

# --------------------------------------------------------------------------
# Границы обхода
# --------------------------------------------------------------------------

# Файловые системы, которые несут настоящие файлы. Всё остальное (tmpfs, proc,
# sysfs, squashfs снапов, overlay докера, cgroup, fuse.gvfs) корнем не станет и
# будет отсечено как точка монтирования.
REAL_FS = frozenset((
    "ext2", "ext3", "ext4", "xfs", "btrfs", "f2fs", "reiserfs", "jfs",
    "vfat", "exfat", "msdos", "ntfs", "ntfs3", "fuseblk",
    "hfsplus", "udf", "iso9660", "zfs",
))

# Точные пути, в которые не заходим никогда.
HARD_PRUNE = frozenset((
    "/proc", "/sys", "/dev", "/run", "/tmp", "/var/tmp",
    "/snap", "/var/lib/docker", "/var/lib/containerd", "/var/lib/snapd",
    "/var/cache", "/lost+found",
))

# Каталоги-шум по имени, на любой глубине.
NOISE_DIRS = frozenset((
    ".git", ".hg", ".svn",
    "node_modules", "bower_components",
    "__pycache__", ".mypy_cache", ".pytest_cache", ".ruff_cache",
    ".tox", ".nox", ".ipynb_checkpoints", ".eggs",
    "site-packages", "dist-packages",
    ".venv", "venv", "virtualenv", "env.bak", "conda-bld", "pkgs",
    ".cache", "Cache", "Cache_Data", "CacheStorage", "Code Cache",
    "GPUCache", "GrShaderCache", "ShaderCache", "DawnCache",
    "DawnGraphiteCache", "DawnWebGPUCache", "CachedData", "CachedProfilesData",
    ".npm", ".yarn", ".gradle", ".m2", ".nuget", ".pub-cache",
    ".thumbnails", "thumbnails", ".Trash", ".Trash-1000", ".local/share/Trash",
    ".steam-shader-cache", "shadercache",
))

# Маркер виртуального окружения: каталог с этим файлом внутрь не разворачивается.
# Имени `.venv`/`venv` мало — окружения зовут как угодно (`.env312`, `tf-gpu`).
VENV_MARKER = "pyvenv.cfg"

# --------------------------------------------------------------------------
# Личное: помечаем, содержимое не трогаем
# --------------------------------------------------------------------------

SECRET_NAMES = frozenset((
    ".env", ".env.local", ".env.production", ".git-credentials", ".netrc",
    ".pgpass", "credentials", "credentials.json", "client_secret.json",
    "id_rsa", "id_dsa", "id_ecdsa", "id_ed25519", "authorized_keys",
    "known_hosts", "shadow", "wallet.dat", "Login Data", "Cookies",
    "Web Data", "key3.db", "key4.db", "logins.json", "cert9.db",
))
SECRET_EXTS = frozenset((
    "pem", "key", "p12", "pfx", "jks", "keystore", "kdbx", "ovpn", "asc", "gpg",
))
# Каталоги, всё содержимое которых считается личным (по имени компонента пути).
SECRET_DIRS = frozenset((
    ".ssh", ".gnupg", ".aws", ".kube", ".docker", ".config/gcloud",
    "keyrings", "wallets", "Wallets",
))
# Профили браузеров: узнаём по компоненту пути. Куки и сохранённые входы там —
# фактически пароли ко всем сайтам (факт pc-dostup-ustroystvo).
BROWSER_DIRS = frozenset((
    "google-chrome", "chromium", "BraveSoftware", "Microsoft Edge",
    "yandex-browser", "YandexBrowser", "opera", "vivaldi", "firefox",
    ".mozilla", "thunderbird", "Default", "tor-browser",
))
_SECRET_NAME_RE = re.compile(
    r"(^\.env($|\.)|secret|token|passw|_key$|-key$|\.key\.|apikey|api_key)", re.I)


def is_secret(path: str, name: str, ext: str) -> bool:
    """Личный ли это путь. Дешёвая проверка: зовётся на каждый файл при сборке."""
    if name in SECRET_NAMES or ext in SECRET_EXTS:
        return True
    if _SECRET_NAME_RE.search(name):
        return True
    parts = path.split("/")
    for p in parts[:-1]:
        if p in SECRET_DIRS or p in BROWSER_DIRS:
            return True
    return False


# --------------------------------------------------------------------------
# Точки монтирования
# --------------------------------------------------------------------------

def _unescape(s: str) -> str:
    return re.sub(r"\\([0-7]{3})", lambda m: chr(int(m.group(1), 8)), s)


def mounts(path: str = "/proc/self/mounts") -> list[tuple[str, str]]:
    """[(куда смонтировано, тип ФС)] — как их видит ядро."""
    out = []
    # Через os.open, а не open(): весь модуль обязан обходиться без питоновского
    # чтения файлов, и самотест это проверяет подменой builtins.open.
    try:
        fd = os.open(path, os.O_RDONLY)
        try:
            chunks = []
            while chunk := os.read(fd, 65536):
                chunks.append(chunk)
        finally:
            os.close(fd)
        raw = b"".join(chunks).decode("utf-8", "replace")
    except OSError:
        return out
    for line in raw.splitlines():
        parts = line.split()
        if len(parts) >= 3:
            out.append((_unescape(parts[1]), parts[2]))
    return out


def classify(mount_list) -> tuple[list[str], set[str]]:
    """Разложить точки монтирования на «настоящие ФС» и «отсечь».

    Отдельно от `roots_and_prune` затем, что здесь нет ни одного обращения к
    диску: правило проверяется самотестом на выдуманном списке монтирований.
    """
    real, prune = [], set(HARD_PRUNE)
    for target, fstype in mount_list:
        if fstype in REAL_FS:
            real.append(target)
        else:
            prune.add(target)
    return sorted(set(real), key=lambda t: (len(t), t)), prune


def roots_and_prune(mount_list=None) -> tuple[list[str], set[str]]:
    """Что обходим и что отсекаем.

    Корень — каждая смонтированная настоящая ФС: системный раздел и всё, что
    Илья подключил (второй диск, флешка). Одно устройство, смонтированное
    дважды (bind), берётся один раз — по номеру устройства, а не по пути.

    Отсекается: всё остальное смонтированное (tmpfs, squashfs снапов, overlay
    докера), жёсткий список `HARD_PRUNE` и сами соседние корни — чтобы диск,
    обходимый своим корнем, не попал в индекс вторым экземпляром.
    """
    real, prune = classify(mounts() if mount_list is None else mount_list)
    seen_dev, roots = {}, []
    for target in real:
        try:
            dev = os.stat(target).st_dev
        except OSError:
            continue
        if dev in seen_dev:          # bind-монтирование того же устройства
            prune.add(target)
            continue
        seen_dev[dev] = target
        roots.append(target)
    prune |= {r for r in roots}      # корень отсекается в чужом обходе, свой не трогает
    return roots, prune


# --------------------------------------------------------------------------
# Собственно обход
# --------------------------------------------------------------------------

def fold(s: str) -> str:
    """Имя в вид, по которому ищут: нижний регистр, ё сведена к е.

    `remove_diacritics` у unicode61 кириллицу не трогает — проверено: «отчет»
    не находит «Отчёт». Правится это только здесь, до записи в индекс: и FTS,
    и поиск подстрокой смотрят на один и тот же приведённый столбец `norm`.
    """
    return s.lower().replace("ё", "е")


def split_ext(name: str) -> str:
    dot = name.rfind(".")
    if dot <= 0 or dot == len(name) - 1:
        return ""
    ext = name[dot + 1:].lower()
    return ext if len(ext) <= 16 and " " not in ext else ""


def walk(root: str, known: dict[str, int] | None = None,
         prune: frozenset[str] | set[str] = frozenset(),
         errors: list | None = None):
    """Идёт по дереву вглубь, отдаёт по каталогу.

    Даёт `(путь каталога, его mtime в наносекундах, список детей | None)`.
    `None` вместо списка означает «каталог с прошлого раза не менялся»:
    `known` — это `{путь: mtime_ns}` из индекса, и для совпавших каталогов stat
    детей не делается вовсе. Именно этот stat и стоит всё время обхода.

    Наносекунды, а не секунды, намеренно: файл, созданный в ту же секунду, в
    которую собирался индекс, при секундной точности не попал бы в индекс
    никогда — до следующей правки того же каталога.

    Ребёнок — `(имя, каталог ли, размер, mtime, mtime_ns)`. Символические ссылки
    не разворачиваются: и от петель, и от повторного обхода того же дерева.
    """
    root = os.path.normpath(root)
    stack = [root]
    while stack:
        d = stack.pop()
        try:
            dmtime_ns = os.stat(d).st_mtime_ns
            with os.scandir(d) as it:
                raw = list(it)
        except OSError as e:
            if errors is not None:
                errors.append((d, e.__class__.__name__))
            continue

        venv = any(e.name == VENV_MARKER for e in raw)
        changed = known is None or known.get(d) != dmtime_ns
        entries = [] if changed else None
        for e in raw:
            try:
                isdir = e.is_dir(follow_symlinks=False)
            except OSError:
                isdir = False
            if isdir:
                p = os.path.join(d, e.name)
                if venv or e.name in NOISE_DIRS or p in prune:
                    continue         # шум: ни в индекс, ни внутрь
                stack.append(p)
            if entries is not None:
                try:
                    st = e.stat(follow_symlinks=False)
                    size = 0 if isdir else st.st_size
                    mt, mtns = int(st.st_mtime), (st.st_mtime_ns if isdir else None)
                except OSError:
                    size, mt, mtns = 0, 0, None
                entries.append((e.name, isdir, size, mt, mtns))
        yield d, dmtime_ns, entries
