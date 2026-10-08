import { useCallback, useEffect, useRef, useState } from "react";
import {
  ApiClientError,
  cancelAnalysis,
  createAnalysis,
  deleteAnalysis,
  getAnalysisDistribution,
  getAnalysisClients,
  getAnalysisTransactions,
  getAnalysisRelationships,
  getAnalysisInventory,
  getAnalysisPlan,
  getAnalysisResults,
  getAnalysisStatus,
  getAnalysisSummary,
  runAnalysis,
  updateAnalysisPeriod,
} from "./api/client";
import { DashboardShell } from "./layout/DashboardShell";
import { DataQualityPage } from "./routes/DataQualityPage";
import { ModelQualityPage } from "./routes/ModelQualityPage";
import { NewAnalysisPage } from "./routes/NewAnalysisPage";
import { OverviewPage } from "./routes/OverviewPage";
import { RiskRecordsPage } from "./routes/RiskRecordsPage";
import { TransactionsPage } from "./routes/TransactionsPage";
import { RelationshipsPage } from "./routes/RelationshipsPage";
import { useDashboardRoute } from "./routes/useDashboardRoute";
import type {
  AnalysisRow,
  AnalysisPlan,
  AnalysisStatus,
  AnalysisSummary,
  ResultPage,
  RiskDistribution,
  RiskLevel,
  SourceInventory,
  TransactionRow,
  RelationshipRow,
  UniversalPage,
} from "./types/analysis";
import type { SessionNotification } from "./types/notifications";
import { parseQualityIssues } from "./utils/dataQuality";

const PAGE_SIZE = 25;
const delay = (milliseconds: number) =>
  new Promise((resolve) => window.setTimeout(resolve, milliseconds));

const errorMessages: Record<string, string> = {
  analysis_failed: "Анализ не удалось завершить.",
  analysis_not_found: "Предыдущая сессия уже завершена или была удалена.",
  file_too_large: "Размер файла превышает допустимые 500 МБ.",
  model_unavailable: "Локальная модель сейчас недоступна.",
  request_failed: "Не удалось связаться с локальным сервисом анализа.",
  unsupported_file: "Поддерживаются CSV, JSON, SQL, SQLite и BSON-файлы.",
};

interface VisibleError {
  message: string;
  details: string[];
}

interface AdvancedRecordFilters {
  requiresReview: boolean;
  probabilityMin: number;
  probabilityMax: number;
  recordId: string;
}

const DEFAULT_ADVANCED_FILTERS: AdvancedRecordFilters = {
  requiresReview: false,
  probabilityMin: 0,
  probabilityMax: 100,
  recordId: "",
};

export default function App({ analystId = 0 }: { analystId?: number }) {
  const ACTIVE_ANALYSIS_KEY = `risk-ledger-active-analysis:${analystId}`;
  const [analysisId, setAnalysisId] = useState<string | null>(null);
  const [status, setStatus] = useState<AnalysisStatus | null>(null);
  const [inventory, setInventory] = useState<SourceInventory | null>(null);
  const [plan, setPlan] = useState<AnalysisPlan | null>(null);
  const [summary, setSummary] = useState<AnalysisSummary | null>(null);
  const [results, setResults] = useState<ResultPage | null>(null);
  const [topRiskRows, setTopRiskRows] = useState<AnalysisRow[]>([]);
  const [distribution, setDistribution] = useState<RiskDistribution | null>(null);
  const [clients, setClients] = useState<UniversalPage<AnalysisRow> | null>(null);
  const [transactions, setTransactions] = useState<UniversalPage<TransactionRow> | null>(null);
  const [relationships, setRelationships] = useState<UniversalPage<RelationshipRow> | null>(null);
  const [error, setError] = useState<VisibleError | null>(null);
  const [riskLevel, setRiskLevel] = useState<RiskLevel | "all">("all");
  const [page, setPage] = useState(1);
  const [thresholdPercent, setThresholdPercent] = useState(50);
  const [modelThresholdPercent, setModelThresholdPercent] = useState(50);
  const [appliedThreshold, setAppliedThreshold] = useState<number | undefined>();
  const [advancedFilters, setAdvancedFilters] = useState(DEFAULT_ADVANCED_FILTERS);
  const [appliedAdvancedFilters, setAppliedAdvancedFilters] = useState(DEFAULT_ADVANCED_FILTERS);
  const [refreshing, setRefreshing] = useState(false);
  const [cancelling, setCancelling] = useState(false);
  const [starting, setStarting] = useState(false);
  const runId = useRef(0);
  const restorationStarted = useRef(false);
  const resultsRequestId = useRef(0);
  const workspacePhase = summary && results
    ? "ready"
    : status
      ? "processing"
      : error
        ? "failed"
        : "empty";
  const { activeRoute, navigate } = useDashboardRoute(workspacePhase);
  const previousWorkspacePhase = useRef(workspacePhase);
  const notificationSources = summary?.summary.warnings ?? status?.warnings ?? [];
  const notifications: SessionNotification[] = parseQualityIssues(notificationSources);
  if (error) {
    const criticalDetails = error.details.length ? parseQualityIssues(error.details) : [];
    notifications.unshift({
      id: "session-error",
      level: "critical",
      title: error.message,
      description: "Текущий анализ остановлен. Исправьте файл или повторите действие.",
      technicalCode: "ANALYSIS_STOPPED",
    }, ...criticalDetails);
  }

  useEffect(() => {
    const becameReady =
      workspacePhase === "ready" && previousWorkspacePhase.current !== "ready";
    previousWorkspacePhase.current = workspacePhase;
    if (becameReady && activeRoute === "new-analysis") {
      navigate("overview", true);
    }
  }, [activeRoute, navigate, workspacePhase]);

  const loadResults = useCallback(
    async (
      id: string,
      nextPage: number,
      nextRisk: RiskLevel | "all",
      threshold?: number,
      updateOverview = false,
      filters: AdvancedRecordFilters = DEFAULT_ADVANCED_FILTERS,
      updateDistribution = updateOverview,
    ) => {
      const requestId = resultsRequestId.current + 1;
      resultsRequestId.current = requestId;
      const [nextSummary, nextResults, nextDistribution, nextClients, nextTransactions, nextRelationships] = await Promise.all([
        getAnalysisSummary(id, threshold),
        getAnalysisResults(id, {
          page: nextPage,
          pageSize: PAGE_SIZE,
          riskLevel: nextRisk === "all" ? undefined : nextRisk,
          threshold,
          requiresReview: filters.requiresReview ? true : undefined,
          probabilityMin: filters.probabilityMin > 0 ? filters.probabilityMin / 100 : undefined,
          probabilityMax: filters.probabilityMax < 100 ? filters.probabilityMax / 100 : undefined,
          recordId: filters.recordId.trim() || undefined,
        }),
        updateDistribution ? getAnalysisDistribution(id, threshold) : Promise.resolve(null),
        updateOverview ? getAnalysisClients(id, { pageSize: 100 }) : Promise.resolve(null),
        updateOverview ? getAnalysisTransactions(id, { pageSize: 100 }) : Promise.resolve(null),
        updateOverview ? getAnalysisRelationships(id, { pageSize: 100 }) : Promise.resolve(null),
      ]);
      if (requestId !== resultsRequestId.current) return;
      setSummary(nextSummary);
      setResults(nextResults);
      if (updateOverview) setTopRiskRows(nextResults.items.slice(0, 5));
      if (updateOverview) setModelThresholdPercent(Math.round(nextSummary.threshold * 100));
      if (nextDistribution) setDistribution(nextDistribution);
      if (nextClients) setClients(nextClients);
      if (nextTransactions) setTransactions(nextTransactions);
      if (nextRelationships) setRelationships(nextRelationships);
      setThresholdPercent(Math.round(nextSummary.threshold * 100));
    },
    [],
  );

  const pollAnalysis = useCallback(async (id: string, currentRun: number) => {
    while (runId.current === currentRun) {
      const nextStatus = await getAnalysisStatus(id);
      if (runId.current !== currentRun) return;
      setStatus(nextStatus);
      if (nextStatus.status === "failed") {
        throw new ApiClientError(
          "analysis_failed",
          nextStatus.stage === "schema_rejected"
            ? "Файл не прошёл проверку совместимости."
            : "Анализ не удалось завершить.",
          nextStatus.errors,
        );
      }
      if (nextStatus.status === "planned") {
        const [nextInventory, nextPlan] = await Promise.all([
          getAnalysisInventory(id),
          getAnalysisPlan(id),
        ]);
        if (runId.current !== currentRun) return;
        setInventory(nextInventory);
        setPlan(nextPlan);
        return;
      }
      if (nextStatus.status === "cancelled") return;
      if (nextStatus.status === "completed") {
        await loadResults(id, 1, "all", undefined, true);
        return;
      }
      await delay(350);
    }
  }, [loadResults]);

  const showError = useCallback((caught: unknown) => {
    if (caught instanceof ApiClientError) {
      setError({
        message: errorMessages[caught.code] ?? caught.message,
        details: caught.details,
      });
    } else {
      setError({ message: "Не удалось завершить анализ.", details: [] });
    }
  }, []);

  useEffect(() => {
    if (restorationStarted.current) return;
    restorationStarted.current = true;
    const storedAnalysisId = window.localStorage.getItem(ACTIVE_ANALYSIS_KEY);
    if (!storedAnalysisId) return;

    const currentRun = runId.current + 1;
    runId.current = currentRun;
    setAnalysisId(storedAnalysisId);
    setStatus({
      analysis_id: storedAnalysisId,
      filename: "Восстановление локальной сессии",
      status: "queued",
      progress: 0,
      stage: "queued",
      warnings: [],
      errors: [],
    });

    void pollAnalysis(storedAnalysisId, currentRun).catch((caught: unknown) => {
      setStatus(null);
      if (caught instanceof ApiClientError && caught.code === "analysis_not_found") {
        setAnalysisId(null);
        window.localStorage.removeItem(ACTIVE_ANALYSIS_KEY);
        return;
      }
      showError(caught);
    });
  }, [ACTIVE_ANALYSIS_KEY, pollAnalysis, showError]);

  useEffect(() => () => {
    runId.current += 1;
    resultsRequestId.current += 1;
    restorationStarted.current = false;
  }, []);

  const handleUpload = async (file: File) => {
    const currentRun = runId.current + 1;
    runId.current = currentRun;
    setError(null);

    const previousAnalysisId = analysisId ?? window.localStorage.getItem(ACTIVE_ANALYSIS_KEY);
    if (previousAnalysisId) {
      try {
        await deleteAnalysis(previousAnalysisId);
        if (runId.current !== currentRun) return;
      } catch (caught) {
        if (runId.current !== currentRun) return;
        if (!(caught instanceof ApiClientError) || caught.code !== "analysis_not_found") {
          showError(caught);
          return;
        }
      }
      window.localStorage.removeItem(ACTIVE_ANALYSIS_KEY);
      setAnalysisId(null);
    }

    setSummary(null);
    setInventory(null);
    setPlan(null);
    setResults(null);
    setTopRiskRows([]);
    setDistribution(null);
    setClients(null);
    setTransactions(null);
    setRelationships(null);
    setRiskLevel("all");
    setPage(1);
    setAppliedThreshold(undefined);
    setAdvancedFilters(DEFAULT_ADVANCED_FILTERS);
    setAppliedAdvancedFilters(DEFAULT_ADVANCED_FILTERS);
    setStatus({
      analysis_id: "pending",
      filename: file.name,
      status: "queued",
      progress: 0,
      stage: "queued",
      warnings: [],
      errors: [],
    });

    try {
      const created = await createAnalysis(file);
      if (runId.current !== currentRun) return;
      setAnalysisId(created.analysis_id);
      window.localStorage.setItem(ACTIVE_ANALYSIS_KEY, created.analysis_id);
      await pollAnalysis(created.analysis_id, currentRun);
    } catch (caught) {
      if (runId.current !== currentRun) return;
      setStatus(null);
      setSummary(null);
      setResults(null);
      setTopRiskRows([]);
      setDistribution(null);
      setClients(null);
      setTransactions(null);
      setRelationships(null);
      showError(caught);
    }
  };

  const handleRun = async (period: { start: string | null; end: string | null }) => {
    if (!analysisId || !status || status.status !== "planned") return;
    const currentRun = runId.current + 1;
    runId.current = currentRun;
    setStarting(true);
    setError(null);
    try {
      if (plan?.time_range) {
        const nextPlan = await updateAnalysisPeriod(analysisId, period);
        if (runId.current !== currentRun) return;
        setPlan(nextPlan);
      }
      await runAnalysis(analysisId);
      if (runId.current !== currentRun) return;
      setStatus({
        ...status,
        status: "validating",
        stage: "validating",
        progress: Math.max(status.progress, 46),
        can_cancel: true,
      });
      await pollAnalysis(analysisId, currentRun);
    } catch (caught) {
      showError(caught);
    } finally {
      setStarting(false);
    }
  };

  const handleCancel = async () => {
    if (!analysisId || cancelling) return;
    const currentRun = runId.current;
    setCancelling(true);
    setError(null);
    try {
      await cancelAnalysis(analysisId);
      if (runId.current !== currentRun) return;
      const nextStatus = await getAnalysisStatus(analysisId);
      if (runId.current !== currentRun) return;
      setStatus(nextStatus);
    } catch (caught) {
      showError(caught);
    } finally {
      setCancelling(false);
    }
  };

  const refresh = async (
    nextPage: number,
    nextRisk: RiskLevel | "all",
    threshold = appliedThreshold,
  ) => {
    if (!analysisId) return;
    setRefreshing(true);
    setError(null);
    try {
      await loadResults(analysisId, nextPage, nextRisk, threshold, false, appliedAdvancedFilters);
    } catch (caught) {
      showError(caught);
    } finally {
      setRefreshing(false);
    }
  };

  const changeRiskLevel = (nextRisk: RiskLevel | "all") => {
    setRiskLevel(nextRisk);
    setPage(1);
    void refresh(1, nextRisk);
  };

  const changePage = (nextPage: number) => {
    setPage(nextPage);
    void refresh(nextPage, riskLevel);
  };

  const applyThreshold = () => {
    const threshold = thresholdPercent / 100;
    setAppliedThreshold(threshold);
    setPage(1);
    if (!analysisId) return;
    setRefreshing(true);
    setError(null);
    void loadResults(analysisId, 1, riskLevel, threshold, false, appliedAdvancedFilters, true)
      .catch(showError)
      .finally(() => setRefreshing(false));
  };

  const resetThreshold = () => {
    setThresholdPercent(modelThresholdPercent);
    setAppliedThreshold(undefined);
    setPage(1);
    if (!analysisId) return;
    setRefreshing(true);
    void loadResults(analysisId, 1, riskLevel, undefined, false, appliedAdvancedFilters, true)
      .catch(showError)
      .finally(() => setRefreshing(false));
  };

  const applyAdvancedFilters = () => {
    const normalized = {
      requiresReview: advancedFilters.requiresReview,
      probabilityMin: Math.max(0, Math.min(100, advancedFilters.probabilityMin)),
      probabilityMax: Math.max(0, Math.min(100, advancedFilters.probabilityMax)),
      recordId: advancedFilters.recordId,
    };
    if (normalized.probabilityMin > normalized.probabilityMax) {
      setError({ message: "Минимальная вероятность не может быть больше максимальной.", details: [] });
      return;
    }
    setAdvancedFilters(normalized);
    setAppliedAdvancedFilters(normalized);
    setPage(1);
    if (!analysisId) return;
    setRefreshing(true);
    setError(null);
    void loadResults(analysisId, 1, riskLevel, appliedThreshold, false, normalized)
      .catch(showError)
      .finally(() => setRefreshing(false));
  };

  const resetAdvancedFilters = () => {
    setAdvancedFilters(DEFAULT_ADVANCED_FILTERS);
    setAppliedAdvancedFilters(DEFAULT_ADVANCED_FILTERS);
    setRiskLevel("all");
    setPage(1);
    if (!analysisId) return;
    setRefreshing(true);
    setError(null);
    void loadResults(analysisId, 1, "all", appliedThreshold, false, DEFAULT_ADVANCED_FILTERS)
      .catch(showError)
      .finally(() => setRefreshing(false));
  };

  const closeSession = async (): Promise<boolean> => {
    runId.current += 1;
    const currentRun = runId.current;
    if (analysisId) {
      try {
        await deleteAnalysis(analysisId);
        if (runId.current !== currentRun) return false;
      } catch (caught) {
        showError(caught);
        return false;
      }
    }
    setAnalysisId(null);
    setStatus(null);
    setInventory(null);
    setPlan(null);
    setSummary(null);
    setResults(null);
    setTopRiskRows([]);
    setDistribution(null);
    setClients(null);
    setTransactions(null);
    setRelationships(null);
    setError(null);
    window.localStorage.removeItem(ACTIVE_ANALYSIS_KEY);
    navigate("new-analysis", true);
    return true;
  };

  const processing = Boolean(status && !summary);

  if (activeRoute === "new-analysis") {
    return (
      <DashboardShell
        analystId={analystId}
        activeRoute={activeRoute}
        notifications={notifications}
        phase={workspacePhase}
        onNavigate={navigate}
      >
        <NewAnalysisPage
          error={error}
          hasActiveAnalysis={Boolean(analysisId || summary)}
          processing={processing}
          status={status}
          inventory={inventory}
          plan={plan}
          cancelling={cancelling}
          starting={starting}
          onCancel={() => { void handleCancel(); }}
          onClearError={() => { void closeSession(); }}
          onRun={(period) => { void handleRun(period); }}
          onStart={(file) => { void handleUpload(file); }}
        />
      </DashboardShell>
    );
  }

  if (!summary || !results || !analysisId) {
    return (
      <DashboardShell
        analystId={analystId}
        activeRoute={activeRoute}
        notifications={notifications}
        phase={workspacePhase}
        onNavigate={navigate}
      >
        <div className="ui-state ui-state--loading" role="status">
          <span className="ui-spinner" aria-hidden="true" />
          <span>Подготавливаем результаты анализа…</span>
        </div>
      </DashboardShell>
    );
  }

  let routeContent;
  if (activeRoute === "risk-records") {
    routeContent = (
      <RiskRecordsPage
        analysisId={analysisId}
        busy={refreshing}
        distribution={distribution}
        errorMessage={error?.message}
        modelThresholdPercent={modelThresholdPercent}
        page={clients?.page ?? page}
        pageSize={clients?.page_size ?? results.page_size}
        probabilityMax={advancedFilters.probabilityMax}
        probabilityMin={advancedFilters.probabilityMin}
        recordId={advancedFilters.recordId}
        requiresReview={advancedFilters.requiresReview}
        riskLevel={riskLevel}
        rows={clients?.items.length ? clients.items : results.items}
        relatedTransactions={transactions?.items ?? []}
        thresholdPercent={thresholdPercent}
        total={clients?.total ?? results.total}
        onFiltersApply={applyAdvancedFilters}
        onFiltersReset={resetAdvancedFilters}
        onPageChange={changePage}
        onRiskLevelChange={changeRiskLevel}
        onThresholdApply={applyThreshold}
        onThresholdChange={setThresholdPercent}
        onThresholdReset={resetThreshold}
        onProbabilityMaxChange={(value) => setAdvancedFilters((current) => ({ ...current, probabilityMax: value }))}
        onProbabilityMinChange={(value) => setAdvancedFilters((current) => ({ ...current, probabilityMin: value }))}
        onRecordIdChange={(value) => setAdvancedFilters((current) => ({ ...current, recordId: value }))}
        onRequiresReviewChange={(value) => setAdvancedFilters((current) => ({ ...current, requiresReview: value }))}
      />
    );
  } else if (activeRoute === "transactions") {
    routeContent = <TransactionsPage analysisId={analysisId} rows={transactions?.items ?? []} total={transactions?.total ?? 0} />;
  } else if (activeRoute === "relationships") {
    routeContent = <RelationshipsPage rows={relationships?.items ?? []} total={relationships?.total ?? 0} />;
  } else if (activeRoute === "model-quality") {
    routeContent = <ModelQualityPage metrics={summary.metrics} />;
  } else if (activeRoute === "data-quality") {
    routeContent = (
      <DataQualityPage
        targetPresent={summary.summary.target_present}
        targetValid={summary.summary.target_valid}
        warnings={summary.summary.warnings}
      />
    );
  } else {
    routeContent = (
      <OverviewPage
        analysisId={analysisId}
        filename={status?.filename ?? null}
        rows={topRiskRows}
        clients={clients?.items ?? topRiskRows}
        clientTotal={clients?.total ?? results.total}
        transactions={transactions?.items ?? []}
        transactionTotal={transactions?.total ?? 0}
        summary={summary}
        onCloseSession={closeSession}
        onNavigate={navigate}
      />
    );
  }

  return (
    <DashboardShell
        analystId={analystId}
      activeRoute={activeRoute}
      notifications={notifications}
      phase={workspacePhase}
      onNavigate={navigate}
    >
      {routeContent}
    </DashboardShell>
  );
}
