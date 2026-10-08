export type AnalysisPhase =
  | "queued"
  | "planned"
  | "validating"
  | "predicting"
  | "cancel_requested"
  | "cancelled"
  | "completed"
  | "failed";

export type SourceFormat =
  | "csv"
  | "json"
  | "jsonl"
  | "ndjson"
  | "sql_dump"
  | "sqlite"
  | "bson";

export type RiskLevel = "low" | "medium" | "high" | "critical";

export interface HealthStatus {
  status: "ok" | "degraded";
  model_ready: boolean;
}

export interface ModelStatus {
  ready: boolean;
  version: string | null;
  features: number | null;
  review_threshold: number | null;
  error: string | null;
}

export interface ApiErrorPayload {
  error: {
    code: string;
    message: string;
    details: string[];
  };
}

export interface CreateAnalysisResponse {
  analysis_id: string;
  status: "queued";
  status_url: string;
}

export interface AnalysisStatus {
  analysis_id: string;
  filename: string;
  status: AnalysisPhase;
  progress: number;
  stage: string;
  source_format?: SourceFormat;
  received_bytes?: number;
  can_cancel?: boolean;
  warnings: string[];
  errors: string[];
}

export interface DatasetInventory {
  dataset_id: string;
  display_label: string;
  row_count: number;
  fields: Array<{
    display_label: string | null;
    physical_type: string;
  }>;
}

export interface SourceInventory {
  analysis_id: string;
  filename: string;
  source_format: SourceFormat;
  file_size_bytes: number;
  datasets: DatasetInventory[];
  warnings: string[];
}

export type AnalysisProfileName = "client_risk" | "transaction_anomaly";

export interface AnalysisPlanProfile {
  profile: AnalysisProfileName;
  state: "planned" | "blocked" | "skipped";
  comparison_mode: "not_applicable" | "cohort" | "historical" | "mixed";
}

export interface AnalysisPlan {
  analysis_id: string;
  time_range: { start: string | null; end: string | null } | null;
  profiles: AnalysisPlanProfile[];
  warnings: string[];
}

export interface AnalysisMetrics {
  available: boolean;
  threshold: number;
  gini: number | null;
  ks: number | null;
  accuracy: number | null;
  precision: number | null;
  recall: number | null;
  roc_auc: number | null;
  pr_auc: number | null;
  confusion_matrix: number[][] | null;
  unavailable_reason: string | null;
}

export interface AnalysisSummary {
  analysis_id: string;
  model_version: string;
  threshold: number;
  summary: {
    rows: number;
    requires_review: number;
    risk_counts: Record<RiskLevel, number>;
    warnings: string[];
    target_present: boolean;
    target_valid: boolean;
  };
  metrics: AnalysisMetrics;
}

export interface ExplanationFactor {
  feature: string;
  value: string | number | null;
  contribution: number;
  direction: "increases_risk" | "decreases_risk";
}

export interface AnalysisRow {
  record_id: string;
  risk_probability: number;
  risk_level: RiskLevel;
  requires_review: boolean;
  explanation_factors: ExplanationFactor[];
  analysis_warnings: string[];
  [column: string]: unknown;
}

export interface ResultPage {
  items: AnalysisRow[];
  total: number;
  page: number;
  page_size: number;
  threshold: number;
}

export interface UniversalPage<T> {
  items: T[];
  total: number;
  page: number;
  page_size: number;
}

export interface TransactionRow extends AnalysisRow {
  transaction_id?: string;
  client_id?: string;
  sender_account_id?: string;
  recipient_account_id?: string;
  transaction_timestamp?: string;
  transaction_amount?: number;
  amount?: number;
  currency?: string;
  channel?: string;
  direction?: string;
  scenario_score?: number;
  graph_score?: number;
  rule_explanation?: unknown;
  graph_explanation?: unknown;
  ml_explanation?: unknown;
}

export interface RelationshipRow {
  relationship_id: string;
  kind: "transaction_client" | "transfer" | string;
  from_type: string;
  from_id: string;
  to_type: string;
  to_id: string;
  transaction_id?: string;
  risk_signal_score: number;
}

export interface UniversalResultsQuery {
  page?: number;
  pageSize?: number;
  riskLevel?: RiskLevel;
  requiresReview?: boolean;
  probabilityMin?: number;
  probabilityMax?: number;
  search?: string;
}

export interface AnalysisResultsQuery {
  page: number;
  pageSize: number;
  riskLevel?: RiskLevel;
  threshold?: number;
  requiresReview?: boolean;
  probabilityMin?: number;
  probabilityMax?: number;
  recordId?: string;
}

export interface ProbabilityBin {
  from: number;
  to: number;
  count: number;
}

export interface RiskDistribution {
  analysis_id: string;
  threshold: number;
  risk_counts: Record<RiskLevel, number>;
  probability_histogram: ProbabilityBin[];
}

export interface ReportDownload {
  blob: Blob;
  filename: string;
}

export type InvestigationStatus = "new" | "in_review" | "confirmed" | "dismissed";

export interface InvestigationEvent {
  event_id: number;
  previous_status: InvestigationStatus | null;
  status: InvestigationStatus;
  comment: string;
  occurred_at: number;
}

export interface ConfirmedFeedbackLabel {
  human_label: 0 | 1;
  source: "human_confirmed";
  profile: string | null;
  model_version: string | null;
  risk_probability: number | null;
  confirmed_at: number;
}

export interface InvestigationDetails {
  analysis_id: string;
  entity_id: string;
  status: InvestigationStatus;
  comment: string;
  updated_at: number | null;
  confirmed_label: ConfirmedFeedbackLabel | null;
  history: InvestigationEvent[];
}

export type UniversalExportKind = "full" | "review" | "transactions" | "relationships" | "mapping_quality";
