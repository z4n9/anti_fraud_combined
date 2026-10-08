import { useState } from "react";
import { TransactionDetails } from "../components/TransactionDetails";
import { AnalystBrief } from "../components/AnalystBrief";
import { TransactionAnalytics } from "../components/TransactionAnalytics";
import type { TransactionRow } from "../types/analysis";
import { getRiskLevelLabel } from "../utils/labels";
import { maskSensitiveValue } from "../utils/masking";

export function TransactionsPage({ analysisId, rows, total }: { analysisId?: string; rows: TransactionRow[]; total: number }) {
  const [selected, setSelected] = useState<TransactionRow | null>(null);
  return (
    <div className="section-page transactions-page">
      <header className="section-page__intro"><div><p className="eyebrow">Транзакционный профиль</p><h1>Подозрительные операции</h1><p>Операции с наибольшим риском показаны первыми. Найдено: {total.toLocaleString("ru-RU")}.</p></div></header>
      <p className="active-session-note">Оценка отражает силу сигналов правил и связей, а не вероятность мошенничества. При отсутствии ML-модели анализ операций продолжает работать по правилам.</p>
      <AnalystBrief
        title="Как проверить операцию"
        description="Начните с максимального риска, затем подтвердите контекст сигнала и зафиксируйте решение."
        steps={[
          { label: "Приоритет", text: "Сначала критические и высокорисковые операции" },
          { label: "Причина", text: "Откройте карточку и изучите понятные факторы" },
          { label: "Решение", text: "Подтвердите риск или снимите тревогу" },
        ]}
      />
      {rows.length > 0 && <TransactionAnalytics rows={rows} total={total} />}
      {!rows.length ? <div className="ui-state"><h2>Операции не найдены</h2><p>В загруженном источнике нет распознанного транзакционного профиля.</p></div> : (
        <div className={`transaction-layout${selected ? " has-selection" : ""}`}>
          <div className="transaction-table-wrap"><table className="transaction-table"><caption className="sr-only">Операции по убыванию риска</caption><thead><tr><th>Операция</th><th>Клиент и направление</th><th>Сумма</th><th>Риск</th><th>Решение</th><th><span className="sr-only">Действие</span></th></tr></thead><tbody>
            {rows.map((row) => { const rawAmount = row.transaction_amount ?? row.amount; return <tr key={row.record_id}><td data-label="Операция"><strong>{row.transaction_id ?? row.record_id}</strong><small>{row.transaction_timestamp ?? "Время не определено"}</small></td><td data-label="Участники"><strong>{row.client_id ? maskSensitiveValue(row.client_id) : "Клиент не определён"}</strong><small>{row.sender_account_id && row.recipient_account_id ? `${maskSensitiveValue(row.sender_account_id)} → ${maskSensitiveValue(row.recipient_account_id)}` : "Связь счетов не определена"}</small></td><td data-label="Сумма">{typeof rawAmount === "number" ? rawAmount.toLocaleString("ru-RU") : "—"} {row.currency ?? ""}</td><td data-label="Риск"><strong className="transaction-table__score">{(row.risk_probability * 100).toFixed(1)}%</strong><span className={`risk-badge risk-badge--${row.risk_level}`}>{getRiskLevelLabel(row.risk_level)}</span></td><td data-label="Решение"><span className={row.requires_review ? "review review--yes" : "review review--watch"}>{row.requires_review ? "На проверку" : "Наблюдение"}</span></td><td><button type="button" onClick={() => setSelected(row)}>Открыть</button></td></tr>; })}
          </tbody></table></div>
          {selected && <TransactionDetails row={selected} analysisId={analysisId} onClose={() => setSelected(null)} />}
        </div>
      )}
    </div>
  );
}
