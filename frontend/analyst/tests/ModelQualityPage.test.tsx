import { cleanup, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { ModelQualityPage } from "../src/routes/ModelQualityPage";
import type { AnalysisMetrics } from "../src/types/analysis";

const metrics: AnalysisMetrics = {
  available: true,
  threshold: 0.5,
  gini: 0.82,
  ks: 0.64,
  accuracy: 0.91,
  precision: 0.41,
  recall: 0.833,
  roc_auc: 0.91,
  pr_auc: 0.76,
  confusion_matrix: [[90, 10], [2, 10]],
  unavailable_reason: null,
};

function renderPage(mode: "simple" | "expert" = "simple", value = metrics) {
  return render(<div className={`mode-${mode}`}><ModelQualityPage metrics={value} /></div>);
}

afterEach(cleanup);

describe("ModelQualityPage", () => {
  it("uses the same values for simple conclusions and expert metrics", () => {
    const { container } = renderPage();
    const simple = screen.getByLabelText("Простое объяснение качества");
    const expert = screen.getByLabelText("Экспертные метрики качества");

    expect(within(simple).getByText("83.3%")).toBeInTheDocument();
    expect(expert.querySelector('[data-metric="recall"]')).toHaveTextContent("0.833");
    expect(container.querySelector(".mode-simple .expert-only")).toBeInTheDocument();
    expect(screen.getByText(/Accuracy 91.0% может быть высокой/)).toBeInTheDocument();
  });

  it("shows all expert metrics and value-aware keyboard tooltips", () => {
    renderPage("expert");

    const expert = screen.getByLabelText("Экспертные метрики качества");
    for (const code of ["Gini", "KS", "Accuracy", "Precision", "Recall", "ROC-AUC", "PR-AUC"]) {
      expect(within(expert).getByText(code)).toBeInTheDocument();
    }
    const recallHelp = screen.getByLabelText("Что означает Recall");
    expect(recallHelp).toHaveAttribute("tabindex", "0");
    expect(recallHelp).toHaveAttribute("aria-describedby", "metric-tooltip-recall");
    expect(screen.getByRole("tooltip", { name: /83.3% всех реальных мошенников/ })).toBeInTheDocument();
  });

  it("renders four distinct heatmap outcomes with per-class percentages", () => {
    renderPage();
    const matrix = screen.getByLabelText("Тепловая матрица ошибок модели");

    expect(within(matrix).getByLabelText(/Верно распознаны честные клиенты: 90, 90.0%/)).toHaveClass("matrix-cell--tn");
    expect(within(matrix).getByLabelText(/Ложные тревоги: 10, 10.0%/)).toHaveClass("matrix-cell--fp");
    expect(within(matrix).getByLabelText(/Пропущенный риск: 2, 16.7%/)).toHaveClass("matrix-cell--fn");
    expect(within(matrix).getByLabelText(/Верно найден риск: 10, 83.3%/)).toHaveClass("matrix-cell--tp");
    expect(within(matrix).getByText("Честные клиенты")).toBeInTheDocument();
    expect(within(matrix).getByText("Сигнал тревоги")).toBeInTheDocument();
  });

  it("explains that scoring remains available without GB_flag", () => {
    renderPage("simple", {
      ...metrics,
      available: false,
      confusion_matrix: null,
      unavailable_reason: "Target column is not present.",
    });

    expect(screen.getByText("Метрики недоступны")).toBeInTheDocument();
    expect(screen.getByText(/всё равно рассчитала риск и ранжировала записи/)).toBeInTheDocument();
    expect(screen.getByText(/добавьте GB_flag со значениями 0 и 1/)).toBeInTheDocument();
  });
});
