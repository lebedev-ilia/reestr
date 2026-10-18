# -*- coding: utf-8 -*-
import json

with open('derived/scratch/2026-09-02-inventar/files.json', encoding='utf-8') as f:
    files = json.load(f)
with open('derived/scratch/2026-09-02-inventar/id_to_theme.json', encoding='utf-8') as f:
    data = json.load(f)
THEMES = data['themes']
id_to_theme = data['id_to_theme']
NOT_FIT = data['not_fit']

# patch missing dates for the untracked new file
p = 'memory/facts/arhitektura-pamyati-trebovaniya.md'
if files[p]['created_git'] == '?':
    files[p]['created_git'] = '2026-09-02'
    files[p]['updated_git'] = '2026-09-02 (не закоммичено)'

def summary_of(rec):
    fm = rec['fm']
    if rec['dir'] == 'facts':
        return fm.get('name', '(без name)')
    if rec['dir'] == 'procedures':
        return fm.get('trigger', '(без trigger)')
    if rec['dir'] == 'rules':
        return fm.get('scope', '(без scope)')
    return ''

def status_of(rec):
    fm = rec['fm']
    return fm.get('status', '')

# ---------------- inventory table ----------------
lines = []
lines.append("# Инвентарь памяти — все файлы memory/facts, memory/procedures, memory/rules")
lines.append("")
lines.append(f"Собрано 02.09.2026 заданием job-211, для реформы схемы памяти (arhitektura-pamyati-trebovaniya). "
              f"Всего файлов на диске: {len(files)} (facts 68, procedures 29, rules 62 — один факт "
              f"arhitektura-pamyati-trebovaniya.md появился в ходе самого этого задания, ещё не закоммичен).")
lines.append("")
lines.append("| путь | id | тип | тема | байт | создан | правлен | статус | о чём (name/trigger/scope, коротко) | сущности |")
lines.append("|---|---|---|---|---|---|---|---|---|---|")

for path in sorted(files.keys(), key=lambda p: (files[p]['dir'], p)):
    rec = files[path]
    theme = id_to_theme.get(path)
    theme_label = THEMES[theme]['name'] if theme else ('НЕ ВЛЕЗЛО' if path in NOT_FIT else '???')
    summ = summary_of(rec)
    if len(summ) > 90:
        summ = summ[:87] + '...'
    summ = summ.replace('|', '/').replace('\n', ' ')
    ents = ', '.join(rec['entities'][:8])
    lines.append(f"| {path} | {rec['id']} | {rec['dir']} | {theme_label} | {rec['size']} | "
                 f"{rec['created_git']} | {rec['updated_git']} | {status_of(rec)} | {summ} | {ents} |")

with open('derived/pamyat-inventar.md', 'w', encoding='utf-8') as f:
    f.write('\n'.join(lines) + '\n')

print("inventory rows:", len(files))

# ---------------- themes draft ----------------
tlines = []
tlines.append("# Черновик тем для новой схемы памяти")
tlines.append("")
tlines.append("Собрано 02.09.2026 (job-211) как вход для новой редакции MEMORY.md (сама схема пишется отдельно, "
               "по решению Ильи — ничего не переносить до его «да», см. memory/facts/arhitektura-pamyati-trebovaniya.md). "
               "Критерий нарезки — поиск: через год на вопрос вроде «какое первое письмо мы отправили Николаю» "
               "агент должен за несколько шагов понять, где смотреть.")
tlines.append("")
tlines.append(f"Тем: {len(THEMES)}. Покрывают {sum(len(t['ids']) for t in THEMES.values())} файлов "
              f"из {len(files)} на диске; остальные {len(NOT_FIT)} — три README.md каталогов, вынесены отдельно ниже.")
tlines.append("")

order = ['1a','1b','2','3a','3b','3c','4','5a','5b','6a','6b','7a','7b','8a','8b','9']
for code in order:
    t = THEMES[code]
    ids = t['ids']
    total_bytes = sum(files[i]['size'] for i in ids if i in files)
    dts = []
    for i in ids:
        if i in files:
            c = files[i]['created_git']
            u = files[i]['updated_git']
            if c and c != '?':
                dts.append(c[:10])
            if u and u != '?':
                dts.append(u[:10])
    date_range = f"{min(dts)} — {max(dts)}" if dts else "?"
    ents = set()
    for i in ids:
        if i in files:
            ents.update(files[i]['entities'])
    facts_n = sum(1 for i in ids if i.startswith('memory/facts/'))
    proc_n = sum(1 for i in ids if i.startswith('memory/procedures/'))
    rules_n = sum(1 for i in ids if i.startswith('memory/rules/'))
    tlines.append(f"## {code}. {t['name']}")
    tlines.append("")
    tlines.append(f"{t['desc']}")
    tlines.append("")
    tlines.append(f"Файлов: {len(ids)} (фактов {facts_n}, процедур {proc_n}, правил {rules_n}). "
                  f"Байт: {total_bytes}. Диапазон дат: {date_range}.")
    tlines.append(f"Сущности: {', '.join(sorted(ents)) if ents else '—'}.")
    tlines.append(f"Поисковые подсказки: {', '.join(t['hints'])}.")
    tlines.append("")
    tlines.append("Файлы:")
    for i in ids:
        if i in files:
            tlines.append(f"- {i} — {summary_of(files[i])[:80]}")
    tlines.append("")

tlines.append("## Не влезли ни в одну тему")
tlines.append("")
tlines.append("Три файла README.md — по одному на каталог (memory/facts, memory/procedures, memory/rules). "
              "Это не тематический контент, а одностроечное описание самого каталога и его владельца "
              "(требование раздела 9 конституции: у каждой папки README с одной строкой). Помещать их в "
              "тематический узел некуда — они не про факт, процедуру или правило, а про формат хранения. "
              "В новой схеме им, видимо, место в шапке самого раздела/дерева, а не в списке тем.")
for p in NOT_FIT:
    tlines.append(f"- {p}")
tlines.append("")

tlines.append("## Спорные места классификации (для обсуждения)")
tlines.append("")
tlines.append("1. Два надгробия status=refuted (`ilya-polozhenie-del-avgust`, `pamyat-uzkie-mesta`) помещены в "
              "тему 1b «Быт Ильи», потому что исторически они были снимком того же дня 23-24.08. Но правило "
              "MEMORY.md прямо говорит не удалять refuted — то есть в новой схеме нужен явный статус "
              "«надгробие», а не тихое сидение внутри живой темы: иначе через год они будут мешать поиску "
              "(два файла в теме, которые прямо запрещено читать как источник).")
tlines.append("2. Правило `ot-imeni-ili-cherez-chernovik` реально пересекает три темы: рассылка (там и живёт "
              "исключение про холодные письма), No Logo/клиенты (черновик ответа клиенту) и отклики на "
              "вакансии (HH/FL). Положено в 3c «Отправка и планировщик», но с равным правом могло уйти в тему "
              "2 (No Logo) — это тот случай, где одно правило нужно в нескольких картах одновременно, а не в "
              "одной.")
tlines.append("3. `dostup-k-saytam-login-parol` (тема 4, площадки заработка) по смыслу — общее правило про ЛЮБОЙ "
              "внешний сервис с логином Ильи (Notion, банки тоже перечислены в тексте), а не только про "
              "площадки заработка. Оставлено в теме 4, потому что источник (log:2026-08-24#2187) — про HH, но "
              "формально годится и в тему 1b (Notion), и в тему 8 (доступы вообще).")
tlines.append("")

tlines.append("## Кандидаты в карточки сущностей")
tlines.append("")
tlines.append("Отдельная задача Ильи (см. arhitektura-pamyati-trebovaniya.md): карточка на человека, компанию, "
              "документ, площадку — со ссылками на файлы, где уже есть данные. Ниже — что нашлось этим "
              "проходом, без гарантии полноты (не искал по логу и derived/, только по фактам/процедурам/правилам).")
tlines.append("")
tlines.append("ЛЮДИ:")
tlines.append("- Николай Кочубеев (No Logo Studio) — memory/facts/nologo-zakaz-agent-dizayner.md (19 КБ, основной файл), "
              "memory/procedures/otvet-klientu-pismom-s-vlozheniem.md, memory/rules/chernovik-ot-poslednego-pisma.md, "
              "memory/rules/yuridicheskiy-tekst-cherez-yurista.md; переписка — derived/threads/info-nologostudio-ru.md, "
              "derived/exports/Письмо_Николаю_финал.md, derived/exports/Разбор_проекта_No_Logo.md (упомянуты в sources, "
              "сами файлы этим проходом не читались).")
tlines.append("- Илья — memory/facts/ilya.md (главная карточка уже фактически есть), плюс деньги/долги/квартира/календарь.")
tlines.append("")
tlines.append("КОМПАНИИ / ПЛОЩАДКИ:")
tlines.append("- No Logo Studio — тема 2 целиком.")
tlines.append("- Alverra (наш бренд) — memory/facts/brend-kompanii-avtomatizacii.md, memory/facts/alverra-predlozhenie.md.")
tlines.append("- HH.ru, FL.ru, FarPost, Telegram, MAX — тема 4, по факту на площадку уже.")
tlines.append("- Diadoc/Госключ/Контур.Сайн — внутри memory/facts/podpis-ilyi-elektronnaya.md, отдельных карточек нет.")
tlines.append("")
tlines.append("ДОКУМЕНТЫ:")
tlines.append("- NDA (No Logo) — упомянут в nologo-zakaz-agent-dizayner.md и правилах темы 2, отдельного файла-карточки нет; "
              "сам текст NDA — вероятно в derived/exports или modules/outbox (этим проходом не проверялось).")
tlines.append("- Список 60 контактов (T-835_60_KONTAKTOV.md) — memory/facts/spisok-60-kontaktov.md указывает на путь "
              "вне репозитория агента (~/Рабочий стол/TrendFlowML/docs/marketing/).")
tlines.append("")
tlines.append("ЖУРНАЛЫ (образец «узкий журнал вместо grep по логу», уже упомянутый Ильёй):")
tlines.append("- memory/outbox/sent.jsonl — отправленные письма рассылки (тема 3c).")
tlines.append("- Не проверялось на этом проходе: есть ли аналогичные узкие журналы для No Logo и для откликов "
              "HH/FL — стоит проверить отдельно, прежде чем проектировать карточки.")

with open('derived/pamyat-temy-chernovik.md', 'w', encoding='utf-8') as f:
    f.write('\n'.join(tlines) + '\n')

print("themes file written, themes:", len(THEMES))
