import { MetricsPanel } from "../components/MetricsPanel";
import { AnalystBrief } from "../components/AnalystBrief";
import type { AnalysisMetrics } from "../types/analysis";

export function ModelQualityPage({ metrics }: { metrics: AnalysisMetrics }) {
  return (
    <div className="section-page">
      <header className="section-page__intro">
        <p className="eyebrow">Контроль модели</p>
        <h1>Качество модели</h1>
        <p>Показатели рассчитаны только по загруженной выборке и её фактическим меткам.</p>
      </header>
      <AnalystBrief
        title="Как оценить модель"
        description="Для поиска редкого мошенничества одной общей точности недостаточно."
        steps={[
          { label: "Полнота", text: "Показывает, сколько реальных рисков найдено" },
          { label: "Точность тревог", text: "Показывает долю подтвердившихся сигналов" },
          { label: "Ошибки", text: "Матрица показывает пропуски и ложные тревоги" },
        ]}
      />
      <MetricsPanel metrics={metrics} />
    </div>
  );
}
