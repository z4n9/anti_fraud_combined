import { useMemo, useState } from "react";
import { AnalystBrief } from "../components/AnalystBrief";
import { RelationshipAnalytics } from "../components/RelationshipAnalytics";
import type { RelationshipRow } from "../types/analysis";
import { maskSensitiveValue } from "../utils/masking";

const kindLabel = (kind: string) => kind === "transfer" ? "Перевод между счетами" : kind === "transaction_client" ? "Операция клиента" : "Связь объектов";

export function RelationshipsPage({ rows, total }: { rows: RelationshipRow[]; total: number }) {
  const [view, setView] = useState<"graph" | "table">("graph");
  const graphRows = useMemo(() => rows.slice(0, 12), [rows]);
  return (
    <div className="section-page relationships-page">
      <header className="section-page__intro"><div><p className="eyebrow">Контекст риска</p><h1>Связи клиентов и операций</h1><p>Показано, как подозрительные операции связывают клиентов и счета. Всего связей: {total.toLocaleString("ru-RU")}.</p></div></header>
      <AnalystBrief
        title="Как читать связи"
        description="Ищите повторяющиеся счета и клиентов: они помогают обнаружить цепочки и группы операций."
        steps={[
          { label: "Сила", text: "Процент показывает значимость связи" },
          { label: "Повторы", text: "Один объект в нескольких связях важнее одиночного" },
          { label: "Проверка", text: "Таблица помогает сопоставить точные идентификаторы" },
        ]}
      />
      {rows.length > 0 && <RelationshipAnalytics rows={rows} total={total} />}
      <div className="view-switch" role="tablist" aria-label="Представление связей"><button role="tab" aria-selected={view === "graph"} className={view === "graph" ? "is-active" : ""} onClick={() => setView("graph")}>Граф</button><button role="tab" aria-selected={view === "table"} className={view === "table" ? "is-active" : ""} onClick={() => setView("table")}>Таблица</button></div>
      {!rows.length ? <div className="ui-state"><h2>Связи не найдены</h2><p>Для этой выборки модель не смогла построить связи между объектами.</p></div> : view === "graph" ? (
        <figure className="relationship-graph"><figcaption>Наиболее рискованные связи</figcaption><div className="relationship-graph__canvas">
          {graphRows.map((row) => <div className="relationship-edge" key={row.relationship_id}><span className="relationship-node">{maskSensitiveValue(row.from_id)}</span><span className="relationship-link"><strong>{(row.risk_signal_score * 100).toFixed(0)}%</strong><i aria-hidden="true">→</i><small>{kindLabel(row.kind)}</small></span><span className="relationship-node">{maskSensitiveValue(row.to_id)}</span></div>)}
        </div><p>Толщина и процент показывают силу сигнала риска. Для точного чтения всех связей используйте табличный вид.</p></figure>
      ) : (
        <div className="relationship-table-wrap"><table className="relationship-table"><caption className="sr-only">Табличное представление связей</caption><thead><tr><th>Тип связи</th><th>Откуда</th><th>Куда</th><th>Операция</th><th>Сила сигнала</th></tr></thead><tbody>{rows.map((row) => <tr key={row.relationship_id}><td>{kindLabel(row.kind)}</td><td>{maskSensitiveValue(row.from_id)}</td><td>{maskSensitiveValue(row.to_id)}</td><td>{row.transaction_id ?? "—"}</td><td><strong>{(row.risk_signal_score * 100).toFixed(1)}%</strong></td></tr>)}</tbody></table></div>
      )}
    </div>
  );
}
