# 📦 Core SaaS Database Specification

*PostgreSQL • Multi-Tenant • Enterprise-Ready*

---

# 🧱 1️⃣ USERS & AUTH (Global Scope)

## 🔹 users

| Field          | Type                    | Notes              |
| -------------- | ----------------------- | ------------------ |
| id             | UUID (PK)               | Primary key        |
| email          | TEXT (UNIQUE, NOT NULL) | Login identifier   |
| email_verified | BOOLEAN                 | Default: false     |
| password_hash  | TEXT (nullable)         | Null if OAuth-only |
| created_at     | TIMESTAMP               |                    |
| updated_at     | TIMESTAMP               |                    |
| deleted_at     | TIMESTAMP (nullable)    | Soft delete        |

### Relationships

* 1 → N `user_oauth_accounts`
* 1 → 1 `user_security`
* M ↔ N `workspaces` (via `workspace_members`)
* 1 → N `analysis_jobs`
* 1 → N `audit_logs`

---

## 🔹 user_oauth_accounts

| Field            | Type          | Notes                     |
| ---------------- | ------------- | ------------------------- |
| id               | UUID (PK)     |                           |
| user_id          | FK → users.id | ON DELETE CASCADE         |
| provider         | TEXT          | github / google / youtube |
| provider_user_id | TEXT          | External ID               |
| access_token     | TEXT          |                           |
| refresh_token    | TEXT          |                           |
| token_expires_at | TIMESTAMP     |                           |
| created_at       | TIMESTAMP     |                           |

**Constraints**

* UNIQUE(provider, provider_user_id)

---

## 🔹 user_security

| Field                     | Type              |
| ------------------------- | ----------------- |
| user_id                   | PK, FK → users.id |
| two_factor_enabled        | BOOLEAN           |
| two_factor_secret         | TEXT              |
| password_reset_token      | TEXT              |
| password_reset_expires_at | TIMESTAMP         |

Relationship:

* 1 ↔ 1 with `users`

---

# 🏢 2️⃣ WORKSPACES (Multi-Tenant Core)

## 🔹 workspaces

| Field         | Type                    |
| ------------- | ----------------------- |
| id            | UUID (PK)               |
| name          | TEXT                    |
| slug          | TEXT (UNIQUE)           |
| owner_user_id | FK → users.id           |
| created_at    | TIMESTAMP               |
| updated_at    | TIMESTAMP               |
| archived_at   | TIMESTAMP (soft delete) |

Relationships:

* 1 → N `workspace_members`
* 1 → N `subscriptions`
* 1 → N `channels`
* 1 → N `analysis_jobs`
* 1 → N `api_keys`
* 1 → N `audit_logs`

---

## 🔹 workspace_members

| Field        | Type                            |
| ------------ | ------------------------------- |
| id           | UUID (PK)                       |
| workspace_id | FK → workspaces.id (CASCADE)    |
| user_id      | FK → users.id (CASCADE)         |
| role         | owner / admin / editor / viewer |
| invited_by   | FK → users.id                   |
| joined_at    | TIMESTAMP                       |
| archived_at  | TIMESTAMP                       |

**Constraints**

* UNIQUE(workspace_id, user_id)

Purpose:
Implements Many-to-Many between Users and Workspaces.

---

# 💳 3️⃣ BILLING SYSTEM

## 🔹 subscription_plans

| Field                       | Type                          |
| --------------------------- | ----------------------------- |
| id                          | INT (PK)                      |
| name                        | Free / Basic / Pro / Personal |
| max_videos_per_month        | INT                           |
| max_analyses_per_month      | INT                           |
| max_channels                | INT                           |
| max_storage_gb              | INT                           |
| has_api_access              | BOOLEAN                       |
| has_advanced_explainability | BOOLEAN                       |
| price                       | FLOAT                         |
| created_at                  | TIMESTAMP                     |

---

## 🔹 subscriptions

| Field                | Type                         |
| -------------------- | ---------------------------- |
| id                   | UUID (PK)                    |
| workspace_id         | FK → workspaces.id (CASCADE) |
| plan_id              | FK → subscription_plans.id   |
| status               | active / canceled / expired  |
| current_period_start | TIMESTAMP                    |
| current_period_end   | TIMESTAMP                    |
| cancel_at_period_end | BOOLEAN                      |
| created_at           | TIMESTAMP                    |

Relationships:

* N → 1 `workspaces`
* N → 1 `subscription_plans`
* 1 → N `billing_transactions`

---

## 🔹 subscription_usage

| Field           | Type               |
| --------------- | ------------------ |
| id              | UUID (PK)          |
| workspace_id    | FK → workspaces.id |
| period_start    | DATE               |
| videos_uploaded | INT                |
| analyses_run    | INT                |
| storage_used_mb | INT                |
| updated_at      | TIMESTAMP          |

Constraint:

* UNIQUE(workspace_id, period_start)

---

## 🔹 billing_transactions

| Field                   | Type                  |
| ----------------------- | --------------------- |
| id                      | UUID (PK)             |
| subscription_id         | FK → subscriptions.id |
| amount                  | FLOAT                 |
| currency                | TEXT                  |
| payment_provider        | TEXT                  |
| provider_transaction_id | TEXT                  |
| status                  | TEXT                  |
| created_at              | TIMESTAMP             |

---

# 📺 4️⃣ CHANNELS

## 🔹 channels

| Field               | Type                                        |
| ------------------- | ------------------------------------------- |
| id                  | UUID (PK)                                   |
| workspace_id        | FK → workspaces.id                          |
| platform            | youtube / tiktok / twitch / rutube / upload |
| external_channel_id | TEXT                                        |
| channel_name        | TEXT                                        |
| connected_oauth_id  | FK → user_oauth_accounts.id                 |
| created_at          | TIMESTAMP                                   |
| archived_at         | TIMESTAMP                                   |

Indexes:

* INDEX(workspace_id)
* INDEX(platform, external_channel_id)

---

## 🔹 channel_owners

| Field      | Type                     |
| ---------- | ------------------------ |
| id         | UUID (PK)                |
| channel_id | FK → channels.id         |
| user_id    | FK → users.id            |
| role       | primary_owner / co_owner |
| created_at | TIMESTAMP                |

Purpose:
Many-to-Many between Users and Channels.

---

# 🎥 5️⃣ VIDEOS

## 🔹 videos

| Field             | Type             |
| ----------------- | ---------------- |
| id                | UUID (PK)        |
| channel_id        | FK → channels.id |
| external_video_id | TEXT             |
| title             | TEXT             |
| description       | TEXT             |
| duration_seconds  | INT              |
| video_type        | shorts / video   |
| source_type       | upload / link    |
| source_url        | TEXT             |
| storage_path      | TEXT             |
| file_size_mb      | FLOAT            |
| checksum          | TEXT             |
| created_at        | TIMESTAMP        |
| archived_at       | TIMESTAMP        |

Constraint:

* UNIQUE(channel_id, external_video_id)

---

## 🔹 video_snapshots

| Field               | Type           |
| ------------------- | -------------- |
| id                  | UUID (PK)      |
| video_id            | FK → videos.id |
| views               | INT            |
| likes               | INT            |
| comments_count      | INT            |
| subscribers_count   | INT            |
| engagement_rate     | FLOAT          |
| snapshot_created_at | TIMESTAMP      |

Index:

* INDEX(video_id, snapshot_created_at)

---

## 🔹 video_comments

| Field               | Type           |
| ------------------- | -------------- |
| id                  | UUID (PK)      |
| video_id            | FK → videos.id |
| external_comment_id | TEXT           |
| author_name         | TEXT           |
| text                | TEXT           |
| like_count          | INT            |
| published_at        | TIMESTAMP      |
| created_at          | TIMESTAMP      |

Limit enforced at application level (max 100 per video).

---

# ⚙ 6️⃣ PROCESSING CONFIGS

## 🔹 processing_configs

| Field               | Type                                           |
| ------------------- | ---------------------------------------------- |
| id                  | UUID (PK)                                      |
| workspace_id        | FK → workspaces.id (nullable if global preset) |
| name                | TEXT                                           |
| is_preset           | BOOLEAN                                        |
| frame_sampling_rate | INT                                            |
| audio_window_size   | INT                                            |
| use_comments        | BOOLEAN                                        |
| created_by          | FK → users.id                                  |
| created_at          | TIMESTAMP                                      |

---

# 🧠 7️⃣ ANALYSIS SYSTEM

## 🔹 analysis_jobs

| Field                | Type                                                |
| -------------------- | --------------------------------------------------- |
| id                   | UUID (PK)                                           |
| workspace_id         | FK → workspaces.id                                  |
| video_id             | FK → videos.id                                      |
| triggered_by_user_id | FK → users.id                                       |
| processing_config_id | UUID                                                |
| model_version_id     | STRING (ML DB reference)                            |
| status               | queued / processing / completed / failed / canceled |
| retry_count          | INT                                                 |
| error_message        | TEXT                                                |
| started_at           | TIMESTAMP                                           |
| completed_at         | TIMESTAMP                                           |
| created_at           | TIMESTAMP                                           |

Indexes:

* INDEX(workspace_id)
* INDEX(video_id)

---

## 🔹 predictions

| Field            | Type                  |
| ---------------- | --------------------- |
| id               | UUID (PK)             |
| analysis_job_id  | FK → analysis_jobs.id |
| horizon_days     | INT                   |
| predicted_views  | FLOAT                 |
| predicted_likes  | FLOAT                 |
| percentile_score | FLOAT                 |
| confidence_lower | FLOAT                 |
| confidence_upper | FLOAT                 |
| model_version_id | STRING                |
| created_at       | TIMESTAMP             |

---

## 🔹 explainability_summary

| Field                       | Type                           |
| --------------------------- | ------------------------------ |
| id                          | UUID (PK)                      |
| analysis_job_id             | FK → analysis_jobs.id (UNIQUE) |
| video_modality_contribution | FLOAT                          |
| audio_modality_contribution | FLOAT                          |
| text_modality_contribution  | FLOAT                          |
| top_positive_factor         | TEXT                           |
| top_negative_factor         | TEXT                           |
| created_at                  | TIMESTAMP                      |

---

## 🔹 recommendations

| Field             | Type                  |
| ----------------- | --------------------- |
| id                | UUID (PK)             |
| analysis_job_id   | FK → analysis_jobs.id |
| feature_group     | TEXT                  |
| current_value     | FLOAT                 |
| recommended_value | FLOAT                 |
| predicted_gain    | FLOAT                 |
| priority_score    | FLOAT                 |
| created_at        | TIMESTAMP             |

---

## 🔹 model_serving_log (A/B Testing)

| Field            | Type                  |
| ---------------- | --------------------- |
| id               | UUID (PK)             |
| analysis_job_id  | FK → analysis_jobs.id |
| model_version_id | STRING                |
| ab_test_id       | UUID (nullable)       |
| created_at       | TIMESTAMP             |

---

# 🔐 8️⃣ API SYSTEM

## 🔹 api_keys

| Field                 | Type               |
| --------------------- | ------------------ |
| id                    | UUID (PK)          |
| workspace_id          | FK → workspaces.id |
| key_hash              | TEXT               |
| name                  | TEXT               |
| rate_limit_per_minute | INT                |
| created_at            | TIMESTAMP          |
| revoked_at            | TIMESTAMP          |

---

## 🔹 api_usage_logs

| Field            | Type             |
| ---------------- | ---------------- |
| id               | UUID (PK)        |
| api_key_id       | FK → api_keys.id |
| endpoint         | TEXT             |
| status_code      | INT              |
| response_time_ms | INT              |
| created_at       | TIMESTAMP        |

---

# 📜 9️⃣ AUDIT LOGS

## 🔹 audit_logs

| Field        | Type               |
| ------------ | ------------------ |
| id           | UUID (PK)          |
| workspace_id | FK → workspaces.id |
| user_id      | FK → users.id      |
| action       | TEXT               |
| entity_type  | TEXT               |
| entity_id    | UUID               |
| ip_address   | TEXT               |
| user_agent   | TEXT               |
| created_at   | TIMESTAMP          |

---

# 🏗 Архитектурная Иерархия

```
User (global)
   ↓
Workspace (tenant)
   ↓
Channels → Videos → Analysis
   ↓
Billing / API / Audit
```

---

# 🛡 Multi-Tenant Правило

Все бизнес-таблицы содержат `workspace_id`.
Все запросы обязаны фильтроваться по нему.

---