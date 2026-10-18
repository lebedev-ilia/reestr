# Мастер-индекс решений дизайн-журнала

**Источник:** [`systema/systema_corr.md`](../systema/systema_corr.md) — 120 основных вопросов
(номера 1–124; собственных разделов нет у 63, 108, 120, 123 — на них только ссылки из других
вопросов). Номера строк сверены скриптом: 120 заголовков, 0 расхождений.
**Извлечено:** 2026-10-13, метод — grep по заголовкам + чтение разделов «Принятое решение»
(вопросы 1–112), «Скорректированная сводка» (83–99), подвопросов `N.x` и «Открытые вопросы».

**Как пользоваться:** нашёл тему → открыл строку журнала → прочитал решение целиком.
Тег показывает очередь работ: сначала [MVP], потом [Foundation], остальное по потребности.

## Сводка

| Тег | Сколько | Что это |
|---|---|---|
| **[MVP]** | **19** | без этого не собрать сквозной срез |
| [Foundation] | 64 | к концу первого месяца работы |
| [Scale] | 15 | при росте: арендаторы, квоты, федерация |
| [Advanced] | 20 | по потребности: adversarial, тонкий надзор |
| [Research] | 2 | идея, не для реализации |

**Ближайший фронт (19 вопросов [MVP]):** 1, 2, 3, 5, 6, 7, 8, 10, 11, 13, 14, 16, 17, 19, 21, 33, 34, 61, 80.

**Из них уже реализовано в скелете:** 3 (частично — журнал и аудит), 5 (шлюз PDP/PEP),
6 (уровни автономии, сведены к четырём), 7 (протокол задачи и виза границей задания),
10 (манифест агента), 34 (состояние задачи как плоскость управления), 61 (аудит решений).

## Блок экономики: 17 вопросов «требует корректировки»

У вопросов 83–99 **нет раздела «Принятое решение»** — только «Скорректированная сводка».
Ключевые незакрытые места (по одному на вопрос):

| № | Что не решено |
|---|---|
| 83 | не сведены «подписка как floor» и отдельный credit для агентов; кто владеет overflow-маршрутизацией |
| 84 | нет правила атрибуции подписок между арендаторами до покрытия 80 % |
| 85 | monitoring 20 % (floor) против прежней оценки 3–5 %; методика измерения agentic cloud tax |
| 86 | golden dataset 50 кейсов и статистические гейты не откалиброваны |
| 87 | пять стратегий fallback не сведены с five-tier degradation (115) и failover агентов (29) |
| 88 | скидки 30–60 % — внешние оценки, не подтверждены |
| 89 | цель mode collapse < 10 % без методики; consent gate для синтетики не определён |
| 90 | конфликт удаления персональных данных и воспроизводимости не разрешён |
| 91 | приёмы исследовательские, пороги не заданы |
| 92 | противоречие machine-readable opt-out и opt-in consent не снято |
| 93 | одновременно «семь измерений изоляции» и два уровня namespace+profile |
| 94 | цели (детекция > 90 %, успех атаки на кэш = 0) не проверены |
| 95 | recall 39.9 % при precision 99.4 % — детекция неполна |
| 96 | ссылки на акты без финальных текстов |
| 97 | два несовместимых уровня цели: TTFT 25/50/100 мс против 1.5–4.5 с P99 |
| 98 | порог semantic-кэша задан диапазоном 0.80–0.98 — слишком широко |
| 99 | числа ускорения из внешних замеров, безопасность спекуляции не проверена |

## Противоречия между вопросами (20; требуют решения)

| # | Кто с кем | В чём расходятся |
|---|---|---|
| 1 | 1 ↔ 2 | порог появления куратора: «8 проектов и 5 эскалаций» против «5–7 тимлидов, 2 домена, 10+ задач, 80+ агентов» |
| 2 | 6 ↔ 12 ↔ 22 ↔ 81/82 | четыре несводимые шкалы уровней: action L0–L5, autonomy A0–A5, authority T0–T4, WAL-0…3 |
| 3 | 6 ↔ 87 | L0–L5 означает классы действий и одновременно уровень деградации системы |
| 4 | 37 ↔ 52 | пороги cost circuit breaker: 70/80/90/100 % против 60/80/100 % |
| 5 | 71 ↔ 114 | задержка kill switch: <200/<300/<500 мс против p99 = 17 мс |
| 6 | 7 и 25 ↔ 16 ↔ 26 | порог заполнения контекста: 60 % против 0.75 против 60–70 % |
| 7 | 24 ↔ 25 | при переиспользовании сохранять контекст (24) или начинать с чистого (25) — условие выбора не задано |
| 8 | 3 ↔ 44 | TTL эпизодической памяти: 45 дней против 30–90 |
| 9 | 26 и 59 ↔ 117 | Trust Margin: «динамический диапазон 39.2 пункта» против порога «> 0.5» |
| 10 | 117 ↔ 124 | recovery rate: «> 40 %» против «> 90 %» — одни единицы, разные события |
| 11 | 115 (внутри) | заявлено «пять уровней деградации», перечислено четыре |
| 12 | 113 (две версии) | первая версия и вторая по-разному трактуют диверсификацию провайдеров |
| 13 | 100 ↔ 79 | независимая верификация: multi-verifier против IV&V через se-harness |
| 14 | 27 ↔ 93 | квоты: три уровня model/agent/project против per-tenant; связь «проект ↔ арендатор» не определена |
| 15 | 83 ↔ 98 | кэширование и ротация подписок: владелец механизма и пороги не сведены |
| 16 | 111 ↔ 66 и 18 | адаптация вынесена в офлайн (111) против непрерывной адаптации во время работы (66, 18) |
| 17 | 9, 11, 27, 68 | обозначение P0–P3 применено к четырём разным сущностям: очереди, сообщения, квоты, инциденты |
| 18 | 109 ↔ 95 | метрика Provenance Entropy заявлена в обоих, владелец не назван |
| 19 | 5 ↔ 103 | PDP/PEP: «один PDP, много PEP» против добавления Policy Compiler и пяти чекпоинтов — границы не разграничены |
| 20 | 71 ↔ 77 | dead man's switch: один режим (71) против двух (77) |

## Открытые противоречия внутри «решённых» вопросов

1 (таксономия направлений), 2 (HITL/HOTL создания куратора), 3 (слой `direction` не описан,
владелец `area`), 4 (динамическое вето), 6 (две таксономии), 37 ↔ 52 и 37 ↔ 115 (пороги breaker),
71 ↔ 114 (kill switch), 100 (пороги ложных приёмок), 101 (глубина декомпозиции 3 или 4),
107 (гранулярность пиннинга версий), 117 ↔ 26 (единицы Trust Margin).

---

## Таблица решений (120 вопросов)

| № | Тема | Решение | Тег | Строка |
|---|---|---|---|---|
| 1 | Глубина иерархии | Вариант C: 2 уровня по умолчанию (Джарвис → тимлиды → исполнители); куратор — динамическая роль. Span of control: 5–7 тимлидов на куратора, ≤2 проекта на тимлида, ≤8 исполнителей на проект; сервисные агенты вне вертикали | [MVP] | 1 |
| 2 | Появление куратора | Критерий «И» (все сразу): 5–7 тимлидов, 5 обоснованных эскалаций/нед после 2 попыток, 2+ домена, 10+ задач L3+/нед, 80+ агентов в направлении; ревизия раз в месяц | [MVP] | 150 |
| 3 | Архитектура памяти | Файлы — канонический источник, SQLite — производный индекс; 4 слоя personal/area/direction/project, direction+project шифруются; исполнитель не пишет в память проекта — переносит тимлид | [MVP] | 319 |
| 4 | Сервисные агенты | Общий пул, подчинение напрямую Джарвису; Compliance/Ethics/Security — hard veto, Quality (Критик) — soft veto; память: direction (чтение/запись) + project (чтение) | [Foundation] | 524 |
| 5 | Шлюз разрешений | RBAC + ABAC + Capability; PDP один (allow/deny/warn), PEP множество; запись — только через PEP | [MVP] | 697 |
| 6 | Классы действий L0–L5 | action L0–L5 по обратимости и blast radius + autonomy A0–A5; L4-шаблоны компилируются в guardrails; аномалия L4 → HOLD; L5 — dual-gate M-of-N, 60 с задержки, fail closed | [MVP] | 968 |
| 7 | Handoff и контекст | H_exact + H_stat + H_residual; «не суммировать дважды»; watermark watchdog 60 %; acceptance confirmation; тримминг детерминированный, не LLM | [MVP] | 1170 |
| 8 | Эскалация | 9 триггеров; пакет из 22 полей; options — закрытый список (retry/reassign/split/abandon); attempted_actions_log дословно; каналы normal/urgent (пауза 30 мин); dead-letter и loop closure | [MVP] | 1361 |
| 9 | Ресурсы и перегрузка | Admission control + очереди P0–P3 + backpressure AIMD + system circuit breaker + drain mode 30 мин + virtual lanes; приоритет считает код | [Foundation] | 1588 |
| 10 | Спецификация агента | Директория: agent.yaml (единственный strict-schema) + SOUL.md/RULES.md/DUTIES.md + GovernSpec-секции + capability_profile + evaluation + compliance/; lifecycle Draft→…→Retired; spec-as-code | [MVP] | 1855 |
| 11 | Сообщения пользователю | 4 приоритета P0–P3 и 3 исхода (notify_now / add_to_digest / stay_silent): решает код; окно агрегации 2–5 с; cooldown ≥10 мин и ≤10/день; детекция усталости | [MVP] | 2169 |
| 12 | Автономия Джарвиса | HITL для стратегии / HOTL для рутины по обратимости и blast radius; authority T0–T4; earned autonomy; мониторинг дрейфа; запрет self-authorization | [Foundation] | 2446 |
| 13 | Pull-модель | Команды + свободный ввод с подтверждением классификации; risk-based confirmation; очередь P0–P3; Context Inbox; маршрутизация без вызовов модели | [MVP] | 2659 |
| 14 | Молчание пользователя | Три таймаута (soft/hard/late) + политика non-response; режимы degraded и off-hours; исходы ALLOW/DENY/TIMED_OUT; fail-closed по умолчанию | [MVP] | 2895 |
| 15 | Отчёт и дайджест | Data contract + пункт из 3 предложений + cap 3–5 в день / 5–7 в неделю + привязка к event ID + трекер реакций | [Foundation] | 3144 |
| 16 | Контекст сессии | Lossless Context Management (DAG-резюме), свежий хвост 64 сообщения, порог уплотнения 0.75, Context Card, cache-aware уплотнение, bi-temporal | [MVP] | 3350 |
| 17 | Аудит решений | Внешний детерминированный детектор вместо самооценки + ACR (Ed25519, hash-chained) + immutable ledger + заранее подготовленный откат; approval rate > 95 % — тревога | [MVP] | 3572 |
| 18 | Мета-правила | Constitutional invariants + жизненный цикл поправок (0.75, с ограничением частоты) + maxDriftPerCycle 0.1 + PID-адаптация; Proposer ≠ Approver ≠ Implementer | [Foundation] | 3815 |
| 19 | Право оспорить | Challenge-but-comply; уровни R1/R2/R3; структурированная аргументация; проверка по факту спора; детекция поддакивания (>95 %); согласие в неизменяемом журнале | [MVP] | 4054 |
| 20 | Предпочтения пользователя | Явные и неявные гипотезы в пятимерной рамке; confidence LOW/MEDIUM/HIGH; непрерывный контроль дрейфа; clarification budget 1.2 вопроса | [Foundation] | 4283 |
| 21 | Онбординг агента | 6 стадий; Pilot → Probation → Full deployment; старт с Level 0 (только чтение); autonomy dial 1→2→3; orphan detection (24 ч); ротация ключей ≤ 24 ч | [MVP] | 4526 |
| 22 | Повышение потолка | WAL-0…3 (Observe/Prepare/Execute/Autonomous) по возможностям; повышение +1, понижение без ограничения; Bayesian trust; effective = min(earned, ceiling); shadow 30 дней/100 случаев | [Foundation] | 4942 |
| 23 | Роспуск агента | Bounded Agent Closure + wind-down 9 шагов + succession 4 фазы + PoA/PoD; память по каждому хранилищу отдельно; мониторинг 30 дней | [Foundation] | 5256 |
| 24 | Пул: reuse или spawn | Warm Worker Pool + формализованное решение; durable identity; обнаружение устаревания; защита от загрязнения контекста; WAL и pending handoff при reuse сохраняются | [Foundation] | 5566 |
| 25 | Сброс контекста | Kill & Handoff; режимы none/compact/session, по умолчанию — чистый контекст на задачу (исключение — малое локальное изменение); watermark 60 %; context folding | [Foundation] | 5848 |
| 26 | Здоровье агента | С первого дня: Trust Margin (20 сигналов), прогноз на 8 ч, adaptive controller, canary probes, context rot при 60–70 %, PIG вне модельного пути, agent SLO | [Foundation] | 6219 |
| 27 | Квоты | 3 уровня (model/agent/project) + защита от quota storm + 4-гейтовый admission + приоритеты P0–P3 + token bucket + task budget + irreversibility budget | [Scale] | 6615 |
| 28 | Онбординг в проект | Пакет из 8 компонентов + read-first trail + Context Pack + граф знаний + сократический подход + план 30-60-90 + buddy + проверка перед стартом | [Foundation] | 6928 |
| 29 | Failover и непрерывность | Резервирование через пул + заместитель для критичных ролей; heartbeat timeout 2–3× интервала; dead man's switch; семь уровней fallback; standby cold/warm/hot | [Scale] | 7346 |
| 30 | Завершение проекта | Five-Gate Closure + извлечение знаний (7 категорий) + closeout 10–15 строк + архив вместо удаления + lessons index + хроника | [Foundation] | 7635 |
| 31 | Создание проекта | Семь стадий intake + AAC (6 измерений) + one-page charter + трёхстадийный пилот + 3 условия прекращения + поиск дублей | [Foundation] | 7968 |
| 32 | Команда проекта | Состав по сложности, а не по шаблону; детерминированный TeamSpec; ограничения критичных ролей; fallback policy; anti-patterns | [Foundation] | 8311 |
| 33 | Назначение тимлида | Два пути: назначение и выборы для существующих команд; заместитель заранее; rubric оценки; испытательный период в плане пилота; WAL сохраняется | [MVP] | 8658 |
| 34 | Прогресс и изменения | STATE.yaml в git + five(+1)-question checkpoint + controller + change packet + risk-based taxonomy + качественный circuit breaker + ITIL 4; appetite S/M/L | [MVP] | 8888 |
| 35 | Реестр рисков | RBS + подтверждаемые свидетельствами поля + динамический risk appetite; превышение блокирует повышение уровня; Monte Carlo 2000 итераций | [Foundation] | 9228 |
| 36 | Безопасность (АСБ) | Defense-in-depth 7 слоёв: RBAC+ABAC+Capability, zero-trust, изоляция арендаторов, sandbox для L3+, MITRE ATLAS + STRIDE, incident response; шифрование для L2+ | [Foundation] | 9452 |
| 37 | Бюджет и токены | Task budget (6 параметров) + four-level budget + circuit breaker 70/80/90/100 % + adaptive routing + кэширование + chargeback + economic circuit breaker | [Foundation] | 9691 |
| 38 | Live-наблюдение | Постоянный дашборд + live по запросу: Agent Event Protocol (9 типов), Mission Control, steering Hold/Chat/Leash, time-travel debugging, OpenTelemetry | [Foundation] | 9964 |
| 39 | Фазы проекта | 4 фазы + побочные состояния; Q6 viability check; гейты с evidence-scored решениями; phase-barrier HMAC+SHA-256; skip rule 20 %; ring-composition check | [Foundation] | 10209 |
| 40 | Архив и поиск | Multi-tier архив hot/warm/cold + каталог указателей + гибридный поиск + provenance + bitemporal | [Foundation] | 10468 |
| 41 | Схема записи памяти | 33-полевой шаблон; importance — Judgment Function (6 сигналов); type-specific TTL; supersede с указателем; карантин; bitemporal; typed links; адаптивный decay | [Foundation] | 10719 |
| 42 | Поиск по памяти | 4 шага: FTS5+BM25 и sqlite-vec → RRF (k=60) → composite scoring → cross-encoder (cap 300); ACL внутри каждого retriever; confidence-gated PASS/FLAG/FILTER | [Foundation] | 11004 |
| 43 | Консолидация памяти | Быстрая при записи (>0.85) + фоновая по повторяемости (LLM только при устойчивом повторе, −87 % токенов) + reconciliation; запрет молчаливого разрешения противоречий | [Foundation] | 11271 |
| 44 | Ротация памяти | Utility score + Ebbinghaus decay; TTL: episodic 30–90 дней, semantic 6–12 мес, procedural — иммунитет, policy — никогда; tiers Active/Warm/Cold/Archived; Forget Log | [Foundation] | 11491 |
| 45 | Синхронизация памяти | MESI-подобная когерентность + lazy invalidation (−95 % токенов); 4 режима отказа; VAIL gates; конфликты сохраняются, а не разрешаются молча | [Foundation] | 11747 |
| 46 | Провенанс памяти | W3C PROV-DM + Provenance DAG; bitemporal; Causal Seal; TRACE; правило исключения ложного подтверждения; weakest-link | [Foundation] | 12077 |
| 47 | Доступ к памяти | Derivation-gated retrieval: AKAC decide(), MemLineage, Beta-Binomial trust, жёсткий гейт по правам до ранжирования, fail-closed | [Foundation] | 12304 |
| 48 | Аудит памяти | Append-only как доказательство + witness-countersigned логи + Merkle Mountain Range + run capsule + две оси времени + WORM + подпись вне процесса | [Foundation] | 12604 |
| 49 | Право на забвение | Consent-gated: криптографическое стирание (уничтожение ключа + tombstone), 5 операций удаления, каскад по производным, DeletionCertificate, incognito | [Foundation] | 12862 |
| 50 | Метрики памяти | staleness@k, forgetting, write precision/recall, false-memory rate, current/historical accuracy (гейт 1.0), context-noise ≤ 0.5, deletion completeness | [Foundation] | 13154 |
| 51 | Реестр инструментов | Capability-based control plane; lifecycle DRAFT→APPROVED→DEPRECATED; two-sided grant chain; per-call allowlist; digest pinning; JIT-credentials | [Foundation] | 13452 |
| 52 | Политики инструментов | 6-состояние circuit breaker; идемпотентный ledger; 6 типов ошибок, после 2 попыток — эскалация; bulkhead; WASM-песочница; cost CB 60/80/100 % | [Foundation] | 13748 |
| 53 | Внешние API | Короткоживущие удостоверения, привязанные к задаче; OAuth 2.1 + RFC 8707 + PKCE; fail-closed gateway; секреты в vault; предупредительная ротация | [Foundation] | 13987 |
| 54 | Асинхронная координация | MCP Events + push-уведомления + пробуждение по событиям + очередь + event sourcing + correlation ID; replay recovery | [Foundation] | 14285 |
| 55 | Выбор инструментов | Маршрутизация по возможностям + выбор на каждом шаге; в контексте динамический набор, а не фиксированные 5–7 | [Foundation] | 14533 |
| 56 | Версионирование инструментов | Name-versioning + semver; окно устаревания 12 месяцев; три оси дрейфа + Drift Score; инкрементальная переиндексация; expand/contract | [Foundation] | 14767 |
| 57 | Регистрация инструментов | Управляемый жизненный цикл + динамическое обнаружение: проверка дублей, обнаружение перекрытия имён, notifications, три мета-инструмента | [Foundation] | 15060 |
| 58 | Генерация инструментов | Управляемый синтез: generate → validate → sandbox → approval → register → reuse; двойной гейт; обнаружение галлюцинаций; библиотека инструментов | [Advanced] | 15348 |
| 59 | Наблюдаемость инструментов | Три уровня (задача/инструмент/рассуждение) + Scorecard + Trust Margin + Drift Score + CUSUM-алерты + canary probes + Trajectory Guard | [Foundation] | 15567 |
| 60 | Отказоустойчивость инструментов | Retry-Switch-Abstain + Fallback Map + bulkhead + hedging (только чтение L0–L2) + degraded mode + saga recovery | [Foundation] | 15848 |
| 61 | Формат аудита решений | Семь минимальных полей + пять плоскостей; DRR schema; content-optional (по умолчанию только хеш); канонизация RFC 8785 | [MVP] | 16098 |
| 62 | Ретроспективы | Ceremonies по триггеру + flow Phase 0–5 + five whys + atomicity score + обновление skillbook + атрибуция по 10 измерениям | [Foundation] | 16375 |
| 64 | Тестирование агентов | Гейт из 4 измерений + pass@1 / pass^k / headroom + metamorphic testing + оценка по трассе + вердикт Green/Yellow/Red + chaos + red teaming | [Foundation] | 16715 |
| 65 | Метрики и KPI | 3 столпа (надёжность, внедрение, ценность) + cost-of-pass + 6 метрик + 6 SLO + CNSR + покрытие governance | [Foundation] | 17033 |
| 66 | Самоэволюция | PURER + EvolveR + WikiSkill + Atlas; ITIL 4 (25 автономных практик / 9 advisory); пересертификация после обновления; sealed parameters не меняются | [Advanced] | 17325 |
| 67 | Деплой и изменения | Неизменяемые развёртывания + pinned executions + coordinated CI/CD + canary/blue-green + feature flags + 50+ поведенческих метрик + заранее подготовленный откат | [Foundation] | 17635 |
| 68 | Инциденты | P0–P3 + 9 категорий (OWASP Agentic); 6 шагов Triage→…→Post-Mortem; self-healing по runbook; timeline — код; отдельные error budgets | [Foundation] | 17955 |
| 69 | Накопление знаний | ReasoningBank + EvolveR + WikiSkill (сырое неизменяемо → wiki → навык) + nightly compound engineering; knowledge — пятый слой памяти | [Advanced] | 18336 |
| 70 | Объяснимость | 3 уровня (локальный/глобальный/контрфактический) + под аудиторию + graph-based для мультиагентности + прозрачные границы + explainability debt | [Foundation] | 18654 |
| 71 | Аварийные режимы | 6 режимов: graceful shutdown, safe mode, quarantine, system CB, dead man's switch, standby cold/warm/hot; kill switch PAUSE/STOP/KILL | [Foundation] | 18998 |
| 72 | Восстановление и целостность | Event sourcing как источник истины; idempotency_key = sha256(run+seq+tool+args); tamper evidence; RTO 4 ч, RPO 24 ч; квартальные учения | [Foundation] | 19298 |
| 73 | Миграция и bootstrap | Обратимый Strangler-Fig + теневая миграция (agreement rate) + dual-write/CDC + перенос памяти и знаний; cutover по уверенности (96 %); откат одним флагом | [Foundation] | 19661 |
| 74 | Мета-эволюция | Неизменяемое ядро + изменяемая периферия; 5 слоёв изменяемости; разделение властей; три статистических гейта; Meta-Agent — только Proposer | [Advanced] | 19985 |
| 75 | Долгосрочная устойчивость | MARE + реестр зависимостей (4 слоя с ценой выхода) + аудит стоимости выхода (7 измерений) + переносимость памяти + AI Gateway | [Scale] | 20318 |
| 76 | Этика и alignment | CAP 3 уровня (Tier 0 — абсолютные запреты = неизменяемое ядро); монитор дрейфа ценностей каждые 50 циклов; этический red teaming; двойная оценка | [Foundation] | 20707 |
| 77 | Преемственность | Digital testament + 3 типа церемонии + 4 фазы передачи + матрица делегирования полномочий + Proof-of-Descendancy + DAG наследования возможностей | [Scale] | 20988 |
| 78 | Федерация | Фрактальность Node → Cell → Region → Federation + 15 инвариантов + Universal Scalability Law + 4 режима федерации + trust domains | [Scale] | 21324 |
| 79 | Conformance-валидация | RTM + acceptance tests + CCS (Required ⊆ Supported) + IV&V через se-harness + регрессия архитектуры + ARC chaos; gap analysis → вход для 74 | [Foundation] | 21699 |
| 80 | Переход к реализации | DoD проектирования + Design Freeze + ревизионный совет (8 экспертов + арбитр) + Walking Skeleton через CI/CD → MVP → MVA; ADR только как superseded | [MVP] | 22047 |
| 81 | Выбор модели для роли | Модель по умолчанию для роли + переопределение на шаге; классификатор 12M параметров, <5 мс; ~10–15 % frontier / ~85–90 % исполнение | [Foundation] | 22339 |
| 82 | Пошаговая маршрутизация | Фазовая (explore → implement → verify) + переопределение на шаге + budget-aware (BoPO) + loop-aware; receipts только хешем | [Foundation] | 22633 |
| 83 ⚠ | API против подписки | Subscription-first + API-overflow; подписка — floor; привязка префикса (одна беседа — одна подписка); метрики cache hit и utilisation > 80 % | [Foundation] | 22962 |
| 84 ⚠ | Billing и chargeback | Showback 4–6 недель до покрытия атрибуции > 80 %, затем chargeback; API — прямое отнесение, подписки — пропорционально; unattributed < 20 % | [Scale] | 23258 |
| 85 ⚠ | Не-токеновые затраты | TCO 10+ компонентов: API ~10 %, compute ~39 %, vector DB ~7 %, storage ~6 %, networking ~3 %, monitoring 20 % (floor) | [Scale] | 23619 |
| 86 ⚠ | Оценка моделей | Golden dataset из 50 реальных провалов (binary, свежесть < 30 дней); reference + судья + человек, согласие > 85 %; CI-гейт в три уровня со статистикой | [Foundation] | 23955 |
| 87 ⚠ | Fallback моделей | Три уровня (same-provider → cross-provider → degraded) + пять стратегий; success > 95 %, failover < 5 мин | [Foundation] | 24292 |
| 88 ⚠ | Закупки | Панель 5–7 вендоров + карта функциональных слоёв; commitment/spot 40/40/20; окно цен 12–18 мес; 2–3 провайдера на возможность | [Scale] | 24656 |
| 89 ⚠ | Синтетические данные | Real + synthetic как дополнение к golden set; 50–200 сэмплов достаточно; mode collapse < 10 %; provenance 100 % | [Foundation] | 25035 |
| 90 ⚠ | Версионирование данных | Iceberg branching + zero-copy ветка на агента; неизменяемые снимки к прогонам моделей; model cards версионируются; PITR | [Foundation] | 25406 |
| 91 ⚠ | Аугментация данных | Пять типов; разнообразие важнее объёма (4× меньше данных, +22 пункта); намеренно недоопределённые инструкции | [Foundation] | 25785 |
| 92 ⚠ | Governance данных | Croissant 1.1 + DUO/ODRL; policy-aware ingestion + ABAC при извлечении (cross-tenant leakage = 0); machine-readable opt-out | [Scale] | 26141 |
| 93 ⚠ | Multi-tenancy | Гибрид shared + dedicated; RLS; изоляция namespace и profile; per-tenant ключи; tenant_id обязателен везде; 7 измерений изоляции | [Scale] | 26515 |
| 94 ⚠ | Атаки через инференс | Обнаружение извлечения модели как поведенческое; HMAC на принципала; нормализация таймингов; watermarking; detection > 90 % | [Advanced] | 26869 |
| 95 ⚠ | Threat modeling | MAESTRO + ORCHIDEAS + agentic STRIDE (ASI01–ASI10); OWASP Top 10; MITRE ATLAS; PERSIST; TITO в CI/CD | [Advanced] | 27225 |
| 96 ⚠ | Цепочка поставок | AIBOM + Sigstore + SLSA L1–L4 + Rekor; digest pinning; обнаружение rug pull; MCP sandbox | [Advanced] | 27586 |
| 97 ⚠ | SLO по задержке | Per-surface + tiered TTFT 25/50/100 мс; платформенный SLO отдельно от рабочего; error budget 99.5 % на 30 дней; cache hit ≥ 0.55 | [Scale] | 27986 |
| 98 ⚠ | Кэширование вызовов | Три уровня: prompt (префикс, −90 %), semantic (0.80–0.98), result; ключ = модель + хеш промпта + хеш схемы + клиент; TTL 5 мин–24 ч | [Foundation] | 28368 |
| 99 ⚠ | Спекулятивное выполнение | Три уровня: token, action, multi-step; COW; спекуляция только чтения L0–L2; cognitive gating; zero false accepts | [Advanced] | 28756 |
| 100 | Ложное завершение | Детерминированный верификатор по умолчанию; multi-verifier (любой голос «revise» блокирует); cross-model (строитель ≠ проверяющий); fail-closed | [Advanced] | 29121 |
| 101 | Хрупкость декомпозиции | Canonical goal envelope + invariant contract; Goal Integrity Score; Goal Restatement перед выполнением; лимит глубины; accepted-uncovered witness | [Foundation] | 29433 |
| 102 | Ловушка токенов | Стратификация контекста + token budgets per_task/per_round/per_agent + semantic GC + phase scheduling + progressive distillation + сжатие коммуникаций | [Foundation] | 29817 |
| 103 | Runtime-управление | Policy Compiler → lockfile + PDP/PEP + 5 структурных чекпоинтов + deontic policies + hooks OWASP ACS + Proof-of-Guardrail + Z3/Lean 4 | [Foundation] | 30218 |
| 104 | Надзор за Watcher | Watcher — детерминированный наблюдатель, не модель; панель судей; TEE-аттестация; публичный transparency log; false-alarm rate на каждую тревогу | [Advanced] | 30602 |
| 105 | Распределённый аудит | Event sourcing как system of record; 8 полей на событие; hash chaining + Merkle + anchoring; 6 незаменяемых ролей; Reasoning-Tree Audit | [Advanced] | 31004 |
| 106 | Атрибуция расходов | Запись per-call, per-model, per-agent; TCA-декомпозиция; обнаружение инфляции токенов (94.7 %); earned-compute (−66.8 %) | [Scale] | 31425 |
| 107 | Обновление модели | Version pinning + запрет floating alias + поведенческий контракт; canary 20–30 кейсов; статистическая значимость; три слоя контракта | [Foundation] | 31876 |
| 109 | Prompt injection в памяти | Двойной гейт: write-time (ADMIT/REJECT/QUARANTINE) + перепроверка при чтении; trust-tier invariant; HMAC-конверты; память не арбитр истины | [Advanced] | 32338 |
| 110 | Полномочия Meta-Agent | Propose-only; Containment Floor (меняет поля способностей, никогда — пределов); monotone-tighten; Intent Lineage Tracking; внешняя власть | [Advanced] | 32816 |
| 111 | Смещение при обучении | Адаптация вынесена из runtime в офлайн-процессы; OGPSA; bounded plasticity; constraint-native RL; индекс искажения reward hacking | [Research] | 33291 |
| 112 | Ошибки пользователя | Зеркало решений; Blind Spots вместо выводов без данных; раннее предсказание (AUC 0.76 по первым 10 решениям); adversarial oversight | [Advanced] | 33718 |
| 113 | Защита от копирования | Рваный край + data moat + непрерывная эволюция + owning the record; runtime decryption, watermarking; copy resistance > 90 % | [Research] | 34159 |
| 114 | Требование регулятора | Throttle/Suspend/Shutdown + рекурсивное распространение + сохранение состояния + восстановление; kill switch p99 = 17 мс; отчётность по срокам | [Advanced] | 35073 |
| 115 | Экономический пузырь | CPSO как главная метрика, margin > 30 %, ROI > 1.0; economic CB: >30 % alert, >50 % downgrade, margin <10 % escalate; five-tier degradation | [Scale] | 35581 |
| 116 | Человеческий фактор | Graded friction: L4 — подтверждение с последствиями, L5 — dual-gate + 60 с; тест бдительности раз в месяц; ротация критичных решений | [Advanced] | 36051 |
| 117 | Деградация системы | Coordination fitness + три вида дрейфа агента + Agent Stability Index (12 измерений) + Trust Margin + context rot; ASI > 0.7, обнаружение < 5 мин | [Scale] | 36560 |
| 118 | Монополизация власти | Разделение властей + запрет self-validation + потолок ≤ 49 % звеньев цепочки + Gini внимания < 0.15 как breaker + capital floor | [Advanced] | 37109 |
| 119 | Конфликты целей | IAL (интенции как runtime-объекты, обнаружение до исполнения) + Arbiter + ConPAct + ArgRE; detection > 90 %, pre-execution 100 % | [Advanced] | 37637 |
| 121 | Язык и культура | Многоязычная оценка в протоколе отчётности; MACD; cross-lingual security; разрыв < 5 pass@3; fairness coverage 100 % | [Scale] | 38139 |
| 122 | Этические дилеммы | Мульти-фреймворк (утилитаризм/деонтология/virtue) + context-aware gating; moral concession < 50 %; Responsibility Cascade; арбитраж человеком | [Advanced] | 39201 |
| 124 | После коллапса | Semantic checkpointing + cognitive rollback + durable execution; DriftGuard; глобальное восстановление связности + ANNEAL; recovery > 90 %, antifragility > 0.7 | [Advanced] | 39722 |

**Пропущенные номера:** 63 (regulatory reporting / EU AI Act Art. 13), 108, 120, 123 (black swan protocol) —
на них ссылаются другие вопросы, собственных решений в журнале нет.
