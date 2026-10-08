import type { RiskLevel } from "../types/analysis";

const levelDefinitions: Array<{
  level: RiskLevel;
  label: string;
  color: string;
}> = [
  { level: "critical", label: "Критический", color: "#fb7185" },
  { level: "high", label: "Высокий", color: "#fb923c" },
  { level: "medium", label: "Средний", color: "#fbbf24" },
  { level: "low", label: "Низкий", color: "#34d399" },
];

const number = new Intl.NumberFormat("ru-RU");

export function RiskDonutChart({
  counts,
}: {
  counts: Record<RiskLevel, number>;
}) {
  const total = Object.values(counts).reduce((sum, count) => sum + count, 0);
  let cursor = 0;
  const segments = levelDefinitions.map(({ level, color }) => {
    const start = cursor;
    cursor += total ? (counts[level] / total) * 100 : 0;
    return `${color} ${start}% ${cursor}%`;
  });
  const chartLabel = levelDefinitions
    .map(({ level, label }) => `${label}: ${number.format(counts[level])}`)
    .join("; ");

  return (
    <section className="overview-card risk-distribution" aria-labelledby="risk-distribution-title">
      <div className="overview-card__heading">
        <div>
          <p className="eyebrow">Структура выборки</p>
          <h2 id="risk-distribution-title">Распределение риска</h2>
        </div>
      </div>
      <div className="risk-distribution__body">
        <div
          aria-label={chartLabel}
          className="risk-donut"
          role="img"
          style={{ background: total ? `conic-gradient(${segments.join(", ")})` : "var(--line)" }}
        >
          <div>
            <strong>{number.format(total)}</strong>
            <span>записей</span>
          </div>
        </div>
        <ul className="risk-legend">
          {levelDefinitions.map(({ level, label, color }) => {
            const share = total ? (counts[level] / total) * 100 : 0;
            return (
              <li key={level}>
                <span className="risk-legend__dot" style={{ backgroundColor: color }} aria-hidden="true" />
                <span>{label}</span>
                <strong>{number.format(counts[level])}</strong>
                <small>{share.toFixed(1)}%</small>
              </li>
            );
          })}
        </ul>
      </div>
    </section>
  );
}
