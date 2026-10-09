import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import App from "../src/App";
import { ApiClientError } from "../src/api/client";
import type { AnalysisStatus, AnalysisSummary, ResultPage } from "../src/types/analysis";

const api = vi.hoisted(() => ({
  createAnalysis: vi.fn(),
  cancelAnalysis: vi.fn(),
  getAnalysisInventory: vi.fn(),
  getAnalysisPlan: vi.fn(),
  updateAnalysisPeriod: vi.fn(),
  runAnalysis: vi.fn(),
  getAnalysisStatus: vi.fn(),
  getAnalysisSummary: vi.fn(),
  getAnalysisResults: vi.fn(),
  getAnalysisDistribution: vi.fn(),
  getAnalysisClients: vi.fn(),
  getAnalysisTransactions: vi.fn(),
  getAnalysisRelationships: vi.fn(),
  downloadAnalysisReport: vi.fn(),
  deleteAnalysis: vi.fn(),
  reportUrl: vi.fn(),
}));

vi.mock("../src/api/client", () => ({
  ApiClientError: class ApiClientError extends Error {
    code: string;
    details: string[];
    constructor(code: string, message: string, details: string[] = []) {
      super(message);
      this.code = code;
      this.details = details;
    }
  },
  ...api,
}));

const completeStatus: AnalysisStatus = {
  analysis_id: "analysis-1",
  filename: "clients.csv",
  status: "completed",
  progress: 100,
  stage: "completed",
  warnings: [],
  errors: [],
};

const plannedStatus: AnalysisStatus = {
  analysis_id: "analysis-1",
  filename: "transactions.sqlite",
  status: "planned",
  progress: 45,
  stage: "planning",
  source_format: "sqlite",
  received_bytes: 4096,
  can_cancel: true,
  warnings: [],
  errors: [],
};

const inventory = {
  analysis_id: "analysis-1",
  filename: "transactions.sqlite",
  source_format: "sqlite" as const,
  file_size_bytes: 4096,
  datasets: [{
    dataset_id: "technical_transactions_table",
    display_label: "technical_transactions_table",
    row_count: 12068,
    fields: [{ display_label: "transaction_timestamp", physical_type: "datetime" }],
  }],
  warnings: [],
};

const plan = {
  analysis_id: "analysis-1",
  time_range: { start: "2026-01-01T00:00:00Z", end: "2026-04-30T23:59:00Z" },
  profiles: [
    { profile: "client_risk" as const, state: "skipped" as const, comparison_mode: "not_applicable" as const },
    { profile: "transaction_anomaly" as const, state: "planned" as const, comparison_mode: "historical" as const },
  ],
  warnings: [],
};

const summary: AnalysisSummary = {
  analysis_id: "analysis-1",
  model_version: "1.0-test",
  threshold: 0.5,
  summary: {
    rows: 2,
    requires_review: 1,
    risk_counts: { low: 0, medium: 1, high: 0, critical: 1 },
    warnings: ["Unknown categories in GENDER: 999"],
    target_present: true,
    target_valid: true,
  },
  metrics: {
    available: true,
    threshold: 0.5,
    gini: 0.8,
    ks: 0.75,
    accuracy: 0.9,
    precision: 0.7,
    recall: 0.95,
    roc_auc: 0.9,
    pr_auc: 0.65,
    confusion_matrix: [[8, 1], [0, 1]],
    unavailable_reason: null,
  },
};

const resultPage: ResultPage = {
  total: 2,
  page: 1,
  page_size: 25,
  threshold: 0.5,
  items: [
    {
      record_id: "row-critical",
      risk_probability: 0.9,
      risk_level: "critical",
      requires_review: true,
      explanation_factors: [
        { feature: "signal", value: 9, contribution: 1.2, direction: "increases_risk" },
      ],
      analysis_warnings: [],
    },
    {
      record_id: "row-low",
      risk_probability: 0.4,
      risk_level: "medium",
      requires_review: false,
      explanation_factors: [
        { feature: "term", value: 12, contribution: -0.2, direction: "decreases_risk" },
      ],
      analysis_warnings: [],
    },
  ],
};

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((next) => {
    resolve = next;
  });
  return { promise, resolve };
}

async function chooseAndUpload(user: ReturnType<typeof userEvent.setup>) {
  const file = new File(["signal;GB_flag\n9;1"], "clients.csv", { type: "text/csv" });
  await user.upload(screen.getByLabelText("Выберите файл с данными"), file);
  await user.click(screen.getByRole("button", { name: "Проверить источник" }));
}

describe("local analysis workspace", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    window.localStorage.clear();
    window.history.replaceState({}, "", "/new-analysis");
    api.createAnalysis.mockResolvedValue({
      analysis_id: "analysis-1",
      status: "queued",
      status_url: "/api/analyst/analyses/analysis-1/status",
    });
    api.cancelAnalysis.mockResolvedValue({ analysis_id: "analysis-1", status: "cancelled" });
    api.runAnalysis.mockResolvedValue({ analysis_id: "analysis-1", status: "queued" });
    api.getAnalysisInventory.mockResolvedValue(inventory);
    api.getAnalysisPlan.mockResolvedValue(plan);
    api.updateAnalysisPeriod.mockResolvedValue(plan);
    api.getAnalysisSummary.mockResolvedValue(summary);
    api.getAnalysisResults.mockResolvedValue(resultPage);
    api.getAnalysisClients.mockResolvedValue({ ...resultPage, items: resultPage.items });
    api.getAnalysisTransactions.mockResolvedValue({ items: [], total: 0, page: 1, page_size: 100 });
    api.getAnalysisRelationships.mockResolvedValue({ items: [], total: 0, page: 1, page_size: 100 });
    api.getAnalysisDistribution.mockResolvedValue({
      analysis_id: "analysis-1",
      threshold: 0.5,
      risk_counts: summary.summary.risk_counts,
      probability_histogram: [
        { from: 0, to: 0.5, count: 1 },
        { from: 0.5, to: 1, count: 1 },
      ],
    });
    api.downloadAnalysisReport.mockResolvedValue({
      blob: new Blob(["record_id\nrow-critical"]),
      filename: "analysis-1.csv",
    });
    api.deleteAnalysis.mockResolvedValue(undefined);
    api.reportUrl.mockReturnValue("/api/report.csv");
  });

  afterEach(cleanup);

  it("shows a human-readable plan, applies the period and starts the model", async () => {
    const user = userEvent.setup();
    api.getAnalysisStatus
      .mockResolvedValueOnce(plannedStatus)
      .mockResolvedValueOnce(completeStatus);
    render(<App />);

    const file = new File(["SQLite format 3\0fixture"], "transactions.sqlite");
    await user.upload(screen.getByLabelText("Выберите файл с данными"), file);
    await user.click(screen.getByRole("button", { name: "Проверить источник" }));

    expect(await screen.findByRole("heading", { name: "Источник распознан" })).toBeInTheDocument();
    expect(screen.getByText("База SQLite")).toBeInTheDocument();
    expect(screen.getByText("12 068")).toBeInTheDocument();
    expect(screen.getByText("Подозрительные операции")).toBeInTheDocument();
    expect(screen.queryByText("transaction_timestamp")).not.toBeInTheDocument();
    expect(screen.queryByText("technical_transactions_table")).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Запустить анализ" }));
    await waitFor(() => {
      expect(api.updateAnalysisPeriod).toHaveBeenCalledWith("analysis-1", {
        start: "2026-01-01T00:00:00.000Z",
        end: "2026-04-30T23:59:00.000Z",
      });
      expect(api.runAnalysis).toHaveBeenCalledWith("analysis-1");
    });
    expect(await screen.findByText("Карта риска выборки")).toBeInTheDocument();
  });

  it("cancels a prepared analysis and offers a clean restart", async () => {
    const user = userEvent.setup();
    api.getAnalysisStatus
      .mockResolvedValueOnce(plannedStatus)
      .mockResolvedValueOnce({ ...plannedStatus, status: "cancelled", stage: "cancelled", can_cancel: false });
    render(<App />);
    await chooseAndUpload(user);
    await screen.findByRole("heading", { name: "Источник распознан" });

    await user.click(screen.getByRole("button", { name: "Отменить" }));
    expect(api.cancelAnalysis).toHaveBeenCalledWith("analysis-1");
    expect(await screen.findByRole("heading", { name: "Расчёт остановлен" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Выбрать другой файл" })).toBeInTheDocument();
  });

  it("shows upload progress, overview and keeps detailed analytics in their sections", async () => {
    const user = userEvent.setup();
    const status = deferred<AnalysisStatus>();
    api.getAnalysisStatus.mockReturnValueOnce(status.promise);
    render(<App />);

    await chooseAndUpload(user);
    expect(await screen.findByLabelText("Прогресс анализа")).toBeInTheDocument();
    expect(screen.getByText("Ожидание обработки")).toBeInTheDocument();

    status.resolve(completeStatus);
    expect(await screen.findByText("Карта риска выборки")).toBeInTheDocument();
    expect(screen.getByText("Распределение риска")).toBeInTheDocument();
    expect(screen.getByText("Самые рискованные записи")).toBeInTheDocument();
    expect(screen.getAllByText("row-critical").length).toBeGreaterThanOrEqual(1);
    expect(screen.getAllByText("row-low").length).toBeGreaterThanOrEqual(1);

    await user.click(screen.getByRole("link", { name: "Качество модели" }));
    expect(screen.getByText("ROC-AUC")).toBeInTheDocument();
    expect(screen.getByText("Качество ранжирования")).toBeInTheDocument();

    await user.click(screen.getByRole("link", { name: "Качество данных" }));
    expect(screen.getAllByText("Новые значения в поле «Пол»").length).toBeGreaterThanOrEqual(1);
    expect(screen.getAllByText(/Модель обработала их как неизвестные значения/).length).toBeGreaterThanOrEqual(1);

    await user.click(screen.getByRole("link", { name: "Клиенты" }));
    expect(screen.getByText("Срок договора")).toBeInTheDocument();

    const critical = screen.getAllByText("90.0%").find((element) =>
      element.classList.contains("risk-score"),
    )!;
    const medium = screen.getAllByText("40.0%").find((element) =>
      element.classList.contains("risk-score"),
    )!;
    expect(critical.compareDocumentPosition(medium) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(screen.getByRole("button", { name: "Критический" })).toBeInTheDocument();
    expect(screen.getByText("Критический риск")).toBeInTheDocument();
    expect(screen.getByText("На ручную проверку")).toBeInTheDocument();
    await user.click(screen.getAllByRole("button", { name: /Показать факторы/ })[0]);
    expect(screen.getByText("Почему модель присвоила такой риск")).toBeInTheDocument();
    expect(screen.getByText("Повышает риск")).toBeInTheDocument();
  });

  it("applies a temporary threshold and risk filter through the API", async () => {
    const user = userEvent.setup();
    api.getAnalysisStatus.mockResolvedValue(completeStatus);
    render(<App />);
    await chooseAndUpload(user);
    await screen.findByText("Карта риска выборки");
    await user.click(screen.getByRole("link", { name: "Клиенты" }));

    fireEvent.change(screen.getByRole("slider", { name: /Порог ручной проверки/ }), {
      target: { value: "80" },
    });
    await user.click(screen.getByRole("button", { name: "Применить" }));
    await waitFor(() => {
      expect(api.getAnalysisSummary).toHaveBeenLastCalledWith("analysis-1", 0.8);
      expect(api.getAnalysisResults).toHaveBeenLastCalledWith(
        "analysis-1",
        expect.objectContaining({ threshold: 0.8 }),
      );
    });

    await user.click(screen.getByRole("button", { name: "Критический" }));
    await waitFor(() => {
      expect(api.getAnalysisResults).toHaveBeenLastCalledWith(
        "analysis-1",
        expect.objectContaining({ riskLevel: "critical", page: 1 }),
      );
    });

    await user.type(screen.getByRole("searchbox", { name: "Поиск по ID" }), "row-critical");
    await user.clear(screen.getByRole("spinbutton", { name: "Риск от, %" }));
    await user.type(screen.getByRole("spinbutton", { name: "Риск от, %" }), "70");
    await user.click(screen.getByRole("checkbox", { name: "Только на ручную проверку" }));
    await user.click(screen.getByRole("button", { name: "Применить фильтры" }));
    await waitFor(() => {
      expect(api.getAnalysisResults).toHaveBeenLastCalledWith(
        "analysis-1",
        expect.objectContaining({
          page: 1,
          riskLevel: "critical",
          requiresReview: true,
          probabilityMin: 0.7,
          recordId: "row-critical",
        }),
      );
    });
  });

  it("preserves the HTTP rejection reason instead of reporting a connection failure", async () => {
    const user = userEvent.setup();
    api.createAnalysis.mockRejectedValueOnce(
      new ApiClientError("request_failed", "There was an error parsing the body"),
    );
    render(<App />);
    await chooseAndUpload(user);
    expect(await screen.findByRole("alert")).toHaveTextContent("There was an error parsing the body");
    expect(screen.queryByText("Не удалось связаться с локальным сервисом анализа.")).not.toBeInTheDocument();
  });

  it("renders compatibility errors and a no-target metrics state", async () => {
    const user = userEvent.setup();
    api.createAnalysis.mockRejectedValueOnce(
      new ApiClientError("incompatible_schema", "Файл несовместим.", [
        "Missing critical features: amount",
      ]),
    );
    render(<App />);
    await chooseAndUpload(user);

    expect(await screen.findByRole("alert")).toHaveTextContent("Файл несовместим.");
    expect(screen.getByText(/Отсутствуют обязательные признаки: amount/)).toBeInTheDocument();

    api.createAnalysis.mockResolvedValueOnce({
      analysis_id: "analysis-1",
      status: "queued",
      status_url: "/status",
    });
    api.getAnalysisStatus.mockResolvedValueOnce(completeStatus);
    api.getAnalysisSummary.mockResolvedValueOnce({
      ...summary,
      summary: { ...summary.summary, target_present: false, target_valid: false },
      metrics: {
        ...summary.metrics,
        available: false,
        unavailable_reason: "Target column is not present.",
      },
    });
    await user.click(screen.getByRole("button", { name: "Выбрать другой файл" }));
    await chooseAndUpload(user);
    await screen.findByText("Карта риска выборки");
    await user.click(screen.getByRole("link", { name: "Качество модели" }));
    expect(await screen.findByText("Метрики недоступны")).toBeInTheDocument();
    expect(screen.getByText(/нет корректного GB_flag/i)).toBeInTheDocument();
  });

  it("restores a completed local session without uploading the file again", async () => {
    window.localStorage.setItem("risk-ledger-active-analysis:0", "previous-analysis");
    api.getAnalysisStatus.mockResolvedValue(completeStatus);
    render(<App />);

    expect(await screen.findByText("Карта риска выборки")).toBeInTheDocument();
    expect(api.getAnalysisStatus).toHaveBeenCalledWith("previous-analysis");
    expect(api.createAnalysis).not.toHaveBeenCalled();
    expect(window.localStorage.getItem("risk-ledger-active-analysis:0")).toBe(
      "previous-analysis",
    );
  });

  it("clears current-session notifications after the session is finished", async () => {
    const user = userEvent.setup();
    api.getAnalysisStatus.mockResolvedValue(completeStatus);
    render(<App />);

    await chooseAndUpload(user);
    await screen.findByText("Карта риска выборки");
    expect(screen.getByLabelText("Уведомления текущей сессии: 1")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Завершить сессию" }));
    expect(screen.getByRole("dialog", { name: "Завершить локальную сессию?" })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Удалить сессию" }));
    await waitFor(() => expect(api.deleteAnalysis).toHaveBeenCalledWith("analysis-1"));
    expect(await screen.findByRole("heading", { name: "Новый анализ", level: 1 })).toBeInTheDocument();
    expect(screen.getByLabelText("Уведомления текущей сессии: 0")).toBeInTheDocument();
  });

  it("keeps the result visible when confirmed session deletion fails", async () => {
    const user = userEvent.setup();
    api.getAnalysisStatus.mockResolvedValue(completeStatus);
    api.deleteAnalysis.mockRejectedValueOnce(new ApiClientError("delete_failed", "Не удалось удалить сессию анализа."));
    render(<App />);

    await chooseAndUpload(user);
    await screen.findByText("Карта риска выборки");
    await user.click(screen.getByRole("button", { name: "Завершить сессию" }));
    await user.click(screen.getByRole("button", { name: "Удалить сессию" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("Текущий результат сохранён");
    expect(screen.getByText("Карта риска выборки")).toBeInTheDocument();
    expect(window.localStorage.getItem("risk-ledger-active-analysis:0")).toBe("analysis-1");
  });

  it("asks before replacing the active local session", async () => {
    const user = userEvent.setup();
    api.getAnalysisStatus.mockResolvedValue(completeStatus);
    render(<App />);

    await chooseAndUpload(user);
    await screen.findByText("Карта риска выборки");

    await user.click(screen.getByRole("link", { name: "Новый анализ" }));
    api.createAnalysis.mockResolvedValueOnce({
      analysis_id: "analysis-2",
      status: "queued",
      status_url: "/api/analyst/analyses/analysis-2/status",
    });
    await chooseAndUpload(user);

    expect(screen.getByRole("dialog", { name: "Заменить текущий анализ?" })).toBeInTheDocument();
    expect(api.deleteAnalysis).not.toHaveBeenCalled();
    await user.click(screen.getByRole("button", { name: "Удалить и продолжить" }));

    await waitFor(() => expect(api.deleteAnalysis).toHaveBeenCalledWith("analysis-1"));
    expect(api.deleteAnalysis.mock.invocationCallOrder[0]).toBeLessThan(
      api.createAnalysis.mock.invocationCallOrder[1],
    );
    expect(window.localStorage.getItem("risk-ledger-active-analysis:0")).toBe("analysis-2");
  });

  it("keeps the current result when session replacement cannot delete it", async () => {
    const user = userEvent.setup();
    api.getAnalysisStatus.mockResolvedValue(completeStatus);
    render(<App />);
    await chooseAndUpload(user);
    await screen.findByText("Карта риска выборки");

    await user.click(screen.getByRole("link", { name: "Новый анализ" }));
    api.deleteAnalysis.mockRejectedValueOnce(
      new ApiClientError("delete_failed", "Не удалось удалить сессию анализа."),
    );
    await chooseAndUpload(user);
    await user.click(screen.getByRole("button", { name: "Удалить и продолжить" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Не удалось удалить сессию анализа.",
    );
    expect(window.localStorage.getItem("risk-ledger-active-analysis:0")).toBe("analysis-1");
    await user.click(screen.getByRole("link", { name: "Обзор" }));
    expect(screen.getByText("Карта риска выборки")).toBeInTheDocument();
    expect(api.createAnalysis).toHaveBeenCalledTimes(1);
  });
});
