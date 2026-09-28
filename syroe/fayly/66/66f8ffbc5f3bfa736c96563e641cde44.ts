// ─── Auth ────────────────────────────────────────────────────────
export interface User {
  id: string;
  email: string;
  email_verified: boolean;
  created_at: string;
  is_superuser?: boolean;
}

export interface AuthTokens {
  access_token: string;
  token_type: "bearer";
}

// ─── Workspace ───────────────────────────────────────────────────
export type WorkspaceRole = "owner" | "admin" | "editor" | "viewer";

export interface Workspace {
  id: string;
  name: string;
  slug: string;
  owner_user_id: string;
  credit_balance: number;
  created_at: string;
}

export interface WorkspaceMember {
  id: string;
  workspace_id: string;
  user_id: string;
  role: WorkspaceRole;
  joined_at: string;
}

// ─── Video ───────────────────────────────────────────────────────
export type VideoType = "shorts" | "video";
export type SourceType = "upload" | "link";

export interface Video {
  id: string;
  channel_id: string;
  title: string;
  description?: string;
  duration_seconds: number;
  video_type: VideoType;
  source_type: SourceType;
  source_url?: string;
  storage_path?: string;
  thumbnail_url?: string;
  created_at: string;
}

// ─── Analysis ────────────────────────────────────────────────────
export type AnalysisStatus = "queued" | "processing" | "completed" | "failed" | "canceled";

export interface AnalysisJob {
  id: string;
  workspace_id: string;
  video_id: string;
  triggered_by_user_id: string;
  processing_config_id: string;
  model_version_id: string;
  status: AnalysisStatus;
  retry_count: number;
  error_message?: string;
  started_at?: string;
  completed_at?: string;
  created_at: string;
  video?: Video;
  virality_score?: number;
  credits_spent?: number;
}

export interface Prediction {
  id: string;
  analysis_job_id: string;
  horizon_days: 7 | 14 | 21;
  predicted_views: number;
  predicted_likes: number;
  percentile_score?: number;
  confidence_lower?: number;
  confidence_upper?: number;
  model_version_id: string;
  created_at: string;
}

// ─── Processing Config ───────────────────────────────────────────
export interface ProcessingConfig {
  id: string;
  workspace_id?: string;
  name: string;
  is_preset: boolean;
  components: string[];
  estimated_credits_per_minute?: number;
  created_at: string;
}

// ─── Billing ─────────────────────────────────────────────────────
export interface CreditTransaction {
  id: string;
  workspace_id: string;
  amount: number;
  type: "purchase" | "deduction" | "refund" | "bonus" | "plan_renewal";
  analysis_job_id?: string;
  payment_provider?: string;
  comment?: string;
  created_at: string;
}

export interface SubscriptionPlan {
  id: number;
  name: string;
  credits_per_month: number;
  max_video_duration_sec: number;
  results_retention_days: number;
  price: number;
  has_api_access: boolean;
  has_advanced_explainability: boolean;
}

// ─── Events (WebSocket) ──────────────────────────────────────────
export interface ProcessingEvent {
  type: "stage_start" | "stage_progress" | "stage_done" | "frame" | "component_done" | "error" | "complete";
  component?: string;
  stage?: string;
  progress?: number;
  message?: string;
  frame_url?: string;
  timestamp_sec?: number;
  bboxes?: BBox[];
  error?: string;
}

export interface BBox {
  x: number;
  y: number;
  w: number;
  h: number;
  label: string;
  confidence: number;
}

// ─── API responses ────────────────────────────────────────────────
export interface ApiError {
  detail: string;
  status_code?: number;
}

export interface PaginatedResponse<T> {
  items: T[];
  total: number;
  page: number;
  size: number;
}
