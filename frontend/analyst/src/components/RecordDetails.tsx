import { useEffect, useId, useRef, useState } from "react";
import type { KeyboardEvent } from "react";
import type { AnalysisRow, TransactionRow } from "../types/analysis";
import { formatDataWarning, formatFeatureValue, getFeaturePresentation, getRiskLevelLabel } from "../utils/labels";
import { FactorCard } from "./FactorCard";
import { InvestigationPanel } from "./InvestigationPanel";

type DetailsTab = "summary" | "factors" | "related" | "source" | "warnings";

const tabs: Array<{ id: DetailsTab; label: string }> = [
  { id: "summary", label: "Сводка" },
  { id: "factors", label: "Факторы риска" },
  { id: "related", label: "Связанные операции" },
  { id: "source", label: "Исходные данные" },
  { id: "warnings", label: "Предупреждения" },
];

const outputFields = new Set([
  "record_id", "risk_probability", "risk_level", "requires_review",
  "explanation_factors", "analysis_warnings",
]);

function sourceValue(value: unknown): string {
  if (value === null || value === undefined || value === "") return "нет данных";
  if (typeof value === "number" || typeof value === "string") return formatFeatureValue(value);
  if (typeof value === "boolean") return value ? "да" : "нет";
  return JSON.stringify(value);
}

export function RecordDetails({ row, analysisId, relatedTransactions = [], onClose }: { row: AnalysisRow; analysisId?: string; relatedTransactions?: TransactionRow[]; onClose: () => void }) {
  const [activeTab, setActiveTab] = useState<DetailsTab>("summary");
  const [copied, setCopied] = useState(false);
  const titleRef = useRef<HTMLHeadingElement>(null);
  const tabRefs = useRef<Partial<Record<DetailsTab, HTMLButtonElement>>>({});
  const baseId = useId().replace(/:/g, "");
  const riskPercent = row.risk_probability * 100;
  const sourceEntries = Object.entries(row)
    .filter(([key]) => !outputFields.has(key))
    .sort(([left], [right]) => left.localeCompare(right));

  useEffect(() => {
    setActiveTab("summary");
    setCopied(false);
    titleRef.current?.focus();
  }, [row.record_id]);

  const selectTab = (tab: DetailsTab, moveFocus = false) => {
    setActiveTab(tab);
    if (moveFocus) {
      const focusTab = () => tabRefs.current[tab]?.focus();
      if (typeof window.requestAnimationFrame === "function") window.requestAnimationFrame(focusTab);
      else window.setTimeout(focusTab, 0);
    }
  };

  const handleTabKeyDown = (event: KeyboardEvent<HTMLButtonElement>, index: number) => {
    let nextIndex = index;
    if (event.key === "ArrowRight") nextIndex = (index + 1) % tabs.length;
    else if (event.key === "ArrowLeft") nextIndex = (index - 1 + tabs.length) % tabs.length;
    else if (event.key === "Home") nextIndex = 0;
    else if (event.key === "End") nextIndex = tabs.length - 1;
    else return;
    event.preventDefault();
    selectTab(tabs[nextIndex].id, true);
  };

  const copyRecordId = async () => {
    await navigator.clipboard?.writeText(row.record_id);
    setCopied(true);
  };

  return (
    <aside className="record-details" aria-labelledby={`${baseId}-title`}>
      <div className="record-details__heading">
        <div>
          <p className="eyebrow">Карточка записи</p>
          <h2 id={`${baseId}-title`} ref={titleRef} tabIndex={-1} title={row.record_id}>{row.record_id}</h2>
          <button className="record-details__copy" type="button" onClick={() => { void copyRecordId(); }}>
            {copied ? "Скопировано" : "Копировать ID"}
          </button>
          <span className="sr-only" aria-live="polite">{copied ? `ID ${row.record_id} скопирован` : ""}</span>
        </div>
        <button className="record-details__close" aria-label="Закрыть карточку записи" type="button" onClick={onClose}>×</button>
      </div>

      <div className="record-details__tabs" role="tablist" aria-label="Разделы карточки записи">
        {tabs.map((tab, index) => (
          <button
            aria-controls={`${baseId}-panel-${tab.id}`}
            aria-selected={activeTab === tab.id}
            className={activeTab === tab.id ? "is-active" : ""}
            id={`${baseId}-tab-${tab.id}`}
            key={tab.id}
            ref={(node) => { tabRefs.current[tab.id] = node ?? undefined; }}
            role="tab"
            tabIndex={activeTab === tab.id ? 0 : -1}
            type="button"
            onClick={() => selectTab(tab.id)}
            onKeyDown={(event) => handleTabKeyDown(event, index)}
          >
            {tab.label}
            {tab.id === "warnings" && row.analysis_warnings.length > 0 && <span>{row.analysis_warnings.length}</span>}
          </button>
        ))}
      </div>

      <div className="record-details__panel" id={`${baseId}-panel-${activeTab}`} role="tabpanel" aria-labelledby={`${baseId}-tab-${activeTab}`} tabIndex={0}>
        {activeTab === "summary" && (
          <div className="record-summary">
            <div className="record-summary__score">
              <span>Вероятность риска</span><strong>{riskPercent.toFixed(1)}%</strong>
              <span className="risk-track" role="progressbar" aria-label={`Риск ${riskPercent.toFixed(1)}%`} aria-valuemin={0} aria-valuemax={100} aria-valuenow={riskPercent}><span style={{ width: `${riskPercent}%` }} /></span>
            </div>
            <dl>
              <div><dt>Уровень</dt><dd><span className={`risk-badge risk-badge--${row.risk_level}`}>{getRiskLevelLabel(row.risk_level)}</span></dd></div>
              <div><dt>Решение</dt><dd><span className={row.requires_review ? "review review--yes" : "review review--watch"}>{row.requires_review ? "На ручную проверку" : "Наблюдение"}</span></dd></div>
              <div><dt>Факторов в объяснении</dt><dd>{row.explanation_factors.length}</dd></div>
            </dl>
            <p>Оценка помогает определить приоритет проверки и не является доказательством мошенничества.</p>
          </div>
        )}

        {activeTab === "factors" && (
          <div className="record-factors">
            <p>Факторы расположены по силе влияния. Красная отметка повышает риск, синяя — снижает.</p>
            {row.explanation_factors.length ? <ul className="factor-list">{row.explanation_factors.slice(0, 5).map((factor) => <FactorCard factor={factor} key={factor.feature} />)}</ul> : <p className="record-details__empty">Объяснение факторов для записи недоступно.</p>}
          </div>
        )}

        {activeTab === "related" && (
          <div className="record-related">
            <p>Операции, которые модель связала с этим клиентом.</p>
            {relatedTransactions.length ? <ul>{relatedTransactions.slice(0, 8).map((transaction) => <li key={transaction.record_id}><span><strong>{transaction.transaction_id ?? transaction.record_id}</strong><small>{transaction.transaction_timestamp ?? "Время не определено"}</small></span><strong>{(transaction.risk_probability * 100).toFixed(1)}%</strong></li>)}</ul> : <p className="record-details__empty">Связанные операции в этой выборке не найдены.</p>}
          </div>
        )}

        {activeTab === "source" && (
          <div className="record-source">
            {sourceEntries.length ? <dl>{sourceEntries.map(([key, value]) => { const feature = getFeaturePresentation(key); const label = feature.label === key ? "Техническое поле" : feature.label; return <div key={key}><dt><span>{label}</span><code>{key}</code></dt><dd>{sourceValue(value)}</dd></div>; })}</dl> : <p className="record-details__empty">Исходные поля записи отсутствуют в ответе.</p>}
          </div>
        )}

        {activeTab === "warnings" && (
          <div className="record-warnings">
            {row.analysis_warnings.length ? <ul>{row.analysis_warnings.map((warning) => <li key={warning}>{formatDataWarning(warning)}</li>)}</ul> : <p className="record-details__empty">Для этой записи отдельных предупреждений нет.</p>}
          </div>
        )}
      </div>
      {analysisId && <InvestigationPanel analysisId={analysisId} entityId={row.record_id} />}
    </aside>
  );
}
