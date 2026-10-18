# Спецификация: РОЛИ, ЗАДАЧИ И ПЕРЕДАЧА РАБОТЫ (извлечено из дизайн-журнала)

**Источник:** [systema/systema_corr.md](../systema/systema_corr.md) — вопросы 7, 8, 10, 21–26, 28, 29, 33, 34, 39.
**Извлечено:** 2026-10-13, подагент-исполнитель (только чтение). Ссылки `:N` — номера строк журнала.
**Статус:** извлечение, не решения. Противоречия (14 штук) — в §10.

---

## 1. Спецификация агента (Вопрос 10, `:1855–2165`)

**Форма:** директория `agents/<agent-name>/`, а не один файл; `agent.yaml` — единственный
strict-schema файл, остальное — markdown + YAML frontmatter (`:1867–1869, :1943`).

Состав (`:1928–1942`): `agent.yaml` [REQUIRED] · `SOUL.md` [REQUIRED] · `RULES.md` · `DUTIES.md` ·
`skills/` · `tools/` · `memory/` (4 слоя) · `workflows/` · `hooks/` · `examples/` ·
`compliance/` · `evaluation/` · `config/`.

- `SOUL.md` — идентичность; без него агент «generic assistant» (`:1945`).
- `RULES.md` — жёсткие запреты, а не разрешения (`:1947`).
- `DUTIES.md` — разделение обязанностей, запрет self-authorization (`:1949`).

### 1.1. Обязательные поля `agent.yaml` — 19 (`:1952–1973`)

`name` · `version` (semver) · `description` · `role_name` · `role_type` (project/service/curator/timlid) ·
`purpose` · `scope` · `reports_to` · `permissions` (max_level L0–L5 + constraints) ·
`memory_access` (4 слоя × read/write/none) · `handoff_format` · `capability_profile` ·
`evidence_requirements` · `output_contract` · `quality_criteria` · `verification_steps` ·
`escalation_triggers` · `policy_receipt` · `lifecycle_state`
(draft / preview / committed / published / activated / deprecated / retired).
Стартовая проверка требует все 19 (`:2105`).

### 1.2. Расширения по типу роли (`:1978–2002`)

- **Тимлид:** `subordinates` · `project_memory_owner` · `team_composition_template`.
- **Куратор:** `span_of_control` 5–7 · `direction` · `review_cadence` = еженедельно.
- **Сервисный агент:** `veto_power` (none/soft/hard) · `direct_escalation` ·
  `service_category` (compliance/ethics/security/quality) · `service_scope`.
- **Исполнитель:** `task_format` · `action_templates` (L4-шаблоны).

### 1.3. Зона записи, потолок риска, расписание, память

| Что | Как задано |
|---|---|
| Зона записи | Отдельного поля нет — задаётся `memory_access` по 4 слоям; правило маршрутизации: исполнитель пишет в `personal`, тимлид переносит в `project` (`:1290`, `:7202–7206`) |
| Потолок риска | `permissions.max_level` (L0–L5) + `constraints` (ABAC: project_id, phase, time); фактический потолок — по В22 (WAL-0…WAL-3), эффективный = `min(earned, context_ceiling)` (`:5049–5054, :5082`) |
| Расписание | **Отдельного поля в спеке нет** — `schedule` встречается только в цитате ADR-0011 и в `validation-schedule.yaml` (`:1902, :2079`) |
| Память | `memory_access` + директория `memory/` + `memory inventory` в capability_profile (`:1936, :2041`) |

## 2. Жизненный цикл агента

- **Спецификация (Salesforce-style), 7 состояний** (`:2022–2031`): Draft → Preview → Committed →
  Published → Activated → Deprecated → Retired. Human-readable скрипт компилируется в
  machine-executable metadata при commit; locked через `policy_receipt` (lockfile) для PDP.
- **Создание агента, 6 стадий** (`:4662–4670`): Request & Approval → Provision → Deploy → Monitor →
  Recertify (ежеквартально) → Retire. Правило: Proposer (тимлид) ≠ Approver (Джарвис/пользователь)
  ≠ Implementer (система).
- **Onboarding flow, 7 шагов** (`:4672–4681`): Interview → Generate → Calibrate (5–10 задач
  с известными ответами) → Iterate → Benchmark → Register → Deploy.
- **Испытательный срок** (`:4683–4724`): Pilot 1–2 недели → Probation 2–4 недели (review каждого
  действия) → Full deployment. Стартовый уровень — **L0/L1, не L2** (`:4817`).
  Autonomy dial: 1 one-prompt → 2 supervised → 3 expert.
- **Earned autonomy (NeuBird)** (`:4705`): Level 0 read-only → 1 prepare (approval) → 2 low-risk
  routine (pre-cleared policy) → 3 earned autonomy; **агент не может поднять свой уровень сам**.
- **Orphan detection** (`:4736–4740`): heartbeat каждые 5 минут; 24 ч без heartbeat → orphan →
  уведомление владельцу, затем роспуск.
- **Credential rotation** (`:4742–4747`): TTL ≤ 24 ч, авто-ротация, отзыв при роспуске.

## 3. Повышение потолка риска (Вопрос 22, `:5049–5112`)

| Механизм | Значение |
|---|---|
| Уровни | WAL-0 Observe · WAL-1 Prepare (approval) · WAL-2 Execute (pre-cleared policy) · WAL-3 Autonomous; гранулярность — **per capability** |
| Асимметрия | Повышение строгое: M успехов подряд, не более +1 уровня за раз. Понижение быстрое: 3 провала, без ограничения на число уровней за раз |
| Trust score (Bayesian) | `(successes+1)/(successes+corrections+2)`; ≥0.70 → L4, ≥0.50 → L3, ≥0.30 → L2, <0.30 → L1 |
| Потолки по контексту | DIRECT_SESSION 7 · BACKGROUND_COGNITIVE 3 · SUB_AGENT 2 · OUTREACH 2; эффективный = `min(earned, ceiling)` |
| Shadow mode | 30 дней или 100 случаев (что раньше) |
| Критерии повышения | Shadow→Supervised: 90 % согласия на 30 кейсах подряд · Supervised→Auto с логом: 95 % одобрений на 50 кейсах · Auto→Full auto: 99 % точности на выборке за 90 дней |
| Триггеры понижения (6) | Смена модели → заново shadow; дрейф → минус уровень; нарушение жёсткой границы → немедленно; новый тип сценария → эскалация; 3+ провала в окне 10 → supervised; score ниже порога → понижение |
| Прочее | Trust decay при простое, cooldown 24 ч между сменами уровня, минимум 10 действий до смены; пороги **запечатаны** — агент не знает их значений |

## 4. Роспуск и переиспользование (Вопросы 23–25)

- **5 типов ухода** (`:5342–5348`): Planned Retirement · Replacement · Sudden Departure ·
  Temporary Hiatus · Gradual Fade. При инциденте порядок меняется: «сначала отзыв доступов,
  потом инвентаризация».
- **Wind-down, 9 шагов + подпись** (`:5358–5368`): уведомить → остановить приём задач →
  отозвать доступы → отключить интеграции → расторгнуть договорённости с провайдерами →
  обработать данные по политике → сохранить аудит → обновить реестр → проверка после ухода.
- **BAC (Bounded Agent Closure)** (`:5371–5376`): делегированное исполнение — погашено или инертно;
  обязательство — урегулировано; запись аудита — может остаться, если инертна; передача — может
  остаться активной после принятия преемником.
- **Succession (ALP), 4 фазы** (`:5379–5384`): Announce → Transfer → Verify → Cutover.
- **PoA** (`:5386–5393`): обнаружение зависимостей + подтверждение **нуля** остаточных доступов
  (не «запрошен отзыв»). **PoD** (`:5395–5404`): независимый от оператора, занесён в неизменяемый
  журнал, проверяем аудитором.
- **Память при роспуске** (`:5430–5435`): `personal` — архивируется, `area`/`direction`/`project` —
  остаются; `superseded` ≠ удалено.
- **Post-retirement monitoring** (`:5413–5418`): 30 дней — нет осиротевших заданий, вызовов, расходов.
- **Warm Worker Pool** (`:5677–5685`): `acquire` / `release` / `evict` / `stats`, состояние `idle`,
  вытеснение по TTL. При reuse: WAL сохраняется, pending handoff сохраняется, контекст проекта
  сбрасывается (архивируется), память области сохраняется (`:5757–5770`).
- **Reset при переиспользовании** (`:5982–6017`): режимы `compact` (по умолчанию) / `session` / `none`;
  watermark 60 % + обязательная директива «stop new work, land the plane»; сброс взводится
  в execute, срабатывает в turn_end; сворачивание с спанами → SHA-256 store, оригиналы достаются
  по `peek`/`expand`.

## 5. Передача задачи (handoff) — Вопрос 7 (`:1170–1357`)

### 5.1. Три части (`:1220–1227`)

| Часть | Правило | Содержимое |
|---|---|---|
| `H_exact` | Нулевая потеря: дословно, append-only, с хешем | Решения, ограничения, запреты, отклонённые варианты, открытые проблемы, права |
| `H_stat` | Допустима только с явной связью с задачей | Выжимки с указанием, к чему относятся |
| `H_residual` | Отбор по названному правилу, ценность на бит | Редкие сбои, контрпримеры, нерешённые ошибки |

Правила: `H_exact` **никогда не пересуммируется** — передаётся копией; «summarizing a summary is
monotonically lossy» (`:1227`); изменение статуса = новая ревизия, вытесняющая старую.

### 5.2. Обязательные поля handoff-объекта (`:1230–1245`)

`task_id` · `from` / `to` · `task_description` (**только оставшаяся работа**) · `completed_work` (H_exact,
финальные результаты) · `source_refs` (устойчивые ссылки на свидетельства) · `constraints` (H_exact) ·
`expected_output` · `risk_level` (L0–L5) · `deadline` (ISO-8601) · `evidence` (каждое утверждение →
≥1 ссылка) · `verified` (словарь baton-pass) · `deviations` · `environment` · `next_actions` ·
`acceptance_required`.

Словарь верификации (`:1214`): `passed` / `passed outside sandbox` / `not run — <причина>` /
`expected to pass, unverified`; «никогда не писать passed, когда имеется в виду
expected to pass, unverified».

### 5.3. Что запрещено терять и как проверяется

- Границы и провенанс (`:1201`): без границы получатель «либо переусердствует, либо встанет,
  спрашивая разрешения»; без провенанса ошибку не возвести к тому переходу, где она возникла.
- `source_refs` **не триммятся** — свидетельства дословно (`:1271`).
- Естественный язык — только для целей, объяснений и рисков; состояние несут структурные поля (`:1203`).
- Полный транскрипт передавать запрещено — «стоит токенов и хоронит инструкцию» (`:1201`).
- Watermark: порог 60 %, проверка каждый ход, обязательная директива остановиться (`:1248–1256`).
- Acceptance: получатель отвечает accept/reject; при reject — типизированная причина и до
  `maxAlternatives` альтернативных агентов; отправитель не считается завершённым без acceptance (`:1257–1263`).
- Тримминг детерминированный: сначала recall, потом precision; фильтр — хеш-дедупликация и явный
  список типов, **не LLM** (`:1264–1274`).
- Аудит handoff: hash-chained, поля `handoff_id`, `from`, `to`, `task_id`, `timestamp`,
  `decision`, `rejection_reason` (`:1294–1300`).
- `HANDOFF.md` (человекочитаемый), 10 секций: Goal · Done · Tasks · Files changed · Verified ·
  Deviations · Environment · Next task · Risks · Next agent (`:1276–1287`).
- Хранение: `handoff/` = схема + шаблоны + `active/` + `archive/` (`:1303–1309`).

## 6. Эскалация — Вопрос 8 (`:1361–1584`)

**9 триггеров** (`:1425–1436`): 1) уверенность ниже порога (soft, <0.7 после попытки) ·
2) действие вне scope (hard, немедленно) · 3) повторный провал после >2 попыток (hard) ·
4) неоднозначное намерение (soft) · 5) spinning: 3 неудачных цикла, превышено время или бюджет
(hard) · 6) блокер уровня спецификации (hard) · 7) требуется человеческое «да» (hard) ·
8) регуляторный запрет (hard) · 9) предотвращение каскада: 2+ агента в цепочке провалились
(hard, немедленно человеку).

**Пакет эскалации — 22 поля** (`:1438–1462`): `escalation_id` · `task_id` · `correlation_id` ·
`from`/`to` · `trigger` · `priority` (urgent/normal/low) · `triggering_question` (дословно) ·
`context` (= H_exact + H_stat + H_residual) · `human_goal_statement` · `journey_phase` ·
`failure_summary` · `partial_completion_state` · `attempted_actions_log` (**дословно, не
пересуммировать**) · `retry_history` · `emotional_signal` · `session_memory_snapshot` ·
`compliance_flags` · `recommended_next_action` (**гипотеза, не директива**) · `options`
(закрытый список: retry_with_guidance / reassign / split / abandon) · `risk_level` · `deadline` ·
`escalation_channel` · `acceptance_required`.

**Правило двух попыток** (`:1475–1482`): две попытки **решения** (два разных подхода), а не
выполнения; два повтора одного подхода — это spinning. Spinning counter: три проваленных цикла
проверки. Исключения: L5, ошибка вышестоящего, авария, регуляторный запрет, каскад.
Лестница (уровни не пропускать): Retry (max 1) → Replace (1) → Skip → Redistribute → Degrade.

**Каналы** (`:1470–1472`): normal — ответ в течение 24 ч; urgent (L4+/регуляторика/каскад) —
30-минутная пауза и независимая проверка, действие не выполняется до истечения паузы.
**Dead letter** (`:1485–1491`): триггер вне списка → файл в `/inbox/dead-letter/`, ревизия раз в неделю.
**Замыкание петли** (`:1496–1502`): решение возвращается в контекст исходного агента — он продолжает,
а не начинает заново. Целевая частота эскалаций 10–15 % (`:1505–1513`).

## 7. Прогресс проекта — Вопрос 34 (`:8888–9225`)

- **STATE.yaml — плоскость управления проекта и единственный источник правды**, это файл
  в проекте, а не память (`:9096`). Поля (`:8928–8946`): `project`, `updated`, `phase`,
  `appetite` (S/M/L), `tasks[]` (id, status, owner, notes, output, blocked_by), `next_actions[]`.
  Схема работы: агент читает файл → находит свои задачи → работает → обновляет файл;
  «file-based coordination scales better than central orchestrator» (`:8949`).
- Запреты для трекера (`:9005`): не искажать статус; не сообщать статус без обновления файлов;
  не обновлять, не прочитав текущее состояние.
- **Five-Question Checkpoint** (`:8960–8972`): что существует · что важно · что происходит
  (включая самый старый блокер) · что изменилось · что сделано (по критериям приёмки).
  Шестой вопрос на гейтах: «стоит ли это всё ещё делать?». Ответ «не знаю» — эскалация.
- Три артефакта (`:8957–8959`): `PROJECT.md` (статичный scope) · `STATE.md` (динамика: фаза,
  решения, блокеры) · `task_plan.md` (рабочие пункты).
- **Change packet** (`:8981–8984`, `:9030–9047`): пакет релизной значимости — классификация риска,
  свидетельства, утверждение, решение о раскатке. Critical (CAB + план отката): policy bundles,
  approval rules, access control. High (полный пакет): выбор модели, системные промпты, контракты
  возможностей, семантика записи в память.
- **Качественный circuit breaker** (`:9049–9055`): вопрос «застряло ли это без пользы», а не
  «превышено ли N дней»; appetite — размер, а не бюджет времени; «по умолчанию — смерть»:
  продолжение требует активного обоснованного выбора.

## 8. Фазы проекта — Вопрос 39 (`:10209–10464`)

| Фаза | Название | Цель | Условие прекращения |
|---|---|---|---|
| 0 | Intake | решить, стоит ли браться | проблема размыта и ценность не формулируется |
| 1 | Shape | снять риски до обязательств | не решается в рамках appetite или ценность < стоимости |
| 2 | Build | выдавать работающие приращения | appetite исчерпан без основной ценности |
| 3 | Review | оценить результат, снять уроки | — (всегда выполняется) |
| 4 | Close | архив, освобождение ресурсов | — (всегда выполняется) |

Побочные состояния: paused, pivoted, abandoned (abandoned требует мини-ретро «≥2 предложения»).

- **Q6 — обязательная проверка жизнеспособности на каждом переходе** (`:10299–10308`):
  «учитывая то, что мы теперь знаем, стоит ли это оставшегося appetite?» Нет → закрыть или
  вернуться в фазу 1. Убийство подтверждает человек.
- **Кто утверждает переходы** (`:10310–10316`): 0→1 человек (неявно) · 1→2 adversarial-reviewer ·
  2→3 architect-review + pentest · 3→4 человек. Ни один уровень не пропускается: QA на задачу →
  pentest на приращение → оценка на веху.
- **Доказательства** (`:10319–10326`): типы — Spec, Tests, Coverage, Syntax, Security, Business case;
  подпись HMAC, SHA-256, сверка с git-baseline; подделка, прыжок и обход блокируются.
- **Skip rule** (`:10331–10333`): задача, съевшая >20 % бюджета фазы без проверяемого результата,
  помечается BLOCKED; регрессия в фазе N останавливает последующие.
- **Ring-composition check** (`:10336–10338`): в конце сессии сверить закрывающее состояние
  с открывающей декларацией, иначе DRIFT.

## 9. Метрики (дословно, формат журнала «метрика | целевое | метод»)

| Вопрос | Метрики |
|---|---|
| 7 (handoff) | tokens per successful task < 50k · handoff success > 90 % · acceptance > 95 % · context collapse 0 · compression 10–50× · time to handoff < 5 мин · audit chain 100 % VERIFIED |
| 8 (эскалация) | escalation rate 10–15 % · time to resolution < 24 ч (normal) / < 30 мин (urgent) · dead letter < 5 % · loop closure > 90 % |
| 10 (спека) | schema validation 100 % · critical-error rate < 0.02 · evidence-linked claims 100 % · evaluation coverage > 80 % · compliance 100 % · spec drift 0 |
| 21 (онбординг) | calibration success > 90 % · probation graduation > 80 % · orphan rate 0 · credential TTL ≤ 24 ч · progressive disclosure savings > 50 % токенов |
| 22 (потолок) | shadow agreement > 90 % · supervised approval > 95 % · full auto accuracy > 99 % · demotion rate > 0 (иначе механизм не работает) · isolation compliance 100 % |
| 23 (роспуск) | orphan rate 0 · PoA 100 % · PoD 100 % · knowledge preservation > 70 % · post-retirement issues 0 · credential TTL 0 |
| 24 (переиспользование) | reuse rate 70–90 % · cold start < 10 % · overhead reduction > 85 % · pollution rate < 33.3 % |
| 25 (сброс) | token reduction 0.60× · dollar reduction 0.55× · trimming 20–86 % · session bloat 0 · no prior history leaks 100 % · cache hit > 80 % |
| 26 (здоровье) | detection precision > 70 % · recall > 63 % · recovery > 10 % · crew health > 0.65 · context rot threshold 60–70 % · SLO adherence 100 % |
| 28 (онбординг в проект) | time to first output < 1 день · time to first merged change < 1 неделя · knowledge completeness > 80 % · context pack freshness < 1 неделя · verification pass > 90 % |
| 29 (failover) | MTTD < 90 сек · MTTR < 5 мин (hot) / < 30 мин (warm) · orphaned groups 0 · false positives < 5 % · failover success 100 % |
| 33 (тимлид) | election accuracy > 80 % · probation success > 80 % · succession context preservation > 90 % · failover 100 % |
| 34 (прогресс) | checkpoint adherence 100 % · change packet rate 100 % · circuit breaker accuracy > 80 % · stalled detection < 1 неделя · STATE.yaml freshness < 1 сессия |
| 39 (фазы) | Q6 pass > 80 % · kill rate 10–20 % · evidence-scored 100 % · phase-barrier violations 0 · ring-composition failures 0 · circuit breaker < 1/месяц |

## 10. Противоречия: требуют решения пользователя

**Признаны самим журналом:**

1. **В21:** 12 расхождений исходной версии с практикой (`:4534–4546`) — включая «тимлид создаёт
   агента» против разделения властей Proposer ≠ Approver ≠ Implementer.
2. **В26:** 15 расхождений (`:6253–6267`) — health-метрики агента пересекаются с метриками
   инструментов В59; baseline против «запечатанных» порогов В22; «degraded → флаг» против
   требования влиять на WAL-уровень; relational monitoring «при 50+ проектах» против «с первого дня».
3. **В29:** 15 расхождений (`:7380–7394`) — «failover — не понижение» против WAL;
   статический заместитель против Warm Worker Pool; «30 минут недоступности» против degraded
   режима В14 (норма 0–3 ч); обратный handoff против HANDOFF.md В7.

**Не сведены между вопросами:**

4. **Шкала потолка риска:** В10 задаёт `max_level` в L0–L5, В22 — WAL-0…WAL-3; соответствие не задано.
5. **Три нотации автономии:** В21 (earned level 0–3 и dial 1→2→3), В22 (WAL-0…3), В33 (подпись «L3»).
6. **Жизненный цикл:** 7 состояний (В10) против 6 стадий (В21) против отвергнутых 8 состояний;
   не сведены.
7. **Heartbeat:** 5 минут с порогом 24 ч (В21) против интервала 5–30 сек с таймаутом 15–90 сек (В29).
8. **STATE.yaml:** «не память, а файл в проекте» (В34, `:9096`) против «project-слой содержит
   STATE.yaml + task_plan.md» (`:9095`).
9. **Фазы:** «4 фазы + side states» против таблицы Phase 0–4 (пять значений).
10. **Hard checkpoints:** правило «5» против 6 строк со статусом HARD в таблице переходов.
11. **REQUIRED-файлы:** в дереве помечены только `agent.yaml` и `SOUL.md`, а сводка изменений
    объявляет обязательными ещё `RULES.md` и `DUTIES.md`.
12. **Orphan-роспуск:** «уведомить и распустить» (В21) против обязательного полного wind-down
    checklist, BAC и PoD (В23).
13. **Reuse против fresh:** В24 сохраняет WAL и pending handoff при reuse; В25 объявляет fresh
    контекст по умолчанию; граница не сведена.
14. **Запрет «тимлид не на свой проект»** (В33) против «reuse the reviewer» (В24) как общей практики.

## 11. Что из этого уже реализовано в скелете

| Требование | Где | Состояние |
|---|---|---|
| Журнал handoff/эскалации (hash-chained, append-only) | [`src/systema/journal/log.py`](../system/src/systema/journal/log.py) | журнал есть, конкретные схемы handoff/эскалации — нет |
| Право на действие выдаётся шлюзом, а не агентом | [`src/systema/policy/gate.py`](../system/src/systema/policy/gate.py) | сделано: `default_deny`, потолки, бюджеты |
| Ложные отказы как отдельная метрика | `policy.gate.Executor.metrics` | сделано |
| STATE.yaml, five-question checkpoint, фазы | — | не сделано |
| Handoff H_exact/H_stat/H_residual, acceptance | — | не сделано (нужен для сквозного среза) |
