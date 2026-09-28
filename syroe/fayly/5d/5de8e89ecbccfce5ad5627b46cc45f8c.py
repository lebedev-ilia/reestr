from __future__ import annotations

import uuid
from datetime import datetime
from typing import List, Optional

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import Enum as SAEnum

from .base import Base, SoftDeleteMixin, TimestampMixin, UUIDPrimaryKeyMixin
from .enums import (
    AnalysisStatus,
    SourceType,
    SubscriptionStatus,
    VideoType,
    WorkspaceRole,
)


class User(Base, TimestampMixin, SoftDeleteMixin, UUIDPrimaryKeyMixin):
    __tablename__ = "users"
    __table_args__ = {"schema": "core"}

    email: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    email_verified: Mapped[bool] = mapped_column(Boolean, default=False)
    password_hash: Mapped[Optional[str]] = mapped_column(String, nullable=True)

    oauth_accounts: Mapped[List["UserOAuthAccount"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    security: Mapped[Optional["UserSecurity"]] = relationship(
        back_populates="user", uselist=False, cascade="all, delete-orphan"
    )
    memberships: Mapped[List["WorkspaceMember"]] = relationship(
        "WorkspaceMember",
        back_populates="user",
        cascade="all, delete-orphan",
        foreign_keys="WorkspaceMember.user_id",
    )


class UserOAuthAccount(Base, TimestampMixin, UUIDPrimaryKeyMixin):
    __tablename__ = "user_oauth_accounts"
    __table_args__ = (UniqueConstraint("provider", "provider_user_id"), {"schema": "core"})

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("core.users.id", ondelete="CASCADE"), nullable=False
    )

    provider: Mapped[str] = mapped_column(String, nullable=False)
    provider_user_id: Mapped[str] = mapped_column(String, nullable=False)

    access_token: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    refresh_token: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    token_expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    user: Mapped["User"] = relationship(back_populates="oauth_accounts")


class UserSecurity(Base):
    __tablename__ = "user_security"
    __table_args__ = {"schema": "core"}

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("core.users.id", ondelete="CASCADE"), primary_key=True
    )

    two_factor_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    two_factor_secret: Mapped[Optional[str]] = mapped_column(String, nullable=True)

    password_reset_token: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    password_reset_expires_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime, nullable=True
    )

    user: Mapped["User"] = relationship(back_populates="security")


class Workspace(Base, TimestampMixin, UUIDPrimaryKeyMixin):
    __tablename__ = "workspaces"
    __table_args__ = {"schema": "core"}

    name: Mapped[str] = mapped_column(String, nullable=False)
    slug: Mapped[str] = mapped_column(String, unique=True, nullable=False)

    owner_user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("core.users.id"), nullable=False)

    archived_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    owner: Mapped["User"] = relationship()
    members: Mapped[List["WorkspaceMember"]] = relationship(
        back_populates="workspace", cascade="all, delete-orphan"
    )
    subscriptions: Mapped[List["Subscription"]] = relationship(
        back_populates="workspace", cascade="all, delete-orphan"
    )
    channels: Mapped[List["Channel"]] = relationship(
        back_populates="workspace", cascade="all, delete-orphan"
    )


class WorkspaceMember(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "workspace_members"
    __table_args__ = (UniqueConstraint("workspace_id", "user_id"), {"schema": "core"})

    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("core.workspaces.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("core.users.id", ondelete="CASCADE"), nullable=False
    )

    role: Mapped[WorkspaceRole] = mapped_column(
        SAEnum(WorkspaceRole, name="workspace_role", schema="core"), nullable=False
    )

    invited_by: Mapped[Optional[uuid.UUID]] = mapped_column(
        ForeignKey("core.users.id"), nullable=True
    )

    joined_at: Mapped[datetime] = mapped_column(default=datetime.utcnow)
    archived_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    workspace: Mapped["Workspace"] = relationship(back_populates="members")
    user: Mapped["User"] = relationship(back_populates="memberships", foreign_keys=[user_id])


class SubscriptionPlan(Base):
    __tablename__ = "subscription_plans"
    __table_args__ = {"schema": "core"}

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String, nullable=False)

    max_videos_per_month: Mapped[int] = mapped_column(Integer, nullable=False)
    max_analyses_per_month: Mapped[int] = mapped_column(Integer, nullable=False)
    max_channels: Mapped[int] = mapped_column(Integer, nullable=False)
    max_storage_gb: Mapped[int] = mapped_column(Integer, nullable=False)

    has_api_access: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    has_advanced_explainability: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )

    price: Mapped[float] = mapped_column(Float, nullable=False)


class Subscription(Base, TimestampMixin, UUIDPrimaryKeyMixin):
    __tablename__ = "subscriptions"
    __table_args__ = {"schema": "core"}

    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("core.workspaces.id", ondelete="CASCADE"), nullable=False
    )
    plan_id: Mapped[int] = mapped_column(
        ForeignKey("core.subscription_plans.id"), nullable=False
    )

    status: Mapped[SubscriptionStatus] = mapped_column(
        SAEnum(SubscriptionStatus, name="subscription_status", schema="core"), nullable=False
    )

    current_period_start: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    current_period_end: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    cancel_at_period_end: Mapped[bool] = mapped_column(Boolean, default=False)

    workspace: Mapped["Workspace"] = relationship(back_populates="subscriptions")
    plan: Mapped["SubscriptionPlan"] = relationship()


class Channel(Base, TimestampMixin, UUIDPrimaryKeyMixin):
    __tablename__ = "channels"
    __table_args__ = (
        Index("ix_platform_external", "platform", "external_channel_id"),
        {"schema": "core"},
    )

    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("core.workspaces.id", ondelete="CASCADE"), nullable=False
    )

    platform: Mapped[str] = mapped_column(String, nullable=False)
    external_channel_id: Mapped[Optional[str]] = mapped_column(String, nullable=True)

    channel_name: Mapped[str] = mapped_column(String, nullable=False)

    connected_oauth_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        ForeignKey("core.user_oauth_accounts.id"), nullable=True
    )

    archived_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    workspace: Mapped["Workspace"] = relationship(back_populates="channels")
    videos: Mapped[List["Video"]] = relationship(
        back_populates="channel", cascade="all, delete-orphan"
    )


class Video(Base, TimestampMixin, UUIDPrimaryKeyMixin):
    __tablename__ = "videos"
    __table_args__ = (UniqueConstraint("channel_id", "external_video_id"), {"schema": "core"})

    channel_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("core.channels.id", ondelete="CASCADE"), nullable=False
    )

    external_video_id: Mapped[Optional[str]] = mapped_column(String, nullable=True)

    title: Mapped[str] = mapped_column(String, nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    duration_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    video_type: Mapped[VideoType] = mapped_column(
        SAEnum(VideoType, name="video_type", schema="core"), nullable=False
    )

    source_type: Mapped[SourceType] = mapped_column(
        SAEnum(SourceType, name="source_type", schema="core"), nullable=False
    )
    source_url: Mapped[Optional[str]] = mapped_column(String, nullable=True)

    storage_path: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    file_size_mb: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    checksum: Mapped[Optional[str]] = mapped_column(String, nullable=True)

    archived_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    channel: Mapped["Channel"] = relationship(back_populates="videos")
    analysis_jobs: Mapped[List["AnalysisJob"]] = relationship(
        back_populates="video", cascade="all, delete-orphan"
    )


class AnalysisJob(Base, TimestampMixin, UUIDPrimaryKeyMixin):
    __tablename__ = "analysis_jobs"
    __table_args__ = (
        Index("ix_analysis_workspace", "workspace_id"),
        Index("ix_analysis_video", "video_id"),
        {"schema": "core"},
    )

    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("core.workspaces.id", ondelete="CASCADE"), nullable=False
    )
    video_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("core.videos.id", ondelete="CASCADE"), nullable=False
    )

    triggered_by_user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("core.users.id"), nullable=False
    )

    processing_config_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    model_version_id: Mapped[str] = mapped_column(String, nullable=False)

    status: Mapped[AnalysisStatus] = mapped_column(
        SAEnum(AnalysisStatus, name="analysis_status", schema="core"), nullable=False
    )
    retry_count: Mapped[int] = mapped_column(Integer, default=0)
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    video: Mapped["Video"] = relationship(back_populates="analysis_jobs")
    predictions: Mapped[List["Prediction"]] = relationship(
        back_populates="analysis_job", cascade="all, delete-orphan"
    )


class IngestionRun(Base, TimestampMixin):
    """
    Run ингестиции по URL (YouTube и др.): Backend создаёт запись и передаёт run_id в Fetcher.

    Контракт: docs/BACKEND_FETCHER_INTEGRATION_ANALYSIS.md, Fetcher/docs/BACKEND_CONTRACTS.md.
    run_id — source of truth в Backend; тот же UUID передаётся в Fetcher (POST /api/v1/runs).
    """

    __tablename__ = "ingestion_runs"
    __table_args__ = (
        Index("ix_ingestion_runs_user", "user_id"),
        Index("ix_ingestion_runs_workspace", "workspace_id"),
        {"schema": "core"},
    )

    run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True
    )  # генерируется при создании, передаётся в Fetcher
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("core.users.id", ondelete="CASCADE"), nullable=False
    )
    source_url: Mapped[str] = mapped_column(String, nullable=False)
    workspace_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        ForeignKey("core.workspaces.id", ondelete="SET NULL"), nullable=True
    )
    ingestion_status: Mapped[str] = mapped_column(
        String, nullable=False, default="pending"
    )  # pending | running | completed | failed (синхронизация с Fetcher)
    idempotency_key: Mapped[Optional[str]] = mapped_column(
        String, nullable=True, unique=True
    )  # для Idempotency-Key: повторный запрос возвращает существующий run
    # Phase 4: данные из Fetcher (polling GET /api/v1/runs/{run_id})
    fetcher_stage: Mapped[Optional[str]] = mapped_column(
        String, nullable=True
    )  # текущая стадия (metadata, video, comments, finalize, …)
    fetcher_error_code: Mapped[Optional[str]] = mapped_column(
        String, nullable=True
    )  # код ошибки от Fetcher при status=FAILED
    fetcher_error_message: Mapped[Optional[str]] = mapped_column(
        Text, nullable=True
    )  # сообщение об ошибке от Fetcher


class Prediction(Base, TimestampMixin, UUIDPrimaryKeyMixin):
    __tablename__ = "predictions"
    __table_args__ = {"schema": "core"}

    analysis_job_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("core.analysis_jobs.id", ondelete="CASCADE"), nullable=False
    )

    horizon_days: Mapped[int] = mapped_column(Integer, nullable=False)
    predicted_views: Mapped[float] = mapped_column(Float, nullable=False)
    predicted_likes: Mapped[float] = mapped_column(Float, nullable=False)

    percentile_score: Mapped[float] = mapped_column(Float, nullable=False)
    confidence_lower: Mapped[float] = mapped_column(Float, nullable=False)
    confidence_upper: Mapped[float] = mapped_column(Float, nullable=False)

    model_version_id: Mapped[str] = mapped_column(String, nullable=False)

    analysis_job: Mapped["AnalysisJob"] = relationship(back_populates="predictions")


