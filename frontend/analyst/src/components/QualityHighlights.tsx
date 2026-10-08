import type { AnalysisSummary } from "../types/analysis";
import type { DashboardRouteId } from "../types/dashboard";
import { Badge } from "./ui/primitives";

const percent = (value: number | null) =>
  value === null ? "—" : `${(value * 100).toFixed(1)}%`;

export function QualityHighlights({
  summary,
  onNavigate,
}: {
  summary: AnalysisSummary;
  onNavigate: (route: DashboardRouteId) => void;
}) {
  const warningCount = summary.summary.warnings.length;
  return (
    <div className="quality-highlights">
      <section className="overview-card quality-highlight">
        <div className="quality-highlight__topline">
          <span className="quality-highlight__mark">◇</span>
          <Badge tone={warningCount ? "warning" : "success"}>
            {warningCount ? `${warningCount} предупрежд.` : "Без отклонений"}
          </Badge>
        </div>
        <div>
          <p className="eyebrow">Контроль входа</p>
          <h2>Качество данных</h2>
          <p>
            {warningCount
              ? "Модель завершила анализ, но обнаружила новые значения или дополнительные поля."
              : "Структура файла соответствует текущей версии модели."}
          </p>
        </div>
        <button className="overview-link" type="button" onClick={() => onNavigate("data-quality")}>
          Посмотреть отклонения <span aria-hidden="true">→</span>
        </button>
      </section>

      <section className="overview-card quality-highlight">
        <div className="quality-highlight__topline">
          <span className="quality-highlight__mark">◎</span>
          <Badge tone={summary.metrics.available ? "info" : "neutral"}>
            {summary.metrics.available ? "Метрики рассчитаны" : "Без факта"}
          </Badge>
        </div>
        <div>
          <p className="eyebrow">Контроль модели</p>
          <h2>Качество модели</h2>
          {summary.metrics.available ? (
            <div className="quality-highlight__metrics">
              <span>ROC-AUC <strong>{percent(summary.metrics.roc_auc)}</strong></span>
              <span>Полнота <strong>{percent(summary.metrics.recall)}</strong></span>
            </div>
          ) : (
            <p>В CSV нет корректного <code>GB_flag</code>; оценка риска доступна, контрольные метрики — нет.</p>
          )}
        </div>
        <button className="overview-link" type="button" onClick={() => onNavigate("model-quality")}>
          Открыть показатели <span aria-hidden="true">→</span>
        </button>
      </section>
    </div>
  );
}
