import type { RiskDistribution, RiskLevel } from "../types/analysis";

const levels: Array<{ level: RiskLevel; label: string }> = [
  { level: "critical", label: "Критический" },
  { level: "high", label: "Высокий" },
  { level: "medium", label: "Средний" },
  { level: "low", label: "Низкий" },
];
const number = new Intl.NumberFormat("ru-RU");

export function RiskDistributionCharts({ distribution }: { distribution: RiskDistribution }) {
  const total = Object.values(distribution.risk_counts).reduce((sum, count) => sum + count, 0);
  const maximumLevel = Math.max(1, ...Object.values(distribution.risk_counts));
  const maximumBin = Math.max(1, ...distribution.probability_histogram.map((item) => item.count));
  return (
    <section className="records-charts" aria-label="Распределение риска и вероятностей">
      <article className="overview-card level-bars">
        <div className="overview-card__heading"><div><p className="eyebrow">Уровни риска</p><h2>Состав выборки</h2></div></div>
        <ul>
          {levels.map(({ level, label }) => {
            const count = distribution.risk_counts[level];
            const share = total ? count / total * 100 : 0;
            return <li key={level}><span>{label}</span><div className={`level-bars__track level-bars__track--${level}`} aria-hidden="true"><span style={{ width: `${count / maximumLevel * 100}%` }} /></div><strong>{number.format(count)}</strong><small>{share.toFixed(1)}%</small></li>;
          })}
        </ul>
      </article>
      <article className="overview-card probability-histogram">
        <div className="overview-card__heading"><div><p className="eyebrow">Вероятность</p><h2>Распределение оценок</h2></div></div>
        <div className="probability-histogram__plot" role="img" aria-label={distribution.probability_histogram.map((bin) => `${Math.round(bin.from * 100)}–${Math.round(bin.to * 100)}%: ${bin.count}`).join("; ")}>
          {distribution.probability_histogram.map((bin) => <div className="histogram-bin" key={bin.from}><strong>{number.format(bin.count)}</strong><span style={{ height: `${bin.count / maximumBin * 100}%` }} /><small>{Math.round(bin.from * 100)}–{Math.round(bin.to * 100)}%</small></div>)}
        </div>
      </article>
    </section>
  );
}
