import type { TransactionRow } from "../types/analysis";
import { getRiskLevelLabel } from "../utils/labels";
import { getTransactionSignalPresentations } from "../utils/transactionExplanations";
import { InvestigationPanel } from "./InvestigationPanel";
import { SensitiveValue } from "./SensitiveValue";

const value = (input: unknown) => input === undefined || input === null || input === "" ? "Не определено" : String(input);
const amount = (row: TransactionRow) => {
  const raw = row.transaction_amount ?? row.amount;
  return typeof raw === "number" ? `${raw.toLocaleString("ru-RU")} ${row.currency ?? ""}`.trim() : "Не определена";
};

export function TransactionDetails({ row, analysisId, onClose }: { row: TransactionRow; analysisId?: string; onClose: () => void }) {
  const signals = getTransactionSignalPresentations(
    row.rule_explanation,
    row.graph_explanation,
    row.ml_explanation,
  );
  return (
    <aside className="transaction-details" aria-labelledby="transaction-details-title">
      <header><div><p className="eyebrow">Карточка операции</p><h2 id="transaction-details-title">{row.transaction_id ?? row.record_id}</h2></div><button aria-label="Закрыть карточку операции" type="button" onClick={onClose}>×</button></header>
      <div className="transaction-details__risk">
        <span className={`risk-badge risk-badge--${row.risk_level}`}>{getRiskLevelLabel(row.risk_level)}</span>
        <strong>{(row.risk_probability * 100).toFixed(1)}%</strong>
        <span className="risk-track" role="progressbar" aria-label="Оценка риска" aria-valuemin={0} aria-valuemax={100} aria-valuenow={row.risk_probability * 100}><span style={{ width: `${row.risk_probability * 100}%` }} /></span>
      </div>
      <dl className="transaction-details__facts">
        <div><dt>ML-оценка аномалии</dt><dd>{row.ml_anomaly_score == null ? "Модель отсутствует — оценка недоступна" : value(row.ml_anomaly_score)}</dd></div>
        <div><dt>Сумма</dt><dd>{amount(row)}</dd></div>
        <div><dt>Время</dt><dd>{value(row.transaction_timestamp)}</dd></div>
        <div><dt>Клиент</dt><dd><SensitiveValue value={row.client_id} label="ID клиента" /></dd></div>
        <div><dt>Отправитель</dt><dd><SensitiveValue value={row.sender_account_id} label="счёт отправителя" /></dd></div>
        <div><dt>Получатель</dt><dd><SensitiveValue value={row.recipient_account_id} label="счёт получателя" /></dd></div>
        <div><dt>Канал</dt><dd>{value(row.channel)}</dd></div>
      </dl>
      <section className="transaction-signals">
        <h3>Почему операция отмечена</h3>
        {signals.length ? (
          <ul>
            {signals.map((signal, index) => (
              <li key={`${index}-${signal.title}`}>
                <div className="transaction-signal__heading">
                  <strong>{signal.title}</strong>
                  <span className={`transaction-signal__impact transaction-signal__impact--${signal.impact === "Сильный фактор" ? "strong" : signal.impact === "Средний фактор" ? "medium" : "additional"}`}>
                    {signal.impact}
                  </span>
                </div>
                <p>{signal.description}</p>
                {signal.metrics.length > 0 && (
                  <div className="transaction-signal__metrics">
                    {signal.metrics.map((metric) => <span key={metric}>{metric}</span>)}
                  </div>
                )}
              </li>
            ))}
          </ul>
        ) : <p>Отдельные причины не переданы. Ориентируйтесь на итоговый уровень риска и данные операции.</p>}
      </section>
      <p className="transaction-details__note">Сигнал определяет приоритет проверки, но сам по себе не доказывает мошенничество.</p>
      {analysisId && <InvestigationPanel analysisId={analysisId} entityId={row.record_id} />}
    </aside>
  );
}
