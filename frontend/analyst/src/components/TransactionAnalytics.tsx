import type { RiskLevel, TransactionRow } from "../types/analysis";

const number = new Intl.NumberFormat("ru-RU");
const levels: Array<{ level: RiskLevel; label: string }> = [
  { level: "critical", label: "Критический" },
  { level: "high", label: "Высокий" },
  { level: "medium", label: "Средний" },
  { level: "low", label: "Низкий" },
];

export function TransactionAnalytics({ rows, total }: { rows: TransactionRow[]; total: number }) {
  const reviewCount = rows.filter((row) => row.requires_review).length;
  const urgentCount = rows.filter((row) => row.risk_level === "critical" || row.risk_level === "high").length;
  const averageRisk = rows.length ? rows.reduce((sum, row) => sum + row.risk_probability, 0) / rows.length * 100 : 0;
  const counts = Object.fromEntries(levels.map(({ level }) => [level, rows.filter((row) => row.risk_level === level).length])) as Record<RiskLevel, number>;
  const maximum = Math.max(1, ...Object.values(counts));

  return (
    <section className="signal-overview" aria-label="Сводка по подозрительным операциям">
      <div className="signal-kpis">
        <article><span>Всего найдено</span><strong>{number.format(total)}</strong><small>операций в результате</small></article>
        <article className="signal-kpi--danger"><span>Высокий приоритет</span><strong>{number.format(urgentCount)}</strong><small>среди показанных операций</small></article>
        <article className="signal-kpi--warning"><span>Ручная проверка</span><strong>{number.format(reviewCount)}</strong><small>нужно решение аналитика</small></article>
        <article><span>Средний риск</span><strong>{averageRisk.toFixed(1)}%</strong><small>на текущей странице</small></article>
      </div>
      <article className="signal-chart">
        <header><div><p className="eyebrow">Быстрая оценка</p><h2>Риск показанных операций</h2></div><small>{rows.length} на странице</small></header>
        <ul>
          {levels.map(({ level, label }) => (
            <li key={level}>
              <span>{label}</span>
              <div className={`signal-chart__track signal-chart__track--${level}`}><i style={{ width: `${counts[level] / maximum * 100}%` }} /></div>
              <strong>{counts[level]}</strong>
            </li>
          ))}
        </ul>
      </article>
    </section>
  );
}
