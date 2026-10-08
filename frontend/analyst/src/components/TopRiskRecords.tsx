import type { AnalysisRow } from "../types/analysis";
import { getRiskLevelLabel } from "../utils/labels";

export function TopRiskRecords({
  rows,
  onOpenAll,
}: {
  rows: AnalysisRow[];
  onOpenAll: () => void;
}) {
  const topRows = [...rows]
    .sort((left, right) => right.risk_probability - left.risk_probability)
    .slice(0, 5);

  return (
    <section className="overview-card top-risk" aria-labelledby="top-risk-title">
      <div className="overview-card__heading overview-card__heading--inline">
        <div>
          <p className="eyebrow">Приоритет проверки</p>
          <h2 id="top-risk-title">Самые рискованные записи</h2>
        </div>
        <button className="overview-link" type="button" onClick={onOpenAll}>
          Открыть все записи <span aria-hidden="true">→</span>
        </button>
      </div>
      {topRows.length ? (
        <ol className="top-risk__list">
          {topRows.map((row, index) => (
            <li key={row.record_id}>
              <span className="top-risk__position">{String(index + 1).padStart(2, "0")}</span>
              <code>{row.record_id}</code>
              <span className={`risk-badge risk-badge--${row.risk_level}`}>
                {getRiskLevelLabel(row.risk_level)}
              </span>
              <div className="top-risk__score">
                <strong>{(row.risk_probability * 100).toFixed(1)}%</strong>
                <span className="risk-track" aria-hidden="true">
                  <span style={{ width: `${row.risk_probability * 100}%` }} />
                </span>
              </div>
            </li>
          ))}
        </ol>
      ) : (
        <p className="overview-empty">В текущей выборке нет записей.</p>
      )}
    </section>
  );
}
