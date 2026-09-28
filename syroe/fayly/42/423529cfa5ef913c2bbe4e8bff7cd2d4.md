Описание продукта: Я создаю большую систему по аналитике видео контента и предсказанию его популярности на основе алгоритмов машинного обучения. Система делится на 2 глобальные части: 1 - Сайт, 2 - MLService. Аудитория: система нацелена на обширную аудиторию. 1 - пользователи, которые не знаю ничего о аналитике, машинном обучении, а просто хотят загрузить свое видео и получить прогноз популярности и рекомендации по улучшению, 2 - Профессиональные аналитики, которым помимо простого предсказания и рекомендаций нужно знать конкретные причины роста, факторы, которые на это влияют, видеть графики и распределения значений с описаниями и подсказками, анализировать результаты между видео и тд.

По частям:
1. Сайт. Сайт делится на 3 части: frontend, backend и базы данных. На данный момент каркас и базовая реализация есть только у backend. В планах реализации: Должна быть красивая первая страница с описанием всей системы, того как это работает и чем полезно. Далее должен быть ЛК с персональными конфигурациями (Конфигурации - будут описаны ниже как часть MLService), результатами обработки, настройками, билингом, мониторингом. Далее должна быть страница прогресса обработки видео, на котором должны динамически идти этапы обработки, показываться какие то настоящие кадры из видео, например боксы найденых людей или предметов и тд. (Далее будет подробно описано про визуализации), что бы человек понимал на каком конкретно этапе обработка и что конкретно происходит. Далее  страница результатов на которой должны быть вкладки всех компонентов (Про компоненты будет рассказано дальше как часть MLService) и всевозможные визуализации, интерпретации, графики, распределения по результатам каждого компонента, а также взаимосвязи между ними, а также описания, пояснения, рекомендации. Также на этой странице должно быть реализовано сравнения между видео (как между видео самого пользователя, так и других похожих видео для построения более качественных рекомендаций и аналитики). Backend представляет собой обычный как у любого другого сайта, единственное что MLService это отдельный сервис, который работает обособленно и имеет свое API и между ними должна быть четкая связь, контракты, схемы, версии и тд. MLService почти реализован и имеет высший приоритет, а backend как бы подстраивается под него и уже реализован базовый каркас и контракты.
2. MLService. Делится на 3 части: Fetcher, DataProcessor, Models (дополнительно можно выделить собственные базы данных, хранилища, мониторинги и тд. пока не думал над этим). 
2.1. Fetcher. Представляет собой небольшой сервис, который занимается сбором данных. На сайте при запуске анализа должно быть 2 варианта. 1 - пользователь сам загружает видео напрямую на сайт, а также заполняет поля: заголовок, описания, тэги и тд. Далее это все передается уже на обработку, в таком случае Fetcher либо вообще не используется, либо берет на себя базовую валидацию введенных данных (не думал над этим). 2 - пользователь указывает ссылку на видео, например на youtube, twich, tiktok и др. (пока реализованно только youtube). Далее запрос уходит в MLService -> Fetcher, который собирает всю информацию о видео (заголовок, описание, комментарии, данные канала и тд.) и скачивает его либо в локально хранилище, либо в удаленное хранилище. Далее структурирует всю информацию и передает в DataProcessor.
2.2. DataProcessor. Самая большая часть системы. Делится на 3 больших процессора: TextProcessor, AudioProcessor, VisualProcessor. Также можно выделить дополнительные сервисы: Segmenter, Embedding Service, Triton, DP_Models.
2.2.1. TextProcessor. Процессор который занимается обработкой текстовой информации. Имеет 22 компонента (Например asr_text_proxy_audio_features, embedding_source_id_extractor, description_embedder, speaker_turn_embeddings_aggregator). Каждый компонент представляет отдельный микро-сервис и фактически может разрабатываться отдельно, имеет свою документацию, контракты, правила, взаимосвязи с модулями системы и другими компонентами (это относится ко всем компонентам всех процессоров). Важно понимать что каждый процессор занимается только своей модальностью, например у TextProcessor есть компонент который отвечает за анализ ASR, но при этом процессор сам не извлекает и вообще не имеет доступа к аудио, а запрашивает ASR у AudioProcessor. Подобных взаимосвязей много во всем DataProcessor. Также процессор имеет доступ к глобальным Embedding Service, Triton, DP_Models.
2.2.2. AudioProcessor. Глобально не сильно отличается от TextProcessor. Имеет 24 компонента (например speaker_diarization_extractor). Также имеет связи с другими процессорами. Сильно зависит от Segmenter, так как от него он получает сегменты аудио по которым он работает. Сегментация аудио делается для того что бы компоненты процессора могли выдавать последовательность сигналов, которые затем пойдут в модели Transformer.
2.2.3. VisualProcessor. Самый большой процессор. Состоит из 29 компонентов. Также зависит от Segmenter, так как от него получает выборку кадров по которым работают компоненты, причем выборка может быть персональной для каждого компонента. 
2.2.4. DP_Models. Глобальный ModelManager. Включает в себя все веса, для всех моделей всех процессоров, имеет конфигурации для моделей, spec, провайдеры и тд. Сами компоненты процессоров не используют модели напрямую а обращаются к менеджеру указываю нужную модель и конфигурацию. На данный момент ModelManager не является отдельным сервисом и не имеет фактического API. А компоненты просто импортируют его функции.
2.2.5. Embedding Service. На данный момент относится больше к VisualProcessor хоть и вынесен в отдельный глобальный сервис. Его использую как базу Embeddings, такие компоненты как brand_semantics, car_semantics и другие семантические компоненты. Их суть в том что через Embeddings они определяют конкретные объекты на кадрах видео, например брэнды или конкретные машины, сравнивая эмбединги с заготовленной и размеченной базой. Также есть компоненты которые через теже эмбединги опредялют мультики, аниме и тд.
2.2.6. Triton. По факту просто хранит скоопелированные onnx модели и спеки для запуска Triton Service + имеет провайдер. Это отдельный микро-сервис который запускается на отдельном порту. Не все модели переведены на Triton (там стоят штуки 4: CLIP, raft, midas, place_365 и все для VisualProcessor)
2.3. Models. Делится на несколько частей, которые могут быть как отдельными моделями так и частями одной модели, пока окончательно не решено, как убдет лучше для этой задачи. Encoders. Делится на AudioEncoder и VisualEncoder. Это первая часть моделей, которая принимает большой вектор от 2 процессоров (каждый процессор отдает своему энкодеру) и сжимет его до единой размерности. Далее каждый выход из Encoder идет в свой трансформер (Это может быть не отдельная модель а просто голова, как часть одной модели, то есть AudioHead и VisualHead). Далее идут какие то преобразования. Далее идет Fusion слой, который делает совмещения выходов AudioHead и VisualHead с результатом TextProcessor. Далее этот выход идет в последний слой (модель) которая уже выдает прогноз популярности. Таргет - кол-во просмотров/лайков (или мульти-таргет) через 2 недели и 3 недели, то есть всего 4 значения.


Вопросы:

1️⃣ Продукт и позиционирование
1.1 Аудитория
Планируешь ли ты разделять интерфейс для новичков и аналитиков или это будет один интерфейс с режимами (Basic / Pro)?
**Ответ**: Да, точно планирую разделять, но как пока не знаю. Функционал большой и далеко не для всех нужен. Нужны твои советы.

Будет ли онбординг с определением уровня пользователя?
**Ответ**: Не, знаю что это.

Основная целевая аудитория — YouTube-креаторы или мультиплатформенность сразу?
**Ответ**: v1 поддерживает только YouTube. `platform_id="youtube"` фиксируется как каноничное значение до расширения на другие платформы.
**Источник**: `DataProcessor/docs/contracts/PRODUCT_CONTRACT.md:7-8`

1.2 Бизнес-модель
Freemium? Подписка? Оплата за видео?
**Ответ**: У каждого компонента есть множество извлекаемых параметров. Каждый параметр затрачивает разное кол-во ресурсов системы и времени на 1 еденицу обработки (это может быть блок текста, кадр или аудио сегмент, далее е.о). Взависимости от сложности извлечения параметра будет выставлена цена на сайте за 1 е.о. На сайте будет конфигурация с >100 параметрами и можно будет выбрать под свои задачи. Параметр (или группа параметров) - это просто фичи, которые получаются ны выходе компонента по 1 е.о., например есть компонент object_detection и у него 1 параметр - нахождение объектов на видео. Это не самый легкий процесс, которые использует модели YOLO для обработки. При разработке, а точнее при проведении бенчмарков будет выявлена фактическая сложность получения данного параметра в зависимости от разрешения кадра, в расчете на 1 кадр, например это будет 1 рубль/кадр. По этому принципу будут строиться бизнес модель для большинства компонентов, но будут такие у которых 1 е.о, это например все аудио (как в asr_extractor в AudioProcessor) и тут нужно будет как то подругому рассчитывать, например за 1 секунду.

Будет ли ограничение по длительности видео?
**Ответ**: Да, ограничения есть. Минимум 5 секунд, максимум 20 минут. Если < 5 секунд → ошибка "Видео слишком короткое (минимум 5 секунд)". Если > 20 минут → ошибка "Видео слишком длинное (максимум 20 минут)".
**Источник**: `DataProcessor/docs/contracts/PRODUCT_CONTRACT.md:43-45`

Планируется ли B2B-версия (для агентств / студий / продакшенов)?
**Ответ**: Не знаю что это такое

2️⃣ Архитектура взаимодействия Site ↔ MLService
2.1 Контракты
Используешь ли ты OpenAPI/Swagger для версионирования?
**Ответ**: FastAPI используется (автоматически генерирует OpenAPI/Swagger), но явного упоминания версионирования через OpenAPI не найдено. Есть версионирование через `schema_version`, `feature_schema_version`, `model_signature`.
**Источник**: `backend/docs/API.md:1-63`, `Models/docs/contracts/MODEL_SYSTEM_RULES.md:13-16`

Есть ли у тебя versioning схемы для:
результатов компонентов
**Ответ**: Да, есть `schema_version` для NPZ артефактов компонентов. Каждый компонент имеет свою версию схемы (например, `core_clip_npz_v2`, `core_depth_midas_npz_v3`).
**Источник**: `DataProcessor/docs/contracts/ARTIFACTS_AND_SCHEMAS.md:31-33`, `DataProcessor/docs/MAIN_INDEX.md:119-171`

финальной модели
**Ответ**: Да, есть версионирование моделей через `model_signature`, `model_version`, `weights_digest`, `model_interface_version`. Версии фиксируются в `models_used[]` в артефактах.
**Источник**: `Models/docs/contracts/MODEL_SYSTEM_RULES.md:21-32`, `Models/docs/contracts/MODEL_INTERFACE_V2.md:12`

feature schema
**Ответ**: Да, есть `feature_schema_version` для схемы фичей для моделей прогноза (training/inference). Режимы: `v0` (движущийся, допускаются изменения), `v1` (frozen после baseline dataset collection).
**Источник**: `Models/docs/contracts/MODEL_SYSTEM_RULES.md:14`, `Models/docs/contracts/BASELINE_MODEL.md:50-52`

2.2 Оркестрация
Кто оркестрирует пайплайн — MLService или backend сайта?
**Ответ**: Оркестрация на уровне DataProcessor. Backend создает run через `POST /api/runs`, ставит задачу в очередь (Celery), DataProcessor worker подписывается и выполняет обработку.
**Источник**: `DataProcessor/docs/contracts/ORCHESTRATION_AND_CACHING.md:23-54`, `backend/docs/reference/backend_qna_contracts.md:90-115`, `DataProcessor/docs/architecture/PRODUCTION_ARCHITECTURE.md:52-65`

Есть ли централизованный Job Manager?
**Ответ**: Используется Celery для управления задачами. Backend кладет задачи в очередь (Redis), DataProcessor worker подписывается. Гранулярность: 1 видео = 1 job (внутри DataProcessor DAG).
**Источник**: `backend/docs/reference/backend_qna_contracts.md:108-115`, `DataProcessor/dp_queue/celery_app.py:15-30`, `DataProcessor/dp_queue/tasks.py:25-43`

Планируется ли очередь (Kafka / Redis / RabbitMQ)?
**Ответ**: Да, используется Celery + Redis для MVP. В документации упоминается возможность использования RabbitMQ/Kafka для будущего масштабирования.
**Источник**: `backend/docs/reference/backend_qna_contracts.md:108-111`, `DataProcessor/docs/architecture/PRODUCTION_ARCHITECTURE.md:55-57`, `DataProcessor/embedding_service/README.md:344-350`

3️⃣ Fetcher
3.1 Источники
Помимо YouTube, какие платформы приоритетны?
**Ответ**: v1 поддерживает только YouTube. Другие платформы (Twitch, TikTok) упоминаются в описании продукта, но не реализованы. `platform_id="youtube"` фиксируется как каноничное значение до расширения.
**Источник**: `DataProcessor/docs/contracts/PRODUCT_CONTRACT.md:7-8`, `doc.md:6`

Планируется ли сбор метрик динамики (например просмотры через 1 день, 3 дня, 7 дней)?
**Ответ**: Модель предсказывает просмотры/лайки через 7d (masked), 14d, 21d. В `snapshot_0` есть поля `views_0`, `likes_0`, `comments_0`, `channel_subscribers_0`, `channel_total_views_0`, `channel_total_videos_0`. Сбор метрик динамики через 1/3/7 дней не упоминается явно.
**Источник**: `Models/docs/contracts/TARGETS_SPLITS_METRICS.md:19-22`, `Models/docs/contracts/TARGETS_SPLITS_METRICS.md:10-17`

3.2 Данные канала
Используешь ли ты фичи канала (подписчики, средние просмотры, возраст канала)?
**Ответ**: Да, используются фичи канала в `snapshot_0`: `channel_subscribers_0`, `channel_total_views_0`, `channel_total_videos_0`. Также используется `channel_id` для channel-group split при обучении.
**Источник**: `Models/docs/contracts/TARGETS_SPLITS_METRICS.md:10-17`, `Models/docs/contracts/TARGETS_SPLITS_METRICS.md:36-37`

Будет ли модель учитывать "силу канала"?
**Ответ**: Да, косвенно через фичи канала (`channel_subscribers_0`, `channel_total_views_0`, `channel_total_videos_0`) в `snapshot_0`, которые используются как вход модели. Явного упоминания "силы канала" как отдельной метрики не найдено.
**Источник**: `Models/docs/contracts/TARGETS_SPLITS_METRICS.md:10-17`

4️⃣ DataProcessor (критически важный блок)
4.1 Архитектура компонентов

Каждый компонент — это отдельный микросервис (контейнер) или просто логическая абстракция?
**Ответ**: Компоненты — это логические абстракции, которые могут разрабатываться отдельно. Каждый компонент представляет отдельный микро-сервис по факту, но в текущей реализации они запускаются через orchestrator DataProcessor (subprocess или in-process). Компоненты не являются отдельными контейнерами, но могут быть вынесены в отдельные сервисы в будущем.
**Источник**: `doc.md:8-10`, `DataProcessor/docs/contracts/PER_COMPONENT.md:70-73`, `DataProcessor/docs/MAIN_INDEX.md:305-307`

Как происходит коммуникация:
REST?
**Ответ**: Нет, компоненты не используют REST для коммуникации между собой. Они используют shared storage (NPZ артефакты в result_store) и orchestrator координирует выполнение.
**Источник**: `DataProcessor/docs/contracts/ARTIFACTS_AND_SCHEMAS.md:31-33`, `DataProcessor/docs/contracts/ORCHESTRATION_AND_CACHING.md:23-54`

gRPC?
**Ответ**: Нет, gRPC не используется для коммуникации между компонентами DataProcessor.
**Источник**: Не найдено упоминаний gRPC для компонентов

через shared storage?
**Ответ**: Да, компоненты коммуницируют через shared storage (NPZ артефакты в result_store per-run). NPZ является source-of-truth. Компоненты читают артефакты upstream компонентов и пишут свои артефакты.
**Источник**: `DataProcessor/docs/contracts/ARTIFACTS_AND_SCHEMAS.md:31-33`, `DataProcessor/docs/contracts/CONTRACTS_OVERVIEW.md:26-28`

Есть ли DAG зависимостей между компонентами?
**Ответ**: Да, есть DAG зависимостей. Описывается декларативно в `component_graph.yaml` (source-of-truth). Используется для определения порядка выполнения компонентов и параллелизма. Есть `depends_on_components`, `soft_dependencies`, `wait_on_checkpoints`.
**Источник**: `DataProcessor/docs/reference/component_graph.yaml:296`, `DataProcessor/dag/component_graph.py:329-332`, `DataProcessor/docs/MAIN_INDEX.md:295-298`

4.2 Segmenter
Очень важный момент.

Сегментация:
фиксированная длина?
**Ответ**: Нет, не фиксированная. Используется адаптивная сегментация. Для коротких видео (до 30 сек) может быть отказ от сегментации или фиксированное разбиение на 2-3 равных отрезка. Для длинных видео используется адаптивный порог по времени (min_scene_duration, max_scene_duration).
**Источник**: `DataProcessor/Segmenter/README.md:91-95`, `DataProcessor/Segmenter/README.md:103-106`

адаптивная по сценам?
**Ответ**: Да, используется адаптивная сегментация по сценам. Уровень 2: семантическая агрегация в сцены (Scene Detection) - объединение визуально и смыслово связанных shots в сцены через визуально-временную кластеризацию. Адаптивный порог по времени: сцена не может быть короче X секунд и длиннее Y секунд.
**Источник**: `DataProcessor/Segmenter/README.md:78-95`

по речи?
**Ответ**: Да, используется аудио-подсказки для детекции сцен. Резкая смена звуковой дорожки (тишина -> музыка, речь -> музыка) — сильный маркер смены сцены. Метод: вычисление энергии аудио (RMS) или вектор аудио-фичей и поиск точек резкого изменения.
**Источник**: `DataProcessor/Segmenter/README.md:97-101`

Сегментация аудио и видео синхронизирована?
**Ответ**: Да, синхронизирована через time-axis. Видео и аудио живут на общей временной оси. Segmenter пишет `union_timestamps_sec` (sec) для каждого union-кадра. Для аудио хранится `audio.wav` + `audio/metadata.json` (duration/sample_rate/total_samples). Компоненты используют time-domain для синхронизации: `t_frame = union_timestamps_sec[frame_idx]`.
**Источник**: `DataProcessor/Segmenter/README.md:27-35`, `DataProcessor/docs/contracts/SEGMENTER_CONTRACT.md:41-42`

Используешь ли ты shot detection?
**Ответ**: Да, используется shot detection. Уровень 1: быстрая грубая сегментация (Shot Boundary Detection) - поиск технических склеек (резкие смены кадра) и плавных переходов (dissolve, fade). Методы: Histogram-based, Pixel-based (SSD), Pre-trained модели (легкие CNN на ResNet). Результат: видео разбивается на shots (планы).
**Источник**: `DataProcessor/Segmenter/README.md:61-74`, `DataProcessor/VisualProcessor/modules/cut_detection/cut_detection.py:1150-1302`

4.3 Embedding Service
Очень интересный блок.

Где хранятся embeddings?
**Ответ**: Embeddings хранятся в двух местах: PostgreSQL с pgvector (для метаданных и векторного поиска) и FAISS индексы (для быстрого векторного поиска). Каждая модель имеет отдельный FAISS индекс: `{model_name}.faiss` и `{model_name}_ids.npy` (соответствие индексов к UUID объектов).
**Источник**: `DataProcessor/embedding_service/README.md:228-260`, `DataProcessor/embedding_service/core/database/faiss_index.py:1-106`, `DataProcessor/embedding_service/core/database/postgres.py:23-271`

FAISS?
**Ответ**: Да, используется FAISS для быстрого векторного поиска. Каждая модель имеет отдельный FAISS индекс. Используется `IndexFlatIP` для cosine similarity на нормализованных векторах. Индексы создаются автоматически при добавлении объектов и сохраняются на диск для персистентности.
**Источник**: `DataProcessor/embedding_service/core/database/faiss_index.py:1-106`, `DataProcessor/embedding_service/README.md:255-260`

Milvus?
**Ответ**: Нет, Milvus не используется. Используется PostgreSQL с pgvector + FAISS.
**Источник**: Не найдено упоминаний Milvus

просто numpy?
**Ответ**: Частично. Embeddings хранятся как numpy массивы в памяти, но для персистентности используются PostgreSQL (pgvector) и FAISS индексы на диске.
**Источник**: `DataProcessor/embedding_service/core/database/faiss_index.py:53-80`

Есть ли версия embedding базы?
**Ответ**: Да, есть версионирование через `db_digest` для semantic heads (brands, cars, places). В контрактах semantic heads упоминается `db_digest` для детерминированного label-space и версионирования базы.
**Источник**: `DataProcessor/docs/models_docs/SCHEMA_SEMANTIC_HEADS_NPZ.md:237`, `DataProcessor/docs/models_docs/SEMANTIC_HEADS_CONTRACTS_QA.md:232-234`

Как обновляется база брендов / машин?
**Ответ**: Базы собираются offline (no-network в runtime). Формат пакетов, инструкции по сборке баз для brands (v1=500), cars (make/model/segment/body_type/price buckets), celebs (v1=500), places/landmarks описаны в гайде. Preflight проверки перед запуском пайплайна. Обновление через пересборку offline баз.
**Источник**: `DataProcessor/docs/models_docs/SEMANTIC_BASES_BUILD_GUIDE.md:241-243`, `DataProcessor/embedding_service/core/managers/base_manager.py:261-328`

4.4 DP_Models (ModelManager)
Почему он пока не сервис?
**Ответ**: ModelManager не является отдельным сервисом и не имеет фактического API. Компоненты просто импортируют его функции. В планах есть возможность вынести в отдельный сервис, но на данный момент это in-process модуль.
**Источник**: `doc.md:11`, `DataProcessor/dp_models/manager.py:174-217`, `DataProcessor/docs/models_docs/MODEL_MANAGER_PLAN.md:1-38`

Планируется ли:
Model registry?
**Ответ**: Да, есть ModelCatalog в ModelManager. Каталог спецификаций моделей в `spec_catalog/` (audio, text, vision). ModelCatalog загружается из директории и используется для получения ModelSpec по имени или роли.
**Источник**: `DataProcessor/dp_models/manager.py:191`, `DataProcessor/dp_models/MAIN_INDEX.md:266-269`, `DataProcessor/docs/models_docs/MODEL_MANAGER_PLAN.md:18-29`

Versioning?
**Ответ**: Да, есть строгое версионирование моделей через `model_version`, `weights_digest`, `model_signature`. Версии фиксируются в `models_used[]` в артефактах. Правило: апдейт одной модели не требует bump `dataprocessor_version`. Версии моделей живут отдельно и входят в `model_signature`.
**Источник**: `Models/docs/contracts/MODEL_SYSTEM_RULES.md:15-23`, `DataProcessor/docs/models_docs/MODEL_MANAGER_Q.md:192-194`

Canary rollout моделей?
**Ответ**: В документации упоминается возможность держать старую и новую версии моделей параллельно, переключение происходит на уровне `triton_models.yaml`/DB профиля (A/B по пользователям/профилям возможно). После прогрева/валидации новую версию делают default, старую — оставляют на "grace period".
**Источник**: `Models/docs/source_migrations/MODELS_Q.md:96-99`

4.5 Triton
Используешь ли batching?
**Ответ**: Да, используется batching. Baseline GPU модели могут быть batch-enabled (`max_batch_size > 0`). Batch size контролируется scheduler через `--batch-size` или через DynamicBatch. Есть cross-video batching при одинаковых условиях (component_name, model_signature, preprocessing параметры, resolution bucket).
**Источник**: `DataProcessor/docs/models_docs/BASELINE_GPU_BRANCHES.md:13`, `DynamicBatch/docs/DynamicBatching_Q_A.md:224-239`, `DataProcessor/VisualProcessor/core/model_process/core_object_detections/README.md:152-186`

Есть ли dynamic batching?
**Ответ**: Да, планируется dynamic batching. В контракте baseline GPU упоминается: "Dynamic batching (production): модели могут быть batch-enabled (`max_batch_size > 0`) и batch подбирается верхним scheduler (DynamicBatching)". Для MVP используется fixed batch=1 для некоторых моделей, но планируется динамический батчинг.
**Источник**: `DataProcessor/docs/models_docs/BASELINE_GPU_BRANCHES.md:13`, `DynamicBatch/dynamicbatch/plan.py:40-143`

GPU shared между Triton и моделями fusion?
**Ответ**: Не найдено явного упоминания. Triton развертывается как отдельный сервер/контейнер (не на worker'ах). Несколько реплик Triton для высокой нагрузки (load balancing). GPU может быть shared, но явного описания политики sharing между Triton и fusion моделями не найдено.
**Источник**: `DataProcessor/docs/architecture/PRODUCTION_ARCHITECTURE.md:109-112`

5️⃣ ML-архитектура
Это самый важный блок.
5.1 Целевая переменная

Ты предсказываешь:
просмотры
**Ответ**: Да, предсказываются просмотры (`views`).
**Источник**: `Models/docs/contracts/TARGETS_SPLITS_METRICS.md:21`, `Models/docs/contracts/MODEL_CONTRACTS_V1.md:32`

лайки
**Ответ**: Да, предсказываются лайки (`likes`).
**Источник**: `Models/docs/contracts/TARGETS_SPLITS_METRICS.md:21`, `Models/docs/contracts/MODEL_CONTRACTS_V1.md:32`

через 2 и 3 недели
**Ответ**: Да, горизонты: 7d (masked), 14d, 21d. То есть через 1, 2 и 3 недели (7 дней = 1 неделя, 14 дней = 2 недели, 21 день = 3 недели).
**Источник**: `Models/docs/contracts/TARGETS_SPLITS_METRICS.md:22`, `Models/docs/contracts/MODEL_CONTRACTS_V1.md:34-36`, `doc.md:14`

Вопросы:
Логарифмируешь ли target?
**Ответ**: Да, используется функция `log1p(delta)`: \(y_h = \log(1 + \Delta x_h)\), где \(\Delta x_h = x_h - x_0\) (дельта относительно snapshot_0).
**Источник**: `Models/docs/contracts/TARGETS_SPLITS_METRICS.md:24-26`, `Models/docs/contracts/MODEL_CONTRACTS_V1.md:38`

Нормализуешь ли по категории?
**Ответ**: Не найдено явного упоминания нормализации по категории. Используется log1p нормализация дельты. В метриках упоминается Spearman по 8 age buckets для устойчивости качества, но не нормализация по категории.
**Источник**: `Models/docs/contracts/TARGETS_SPLITS_METRICS.md:24-26`, `Models/docs/contracts/TARGETS_SPLITS_METRICS.md:44`

Учитываешь ли baseline канала?
**Ответ**: Да, учитывается через фичи канала в `snapshot_0`: `channel_subscribers_0`, `channel_total_views_0`, `channel_total_videos_0`. Также используется `channel_id` для channel-group split при обучении (hybrid time-split по `publishedAt` + channel-group split по `channel_id`).
**Источник**: `Models/docs/contracts/TARGETS_SPLITS_METRICS.md:10-17`, `Models/docs/contracts/TARGETS_SPLITS_METRICS.md:36-37`

5.2 Баланс данных
100к видео — как распределены категории?
**Ответ**: Примерно равномерно, то есть по каждой категории около 5600 видео. Но обязательно перед обучением нужно будет все перепроверять.

Есть ли дисбаланс?
**Ответ**: Вроде нет, так как при сборе были динамические фильтрации как раз для того что бы его избежать, но всеравно нужно все перепроверять.

Используешь ли stratified split?
**Ответ**: Нет, используется hybrid split: time-split по `publishedAt` + channel-group split по `channel_id`. Stratified split не упоминается.
**Источник**: `Models/docs/contracts/TARGETS_SPLITS_METRICS.md:35-37`, `Models/docs/contracts/MODEL_CONTRACTS_V1.md:44`

5.3 Архитектура модели
Очень важно понять:
AudioEncoder и VisualEncoder — это MLP?
**Ответ**: Нет, не просто MLP. Encoders приводят variable-length последовательности к fixed-budget представлению. VisualEncoder и AudioEncoder могут быть trainable (v1) или deterministic (v0). Используется learnable pooling для превращения M локальных токенов в фиксированное число K summary-токенов.
**Источник**: `Models/docs/contracts/ENCODER_CONTRACT.md:1-34`, `docs/MAIN_README.md:55-59`, `Models/docs/contracts/FEATURE_ENCODER_CONTRACT.md:1-33`

Или transformer?
**Ответ**: Encoders сами не являются transformer'ами, но их выходы идут в transformer heads (AudioHead, VisualHead). После pooling все модальности приводятся к единой форме [K, D], затем идет Fusion-transformer.
**Источник**: `Models/docs/contracts/V1_TRANSFORMER_MODEL.md:9-13`, `docs/MAIN_README.md:63-67`

Или pooling поверх последовательностей?
**Ответ**: Да, используется pooling. Learnable Pooling превращает M локальных токенов в фиксированное число K summary-токенов. Для коротких видео важно использовать маску, чтобы пустые summary-токены не портили внимание.
**Источник**: `docs/MAIN_README.md:55-61`, `Models/docs/contracts/ENCODER_CONTRACT.md:29-34`

Transformer в AudioHead:
self-attention по времени?
**Ответ**: Да, в v1 используется transformer с self-attention. AudioHead и VisualHead - это transformer heads, которые обрабатывают последовательности токенов от encoders. Fusion-transformer использует cross-attention между модальностями.
**Источник**: `Models/docs/contracts/V1_TRANSFORMER_MODEL.md:9-17`, `Models/v1/model/v1_skeleton.py:88-160`

есть ли positional encoding?
**Ответ**: Да, есть time encoding. Каждый token получает time embedding: `time_pos_emb = MLP(t_center / duration_sec)`, где `t_center` берется из `summary_times_s`. В коде используется `time_mlp` для time embeddings.
**Источник**: `Models/docs/contracts/V1_TRANSFORMER_MODEL.md:20-23`, `Models/v1/model/v1_skeleton.py:139-143`

Fusion:
concat + MLP?
**Ответ**: Нет, не используется простой concat + MLP. Используется cross-attention fusion (качественнее и устойчивее, чем "concat → 1 transformer" при тех же бюджетах).
**Источник**: `Models/docs/contracts/V1_TRANSFORMER_MODEL.md:16-17`, `Models/docs/QA/CONTRACTS_QA.md:243-250`

cross-attention?
**Ответ**: Да, используется cross-attention fusion. Fusion-transformer: взаимодействие video/audio/text/metadata/temporal через cross-attention.
**Источник**: `Models/docs/contracts/V1_TRANSFORMER_MODEL.md:16-17`, `Models/docs/QA/CONTRACTS_QA.md:243-250`, `docs/MAIN_README.md:67`

gated fusion?
**Ответ**: Нет, gated fusion не упоминается. Используется cross-attention fusion.
**Источник**: Не найдено упоминаний gated fusion

5.4 Последовательность
Длина последовательности сегментов фиксирована?
**Ответ**: Нет, не фиксирована. Используется learnable pooling для превращения M локальных токенов в фиксированное число K summary-токенов. Для коротких видео используется меньшее окно, для длинных - большее. Количество summary токенов K фиксированное, но M (количество локальных токенов) варьируется.
**Источник**: `docs/MAIN_README.md:39-76`, `Models/docs/contracts/ENCODER_CONTRACT.md:29-34`, `DataProcessor/docs/reference/project_questions.md:410-413`

Используешь padding + mask?
**Ответ**: Да, используется padding + mask. Для коротких видео важно использовать маску, чтобы пустые summary-токены не портили внимание. В коде используется `key_padding_mask` для transformer и маскирование при pooled representation.
**Источник**: `docs/MAIN_README.md:61`, `Models/v1/model/v1_skeleton.py:145-151`

Есть ли temporal pooling?
**Ответ**: Да, используется temporal pooling. После pooling все модальности приводятся к единой форме [K, D]. Pooled representation: mean over non-masked. Также используется learnable pooling для сжатия последовательности.
**Источник**: `docs/MAIN_README.md:55-67`, `Models/v1/model/v1_skeleton.py:149-151`

6️⃣ Интерпретируемость (ключевой момент для аналитиков)
6.1 Что ты планируешь показывать?
SHAP?
**Ответ**: Упоминается в контракте prediction report: "explainability (если включено): baseline SHAP / transformer evidence summary". Но это опциональный режим для internal/debug. Для v1 основной режим - "evidence/diagnostics", а не полноценная feature attribution.
**Источник**: `Models/docs/contracts/PREDICTION_REPORT_CONTRACT.md:104-114`, `Models/docs/QA/CONTRACTS_QA.md:470-476`

Feature importance?
**Ответ**: Упоминается в контракте explainability, но как опциональный режим "attribution_lite" для internal/debug. Основной режим для v1 - "evidence" (какие модальности присутствуют, сколько токенов, sanity checks).
**Источник**: `Models/docs/contracts/PREDICTION_REPORT_CONTRACT.md:104-114`, `Models/docs/QA/CONTRACTS_QA.md:470-476`

Attention maps?
**Ответ**: Не найдено явного упоминания attention maps для интерпретации. Упоминается только "evidence" режим с top_modalities и sanity checks.
**Источник**: Не найдено упоминаний attention maps

Grad-CAM для кадров?
**Ответ**: Не найдено упоминания Grad-CAM для кадров.
**Источник**: Не найдено упоминаний Grad-CAM

Вклад модальности в итоговый прогноз?
**Ответ**: Да, упоминается в explainability: `top_modalities` (например `["visual","text"]`) показывают какие модальности присутствуют. Но полноценный вклад модальности в прогноз (attribution) - это опциональный режим для internal/debug.
**Источник**: `Models/docs/contracts/PREDICTION_REPORT_CONTRACT.md:109-112`, `Models/docs/QA/CONTRACTS_QA.md:470-476`

6.2 Локальные объяснения
Для конкретного видео — какие 5 факторов больше всего повлияли?
**Ответ**: Не найдено явного упоминания "топ-5 факторов" для конкретного видео. Есть опциональный режим "attribution_lite" для internal/debug, но это не основной функционал для v1.
**Источник**: `Models/docs/contracts/PREDICTION_REPORT_CONTRACT.md:104-114`

Будет ли "если изменить X → прогноз изменится на Y"?
**Ответ**: Не найдено упоминания counterfactual объяснений ("если изменить X → прогноз изменится на Y").
**Источник**: Не найдено упоминаний counterfactual объяснений

7️⃣ Визуализация на сайте
Очень важный UX-блок.

7.1 Страница прогресса
Это будет WebSocket?
**Ответ**: Да, используется WebSocket. Endpoint: `GET /api/runs/{run_id}/events` → WebSocket (live events). Backend читает `state_events.jsonl` и пушит события в WS. Также упоминается SSE как альтернатива.
**Источник**: `backend/docs/API.md:45`, `backend/docs/reference/backend_qna_contracts.md:116-131`, `backend/docs/EVENTS_AND_LOGGING.md:82-84`

Реальное обновление этапов?
**Ответ**: Да, реальное обновление этапов. Компоненты публикуют прогресс в `state_events.jsonl` (baseline contract). Стадии выполнения: `start` → `load_deps` → `process_frames` → `save` → `done`. Гранулярный прогресс: ≥10 обновлений во время обработки. Backend читает и пушит через WebSocket.
**Источник**: `DataProcessor/VisualProcessor/core/model_process/core_identity/face_identity/README.md:269-280`, `backend/app/tasks.py:84-171`, `DataProcessor/VisualProcessor/modules/shot_quality/shot_quality.py:806-815`

Будут ли превью кадров с bbox (например YOLO)?
**Ответ**: Упоминается в описании продукта: "показываться какие то настоящие кадры из видео, например боксы найденых людей или предметов и тд". В коде есть сохранение bbox для top-1 face (для render assets), но явного упоминания превью кадров с bbox на странице прогресса не найдено.
**Источник**: `doc.md:4`, `DataProcessor/VisualProcessor/core/model_process/core_identity/face_identity/main.py:802-804`

7.2 Страница результатов
Планируешь ли:
распределения по категориям
**Ответ**: Упоминается в описании: "распределения по результатам каждого компонента". В модулях есть `meta.ui_payload` с распределениями (например, `shot_quality` имеет `video_mean_probs_topk_*` для распределений). Но явного упоминания "распределения по категориям" не найдено.
**Источник**: `doc.md:4`, `DataProcessor/VisualProcessor/modules/shot_quality/FEATURES_DESCRIPTION.md:20-21`

сравнение с похожими видео
**Ответ**: Да, планируется сравнение с похожими видео. Есть модуль `similarity_metrics` для сравнения видео с reference set. В описании продукта упоминается: "сравнения между видео (как между видео самого пользователя, так и других похожих видео)". Модуль выдает `topk_refs[]` с top-K reference videos и scores_by_modality.
**Источник**: `doc.md:4`, `DataProcessor/VisualProcessor/modules/similarity_metrics/similarity_metrics.py:367-1476`, `DataProcessor/VisualProcessor/modules/similarity_metrics/FEATURES_DESCRIPTION.md:30-33`

percentile ranking
**Ответ**: Не найдено явного упоминания percentile ranking.
**Источник**: Не найдено упоминаний percentile ranking

radar chart модальностей
**Ответ**: Не найдено явного упоминания radar chart модальностей.
**Источник**: Не найдено упоминаний radar chart

8️⃣ Сравнение видео
Как определяется "похожесть"?
**Ответ**: Похожесть определяется через модуль `similarity_metrics`, который использует multiple модальности: CLIP (visual semantic), audio_clap, text, pacing, quality, emotion. Вычисляется cosine similarity по модальностям. Также есть overall_similarity_score как комбинация всех модальностей с весами.
**Источник**: `DataProcessor/VisualProcessor/modules/similarity_metrics/similarity_metrics.py:593-628`, `DataProcessor/VisualProcessor/modules/similarity_metrics/FEATURES_DESCRIPTION.md:17-27`

по embedding?
**Ответ**: Да, по embedding. Основной метод - cosine similarity по CLIP embeddings (visual semantic). Также используется CLAP embedding для audio similarity, text embeddings для text similarity. Reference pack содержит `clip_video_embeddings`, `clap_audio_embeddings`, `text_primary_embeddings`.
**Источник**: `DataProcessor/VisualProcessor/modules/similarity_metrics/similarity_metrics.py:608-628`, `DataProcessor/VisualProcessor/modules/similarity_metrics/FEATURES_DESCRIPTION.md:19-26`

по категории?
**Ответ**: Не найдено явного упоминания сравнения по категории. Сравнение идет по embedding'ам и фичам (pacing, quality, emotion), но не по категории напрямую.
**Источник**: Не найдено упоминаний сравнения по категории

по predicted popularity?
**Ответ**: Не найдено явного упоминания сравнения по predicted popularity. Сравнение идет по embedding'ам и фичам модальностей, но не по прогнозу популярности.
**Источник**: Не найдено упоминаний сравнения по predicted popularity

9️⃣ Масштабирование
Планируешь ли Kubernetes?
**Ответ**: Да, планируется Kubernetes для будущего масштабирования. Для MVP используется Docker + docker-compose, потом миграция на K8s когда понадобится масштабирование. Kubernetes для оркестрации (auto-scaling, health checks, rolling updates), Helm charts для управления конфигурацией.
**Источник**: `DataProcessor/docs/architecture/PRODUCTION_ARCHITECTURE.md:126-131`, `backend/docs/reference/backend_qna_contracts.md:490`, `DataProcessor/docs/reference/GLOBAL.md:613-625`

Как будет распределяться нагрузка между:
DataProcessor
**Ответ**: Горизонтальное масштабирование DataProcessor workers через queue. Несколько worker'ов читают из одной очереди (parallel processing). 1 worker = 1 видео одновременно (если 1 GPU на worker). Если несколько GPU: можно обрабатывать N видео параллельно (N = количество GPU). Auto-scaling: можно настроить автоматическое добавление worker'ов при росте очереди (Kubernetes HPA или простой скрипт).
**Источник**: `DataProcessor/docs/architecture/PRODUCTION_ARCHITECTURE.md:100-108`, `DataProcessor/docs/reference/GLOBAL.md:613-625`

Triton
**Ответ**: Triton deployment: отдельный сервер/контейнер для Triton (не на worker'ах). Несколько реплик Triton для высокой нагрузки (load balancing). Версии моделей фиксируются через resolved mapping per-run (source-of-truth: профиль анализа).
**Источник**: `DataProcessor/docs/architecture/PRODUCTION_ARCHITECTURE.md:109-112`, `DataProcessor/docs/reference/GLOBAL.md:627-628`

Fusion model
**Ответ**: Не найдено явного описания распределения нагрузки для Fusion model. Fusion model является частью Models и запускается после DataProcessor. Вероятно, запускается на тех же worker'ах или отдельном inference сервисе, но явного описания не найдено.
**Источник**: Не найдено явного описания для Fusion model

🔟 Мониторинг
Есть ли:
ML monitoring (drift)?
**Ответ**: Упоминается в планах baseline: "логирование per prediction: latency, feature missing rate, drift proxies", "алерты: error rate/latency p95/p99, 'необычные' распределения outputs". Но явного упоминания полноценного ML monitoring (drift) системы не найдено.
**Источник**: `Models/docs/plan_dev/BASELINE_DEV_PLAN.md:192-202`

Feature drift?
**Ответ**: Упоминается в рисках baseline: "Feature drift / schema churn: freeze v1 + строгая версия `feature_schema_version`". В планах есть "drift proxies" в логировании, но полноценной системы мониторинга feature drift не найдено.
**Источник**: `Models/docs/plan_dev/BASELINE_DEV_PLAN.md:219`, `Models/docs/plan_dev/BASELINE_DEV_PLAN.md:192-202`

Target drift?
**Ответ**: Не найдено явного упоминания target drift мониторинга.
**Источник**: Не найдено упоминаний target drift

Логируешь ли ты:
входные фичи
**Ответ**: Упоминается в планах: "логирование per prediction: latency, feature missing rate, drift proxies, распределение outputs по buckets". Но явного упоминания логирования всех входных фичей не найдено. Логируются метаданные моделей в `models_used[]` и `model_signature`.
**Источник**: `Models/docs/plan_dev/BASELINE_DEV_PLAN.md:192-202`, `Models/docs/contracts/MODEL_SYSTEM_RULES.md:23-32`

выходы модели
**Ответ**: Да, логируются выходы модели. В планах baseline: "логирование per prediction: распределение outputs по buckets", "алерты: 'необычные' распределения outputs". В prediction report фиксируются outputs для всех heads (views/likes × 7/14/21).
**Источник**: `Models/docs/plan_dev/BASELINE_DEV_PLAN.md:192-202`, `Models/docs/contracts/PREDICTION_REPORT_CONTRACT.md:82-101`

версии моделей
**Ответ**: Да, версии моделей логируются. В каждом артефакте фиксируется `models_used[]` с `model_name`, `model_version`, `weights_digest`, `model_signature`. В prediction report также фиксируются `models_used[]` со всеми версиями.
**Источник**: `Models/docs/contracts/MODEL_SYSTEM_RULES.md:23-32`, `Models/docs/contracts/PREDICTION_REPORT_CONTRACT.md:53-64`

11️⃣ Главный стратегический вопрос
Ты строишь:
Инструмент для креаторов?
**Ответ**: Да, частично. Система нацелена на обширную аудиторию: 1 - пользователи, которые не знают ничего о аналитике, машинном обучении, а просто хотят загрузить свое видео и получить прогноз популярности и рекомендации по улучшению. Это инструмент для креаторов.
**Источник**: `doc.md:1`

Исследовательскую ML-платформу?
**Ответ**: Частично. Система имеет модульную архитектуру с множеством компонентов, которые могут разрабатываться отдельно. Есть строгие контракты, версионирование, reproducibility. Но основная цель - продукт, а не исследовательская платформа.
**Источник**: `doc.md:8-10`, `DataProcessor/docs/contracts/PER_COMPONENT.md:70-73`

Полноценную AI-аналитическую экосистему?
**Ответ**: Да, похоже на это. Система включает: анализ видео/аудио/текста, предсказание популярности, интерпретацию результатов, сравнение видео, рекомендации. Для профессиональных аналитиков нужны конкретные причины роста, факторы, графики, распределения, взаимосвязи. Это полноценная AI-аналитическая экосистема.
**Источник**: `doc.md:1`, `doc.md:4`

Потому что от этого зависит:
глубина explainability
**Ответ**: Для v1 основной режим explainability - "evidence/diagnostics" (какие модальности присутствуют, сколько токенов, sanity checks). Полноценная feature attribution - опциональный режим для internal/debug. Для аналитиков планируется больше деталей, но в v1 это ограничено.
**Источник**: `Models/docs/contracts/PREDICTION_REPORT_CONTRACT.md:104-114`, `Models/docs/QA/CONTRACTS_QA.md:470-476`

архитектура
**Ответ**: Архитектура модульная: каждый компонент - отдельный микро-сервис по факту, может разрабатываться отдельно. Есть четкие контракты, DAG зависимостей, версионирование. Модульная архитектура позволяет расширять систему.
**Источник**: `doc.md:8-10`, `DataProcessor/docs/contracts/PER_COMPONENT.md:70-73`, `DataProcessor/dag/component_graph.py:329-332`

сложность frontend
**Ответ**: Frontend должен быть сложным: страница прогресса с реальными кадрами, страница результатов с вкладками всех компонентов, визуализации, интерпретации, графики, распределения, взаимосвязи, сравнения между видео. Это требует сложного frontend.
**Источник**: `doc.md:4`

тип модели (монолит vs модульная)
**Ответ**: Модульная архитектура моделей: Encoders (AudioEncoder, VisualEncoder) → Transformer Heads (AudioHead, VisualHead) → Fusion → Prediction Heads. Модели могут быть как отдельными, так и частями одной модели. Baseline и v1/v2 - отдельные модели. Модульный подход.
**Источник**: `doc.md:14`, `Models/docs/contracts/V1_TRANSFORMER_MODEL.md:9-13`, `Models/docs/contracts/MODEL_CONTRACTS_V1.md:7-18`