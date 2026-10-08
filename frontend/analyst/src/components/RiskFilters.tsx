import type { RiskLevel } from "../types/analysis";

const levels: Array<{ value: RiskLevel | "all"; label: string }> = [
  { value: "all", label: "Все уровни" },
  { value: "critical", label: "Критический" },
  { value: "high", label: "Высокий" },
  { value: "medium", label: "Средний" },
  { value: "low", label: "Низкий" },
];

interface RiskFiltersProps {
  riskLevel: RiskLevel | "all";
  thresholdPercent: number;
  modelThresholdPercent: number;
  requiresReview: boolean;
  probabilityMin: number;
  probabilityMax: number;
  recordId: string;
  busy?: boolean;
  onRiskLevelChange: (level: RiskLevel | "all") => void;
  onThresholdChange: (value: number) => void;
  onThresholdApply: () => void;
  onThresholdReset: () => void;
  onRequiresReviewChange: (value: boolean) => void;
  onProbabilityMinChange: (value: number) => void;
  onProbabilityMaxChange: (value: number) => void;
  onRecordIdChange: (value: string) => void;
  onFiltersApply: () => void;
  onFiltersReset: () => void;
}

export function RiskFilters({
  riskLevel, thresholdPercent, modelThresholdPercent, requiresReview,
  probabilityMin, probabilityMax, recordId, busy = false,
  onRiskLevelChange, onThresholdChange, onThresholdApply, onThresholdReset,
  onRequiresReviewChange, onProbabilityMinChange, onProbabilityMaxChange,
  onRecordIdChange, onFiltersApply, onFiltersReset,
}: RiskFiltersProps) {
  return (
    <section className="filters records-filters" aria-label="Фильтры рискованных записей">
      <div className="filter-group records-filters__levels">
        <span className="filter-label">Уровень риска</span>
        <div className="segmented">
          {levels.map((level) => (
            <button aria-pressed={riskLevel === level.value} className={riskLevel === level.value ? "is-active" : ""} key={level.value} type="button" onClick={() => onRiskLevelChange(level.value)}>
              {level.label}
            </button>
          ))}
        </div>
      </div>

      <div className="records-filters__advanced">
        <label className="filter-field filter-field--search">
          <span>Поиск по ID</span>
          <input maxLength={200} placeholder="Например, row-005371" type="search" value={recordId} onChange={(event) => onRecordIdChange(event.target.value)} />
        </label>
        <div className="filter-range" role="group" aria-label="Диапазон вероятности риска">
          <label><span>Риск от, %</span><input min={0} max={100} type="number" value={probabilityMin} onChange={(event) => onProbabilityMinChange(Number(event.target.value))} /></label>
          <label><span>Риск до, %</span><input min={0} max={100} type="number" value={probabilityMax} onChange={(event) => onProbabilityMaxChange(Number(event.target.value))} /></label>
        </div>
        <label className="filter-checkbox">
          <input checked={requiresReview} type="checkbox" onChange={(event) => onRequiresReviewChange(event.target.checked)} />
          <span>Только на ручную проверку</span>
        </label>
        <div className="records-filters__actions">
          <button className="ui-button ui-button--secondary" disabled={busy} type="button" onClick={onFiltersApply}>Применить фильтры</button>
          <button className="ui-button ui-button--ghost" disabled={busy} type="button" onClick={onFiltersReset}>Сбросить</button>
        </div>
      </div>

      <div className="threshold-control records-filters__threshold">
        <label htmlFor="review-threshold">Порог ручной проверки <strong>{thresholdPercent}%</strong></label>
        <p>Меняет состав ручной проверки, но не переобучает модель.</p>
        <div>
          <input id="review-threshold" aria-label={`Порог ручной проверки ${thresholdPercent}%`} max={100} min={0} step={1} type="range" value={thresholdPercent} onChange={(event) => onThresholdChange(Number(event.target.value))} />
          <button className="button button--compact" disabled={busy} type="button" onClick={onThresholdApply}>Применить</button>
          <button className="overview-link" disabled={busy || thresholdPercent === modelThresholdPercent} type="button" onClick={onThresholdReset}>Вернуть порог модели ({modelThresholdPercent}%)</button>
        </div>
      </div>
    </section>
  );
}
