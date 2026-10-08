import type {
  AnalysisStatus,
  AnalysisSummary,
  ResultPage,
  RiskLevel,
} from "./analysis";

export type DashboardRouteId =
  | "new-analysis"
  | "overview"
  | "risk-records"
  | "transactions"
  | "relationships"
  | "model-quality"
  | "data-quality";

export interface DashboardRouteDefinition {
  id: DashboardRouteId;
  path: string;
  label: string;
  requiresCompletedAnalysis: boolean;
}

export const DASHBOARD_ROUTES: readonly DashboardRouteDefinition[] = [
  {
    id: "new-analysis",
    path: "/new-analysis",
    label: "Новый анализ",
    requiresCompletedAnalysis: false,
  },
  {
    id: "overview",
    path: "/overview",
    label: "Обзор",
    requiresCompletedAnalysis: true,
  },
  {
    id: "risk-records",
    path: "/risk-records",
    label: "Клиенты",
    requiresCompletedAnalysis: true,
  },
  {
    id: "transactions",
    path: "/transactions",
    label: "Операции",
    requiresCompletedAnalysis: true,
  },
  {
    id: "relationships",
    path: "/relationships",
    label: "Связи",
    requiresCompletedAnalysis: true,
  },
  {
    id: "model-quality",
    path: "/model-quality",
    label: "Качество модели",
    requiresCompletedAnalysis: true,
  },
  {
    id: "data-quality",
    path: "/data-quality",
    label: "Качество данных",
    requiresCompletedAnalysis: true,
  },
] as const;

export type DisplayDensity = "comfortable" | "compact";
export type ExplanationMode = "simple" | "expert";
export type ContrastMode = "standard" | "high";

export interface DashboardPreferences {
  density: DisplayDensity;
  explanationMode: ExplanationMode;
  contrast: ContrastMode;
  textScale: 100 | 112 | 125 | 150 | 200;
}

export const DEFAULT_DASHBOARD_PREFERENCES: DashboardPreferences = {
  density: "comfortable",
  explanationMode: "simple",
  contrast: "standard",
  textScale: 100,
};

export interface RiskRecordFilters {
  riskLevel: RiskLevel | "all";
  requiresReview: boolean | null;
  probabilityMin: number;
  probabilityMax: number;
  recordId: string;
  threshold?: number;
  page: number;
  pageSize: number;
}

export const DEFAULT_RISK_RECORD_FILTERS: RiskRecordFilters = {
  riskLevel: "all",
  requiresReview: null,
  probabilityMin: 0,
  probabilityMax: 1,
  recordId: "",
  page: 1,
  pageSize: 25,
};

export type WorkspacePhase =
  | "empty"
  | "uploading"
  | "processing"
  | "ready"
  | "failed";

export interface AnalysisWorkspaceState {
  phase: WorkspacePhase;
  activeRoute: DashboardRouteId;
  analysisId: string | null;
  filename: string | null;
  status: AnalysisStatus | null;
  summary: AnalysisSummary | null;
  results: ResultPage | null;
  filters: RiskRecordFilters;
  preferences: DashboardPreferences;
}

export const INITIAL_ANALYSIS_WORKSPACE_STATE: AnalysisWorkspaceState = {
  phase: "empty",
  activeRoute: "new-analysis",
  analysisId: null,
  filename: null,
  status: null,
  summary: null,
  results: null,
  filters: DEFAULT_RISK_RECORD_FILTERS,
  preferences: DEFAULT_DASHBOARD_PREFERENCES,
};

export function isDashboardRouteAvailable(
  route: DashboardRouteDefinition,
  phase: WorkspacePhase,
): boolean {
  return !route.requiresCompletedAnalysis || phase === "ready";
}
