# RunPod: проверка по интернету (14.10.2026)

Задача: аренда GPU под обучение RVC (датасет 6 мин аудио, 2–6 ч, нужно 16–24 ГБ VRAM).
Все цифры ниже — с указанных URL, сняты 14.10.2026 02:17 UTC.

## 1. Цены RunPod на октябрь 2026

Источник: [runpod.io/pricing](https://www.runpod.io/pricing) (JSON-LD страницы, поле `description` каждого GPU) и
живой GraphQL `https://api.runpod.io/graphql` (`gpuTypes { communityPrice securePrice }`), замер 14.10.2026 02:17 UTC.

| GPU | VRAM | Community | Secure |
|---|---|---|---|
| RTX 4000 Ada | 20 ГБ | **$0.20** | $0.28 |
| RTX 4000 Ada SFF | 20 ГБ | $0.18 | $0.44 |
| RTX 3090 | 24 ГБ | **$0.22** | $0.50 |
| RTX 3090 Ti | 24 ГБ | $0.27 | $0.46 |
| RTX 4090 | 24 ГБ | **$0.34** | $0.74–0.89* |
| RTX A5000 | 24 ГБ | **$0.16** | $0.27 |
| RTX A4500 | 20 ГБ | $0.19 | $0.25 |
| RTX 4080 | 16 ГБ | $0.27 | $0.50 |
| RTX 5080 | 16 ГБ | $0.39 | $0.59 |
| RTX 5090 | 32 ГБ | **$0.69** | $0.99–1.19* |
| L4 | 24 ГБ | $0.44 | $0.49–0.59* |
| L40S | 48 ГБ | $0.79 | $1.09 |

Цитата (JSON-LD, RTX 3090): *"On-demand RTX 3090 cloud GPU rental on Runpod. Community Cloud $0.22/hr, Secure Cloud $0.50/hr."*
То же для 4090: *"Community Cloud $0.34/hr, Secure Cloud $0.74/hr."*; для 5090: *"Community Cloud $0.69/hr, Secure Cloud $0.99/hr."*;
для A5000: *"Community Cloud $0.16/hr, Secure Cloud $0.27/hr."*

\* расхождение: витрина `/pricing` даёт Secure $0.74 (4090), $0.99 (5090), $0.49 (L4); GraphQL API — $0.89 / $1.19 / $0.59.
Для Community-цен оба источника совпадают.

В вилку **$0.20–0.30/ч** попадают: RTX A5000 ($0.16), RTX 4000 Ada SFF ($0.18), RTX A4500 ($0.19),
RTX 4000 Ada ($0.20), RTX 3090 ($0.22), RTX 3090 Ti ($0.27), RTX 4080 ($0.27) — все в **Community Cloud**.
В Secure Cloud в эту вилку не попадает никто (минимум — RTX 2000 Ada $0.24, A4500/A4000 $0.25, A5000 $0.27).

## 2. Наличие (availability)

Проверяется через тот же GraphQL: `gpuTypes(input:{id:"..."}) { lowestPrice(input:{gpuCount:1}) { uninterruptablePrice stockStatus } }`.
`stockStatus` принимает значения High / Medium / Low, а `null` в обоих полях = под сейчас не запускается.
Замеры 14.10.2026: RTX 3090 — `stock=Low, $0.22`; RTX 4090 — `stock=High, $0.34`; RTX A5000 — `stock=Low, $0.16`;
RTX 4000 Ada — `stock=Low, $0.20`; L4 — `stock=Low`; **RTX 5090 в 02:17 UTC отдал `null` (недоступен)**, хотя в 02:14 был доступен — статус меняется в реальном времени.

Важный контекст из документации: *"Runpod is no longer accepting new hosts for Community Cloud. Existing Community Cloud
resources remain available."* — [docs.runpod.io/pods/choose-a-pod](https://docs.runpod.io/pods/choose-a-pod). То есть дешёвый
пул Community не растёт, и «Low» у 3090/A5000 — это про нехватку свободных машин, а не про сбой.
В веб-консоли то же видно на шаге деплоя: у GPU выводится индикатор доступности, а при выборе дата-центра занятые
комбинации не предлагаются. На публичных страницах (`/pricing`, `/product/cloud-gpus`) слова «Sold Out» нет — это
состояние консоли, а не витрины.

## 3. Оплата из России

Официально ([docs.runpod.io/accounts-billing/billing](https://docs.runpod.io/accounts-billing/billing), раздел Payment methods):
*"Credit card — Visa, Mastercard, American Express, and other cards supported by Stripe. Prepaid cards should deposit at
least $100 per transaction."*; *"Cryptocurrency — Accepted via integrated payment processors. Complete any required KYC
verification before your first crypto payment."*; счёт для бизнеса — *"over $5,000"*.

Карты РФ: Stripe не обслуживает операции с Россией, а Visa/Mastercard с марта 2022 не работают за пределами РФ —
поэтому напрямую картой РФ **нельзя**. Крипта идёт **не через Stripe, а через Crypto.com** (TOS, ред. 24.03.2026:
*"Runpod uses Stripe, Inc. and its affiliates ("Stripe") and Crypto.com as its third-party service provider for payment
services"*) — [runpod.io/legal/terms-of-service](https://www.runpod.io/legal/terms-of-service). То есть крипто-канал
формально отдельный, но у процессора свой KYC — «пройдёт ли» проверяется только на практике.
Кредиты невозвратны: *"Runpod credits are non-refundable and cannot be withdrawn once deposited."*

Посредники: Payholder — комиссия **4%**, приём карт МИР, СБП, VISA, зачисление 1–15 мин
([steam-currency.ru/about/payholder](https://steam-currency.ru/about/payholder), данные на 14.10.2026; там же рейтинг сервиса
2.0/5 и расхождение в тексте страницы между 4% и 7% — читать как «от 4%»). Альтернативы с их же сравнения: Gamemoney/FunPay 3%,
GroguPay/Aifory 5%, МТС Оплата 5%. Схема оплаты «по доступу» описана в [dtf.ru/howto/5155842](https://dtf.ru/howto/5155842-oplata-runpod-v-rossii-i-belarusi):
посредник либо сам заходит в аккаунт, либо просит удалённый доступ.

Риск блокировки аккаунта для РФ: в TOS перечислены эмбарго-территории — *"Cuba, Iran, North Korea, Syria, and the Crimea,
so-called Donetsk People's Republic, and so-called Luhansk People's Republic regions of Ukraine"*. **России в списке нет,
и слово «Russia» в TOS не встречается ни разу** (проверено `grep -c -i russia` = 0). Но TOS даёт право
*"suspend or terminate your account"* за недостоверные данные, а справка по отказам карт предупреждает:
*"If the payment processor detects several failed attempts from your account in a short period, it may temporarily block
all payment attempts. These blocks typically clear after 24 hours"*
([docs.runpod.io/accounts-billing/manage-payment-cards](https://docs.runpod.io/accounts-billing/manage-payment-cards)).
Реальные риски — не «русский паспорт», а передача пароля посреднику и серия неудачных попыток оплаты.

## 4. Хранение данных

[docs.runpod.io/pods/storage/types](https://docs.runpod.io/pods/storage/types), таблица Comparison:
container disk **$0.10/ГБ/мес**; volume disk **$0.10/ГБ/мес** (под запущен) и **$0.20/ГБ/мес** (под остановлен);
network volume **$0.07/ГБ/мес** до 1 ТБ и **$0.05/ГБ/мес** свыше 1 ТБ; global volume $0.09/ГБ/мес + IOPS.
Биллинг посекундный. То есть за 20 ГБ network volume за 2 суток выйдет ≈ $0.09.

При нулевом балансе ([docs.runpod.io/accounts-billing/billing](https://docs.runpod.io/accounts-billing/billing), Low balance behavior):
*"When your balance reaches $0, Runpod automatically stops all of your running Pods."*
*"Pods with a network volume attached are stopped, and your data is preserved on the network volume."*
*"Pods without a network volume are terminated, and their data can't be recovered."*
*"Storage charges continue to accrue on network volumes while your Pods are stopped. If your balance stays at $0 and these
charges can't be covered, the network volume may eventually be terminated, and its data can't be recovered."*

**Фиксированного «срока на скачивание результата» в документации нет** — срок определяется тем, на сколько хватает
остатка на оплату хранения (это ограничение, а не факт из доков). Практический вывод: качать результат сразу после
обучения, не полагаться на том. Плюс предупреждение: *"Runpod is not designed for long-term data storage... If your
account runs out of funds, storage may be deleted and cannot be recovered."*

## 5. Практика запуска

Шаблоны ([docs.runpod.io/pods/templates](https://docs.runpod.io/pods/templates)): официальные (поддержка RunPod),
community (только Discord) и свои. Готовый PyTorch-образ есть: в туториале используется
`runpod/pytorch:1.0.2-cu1281-torch280-ubuntu2404` — *"includes PyTorch 2.8.0, CUDA 12.8.1, and Ubuntu 24.04"*
([docs.runpod.io/pods/templates/create-custom-template](https://docs.runpod.io/pods/templates/create-custom-template)).
Прогревать образ вручную не нужно: он подтягивается при создании пода, но первый pull заметно дольше последующих —
конкретных секунд документация не называет (предположение: единицы минут на многогигабайтный образ).

Передача файлов ([docs.runpod.io/pods/storage/transfer-files](https://docs.runpod.io/pods/storage/transfer-files)):
`runpodctl` (предустановлен в поде, «small to medium files», одноразовые коды — `runpodctl send FILE` / `runpodctl receive CODE`),
`scp -P <port> -i ~/.ssh/id_ed25519 ...`, `rsync -avz` (для датасетов: *"best choice for large datasets"*, не переливает уже
существующие файлы), Cloud Sync в AWS S3 / GCS / Dropbox, плюс S3-совместимый API
([docs.runpod.io/storage/s3-api](https://docs.runpod.io/storage/s3-api)). Точных МБ/с в доках нет — для 1–2 ГБ при
нормальном канале это единицы минут (оценка, не цитата).

Про RVC конкретно: в официальном гайде RunPod написано, что RVC даёт результат уже при *"as little as 5–10 minutes of
target speaker audio for training"* — датасета 6 минут достаточно — и что на RunPod можно
*"use a container that already has the RVC WebUI and all dependencies installed"*, храня чекпойнты `.pth` на network volume
([runpod.io/articles/guides/ai-engineer-guide-rvc-cloud](https://www.runpod.io/articles/guides/ai-engineer-guide-rvc-cloud), обновлён 13.09.2026).
Готового шаблона «RVC» в списке официальных нет — это community/свой образ (предположение, публичный список шаблонов
отдаётся только консоли под авторизацией).

## 6. Альтернативы: CloudCompute.ru

Цифры подтверждены дословно с [cloudcompute.ru/prices](https://cloudcompute.ru/prices) (рендер страницы 14.10.2026):
**NVIDIA RTX 3090, 24 ГБ — «от 27.71 ₽/час»**, **NVIDIA RTX 4090, 24 ГБ — «от 52.25 ₽/час»** — оба совпадают с тем, что
называл пользователь. Там же: RTX A5000 24 ГБ — от 29.65 ₽/час, RTX 4000 Ada 20 ГБ — от 58.71 ₽/час,
RTX 3060 12 ГБ — от 18.35 ₽/час, RTX 5090 32 ГБ — от 136.96 ₽/час. На странице заявлены «Оплата картой РФ»,
«Договор и документы», «Поддержка на русском» — то есть основная проблема (оплата из РФ) здесь снята.
Оговорка: «от» — это минимальная цена по пулу.

## Итог

**$0.24/час — реально: это Community Cloud на RTX 3090 ($0.22/ч), RTX A5000 ($0.16/ч) или RTX 4000 Ada ($0.20/ч),
все 20–24 ГБ VRAM (Secure Cloud таких цен не даёт), но наличие у них «Low» и плавает — 4090 за $0.34/ч доступен заметно
стабильнее; из РФ оплатить RunPod картой нельзя, рабочие пути — крипта через Crypto.com (с KYC) или посредник
(Payholder, ~4%, карта МИР/СБП), при этом Россия не в списке эмбарго RunPod, а реальные риски — передача пароля
посреднику и блокировка оплат на ~24 ч после серии отказов; при нулевом балансе под без network volume удаляется
безвозвратно.**
