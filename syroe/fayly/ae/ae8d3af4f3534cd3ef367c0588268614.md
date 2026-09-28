# API (REST + WebSocket)

Все endpoints определены в `backend/app/routers/*`.

API использует мультитенантную модель (workspaces, channels) и в первую очередь с таблицами в schema **`core.*`**. Параллельно в базе может существовать **legacy**-схема в `public` (см. `DATABASE.md`, `alembic/env.py`).

### 1) Auth

- `POST /api/auth/register`  
  body: `{email, password}` → `UserOut`
- `POST /api/auth/login`  
  body: `{email, password}` → `{access_token, token_type}`
- `GET /api/auth/me`  
  header: `Authorization: Bearer <token>` → `UserOut`

**Особенности V2**:
- Поддержка OAuth через `UserOAuthAccount`
- Email verification через `User.email_verified`
- Security settings через `UserSecurity` (2FA, password reset)

### 2) Workspaces

- `POST /api/workspaces`  
  body: `{name, slug?}` → `WorkspaceOut`
- `GET /api/workspaces` → `[WorkspaceOut]`
- `GET /api/workspaces/{workspace_id}` → `WorkspaceOut`
- `PUT /api/workspaces/{workspace_id}` → `WorkspaceOut`
- `DELETE /api/workspaces/{workspace_id}` → `{status:"ok"}`

**Члены workspace**:
- `GET /api/workspaces/{workspace_id}/members` → `[WorkspaceMemberOut]`
- `POST /api/workspaces/{workspace_id}/members` → `WorkspaceMemberOut`
- `PUT /api/workspaces/{workspace_id}/members/{user_id}` → `WorkspaceMemberOut`
- `DELETE /api/workspaces/{workspace_id}/members/{user_id}` → `{status:"ok"}`

### 3) Channels

- `POST /api/workspaces/{workspace_id}/channels`  
  body: `{platform, external_channel_id?, channel_name}` → `ChannelOut`
- `GET /api/workspaces/{workspace_id}/channels` → `[ChannelOut]`
- `GET /api/channels/{channel_id}` → `ChannelOut`
- `PUT /api/channels/{channel_id}` → `ChannelOut`
- `DELETE /api/channels/{channel_id}` → `{status:"ok"}`

### 4) Videos

- `POST /api/channels/{channel_id}/videos`  
  body: `{external_video_id?, title, description?, duration_seconds, video_type, source_type, source_url?, storage_path?}` → `VideoOut`
- `GET /api/channels/{channel_id}/videos` → `[VideoOut]`
- `GET /api/videos/{video_id}` → `VideoOut`
- `PUT /api/videos/{video_id}` → `VideoOut`
- `DELETE /api/videos/{video_id}` → `{status:"ok"}`

### 5) Runs (ингестиция по URL, Backend ↔ Fetcher)

- `POST /api/runs`  
  body: `{source_url, workspace_id?}`; header: `Idempotency-Key?` → `IngestionRunOut`. Создаёт run в БД и передаёт задачу в Fetcher.
- `GET /api/runs`  
  query: `workspace_id?`, `limit?` → `[IngestionRunOut]`
- `GET /api/runs/{run_id}` → `IngestionRunOut` (Phase 4: поля `fetcher_stage`, `fetcher_error_code`, `fetcher_error_message`)
- `POST /api/runs/{run_id}/trigger-processing` — (Phase 2) Вызов от Fetcher после finalize. Требует `X-API-Key` если задан `TF_BACKEND_RUN_TRIGGER_API_KEY`. Ответ: 202 Accepted.
- `WS /api/runs/{run_id}/events?token=<JWT>` — (Phase 4) WebSocket поток событий run; **обязателен** query `token` (JWT владельца run). См. `SECURITY.md` §3, `FETCHER_INTEGRATION.md` §7.3.

См. `FETCHER_INTEGRATION.md`.

### 6) Analysis Jobs

- `POST /api/workspaces/{workspace_id}/videos/{video_id}/analysis`  
  body: `{processing_config_id, model_version_id}` → `AnalysisJobOut` (HTTP **201 Created**)
- `GET /api/workspaces/{workspace_id}/analysis` → `[AnalysisJobOut]`
- `GET /api/analysis/{analysis_job_id}` → `AnalysisJobOut`
- `POST /api/analysis/{analysis_job_id}/cancel` → JSON: **`{status: "canceled", analysis_job_id}`** (была очередь); **`{status: "cancel_requested", analysis_job_id, dataprocessor_notified: bool}`** (шла обработка, запрошен cancel в DP); **`{status: "noop", job_status}`** (уже финальный статус). См. `OPERATIONS.md` §6.
- `GET /api/analysis/{analysis_job_id}/predictions` → `[PredictionOut]`

**Особенности**:
- AnalysisJob вместо Run
- Привязка к workspace и channel
- Predictions как отдельная сущность
- Интеграция с DataProcessor через адаптер (см. `DATAPROCESSOR_CONTRACT.md`)

### 7) Subscriptions

- `GET /api/workspaces/{workspace_id}/subscriptions` → `[SubscriptionOut]`
- `POST /api/workspaces/{workspace_id}/subscriptions`  
  body: `{plan_id}` → `SubscriptionOut`
- `PUT /api/subscriptions/{subscription_id}` → `SubscriptionOut`
- `DELETE /api/subscriptions/{subscription_id}` → `{status:"ok"}`

---

## Авторизация

Все REST endpoints (кроме `register/login`) защищены JWT:

- заголовок: `Authorization: Bearer <token>`

WS endpoint `/api/runs/{run_id}/events` **в текущей реализации не проверяет JWT**.
Для HTML артефактов используется `token` в query‑param.

---

## Интеграция с DataProcessor

См. `DATAPROCESSOR_CONTRACT.md` для контрактов с DataProcessor.

