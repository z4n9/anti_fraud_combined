import type { RelationshipRow } from "../types/analysis";

const number = new Intl.NumberFormat("ru-RU");

export function RelationshipAnalytics({ rows, total }: { rows: RelationshipRow[]; total: number }) {
  const average = rows.length ? rows.reduce((sum, row) => sum + row.risk_signal_score, 0) / rows.length * 100 : 0;
  const strong = rows.filter((row) => row.risk_signal_score >= .8).length;
  const nodes = new Set(rows.flatMap((row) => [row.from_id, row.to_id])).size;
  const transferCount = rows.filter((row) => row.kind === "transfer").length;
  const otherCount = rows.length - transferCount;
  const maximum = Math.max(1, transferCount, otherCount);

  return (
    <section className="signal-overview" aria-label="Сводка по связям объектов">
      <div className="signal-kpis">
        <article><span>Всего связей</span><strong>{number.format(total)}</strong><small>в результате анализа</small></article>
        <article className="signal-kpi--danger"><span>Сильный сигнал</span><strong>{strong}</strong><small>риск связи 80% и выше</small></article>
        <article><span>Связанных объектов</span><strong>{nodes}</strong><small>на текущей странице</small></article>
        <article className="signal-kpi--warning"><span>Средняя сила</span><strong>{average.toFixed(1)}%</strong><small>по показанным связям</small></article>
      </div>
      <article className="signal-chart signal-chart--compact">
        <header><div><p className="eyebrow">Структура связей</p><h2>Какие связи преобладают</h2></div></header>
        <ul>
          <li><span>Переводы</span><div className="signal-chart__track signal-chart__track--high"><i style={{ width: `${transferCount / maximum * 100}%` }} /></div><strong>{transferCount}</strong></li>
          <li><span>Клиент и операция</span><div className="signal-chart__track signal-chart__track--medium"><i style={{ width: `${otherCount / maximum * 100}%` }} /></div><strong>{otherCount}</strong></li>
        </ul>
      </article>
    </section>
  );
}
