---
id: brend-kompanii-avtomatizacii
type: project
name: Бренд компании по ИИ-агентам: Alverra (выбран 23.08.2026)
status: active
created: 2026-08-23
updated: 2026-08-24
sources: [log:2026-08-23#206, log:2026-08-23#305]
links: [alverra-predlozhenie, pisma-shablony-i-otkliki, rassylka-otpravka-ustroystvo]
---

Илья выбрал название 23.08.2026: **Alverra**. Контакт для подписи — телеграм @haunteedFamily
(ником, без ссылки t.me: ссылка в холодном письме поднимает спам-скор). Подпись писем:
«Илья Лебедев, Alverra» + адрес отправителя + строка телеграма.

TrendFlow — ДРУГОЙ проект Ильи (аналитика видеоконтента), к автоматизации отношения не имеет.
Все 60 исходных писем в T-835_60_KONTAKTOV.md были подписаны им ошибочно.

Устройство:
* modules/outbox/brand.json — единственное место, где живут name, tg, site. Подставляются
  в письма вместо {BRAND} и {TG}.
* Пустое name = отправка отменяется с кодом 3 (черновики при этом рисуются с плейсхолдерами).
* Любое письмо со словом TrendFlow в теле пропускается.

Домены Alverra по DNS на 23.08.2026: .ru, .com, .net, .org — ЗАНЯТЫ. Свободны: alverra.tech,
alverra.pro, alverra.io, alverra.ai, alverra.dev, alverra.digital, alverra.agency,
alverra.studio, alverra-tech.ru. Рекомендация — alverra.tech или alverra-tech.ru.
Тёзки: Alverra Partners (финконсалтинг, ЕС), Alverra co. (товары), ALVERRA LTD (UK),
ООО «Алвера» (бухгалтерия, СПб). В российском IT — чисто, пересечений нет.
NXDOMAIN — первичный фильтр, а не гарантия: перед покупкой смотреть whois.

Что продаём под этим именем — факт alverra-predlozhenie. Как выбиралось имя и какие
варианты отпали — событие 23.08, оно в логе (log:2026-08-23#305), здесь не место.
