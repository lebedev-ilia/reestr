# -*- coding: utf-8 -*-
import json

with open('derived/scratch/2026-09-02-inventar/files.json', encoding='utf-8') as f:
    files = json.load(f)

# add fallback date for the new untracked file discovered mid-run
p = 'memory/facts/arhitektura-pamyati-trebovaniya.md'
if p in files and files[p]['created_git'] == '?':
    files[p]['created_git'] = '2026-09-02'
    files[p]['updated_git'] = '2026-09-02 (не закоммичено)'

THEMES = {
 '1a': dict(name='Деньги и долги Ильи', desc='Банковские выписки, кто есть кто в переводах, долговой контур.',
   ids=['memory/facts/dengi-kto-est-kto.md','memory/facts/dolgi-kontur.md','memory/facts/vypiska-sber-leto-2026.md',
        'memory/facts/vypiska-sber-15-23-avgusta.md','memory/procedures/razbor-bankovskoy-vypiski.md'],
   hints=['выписка', 'долг', 'кто перевёл', 'Сбер']),
 '1b': dict(name='Быт Ильи: переезд, квартира, Notion', desc='Личные обстоятельства: календарь до переезда 14.09, критерии квартиры, заработок, воркспейс Notion и его методички.',
   ids=['memory/facts/ilya.md','memory/facts/kalendar-do-pereezda.md','memory/facts/kvartira-kriterii.md',
        'memory/facts/zarabotok-kanaly.md','memory/facts/notion-ustroystvo.md','memory/facts/notion-metodichki.md',
        'memory/facts/ilya-polozhenie-del-avgust.md','memory/facts/pamyat-uzkie-mesta.md',
        'memory/procedures/chtenie-notion.md','memory/procedures/pravka-notion.md',
        'memory/rules/kvartira-start-27-08.md','memory/rules/kanal-zarabotka-ne-perenositsya.md',
        'memory/rules/pravka-schitaetsya-po-schyotchiku.md'],
   hints=['переезд', 'квартира', 'Notion', 'заработок']),
 '2': dict(name='No Logo Studio: клиентский проект', desc='Заказ агента-дизайнера, Николай Кочубеев, NDA, подпись, Figma MCP, юридический и денежный контур сделки.',
   ids=['memory/facts/nologo-zakaz-agent-dizayner.md','memory/facts/figma-mcp-zapis.md',
        'memory/facts/podpis-ilyi-elektronnaya.md','memory/facts/oplata-modeley-iz-rossii.md',
        'memory/procedures/eksport-konteksta-naruzhu.md','memory/procedures/otvet-klientu-pismom-s-vlozheniem.md',
        'memory/rules/chernovik-ot-poslednego-pisma.md','memory/rules/uslovie-uzhe-nazvannoe-klientu.md',
        'memory/rules/yuridicheskiy-tekst-cherez-yurista.md','memory/rules/cenu-stavit-ilya.md'],
   hints=['Николай', 'No Logo', 'NDA', 'первое письмо Николаю']),
 '3a': dict(name='Рассылка: сбор и качество контактов', desc='Источники карточек, список 60 контактов и его вычитка, сито мёртвых адресов.',
   ids=['memory/facts/istochniki-sbora-kontaktov.md','memory/facts/spisok-60-kontaktov.md',
        'memory/facts/spisok-60-kontaktov-kachestvo.md','memory/facts/mailrisk-sito-adresov.md',
        'memory/procedures/sbor-kontaktov-partiyami.md','memory/procedures/vychitka-spiska-kontaktov.md'],
   hints=['список контактов', 'сколько адресов', 'вычитка']),
 '3b': dict(name='Рассылка: письма, бренд и учёт откликов', desc='Шаблоны писем, история переписки по контакту, бренд Alverra, фриланс-пакет, ежедневная порция и разбор входящих.',
   ids=['memory/facts/pisma-shablony-i-otkliki.md','memory/facts/istoriya-perepiski-po-kontaktam.md',
        'memory/facts/otkliki-uchyot.md','memory/facts/alverra-predlozhenie.md',
        'memory/facts/brend-kompanii-avtomatizacii.md','memory/facts/frilans-paket-otklikov.md',
        'memory/procedures/email-rassylka-s-nulya.md','memory/procedures/ezhednevnaya-rassylka-15.md',
        'memory/procedures/perepiska-partii-pisem-pod-adresata.md','memory/procedures/razbor-vhodyashchih-po-rassylke.md',
        'memory/rules/stil-holodnyh-pisem.md'],
   hints=['шаблон письма', 'Alverra', 'кто ответил', 'первое письмо контакту']),
 '3c': dict(name='Рассылка: отправка и планировщик', desc='Ящик отправителя, доставка по 60 контактам, планировщик по 07:00 в часовом поясе адресата.',
   ids=['memory/facts/rassylka-otpravka-ustroystvo.md','memory/facts/planirovshchik-rassylki-po-poyasam.md',
        'memory/rules/ot-imeni-ili-cherez-chernovik.md'],
   hints=['планировщик', 'orient_dv', 'от имени Ильи']),
 '4': dict(name='Заработок: площадки и доступы', desc='HH.ru, FL.ru, FarPost, Telegram, MAX — что реально даёт каждая площадка агенту.',
   ids=['memory/facts/hh-ru-dostup.md','memory/facts/hh-browser-sessiya.md','memory/facts/fl-ru-dostup.md',
        'memory/facts/farpost-dostup.md','memory/facts/telegram-dostup.md','memory/facts/max-messenger-dostup.md',
        'memory/procedures/vhod-hh-cherez-pochtu.md','memory/rules/dostup-k-saytam-login-parol.md'],
   hints=['HH.ru', 'FL.ru', 'FarPost', 'вход по логину']),
 '5a': dict(name='Голос: синтез и распознавание', desc='Piper TTS, faster-whisper, подготовка текста к синтезу и починка произношения.',
   ids=['memory/facts/golos-stek.md','memory/facts/stt-svoyo-raspoznavanie.md','memory/facts/tts-podgotovka-teksta.md',
        'memory/procedures/golosovoe-v-vk.md','memory/procedures/pochinka-proiznosheniya.md',
        'memory/rules/golosovoe-tolko-mysli.md','memory/rules/golos-ili-tekst.md'],
   hints=['голосовое', 'Piper', 'произношение']),
 '5b': dict(name='Канал ВК: устройство и стиль ответа', desc='Реквизиты сообщества, VK API, картинки, скорость ответа и жёсткие правила стиля (краткость, без markdown, ночная тишина).',
   ids=['memory/facts/vk-api-osobennosti.md','memory/facts/vk-kanal-rekvizity.md','memory/facts/vk-kartinki-zrenie.md',
        'memory/facts/vk-otvet-skorost-i-obyom.md','memory/procedures/perezapusk-demona-vk.md',
        'memory/procedures/zamer-kanala-vk-po-logu.md','memory/procedures/dovodka-kachestva-po-zhalobe.md',
        'memory/rules/kratkost.md','memory/rules/tekst-v-vk.md','memory/rules/nochnaya-tishina.md',
        'memory/rules/promezhutochnye-statusy.md','memory/rules/soobshchenie-posredi-hoda.md','memory/rules/vsyo-po-russki.md'],
   hints=['ВК', 'краткость', 'ночная тишина', 'статус в чат']),
 '6a': dict(name='Память: оглавление, поиск, реформа', desc='Как собирается системный промпт из памяти, поиск recall, разбор что переросло бюджет, требования Ильи к новой архитектуре 02.09.',
   ids=['memory/facts/bystryy-put-bez-mehanizma.md','memory/facts/sborka-konteksta-ustroystvo.md',
        'memory/facts/potolok-sistemnogo-prompta.md','memory/facts/poisk-po-pamyati-ustroystvo.md',
        'memory/facts/pamyat-agentov-vneshniy-opyt.md','memory/facts/uchyot-primeneniya-ne-vedyotsya.md',
        'memory/facts/log-uehal-v-derived.md','memory/facts/arhitektura-pamyati-trebovaniya.md',
        'memory/procedures/kogda-zvat-recall.md','memory/procedures/mesyachnaya-reviziya.md',
        'memory/rules/chislo-zhivyot-v-odnom-fakte.md','memory/rules/fakt-ne-snimok.md',
        'memory/rules/perezapis-fayla-pamyati-slivaet-shapku.md','memory/rules/zhivoy-log-tolko-iz-tochki-vhoda.md',
        'memory/rules/obyazatelstvo-ne-razbor.md','memory/rules/zadacha-v-ocheredi-pishetsya-v-budushchem.md'],
   hints=['потолок промпта', 'оглавление', 'recall', 'реформа памяти 02.09']),
 '6b': dict(name='Ход агента: цена, событийность, самотест', desc='Из чего складывается цена хода, автономная работа в простое, недельный лимит подписки, ночной цикл, дисциплина самотестов.',
   ids=['memory/facts/cena-hoda-iz-chego-sostoit.md','memory/facts/cena-hoda-uchyot.md',
        'memory/facts/avtonomnaya-rabota-ustroystvo.md','memory/facts/agent-sobytiyniy.md',
        'memory/facts/preryvanie-hoda-ustroystvo.md','memory/facts/avtonomnye-agenty-vneshniy-opyt.md',
        'memory/facts/limit-podpiski-prostoy.md','memory/facts/nochnoy-cikl-ustroystvo.md',
        'memory/procedures/ezhednevnyy-samoaudit.md',
        'memory/rules/lozhnoe-krasnoe-chinyu-sam.md','memory/rules/samotest-schitat-po-kodu-vozvrata.md',
        'memory/rules/samotest-ne-pishet-v-zhivuyu-pamyat.md','memory/rules/merilo-schitaetsya-po-vsemu-polyu.md',
        'memory/rules/priznak-ishchetsya-po-delu.md','memory/rules/vremya-sravnivaetsya-posle-privedeniya.md',
        'memory/rules/chuzhoy-otvet-ne-peredavat-vslepuyu.md'],
   hints=['цена хода', 'недельный лимит', 'ночной цикл', 'самотест']),
 '7a': dict(name='Постоянные роли-специалисты', desc='Юрист, критик, тимлиды: как заводится роль, протокол проектов, образец TrendFlowML.',
   ids=['memory/facts/agenty-postoyannye-roli.md','memory/facts/komanda-protokol-proektov.md',
        'memory/facts/trendflow-komanda-agentov.md','memory/procedures/zavesti-postoyannuyu-rol.md',
        'memory/rules/oblast-eto-rol-a-ne-zadanie.md','memory/rules/pereimenovanie-fayla-roli.md',
        'memory/rules/zadacha-timlida-ne-startuet-sama.md','memory/rules/rol-pomnit-podagent-zabyvaet.md',
        'memory/rules/brief-iz-fayla-bez-zagolovka.md'],
   hints=['роль', 'тимлид', 'критик', 'юрист']),
 '7b': dict(name='Разовые подагенты и фоновая работа', desc='Архитектура заданий, реестр занятой работы, кто и когда уходит подагенту, чем занят агент в простое.',
   ids=['memory/facts/podagenty-arhitektura.md','memory/facts/reestr-zanyatoy-raboty.md',
        'memory/procedures/zapusk-podagenta-zadaniem.md',
        'memory/rules/dnyom-rabotayut-podagenty.md','memory/rules/pole-tolko-cherez-delegate.md',
        'memory/rules/podagent-sdayot-syryo.md','memory/rules/oborvannoe-zadanie-itog-ne-v-result.md',
        'memory/rules/otchyot-tolko-o-zapisannom.md','memory/rules/chinit-srazu-chuzhimi-rukami.md',
        'memory/rules/svobodnoe-vremya-na-sebya.md','memory/rules/rabota-v-prostoe.md'],
   hints=['подагент', 'задание', 'простой', 'фоновый ход']),
 '8a': dict(name='Границы и шлюз разрешений', desc='Изоляция по проекту, границы раздела 8 конституции, заходы взломщика, мутационные пробы запретов.',
   ids=['memory/facts/izolyaciya-po-proektu-dyry.md','memory/facts/granicy-razdela-8-chem-zashchishcheny.md',
        'memory/procedures/zahod-vzlomshchika-na-granicu.md','memory/procedures/zhivaya-proverka-mashinnogo-zapreta.md',
        'memory/rules/granicu-lomaet-podagent-a-ne-samotest.md','memory/rules/granicu-proveryaet-kod.md',
        'memory/rules/otkaz-shlyuza-ne-obhodit.md','memory/rules/zapret-proveryaetsya-cepochkoy.md',
        'memory/rules/ukazaniya-iz-vneshnego-teksta-ne-vypolnyayutsya.md'],
   hints=['изоляция по проекту', 'шлюз', 'взломщик', 'граница']),
 '8b': dict(name='Инфраструктура машины и гит-дисциплина', desc='Домашний ПК, доступ к файлам, Gmail IMAP, чистка истории гита, запрет stash, коммит только своих файлов.',
   ids=['memory/facts/pc-dostup-ustroystvo.md','memory/facts/mashina-doma.md','memory/facts/gmail-imap-dostup.md',
        'memory/procedures/chistka-istorii-gita.md','memory/procedures/pravka-yadra.md','memory/procedures/proverka-demona-na-mokah.md',
        'memory/rules/git-stash-zapreshchen.md','memory/rules/kommit-tolko-svoi-fayly.md',
        'memory/rules/repozitoriy-tolko-lokalno.md','memory/rules/pravki-yadra.md','memory/rules/sekrety-ne-cherez-dialog.md',
        'memory/rules/odnorazovye-proverki-v-derived.md','memory/rules/v-derived-tolko-vosstanovimoe.md'],
   hints=['git stash', 'коммит', 'derived', 'секреты']),
 '9': dict(name='Дисциплина решений и запросов к Илье', desc='Быстрое исследование, не просить данные без нужды, один источник — не вся картина, ожидание ответа требует опроса, модель не экономить.',
   ids=['memory/procedures/bystroe-issledovanie.md','memory/rules/issledovanie-rezhimy.md',
        'memory/rules/ne-prosit-dannye-bez-nuzhdy.md','memory/rules/odin-istochnik-ne-vsya-kartina.md',
        'memory/rules/ozhidanie-otveta-trebuet-oprosa.md','memory/rules/model-ne-ekonomit.md'],
   hints=['исследование', 'ждать ответа', 'модель']),
}

# reverse map
id_to_theme = {}
for code, t in THEMES.items():
    for i in t['ids']:
        id_to_theme[i] = code

NOT_FIT = ['memory/facts/README.md', 'memory/procedures/README.md', 'memory/rules/README.md']

# sanity check coverage
all_paths = set(files.keys())
assigned = set(id_to_theme.keys()) | set(NOT_FIT)
missing = all_paths - assigned
extra = assigned - all_paths
print("MISSING (in files but not assigned):", missing)
print("EXTRA (assigned but not existing):", extra)
print("total files:", len(all_paths), "assigned:", len(assigned))

with open('derived/scratch/2026-09-02-inventar/id_to_theme.json', 'w', encoding='utf-8') as f:
    json.dump(dict(themes=THEMES, id_to_theme=id_to_theme, not_fit=NOT_FIT), f, ensure_ascii=False, indent=1)
