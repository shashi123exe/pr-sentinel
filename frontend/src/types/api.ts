/**
 * TypeScript API type definitions for PR Sentinel frontend.
 * Matches backend Pydantic schemas exactly.
 */

export type ReviewStatus = 'queued' | 'running' | 'completed' | 'failed';

export type Severity = 'critical' | 'high' | 'medium' | 'low';

export type FindingStatus = 'open' | 'approved' | 'dismissed';

export type Source = 'static' | 'ai';

export type Category =
  | 'logic_bug'
  | 'security'
  | 'performance'
  | 'error_handling'
  | 'edge_case'
  | 'code_quality'
  | 'testing'
  | 'missing_tests';

export const ALL_CATEGORIES: readonly Category[] = [
  'logic_bug',
  'security',
  'performance',
  'error_handling',
  'edge_case',
  'code_quality',
  'testing',
  'missing_tests',
] as const;

export type ReviewStage =
  | 'queued'
  | 'fetch_pr'
  | 'process_diff'
  | 'static_analysis'
  | 'retrieve_context'
  | 'ai_review'
  | 'validate_findings'
  | 'persist_results'
  | 'completed';

export const STAGE_PROGRESS_MAP: Record<ReviewStage | string, number> = {
  queued: 0,
  fetch_pr: 10,
  process_diff: 25,
  static_analysis: 45,
  retrieve_context: 60,
  ai_review: 80,
  validate_findings: 90,
  persist_results: 95,
  completed: 100,
} as const;

export const STAGE_DISPLAY_NAMES: Record<ReviewStage | string, string> = {
  queued: 'Queued',
  fetch_pr: 'Fetch PR',
  process_diff: 'Process Diff',
  static_analysis: 'Static Analysis',
  retrieve_context: 'Context Retrieval',
  ai_review: 'AI Review',
  validate_findings: 'Validate Findings',
  persist_results: 'Persist Results',
  completed: 'Completed',
} as const;

export interface FindingsSummary {
  critical: number;
  high: number;
  medium: number;
  low: number;
  total: number;
}

export interface ReviewCreate {
  pr_url: string;
}

export interface ReviewCreateResponse {
  id: string;
  status: ReviewStatus;
}

export interface ReviewListItem {
  id: string;
  pr_url: string;
  owner: string;
  repo: string;
  pr_number: number;
  pr_title: string;
  pr_author: string;
  status: ReviewStatus;
  current_stage: string;
  files_changed: number;
  additions: number;
  deletions: number;
  findings_summary: FindingsSummary;
  created_at: string;
  completed_at: string | null;
}

export interface ReviewOut {
  id: string;
  pr_url: string;
  owner: string;
  repo: string;
  pr_number: number;
  pr_title: string;
  pr_author: string;
  base_sha: string;
  head_sha: string;
  status: ReviewStatus;
  current_stage: string;
  progress_percent: number;
  error_code: string | null;
  error_message: string | null;
  files_changed: number;
  additions: number;
  deletions: number;
  truncated: boolean;
  llm_model: string;
  llm_tokens_in: number;
  llm_tokens_out: number;
  warnings: string[];
  findings_summary: FindingsSummary;
  dropped_findings_count: number;
  created_at: string;
  completed_at: string | null;
}

export interface FindingOut {
  id: number;
  review_id: string;
  file_path: string;
  line_start: number;
  line_end: number;
  title: string;
  severity: Severity;
  category: Category;
  source: Source;
  tool_name: string;
  rule_id: string;
  evidence: string;
  explanation: string;
  suggested_fix: string | null;
  confidence: number;
  fingerprint: string;
  status: FindingStatus;
  created_at: string;
  updated_at: string | null;
}

export interface FindingStatusUpdate {
  status: FindingStatus;
}

export interface ReviewFileOut {
  id: number;
  review_id: string;
  path: string;
  status: string;
  additions: number;
  deletions: number;
  patch: string | null;
  language: string | null;
  skipped_reason: string | null;
  findings_count: number;
}

export interface SettingsOut {
  id: number;
  max_files: number;
  max_diff_bytes: number;
  min_confidence: number;
  enabled_categories: Category[];
  gemini_model: string;
}

export interface SettingsUpdate {
  max_files?: number;
  max_diff_bytes?: number;
  min_confidence?: number;
  enabled_categories?: Category[];
  gemini_model?: string;
}

export interface Page<T> {
  items: T[];
  total: number;
  page: number;
  page_size: number;
}

export interface ApiErrorDetail {
  code: string;
  message: string;
  request_id?: string;
}

export interface ApiErrorResponse {
  error: ApiErrorDetail;
}

export interface HealthResponse {
  status: string;
  database: string;
}

export interface ListFindingsParams {
  severity?: Severity[];
  category?: Category;
  source?: Source;
  status?: FindingStatus;
  file_path?: string;
  min_confidence?: number;
  sort_by?: 'severity' | 'confidence' | 'file';
  sort_order?: 'asc' | 'desc';
  page?: number;
  page_size?: number;
}

export interface ListReviewsParams {
  status?: ReviewStatus;
  owner?: string;
  repo?: string;
  page?: number;
  page_size?: number;
}

export interface ListFilesParams {
  include_skipped?: boolean;
  page?: number;
  page_size?: number;
}
