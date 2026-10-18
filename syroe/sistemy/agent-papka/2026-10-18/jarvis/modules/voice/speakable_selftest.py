"""Проверка подготовки текста к синтезу — фонемами, а не на глаз.

Смотреть глазами на результат prepare() бесполезно: «звёздочка» появляется
не в тексте, а в фонемах, которые espeak-ng выдаёт Piper. Поэтому тест
прогоняет готовую речь через тот же фонемизатор и ищет в ней запрещённые
последовательности — имена символов и английские вставки.

    ./.venv/bin/python -m jarvis.modules.voice.speakable_selftest

Голос нужен только для фонемной части: без него текстовые проверки всё равно
проходят, а фонемные помечаются пропущенными.
"""
import sys

from . import speakable as S

# Фонемные записи того, что слышно в голосовом как мусор. Получены прогоном
# espeak-ng ru: '*' -> «звёздочка», '/' -> «косая черта», '≈' -> английское
# «approximately», ':' в 23.08 -> «точка».
FORBIDDEN = {
    "zvʲˈɵzdʌtʃʲka": "звёздочка (осталась разметка **)",
    "kʌsˈɑja tʃʲirtˈɑ": "косая черта (остался путь или слэш)",
    "ɐpɹˈɒksɪmətli": "approximately (не заменён знак ≈)",
    "tʲˈiɭda": "тильда (остался ~~ или ~)",
    "krˈyʃka": "крышка (остался ^)",
    "bɛksɭˈɛʃ": "бэкслэш (остался \\)",
    "nˈomʲir znˈɑk": "знак номера",
}
# Проверено фонемизацией: '#', '|', '_', '-', '>' espeak-ru не произносит
# вообще — маркеров для них не существует, и ловятся они только текстовой
# проверкой ниже. Держать бесполезный маркер вреднее, чем не иметь его:
# зелёный тест начинает означать не то, что кажется.

CASES = [
    # (вход, что обязано быть в речи, чего в речи быть не должно, что в хвост)
    ("**Готово:** 5 писем", ["Готово", "5 писем"], ["*"], []),
    ("файл `core/night.py` правлен", ["файл"], ["night.py", "/"], ["night.py"]),
    ("путь /home/ilya/Рабочий стол/Agent",
     ["путь"], ["home", "Рабочий стол"], ["/home/ilya/Рабочий стол/Agent"]),
    ("смотри https://vk.com/id1", ["ссылка"], ["http"], ["https://vk.com/id1"]),
    ("пиши на orient_dv@mail.ru", ["почту"], ["@"], ["orient_dv@mail.ru"]),
    ("проверка по ob-013", ["обязательство 13"], ["ob-013"], ["ob-013"]),
    ("коммит cc889da готов", ["коммит"], ["cc889da"], ["cc889da"]),
    ("осталось 18 805 ₽", ["18805 рублей"], ["₽", "18 805"], []),
    ("не больше ≤36k", ["36 тысяч"], ["≈", "36k", "k"], []),
    ("старт 27.08.2026",
     ["двадцать седьмого августа 2026 года"], ["27.08"], []),
    ("дедлайн 2026-08-27", ["двадцать седьмого августа"], ["2026-08-27"], []),
    ("сверка 24.08 утром", ["двадцать четвёртого августа"], ["24.08"], []),
    ("работаем 2 ч/день", ["2 часа в день"], ["/"], []),
    ("ставка 800 ₽/ч", ["800 рублей в час"], ["/", "₽"], []),
    ("ждём 21 письмо и 5 минута", ["21 письмо", "5 минут"], [], []),
    ("- первый\n- второй", ["первый.", "второй."], ["-"], []),
    ("## Заголовок\nтекст", ["Заголовок."], ["#"], []),
    ("| a | b |\n|---|---|", [], ["|"], ["| a | b |"]),
    ("```\nprint(1)\n```", [], ["print"], ["print(1)"]),
    ("[документация](https://x.dev/a)", ["документация"], ["https"],
     ["https://x.dev/a"]),
    ("🏠 квартира ✅", ["квартира"], ["🏠", "✅"], []),
    ("это 90% / 10%", ["90 процентов", "10 процентов"], ["%", "/"], []),
    ("нужен Notion и ДВФУ", ["ноушен", "дэ-вэ-эф-у"], ["Notion", "ДВФУ"], []),
    ("зашёл в GitHub", [], ["GitHub"], []),
    ("текст без ничего", ["текст без ничего."], [], []),
]


def check_text() -> list[str]:
    bad = []
    for src, must, must_not, tail_has in CASES:
        spoken, tail = S.prepare(src)
        for m in must:
            if m not in spoken:
                bad.append(f"{src!r}: в речи нет {m!r} — {spoken!r}")
        for m in must_not:
            if m in spoken:
                bad.append(f"{src!r}: в речи осталось {m!r} — {spoken!r}")
        for m in tail_has:
            if m not in tail:
                bad.append(f"{src!r}: в хвосте нет {m!r} — {tail!r}")
        if any(ch.isascii() and ch.isalpha() for ch in spoken):
            bad.append(f"{src!r}: латиница дожила до речи — {spoken!r}")
    return bad


def check_phonemes() -> tuple[list[str], int]:
    """Прогнать речь через настоящий фонемизатор Piper и поискать мусор."""
    try:
        from . import say
        path = say.model_path(say.DEFAULT_VOICE)
        if not path.exists():
            return ["голос не скачан — фонемная часть пропущена"], 0
        voice = say.PiperVoice.load(str(path))
    except Exception as e:  # голос не обязателен для текстовых проверок
        return [f"голос не загрузился ({type(e).__name__}: {e}) — пропуск"], 0

    bad, checked = [], 0
    for src, *_ in CASES:
        spoken, _ = S.prepare(src)
        if not spoken:
            continue
        phon = " ".join("".join(s) for s in voice.phonemize(spoken))
        checked += 1
        for mark, why in FORBIDDEN.items():
            if mark in phon:
                bad.append(f"{src!r}: в фонемах {why} — {phon[:90]!r}")
        unknown = {p for s in voice.phonemize(spoken) for p in s
                   if p not in voice.config.phoneme_id_map}
        if unknown:
            bad.append(f"{src!r}: фонемы вне модели {sorted(unknown)}")
    return bad, checked


def main() -> int:
    text_bad = check_text()
    phon_bad, checked = check_phonemes()
    for line in text_bad + phon_bad:
        print("  ✗", line)
    ok = len(CASES) - len({b.split(':')[0] for b in text_bad})
    print(f"текстовых случаев: {ok}/{len(CASES)} чисто, "
          f"фонемных проверок: {checked}")
    hard = text_bad + [b for b in phon_bad if "пропуск" not in b]
    print("зелёный" if not hard else f"КРАСНЫЙ: {len(hard)} замечаний")
    return 1 if hard else 0


if __name__ == "__main__":
    sys.exit(main())
