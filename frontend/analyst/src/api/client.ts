import type {
  AnalysisStatus,
  AnalysisPlan,
  SourceInventory,
  AnalysisSummary,
  AnalysisResultsQuery,
  ApiErrorPayload,
  CreateAnalysisResponse,
  HealthStatus,
  ModelStatus,
  ResultPage,
  RiskDistribution,
  ReportDownload,
  AnalysisRow,
  TransactionRow,
  RelationshipRow,
  UniversalPage,
  UniversalResultsQuery,
  InvestigationDetails,
  InvestigationStatus,
  UniversalExportKind,
} from "../types/analysis";
import type { BankEvent, BankEventPage } from "../types/bankEvents";

const API_BASE = "/api/analyst";
let expectedAccountId: number | null = null;
export function setAnalystIdentity(id: number | null) { expectedAccountId = id; }
function sessionHeaders(init?: HeadersInit): Headers {
  const headers = new Headers(init);
  if (expectedAccountId !== null) headers.set("X-Account-ID", String(expectedAccountId));
  return headers;
}

async function checkSession(response: Response, requestedAccount: number | null) {
  if (requestedAccount !== expectedAccountId) throw new ApiClientError("account_changed", "Аккаунт изменился во время запроса.");
  let accountChanged = false;
  if (response.status === 409) {
    try {
      const payload = await response.clone().json();
      accountChanged = payload.error?.code === "account_changed" || payload.detail === "В другой вкладке сменился аккаунт. Обновите страницу и войдите заново.";
    } catch { /* An ordinary workflow conflict does not end a session. */ }
    if (requestedAccount !== expectedAccountId) throw new ApiClientError("account_changed", "Аккаунт изменился во время запроса.");
  }
  if (response.status === 401 || response.status === 403 || accountChanged) {
    window.dispatchEvent(new CustomEvent("analyst-session-ended", { detail: response.status }));
    throw new ApiClientError("session_ended", `Ошибка запроса (${response.status})`, [], true);
  }
}

export class ApiClientError extends Error {
  readonly code: string;
  readonly details: string[];
  readonly sessionEnded: boolean;

  constructor(code: string, message: string, details: string[] = [], sessionEnded = false) {
    super(message);
    this.name = "ApiClientError";
    this.code = code;
    this.details = details;
    this.sessionEnded = sessionEnded;
  }
}

async function request<T>(url: string, init?: RequestInit): Promise<T> {
  const requestedAccount = expectedAccountId;
  const response = await fetch(`${API_BASE}${url}`, { ...init, headers: sessionHeaders(init?.headers), credentials: "same-origin" });
  await checkSession(response, requestedAccount);
  if (!response.ok) {
    let payload: (ApiErrorPayload & { detail?: unknown }) | null = null;
    try {
      payload = (await response.json()) as ApiErrorPayload;
    } catch {
      // The fallback below remains readable when a proxy returns a non-JSON error.
    }
    throw new ApiClientError(
      payload?.error?.code ?? "request_failed",
      payload?.error?.message ?? (typeof payload?.detail === "string" ? payload.detail : `Ошибка запроса (${response.status})`),
      payload?.error?.details ?? [],
    );
  }
  return (await response.json()) as T;
}

function query(params: Record<string, string | number | undefined>): string {
  const values = new URLSearchParams();
  Object.entries(params).forEach(([key, value]) => {
    if (value !== undefined) values.set(key, String(value));
  });
  const serialized = values.toString();
  return serialized ? `?${serialized}` : "";
}

export async function createAnalysis(file: File): Promise<CreateAnalysisResponse> {
  const body = new FormData();
  body.append("file", file);
  return request<CreateAnalysisResponse>("/analyses?auto_run=false", { method: "POST", body });
}

export function getHealthStatus(): Promise<HealthStatus> {
  return request<HealthStatus>("/health");
}

export function getModelStatus(): Promise<ModelStatus> {
  return request<ModelStatus>("/model");
}

export function getAnalysisStatus(analysisId: string): Promise<AnalysisStatus> {
  return request<AnalysisStatus>(`/analyses/${analysisId}/status`);
}

export function getAnalysisInventory(analysisId: string): Promise<SourceInventory> {
  return request<SourceInventory>(`/analyses/${analysisId}/inventory`);
}

export function getAnalysisPlan(analysisId: string): Promise<AnalysisPlan> {
  return request<AnalysisPlan>(`/analyses/${analysisId}/plan`);
}

export function updateAnalysisPeriod(
  analysisId: string,
  period: { start: string | null; end: string | null },
): Promise<AnalysisPlan> {
  return request<AnalysisPlan>(`/analyses/${analysisId}/plan`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(period),
  });
}

export function runAnalysis(analysisId: string): Promise<{ analysis_id: string; status: "queued" }> {
  return request(`/analyses/${analysisId}/run`, { method: "POST" });
}

export function cancelAnalysis(
  analysisId: string,
): Promise<{ analysis_id: string; status: "cancel_requested" | "cancelled" }> {
  return request(`/analyses/${analysisId}/cancel`, { method: "POST" });
}

export async function getAnalysisSummary(
  analysisId: string,
  threshold?: number,
): Promise<AnalysisSummary> {
  const payload = await request<AnalysisSummary>(
    `/analyses/${analysisId}/summary${query({ threshold })}`,
  );
  const incomplete = !payload.summary.risk_counts
    || !Array.isArray(payload.summary.warnings)
    || payload.summary.target_present === undefined
    || payload.summary.target_valid === undefined;
  if (!incomplete) return payload;

  // Older universal sessions can still be open after the application was
  // updated. Complete their legacy summary from stable companion endpoints so
  // a restored session renders correctly without forcing the user to rerun it.
  const [distribution, status] = await Promise.all([
    request<RiskDistribution>(
      `/analyses/${analysisId}/distribution${query({ threshold, bins: 10 })}`,
    ),
    getAnalysisStatus(analysisId),
  ]);
  return {
    ...payload,
    summary: {
      ...payload.summary,
      risk_counts: payload.summary.risk_counts ?? distribution.risk_counts,
      warnings: payload.summary.warnings ?? status.warnings,
      target_present: payload.summary.target_present ?? payload.metrics.available,
      target_valid: payload.summary.target_valid ?? payload.metrics.available,
    },
  };
}

export function getAnalysisResults(
  analysisId: string,
  options: AnalysisResultsQuery,
): Promise<ResultPage> {
  return request<ResultPage>(
    `/analyses/${analysisId}/results${query({
      page: options.page,
      page_size: options.pageSize,
      risk_level: options.riskLevel,
      threshold: options.threshold,
      requires_review: options.requiresReview === undefined
        ? undefined
        : String(options.requiresReview),
      probability_min: options.probabilityMin,
      probability_max: options.probabilityMax,
      record_id: options.recordId || undefined,
    })}`,
  );
}

export function getAnalysisDistribution(
  analysisId: string,
  threshold?: number,
  bins = 10,
): Promise<RiskDistribution> {
  return request<RiskDistribution>(
    `/analyses/${analysisId}/distribution${query({ bins, threshold })}`,
  );
}

function universalQuery(options: UniversalResultsQuery = {}) {
  return query({
    page: options.page ?? 1,
    page_size: options.pageSize ?? 50,
    risk_level: options.riskLevel,
    requires_review: options.requiresReview === undefined ? undefined : String(options.requiresReview),
    probability_min: options.probabilityMin,
    probability_max: options.probabilityMax,
    search: options.search || undefined,
  });
}

function normalizeRows<T extends AnalysisRow>(page: UniversalPage<T>): UniversalPage<T> {
  return {
    ...page,
    items: page.items.map((row) => ({
      ...row,
      explanation_factors: row.explanation_factors ?? [],
      analysis_warnings: row.analysis_warnings ?? [],
    })),
  };
}

export async function getAnalysisClients(
  analysisId: string,
  options: UniversalResultsQuery = {},
): Promise<UniversalPage<AnalysisRow>> {
  return normalizeRows(await request(`/analyses/${analysisId}/clients${universalQuery(options)}`));
}

export async function getAnalysisTransactions(
  analysisId: string,
  options: UniversalResultsQuery = {},
): Promise<UniversalPage<TransactionRow>> {
  return normalizeRows(await request(`/analyses/${analysisId}/transactions${universalQuery(options)}`));
}

export function getAnalysisRelationships(
  analysisId: string,
  options: { page?: number; pageSize?: number; kind?: string; entityId?: string; probabilityMin?: number } = {},
): Promise<UniversalPage<RelationshipRow>> {
  return request(`/analyses/${analysisId}/relationships${query({
    page: options.page ?? 1,
    page_size: options.pageSize ?? 100,
    kind: options.kind,
    entity_id: options.entityId,
    probability_min: options.probabilityMin,
  })}`);
}

export function reportUrl(
  analysisId: string,
  threshold?: number,
  requiresReview = false,
): string {
  return `${API_BASE}/analyses/${analysisId}/report.csv${query({
    threshold,
    requires_review: requiresReview ? "true" : undefined,
  })}`;
}

export async function downloadAnalysisReport(
  analysisId: string,
  threshold: number,
  requiresReview = false,
): Promise<ReportDownload> {
  const requestedAccount = expectedAccountId;
  const response = await fetch(reportUrl(analysisId, threshold, requiresReview), { headers: sessionHeaders(), credentials: "same-origin" });
  await checkSession(response, requestedAccount);
  if (!response.ok) {
    throw new ApiClientError(
      "report_download_failed",
      "Не удалось подготовить CSV. Результат анализа сохранён — попробуйте скачать файл ещё раз.",
    );
  }
  const disposition = response.headers.get("Content-Disposition") ?? "";
  const matchedName = /filename="?([^";]+)"?/i.exec(disposition)?.[1];
  return {
    blob: await response.blob(),
    filename: matchedName ?? `analysis-${analysisId}${requiresReview ? "-review" : ""}.csv`,
  };
}

export function getInvestigation(analysisId: string, entityId: string): Promise<InvestigationDetails> {
  return request(`/analyses/${analysisId}/investigations/${encodeURIComponent(entityId)}`);
}

export function updateInvestigation(
  analysisId: string,
  entityId: string,
  patch: { status: InvestigationStatus; comment: string },
): Promise<InvestigationDetails> {
  return request(`/analyses/${analysisId}/investigations/${encodeURIComponent(entityId)}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(patch),
  });
}

export async function downloadUniversalExport(
  analysisId: string,
  kind: UniversalExportKind,
  masked = true,
): Promise<ReportDownload> {
  const requestedAccount = expectedAccountId;
  const response = await fetch(`${API_BASE}/analyses/${analysisId}/exports/${kind}${query({ masked: String(masked) })}`, { headers: sessionHeaders(), credentials: "same-origin" });
  await checkSession(response, requestedAccount);
  if (!response.ok) throw new ApiClientError("report_download_failed", "Не удалось подготовить выбранную выгрузку.");
  const disposition = response.headers.get("Content-Disposition") ?? "";
  const matchedName = /filename="?([^";]+)"?/i.exec(disposition)?.[1];
  return { blob: await response.blob(), filename: matchedName ?? `analysis-${analysisId}-${kind}.csv` };
}

export async function deleteAnalysis(analysisId: string): Promise<void> {
  const requestedAccount = expectedAccountId;
  const response = await fetch(`${API_BASE}/analyses/${analysisId}`, {
    method: "DELETE",
    headers: sessionHeaders(),
    credentials: "same-origin",
  });
  await checkSession(response, requestedAccount);
  if (!response.ok && response.status !== 404) {
    throw new ApiClientError("delete_failed", "Не удалось удалить сессию анализа.");
  }
}

export function getBankEvents(options: { page: number; status?: string; senderName?: string }): Promise<BankEventPage> {
  return request(`/bank-events${query({ page: options.page, page_size: 20, status: options.status || undefined, sender_name: options.senderName || undefined })}`);
}
export function getBankEvent(id: number): Promise<BankEvent> { return request(`/bank-events/${id}`); }
export function decideBankEvent(id: number, action: "approve" | "reject", note: string): Promise<BankEvent> {
  return request(`/bank-events/${id}/${action}`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ note }) });
}
