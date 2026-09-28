from __future__ import annotations

import enum


class WorkspaceRole(str, enum.Enum):
    owner = "owner"
    admin = "admin"
    editor = "editor"
    viewer = "viewer"


class SubscriptionStatus(str, enum.Enum):
    active = "active"
    canceled = "canceled"
    expired = "expired"


class VideoType(str, enum.Enum):
    shorts = "shorts"
    video = "video"


class SourceType(str, enum.Enum):
    upload = "upload"
    link = "link"


class AnalysisStatus(str, enum.Enum):
    queued = "queued"
    processing = "processing"
    completed = "completed"
    failed = "failed"
    canceled = "canceled"


