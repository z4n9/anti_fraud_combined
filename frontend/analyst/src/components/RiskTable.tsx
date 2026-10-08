import { Fragment, useEffect, useRef, useState } from "react";
import type { ReactNode } from "react";
import type { AnalysisRow, RiskLevel } from "../types/analysis";
import { getFeaturePresentation } from "../utils/labels";
import { FactorCard } from "./FactorCard";

const riskLabels: Record<RiskLevel, string> = {
  critical: "Критический риск",
  high: "Высокий риск",
  medium: "Средний риск",
  low: "Низкий риск",
};

export type RiskTableColumn = "record" | "probability" | "level" | "decision" | "factors";

interface RiskTableProps {
  rows: AnalysisRow[];
  page: number;
  pageSize: number;
  total: number;
  visibleColumns?: Set<RiskTableColumn>;
  selectedRecordId?: string | null;
  selectedDetails?: ReactNode;
  onPageChange: (page: number) => void;
  onSelect?: (row: AnalysisRow, trigger: HTMLButtonElement) => void;
}

export function RiskTable({
  rows,
  page,
  pageSize,
  total,
  visibleColumns = new Set<RiskTableColumn>(["record", "probability", "level", "decision", "factors"]),
  selectedRecordId = null,
  selectedDetails,
  onPageChange,
  onSelect,
}: RiskTableProps) {
  const [expandedRows, setExpandedRows] = useState<Set<string>>(new Set());
  const sectionRef = useRef<HTMLElement>(null);
  const previousPage = useRef(page);
  const pages = Math.max(1, Math.ceil(total / pageSize));
  const visibleCount = visibleColumns.size + 1;

  useEffect(() => {
    if (previousPage.current !== page) {
      sectionRef.current?.scrollIntoView?.({ behavior: "smooth", block: "start" });
      previousPage.current = page;
    }
  }, [page]);

  const toggleRow = (recordId: string) => {
    setExpandedRows((current) => {
      const next = new Set(current);
      if (next.has(recordId)) next.delete(recordId);
      else next.add(recordId);
      return next;
    });
  };

  return (
    <section className="results-panel" aria-labelledby="results-title" ref={sectionRef}>
      <div className="section-heading section-heading--inline">
        <div>
          <p className="eyebrow">Приоритет проверки</p>
          <h2 id="results-title">Подозрительные записи — сначала</h2>
        </div>
        <span className="result-count">
          {total ? `${(page - 1) * pageSize + 1}–${Math.min(page * pageSize, total)} из ${total}` : "0 записей"}
        </span>
      </div>
      <div className="table-scroll">
        <table>
          <thead>
            <tr>
              {visibleColumns.has("record") && <th>Запись</th>}
              {visibleColumns.has("probability") && <th>Вероятность</th>}
              {visibleColumns.has("level") && <th>Уровень</th>}
              {visibleColumns.has("decision") && <th>Решение</th>}
              {visibleColumns.has("factors") && <th>Основные факторы</th>}
              <th><span className="sr-only">Управление</span></th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => {
              const expanded = expandedRows.has(row.record_id);
              const riskPercent = row.risk_probability * 100;
              return (
                <Fragment key={row.record_id}>
                  <tr className={`risk-row risk-row--${row.risk_level}${selectedRecordId === row.record_id ? " is-selected" : ""}`}>
                    {visibleColumns.has("record") && <td data-label="Запись"><div className="record-cell"><code className="record-id" title={row.record_id}>{row.record_id}</code><button aria-label={`Копировать ${row.record_id}`} type="button" onClick={() => { void navigator.clipboard?.writeText(row.record_id); }}>⧉</button></div></td>}
                    {visibleColumns.has("probability") && <td data-label="Вероятность">
                      <div className="risk-probability">
                        <strong className="risk-score">{riskPercent.toFixed(1)}%</strong>
                        <span
                          aria-label={`Риск ${riskPercent.toFixed(1)}%`}
                          aria-valuemax={100}
                          aria-valuemin={0}
                          aria-valuenow={riskPercent}
                          className="risk-track"
                          role="progressbar"
                        >
                          <span style={{ width: `${riskPercent}%` }} />
                        </span>
                      </div>
                    </td>}
                    {visibleColumns.has("level") && <td data-label="Уровень">
                      <span className={`risk-badge risk-badge--${row.risk_level}`}>
                        {riskLabels[row.risk_level]}
                      </span>
                    </td>}
                    {visibleColumns.has("decision") && <td data-label="Решение">
                      <span className={row.requires_review ? "review review--yes" : "review review--watch"}>
                        {row.requires_review ? "На ручную проверку" : "Наблюдение"}
                      </span>
                    </td>}
                    {visibleColumns.has("factors") && <td data-label="Факторы">
                      <div className="factor-preview">
                        {row.explanation_factors.slice(0, 2).map((factor) => (
                          <span key={`${row.record_id}-${factor.feature}`}>
                            {getFeaturePresentation(factor.feature).label}
                          </span>
                        ))}
                        <button
                          aria-controls={`factors-${row.record_id}`}
                          aria-expanded={expanded}
                          type="button"
                          onClick={() => toggleRow(row.record_id)}
                        >
                          {expanded ? "Скрыть факторы" : `Показать факторы (${row.explanation_factors.length})`}
                        </button>
                      </div>
                    </td>}
                    <td className="row-action"><button aria-label={`Открыть запись ${row.record_id}`} type="button" onClick={(event) => onSelect?.(row, event.currentTarget)}>Открыть</button></td>
                  </tr>
                  {selectedRecordId === row.record_id && selectedDetails && (
                    <tr className="record-details-row">
                      <td colSpan={visibleCount}>{selectedDetails}</td>
                    </tr>
                  )}
                  {expanded && (
                    <tr className="factor-details-row">
                      <td colSpan={visibleCount}>
                        <div className="factor-details" id={`factors-${row.record_id}`}>
                          <div className="factor-details__heading">
                            <strong>Почему модель присвоила такой риск</strong>
                            <span>Значения SHAP показывают направление влияния, а не доказывают мошенничество.</span>
                          </div>
                          <ul className="factor-list">
                            {row.explanation_factors.map((factor) => <FactorCard factor={factor} key={`${row.record_id}-${factor.feature}`} />)}
                          </ul>
                        </div>
                      </td>
                    </tr>
                  )}
                </Fragment>
              );
            })}
            {!rows.length && (
              <tr>
                <td className="empty-table" colSpan={visibleCount}>Нет записей для выбранного фильтра. Сбросьте фильтры, чтобы увидеть всю выборку.</td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
      <nav className="pagination" aria-label="Страницы результатов">
        <button disabled={page <= 1} type="button" onClick={() => onPageChange(page - 1)}>
          Назад
        </button>
        <span>Страница {page} из {pages}</span>
        <button disabled={page >= pages} type="button" onClick={() => onPageChange(page + 1)}>
          Далее
        </button>
      </nav>
    </section>
  );
}
