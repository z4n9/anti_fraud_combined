import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { OverviewPage } from "../src/routes/OverviewPage";
import type { AnalysisRow, AnalysisSummary } from "../src/types/analysis";

const summary: AnalysisSummary = {
  analysis_id: "overview-test",
  model_version: "1.4.0",
  threshold: 0.5,
  summary: {
    rows: 100,
    requires_review: 12,
    risk_counts: { critical: 2, high: 10, medium: 28, low: 60 },
    warnings: ["Unknown categories in GENDER: 999"],
    target_present: true,
    target_valid: true,
  },
  metrics: {
    available: true,
    threshold: 0.5,
    gini: 0.8,
    ks: 0.7,
    accuracy: 0.9,
    precision: 0.75,
    recall: 0.83,
    roc_auc: 0.91,
    pr_auc: 0.68,
    confusion_matrix: [[80, 10], [2, 8]],
    unavailable_reason: null,
  },
};

const risks = [0.31, 0.99, 0.54, 0.86, 0.73, 0.92].map((risk, index): AnalysisRow => ({
  record_id: `row-${index}`,
  risk_probability: risk,
  risk_level: risk >= .9 ? "critical" : risk >= .7 ? "high" : risk >= .5 ? "medium" : "low",
  requires_review: risk >= .5,
  explanation_factors: [],
  analysis_warnings: [],
}));

afterEach(cleanup);

describe("OverviewPage", () => {
  it("renders accessible risk distribution and only the five highest-risk records", () => {
    render(
      <OverviewPage
        analysisId="overview-test"
        filename="clients.csv"
        rows={risks}
        summary={summary}
        onCloseSession={vi.fn().mockResolvedValue(true)}
        onNavigate={vi.fn()}
      />,
    );

    expect(screen.getByRole("img", { name: /Критический: 2; Высокий: 10/ })).toBeInTheDocument();
    const displayedRows = screen.getAllByText(/^row-/).map((node) => node.textContent);
    expect(displayedRows).toEqual(["row-1", "row-5", "row-3", "row-4", "row-2"]);
    expect(screen.queryByText("row-0")).not.toBeInTheDocument();
  });

  it("opens the detailed quality and records sections", async () => {
    const user = userEvent.setup();
    const onNavigate = vi.fn();
    render(
      <OverviewPage
        analysisId="overview-test"
        filename="clients.csv"
        rows={risks}
        summary={summary}
        onCloseSession={vi.fn().mockResolvedValue(true)}
        onNavigate={onNavigate}
      />,
    );

    await user.click(screen.getByRole("button", { name: /Посмотреть отклонения/ }));
    await user.click(screen.getByRole("button", { name: /Открыть показатели/ }));
    await user.click(screen.getByRole("button", { name: /Открыть все записи/ }));
    expect(onNavigate.mock.calls.map(([route]) => route)).toEqual([
      "data-quality",
      "model-quality",
      "risk-records",
    ]);
  });

  it("shows missing target as a normal informational state", () => {
    render(
      <OverviewPage
        analysisId="overview-test"
        filename="unlabelled.csv"
        rows={risks}
        summary={{ ...summary, metrics: { ...summary.metrics, available: false } }}
        onCloseSession={vi.fn().mockResolvedValue(true)}
        onNavigate={vi.fn()}
      />,
    );
    expect(screen.getByText("Без факта")).toBeInTheDocument();
    expect(screen.getByText(/оценка риска доступна/)).toBeInTheDocument();
  });
});
