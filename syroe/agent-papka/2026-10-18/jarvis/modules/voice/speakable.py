"""Подготовка текста к синтезу: что можно слушать, и что дослать текстом.

Задача не «почистить разметку», а разделить ответ на два потока:
речь — только мысли; хвост — пути, ссылки, код, id, таблицы, то есть всё,
что на слух превращается в кашу и всё равно нужно глазами.

Проверено фонемизацией Piper (espeak-ng ru), а не на слух:

    '**Готово:**'        -> «звёздочка звёздочка готово звёздочка звёздочка»
    '/home/ilya/Agent'   -> «косая черта хоум косая черта илья косая черта…»
    '12 805 ₽'           -> «двенадцать восемьсот пять» (₽ проглочен молча)
    '≈12,9k'             -> «approximately двенадцать и девять десятых кей»
    '27.08'              -> «двадцать семь точка ноль восемь»

Отсюда правила ниже. Часть работы espeak делает сам и мешать ему не надо:
числа без разделителей он разворачивает верно («12805» → «двенадцать тысяч
восемьсот пять»), кавычки и скобки не произносит, латинские бренды читает
по-английски («Notion» → ноушен, «TrendFlow» → трэнд флоу).

    from jarvis.modules.voice import speakable
    spoken, tail = speakable.prepare(text)
"""
import json
import re
import unicodedata
from pathlib import Path

LEXICON = Path(__file__).with_name("lexicon.json")

# Порядковые в родительном падеже: «27.08» читается как дата, а не как число.
# espeak сам этого не умеет — «27 августа» он произносит «двадцать семь августа».
_ORD = {
    1: "первого", 2: "второго", 3: "третьего", 4: "четвёртого", 5: "пятого",
    6: "шестого", 7: "седьмого", 8: "восьмого", 9: "девятого", 10: "десятого",
    11: "одиннадцатого", 12: "двенадцатого", 13: "тринадцатого",
    14: "четырнадцатого", 15: "пятнадцатого", 16: "шестнадцатого",
    17: "семнадцатого", 18: "восемнадцатого", 19: "девятнадцатого",
    20: "двадцатого", 21: "двадцать первого", 22: "двадцать второго",
    23: "двадцать третьего", 24: "двадцать четвёртого", 25: "двадцать пятого",
    26: "двадцать шестого", 27: "двадцать седьмого", 28: "двадцать восьмого",
    29: "двадцать девятого", 30: "тридцатого", 31: "тридцать первого",
}
_MONTH = {
    1: "января", 2: "февраля", 3: "марта", 4: "апреля", 5: "мая", 6: "июня",
    7: "июля", 8: "августа", 9: "сентября", 10: "октября", 11: "ноября",
    12: "декабря",
}

# Сокращения со слэшем разворачиваются ПЕРВЫМИ: иначе «₽/ч» успевает стать
# «рублей/ч» и слэш доживает до espeak, который читает его «косая черта».
_SHORTHAND = {
    "₽/ч": " рублей в час", "руб/ч": " рублей в час", "р/ч": " рублей в час",
    "ч/день": " часов в день", "ч/д": " часов в день",
    "км/ч": " километров в час", "₽/мес": " рублей в месяц",
    "/мес": " в месяц", "/день": " в день", "/сут": " в сутки",
    "и/или": " или ", "т.д.": " так далее", "т.п.": " тому подобное",
    "т.е.": " то есть", "др.": " другое",
}

# Знаки, которые espeak либо проговаривает вслух, либо глотает молча —
# и то и другое плохо. Заменяются словами до фонемизации.
_SYMBOLS = {
    "₽": " рублей", "руб.": " рублей", "$": " долларов", "€": " евро",
    "≈": " примерно ", "≤": " не больше ", "≥": " не меньше ",
    "<=": " не больше ", ">=": " не меньше ",
    "→": " ведёт к ", "->": " ведёт к ", "…": ".",
    "№": " номер ", "×": " на ", "·": ", ", "•": ", ",
    "&": " и ", "+": " плюс ", "±": " плюс-минус ", "%": " процентов",
    " ": " ", " ": " ", " ": " ",
}

# Служебные символы генерации, которыми Notion оборачивает filecite-мусор.
_INVISIBLE = re.compile(r"[-​-‏﻿]")

_FENCE = re.compile(r"```.*?```", re.S)
_TABLE_ROW = re.compile(r"^\s*\|.*\|\s*$", re.M)
_MD_LINK = re.compile(r"\[([^\]\n]+)\]\(([^)\s]+)\)")
_URL = re.compile(r"(?:https?://|www\.)\S+")
_MAIL = re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.]+\b")
# Путь — со слэшем внутри и не начало URL. Русские буквы важны:
# «/home/ilya/Рабочий стол/Agent» — реальный путь на этой машине.
_SEG = r"[\w.~\-Ѐ-ӿ]+"
# Пробел внутри пути допускается только тогда, когда за словом снова идёт
# слэш: «/home/ilya/Рабочий стол/Agent» — реальный путь на этой машине,
# а «стол/Agent» без этого условия обрубается на пробеле и остаток
# уезжает в речь.
_PATH = re.compile(
    rf"(?<![\w:])(?:~|\.{{1,2}})?(?:/{_SEG}(?: {_SEG})*(?=/))*/{_SEG}")
_EXT = (r"py|md|json|jsonl|txt|sh|yml|yaml|toml|csv|log|ogg|onnx|env|html|"
        r"js|ts|cfg|ini")
_ROOTS = r"modules|core|memory|derived|jarvis|docs|tests|facts|procedures|rules"
# Относительный путь: core/night.py, derived/notion/raw.json, memory/log/.
# Требование расширения или известного корня — намеренное: без него под
# «слово/слово» попадали живые сокращения вроде «ч/день» и «₽/ч».
_RELPATH = re.compile(
    rf"(?<![\w/])(?:(?:{_SEG}/)+{_SEG}\.(?:{_EXT})\b"
    rf"|(?:{_ROOTS})/(?:{_SEG}/)*{_SEG}?)")
_WINPATH = re.compile(r"\b[A-Za-z]:\\[^\s]+")
_FILE = re.compile(r"\b[\w.\-]+\.(?:py|md|json|jsonl|txt|sh|yml|yaml|toml|"
                   r"csv|log|ogg|onnx|env|html|js|ts|cfg|ini)\b")
_ID = re.compile(r"\b(?:ob|bl|rl|pr|fc)-\d{2,4}\b", re.I)
_ID_WORDS = {"ob": "обязательство", "bl": "задача", "rl": "правило",
             "pr": "предложение", "fc": "факт"}
# Числительное перед единицей: «2 часов в день» режет слух сильнее, чем
# звёздочки, — из-за него голосовое звучит как машинный перевод.
_PLURAL = {
    "час": ("час", "часа", "часов"),
    "рубль": ("рубль", "рубля", "рублей"),
    "минута": ("минуту", "минуты", "минут"),
    "тысяча": ("тысяча", "тысячи", "тысяч"),
    "процент": ("процент", "процента", "процентов"),
    "письмо": ("письмо", "письма", "писем"),
    "день": ("день", "дня", "дней"),
}
_PLURAL_RX = re.compile(
    r"(\d+)\s+(" + "|".join(sorted(
        {f for key, forms in _PLURAL.items() for f in (key, *forms)},
        key=len, reverse=True)) + r")\b")
_HASH = re.compile(r"\b(?=[0-9a-f]{7,40}\b)(?=[a-f0-9]*\d)[0-9a-f]{7,40}\b")
_INLINE_CODE = re.compile(r"`([^`\n]+)`")

_HEADING = re.compile(r"^\s{0,3}#{1,6}\s*", re.M)
_BULLET = re.compile(r"^\s*(?:[-*+·•‣▪]|\d{1,2}[.)])\s+", re.M)
_QUOTE = re.compile(r"^\s*>+\s*", re.M)
_RULE = re.compile(r"^\s*(?:-{3,}|={3,}|\*{3,}|_{3,})\s*$", re.M)
_BOLD = re.compile(r"(\*\*|__|~~)(.+?)\1", re.S)
_ITALIC = re.compile(r"(?<![\w*])\*([^*\n]+)\*(?![\w*])")
_STRAY = re.compile(r"[*_`#|\\^~<>]")

_THOUSANDS = re.compile(r"(?<=\d)[  ](?=\d{3}\b)")
_DECIMAL = re.compile(r"(?<=\d)\.(?=\d)")
_KILO = re.compile(r"(?<=\d)\s?[kк]\b")
_DATE_FULL = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")
_DATE_DOT = re.compile(r"\b(\d{1,2})\.(\d{2})(?:\.(\d{4}))?\b")
_RANGE = re.compile(r"(?<=\d)\s?[–—-]\s?(?=\d)")
_MINUS = re.compile(r"(?<![\w\d])-(?=\d)")

_EMOJI_CATS = {"So", "Sk", "Cs", "Co", "Cf"}

# Латиница внутри русского текста фонемизируется английскими правилами:
# «Notion» -> nˈəʊʃən. Идентификаторы этих фонем в русской модели есть, но
# обучена она не на них, поэтому длинное английское слово звучит смазанно.
# Частые слова разбираются лексиконом, остаток — этой таблицей.
_TRANSLIT = [
    ("sch", "ш"), ("tch", "ч"), ("ch", "ч"), ("sh", "ш"), ("th", "т"),
    ("ph", "ф"), ("ck", "к"), ("ee", "и"), ("oo", "у"), ("ou", "ау"),
    ("ea", "и"), ("ai", "эй"), ("ay", "эй"), ("ow", "оу"), ("yu", "ю"),
    ("ya", "я"), ("kh", "х"), ("ts", "ц"), ("qu", "кв"), ("x", "кс"),
    ("a", "а"), ("b", "б"), ("c", "к"), ("d", "д"), ("e", "е"), ("f", "ф"),
    ("g", "г"), ("h", "х"), ("i", "и"), ("j", "дж"), ("k", "к"), ("l", "л"),
    ("m", "м"), ("n", "н"), ("o", "о"), ("p", "п"), ("q", "к"), ("r", "р"),
    ("s", "с"), ("t", "т"), ("u", "у"), ("v", "в"), ("w", "в"), ("y", "й"),
    ("z", "з"),
]
# Заглавные подряд — это аббревиатура, её читают по буквам, а не словом.
_ABBR_LETTER = {
    "a": "эй", "b": "би", "c": "си", "d": "ди", "e": "и", "f": "эф",
    "g": "джи", "h": "эйч", "i": "ай", "j": "джей", "k": "кей", "l": "эль",
    "m": "эм", "n": "эн", "o": "оу", "p": "пи", "q": "кью", "r": "ар",
    "s": "эс", "t": "ти", "u": "ю", "v": "ви", "w": "дабл-ю", "x": "икс",
    "y": "уай", "z": "зет",
}
_LATIN = re.compile(r"[A-Za-z][A-Za-z'-]*")


def _load_lexicon() -> dict:
    """Произношение того, что espeak читает неверно. Данные, а не код:
    словарь пополняется по одной строке, когда в голосовом вылезает уродство."""
    try:
        return json.loads(LEXICON.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _drop_emoji(text: str) -> str:
    return "".join(ch for ch in text
                   if unicodedata.category(ch) not in _EMOJI_CATS)


def _date(m: re.Match) -> str:
    d, mo = int(m.group(1)), int(m.group(2))
    if not (1 <= d <= 31 and 1 <= mo <= 12):
        return m.group(0)
    out = f"{_ORD[d]} {_MONTH[mo]}"
    if m.lastindex and m.group(m.lastindex) and len(m.group(m.lastindex)) == 4:
        out += f" {m.group(m.lastindex)} года"
    return out


def _iso_date(m: re.Match) -> str:
    y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
    if not (1 <= d <= 31 and 1 <= mo <= 12):
        return m.group(0)
    return f"{_ORD[d]} {_MONTH[mo]} {y} года"


class _Tail:
    """Копилка непроизносимого. Порядок сохраняется, повторы схлопываются:
    один и тот же путь в ответе встречается по три раза, а читать его глазами
    нужно один."""

    def __init__(self) -> None:
        self.items: list[tuple[str, str]] = []

    def add(self, kind: str, value: str, spoken: str | None = None) -> str:
        """Кладёт значение в хвост и возвращает слово-заглушку для речи.

        Заглушка всегда кириллицей: «id» espeak прочёл бы по-английски
        «айди», и в речь через заднюю дверь вернулась бы та же каша.
        """
        value = value.strip().rstrip(".,;:")
        if not value:
            return ""
        if (kind, value) not in self.items:
            self.items.append((kind, value))
        return kind if spoken is None else spoken

    def render(self) -> str:
        if not self.items:
            return ""
        rows = [f"{kind}: {value}" for kind, value in self.items]
        return "\n".join(rows)


def _extract(text: str, tail: _Tail) -> str:
    """Вынуть из речи всё, что слушать бессмысленно, оставив слово-заглушку.

    Заглушка нужна, чтобы фраза не разваливалась: «правка в файл» звучит
    нормально, «правка в» — нет. Сами значения уходят в хвост текстом.
    """
    text = _FENCE.sub(lambda m: tail.add("код", m.group(0).strip("`").strip()), text)
    table = [m.group(0).strip() for m in _TABLE_ROW.finditer(text)]
    if table:
        tail.add("таблица", "\n".join(table))
        text = _TABLE_ROW.sub("", text)
    # У markdown-ссылки уже есть человеческий текст — слово «ссылка» после
    # него только мешает; адрес всё равно уходит в хвост.
    def _link(m: re.Match) -> str:
        tail.add("ссылка", m.group(2))
        return m.group(1)

    text = _MD_LINK.sub(_link, text)
    text = _URL.sub(lambda m: tail.add("ссылка", m.group(0)), text)
    text = _MAIL.sub(lambda m: tail.add("почта", m.group(0), "почту"), text)
    text = _WINPATH.sub(lambda m: tail.add("путь", m.group(0)), text)
    text = _PATH.sub(lambda m: tail.add("путь", m.group(0)), text)
    # «core/night.py» на слух — это файл, а «memory/log/» — папка. Заглушка
    # должна совпадать с тем, о чём идёт речь, иначе фраза звучит чужой.
    text = _RELPATH.sub(
        lambda m: tail.add("путь", m.group(0),
                           "файл" if re.search(rf"\.(?:{_EXT})$", m.group(0))
                           else "путь"), text)
    text = _FILE.sub(lambda m: tail.add("файл", m.group(0)), text)
    def _ident(m: re.Match) -> str:
        kind, num = m.group(0).split("-")
        tail.add("id", m.group(0))
        # На слух «ob-013» — это «об ноль тринадцать»; человеку нужно слово.
        return f"{_ID_WORDS.get(kind.lower(), 'номер')} {int(num)}"

    text = _ID.sub(_ident, text)
    text = _HASH.sub(lambda m: tail.add("коммит", m.group(0)), text)
    # Инлайн-код: короткое слово-команду произнести можно, длинную строку —
    # нет. Порог по длине, а не по содержимому: угадывать «это команда или
    # просто слово» дороже, чем ошибиться в пользу текста.
    text = _INLINE_CODE.sub(
        lambda m: m.group(1) if len(m.group(1)) <= 24 and " " not in m.group(1)
        else tail.add("команда", m.group(1)), text)
    return text


def _numbers(text: str) -> str:
    text = _THOUSANDS.sub("", text)          # 12 805 -> 12805, иначе «двенадцать восемьсот»
    text = _KILO.sub(" тысяч", text)         # 36k -> 36 тысяч, иначе «кей»
    text = _MINUS.sub("минус ", text)
    text = _DECIMAL.sub(",", text)           # 2.5 -> 2,5: «два и пять десятых»
    text = _RANGE.sub(" до ", text)          # 10-15 -> «десять до пятнадцати»
    return text


def _translit(text: str) -> str:
    """Остаточную латиницу — в кириллицу. Лексикон уже разобрал частые слова,
    сюда доходит редкое: имена людей, названия сервисов, куски команд."""

    def one(m: re.Match) -> str:
        w = m.group(0)
        if len(w) <= 4 and w.isupper():
            return "-".join(_ABBR_LETTER.get(c.lower(), c) for c in w if c.isalpha())
        s = w.lower()
        for src, dst in _TRANSLIT:
            s = s.replace(src, dst)
        return s

    return _LATIN.sub(one, text)


def _agree(text: str) -> str:
    """Согласовать единицу с числом: «2 часов» -> «2 часа»."""
    forms = {f: key for key, group in _PLURAL.items()
             for f in (key, *group)}

    def one(m: re.Match) -> str:
        n, word = int(m.group(1)), m.group(2)
        one_, few, many = _PLURAL[forms[word]]
        tail_, hundred = n % 10, n % 100
        if 11 <= hundred <= 14:
            form = many
        elif tail_ == 1:
            form = one_
        elif 2 <= tail_ <= 4:
            form = few
        else:
            form = many
        return f"{n} {form}"

    return _PLURAL_RX.sub(one, text)


def _apply_lexicon(text: str, lex: dict) -> str:
    for word, how in lex.items():
        text = re.sub(rf"(?<!\w){re.escape(word)}(?!\w)", how, text)
    return text


def prepare(text: str, lexicon: dict | None = None) -> tuple[str, str]:
    """Текст ответа -> (что озвучить, что дослать текстом).

    Второй элемент пуст, когда досылать нечего. Речь всегда заканчивается
    точкой: без неё Piper комкает последнее слово.
    """
    lex = _load_lexicon() if lexicon is None else lexicon
    tail = _Tail()

    t = _INVISIBLE.sub("", text.replace("\r\n", "\n"))
    # Сокращения со слэшем разворачиваются ДО поиска путей: иначе «800 ₽/ч»
    # отдаёт «/ч» в хвост как путь, а в речи остаётся «восемьсот рублейпуть».
    for src, dst in _SHORTHAND.items():
        t = t.replace(src, dst)
    t = _extract(t, tail)

    t = _RULE.sub("", t)
    t = _HEADING.sub("", t)
    t = _QUOTE.sub("", t)
    # Пункт списка — это предложение: точка на конце даёт паузу, без неё
    # пункты слипаются в одну длинную фразу.
    t = _BULLET.sub("", t)
    t = "\n".join(_close(line) for line in t.split("\n"))

    t = _BOLD.sub(r"\2", t)
    t = _ITALIC.sub(r"\1", t)
    t = _drop_emoji(t)

    # Порядок ниже не косметика, а условие правильности.
    # Даты — раньше чисел, иначе «27.08» уже склеится в дробь.
    # Транслитерация — последней: до неё лексикон должен успеть разобрать
    # знакомые слова, а числовые правила — отработать по латинским единицам.
    for src, dst in _SYMBOLS.items():
        t = t.replace(src, dst)
    t = _DATE_FULL.sub(_iso_date, t)
    t = _DATE_DOT.sub(_date, t)
    t = _numbers(t)
    t = _agree(t)
    t = _apply_lexicon(t, lex)
    t = _translit(t)

    # «ссылку ссылка», «в файле файл» — заглушка попала туда, где нужное слово
    # уже сказано. Убираем повтор по корню, а не по точному совпадению.
    t = re.sub(r"\b(ссылк|путь|пути|файл|почт|командой|команду)(\w*)\s+"
               r"(ссылка|путь|файл|почту|команда)\b",
               lambda m: m.group(1) + m.group(2), t, flags=re.I)

    t = _STRAY.sub(" ", t)
    t = re.sub(r"\s*/\s*", ", ", t)   # остаточный слэш — «косая черта» на слух
    t = re.sub(r"[ \t]+", " ", t)
    t = re.sub(r" ([,.;:!?])", r"\1", t)
    t = re.sub(r"(?m)^[ \t]+|[ \t]+$", "", t)
    t = re.sub(r"(?m)^[\s,.;:—–-]*$\n?", "", t)
    t = re.sub(r"\n{2,}", "\n", t).strip()
    if t and t[-1] not in ".!?":
        t += "."
    return t, tail.render()


def _close(line: str) -> str:
    """Точка в конце строки-пункта. Строки без букв не трогаем."""
    s = line.rstrip()
    if not s or not re.search(r"[\wЀ-ӿ]", s):
        return s
    return s if s[-1] in ".!?:;," else s + "."


if __name__ == "__main__":
    import sys
    src = sys.argv[1] if len(sys.argv) > 1 else sys.stdin.read()
    spoken, tail = prepare(src)
    print("=== ГОЛОСОМ ===")
    print(spoken)
    if tail:
        print("=== ТЕКСТОМ ===")
        print(tail)
