import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { RiskRecordsPage } from "../src/routes/RiskRecordsPage";
import type { AnalysisRow, RiskDistribution } from "../src/types/analysis";

afterEach(cleanup);

const rows: AnalysisRow[] = [
  {
    record_id: "row-critical",
    risk_probability: .94,
    risk_level: "critical",
    requires_review: true,
    explanation_factors: [{ feature: "CNT_6M", value: 12, contribution: .8, direction: "increases_risk" }],
    analysis_warnings: ["Новое значение категории занятости"],
    EMPLOYMENTNATURE: "self-employed",
    NEW_SIGNAL: 42,
  },
];

const distribution: RiskDistribution = {
  analysis_id: "analysis-1",
  threshold: .5,
  risk_counts: { critical: 1, high: 2, medium: 3, low: 4 },
  probability_histogram: Array.from({ length: 10 }, (_, index) => ({ from: index / 10, to: (index + 1) / 10, count: index + 1 })),
};

function renderPage() {
  const onFiltersApply = vi.fn();
  render(
    <RiskRecordsPage
      busy={false}
      distribution={distribution}
      modelThresholdPercent={50}
      page={1}
      pageSize={25}
      probabilityMax={100}
      probabilityMin={0}
      recordId=""
      requiresReview={false}
      riskLevel="all"
      rows={rows}
      thresholdPercent={50}
      total={1}
      onFiltersApply={onFiltersApply}
      onFiltersReset={vi.fn()}
      onPageChange={vi.fn()}
      onProbabilityMaxChange={vi.fn()}
      onProbabilityMinChange={vi.fn()}
      onRecordIdChange={vi.fn()}
      onRequiresReviewChange={vi.fn()}
      onRiskLevelChange={vi.fn()}
      onThresholdApply={vi.fn()}
      onThresholdChange={vi.fn()}
      onThresholdReset={vi.fn()}
    />,
  );
  return { onFiltersApply };
}

describe("RiskRecordsPage", () => {
  it("shows distributions and opens a focused record card", async () => {
    const user = userEvent.setup();
    renderPage();
    expect(screen.getByRole("img", { name: /0–10%: 1/ })).toBeInTheDocument();
    expect(screen.getByText("Состав выборки")).toBeInTheDocument();
    const trigger = screen.getByRole("button", { name: "Открыть запись row-critical" });
    await user.click(trigger);
    const card = screen.getByRole("complementary", { name: "row-critical" });
    expect(card).toBeInTheDocument();
    expect(screen.getAllByRole("tab").map((tab) => tab.textContent)).toEqual([
      "Сводка", "Факторы риска", "Связанные операции", "Исходные данные", "Предупреждения1",
    ]);
    expect(screen.getByRole("tab", { name: "Сводка" })).toHaveAttribute("aria-selected", "true");
    expect(within(card).getByRole("progressbar", { name: "Риск 94.0%" })).toBeInTheDocument();

    await user.click(screen.getByRole("tab", { name: "Факторы риска" }));
    expect(screen.getAllByText("Запросы за 6 месяцев")).toHaveLength(2);
    expect(screen.queryByText("CNT_6M")).not.toBeInTheDocument();
    expect(screen.getByText("12 запросов за последние 6 мес.")).toBeInTheDocument();
    expect(screen.getByText(/Количество запросов по субъекту/)).toBeInTheDocument();
    expect(screen.getByText("Повышает риск")).toBeInTheDocument();
    expect(screen.getByText("+0,8")).toBeInTheDocument();

    const factorsTab = screen.getByRole("tab", { name: "Факторы риска" });
    factorsTab.focus();
    await user.keyboard("{ArrowRight}");
    await waitFor(() => expect(screen.getByRole("tab", { name: "Связанные операции" })).toHaveFocus());
    await user.keyboard("{ArrowRight}");
    await waitFor(() => expect(screen.getByRole("tab", { name: "Исходные данные" })).toHaveFocus());
    expect(screen.getByText("Вид деятельности")).toBeInTheDocument();
    expect(screen.getByText("Техническое поле")).toBeInTheDocument();
    expect(screen.getByText("NEW_SIGNAL")).toBeInTheDocument();

    await user.click(screen.getByRole("tab", { name: /Предупреждения/ }));
    expect(screen.getByText("Новое значение категории занятости")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Закрыть карточку записи" }));
    await waitFor(() => expect(trigger).toHaveFocus());
  });

  it("allows hiding columns while retaining at least one column", async () => {
    const user = userEvent.setup();
    renderPage();
    await user.click(screen.getByText("Колонки"));
    await user.click(screen.getByRole("checkbox", { name: "Факторы" }));
    expect(screen.queryByRole("columnheader", { name: "Основные факторы" })).not.toBeInTheDocument();
    expect(screen.getByRole("columnheader", { name: "Запись" })).toBeInTheDocument();
  });
});
