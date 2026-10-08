import { useEffect, useRef, useState } from "react";
import { ColumnPicker } from "../components/ColumnPicker";
import { AnalystBrief } from "../components/AnalystBrief";
import { RecordDetails } from "../components/RecordDetails";
import { RiskDistributionCharts } from "../components/RiskDistributionCharts";
import { RiskFilters } from "../components/RiskFilters";
import { RiskTable } from "../components/RiskTable";
import type { RiskTableColumn } from "../components/RiskTable";
import type { AnalysisRow, RiskDistribution, RiskLevel, TransactionRow } from "../types/analysis";

interface RiskRecordsPageProps {
  analysisId?: string;
  busy: boolean;
  distribution: RiskDistribution | null;
  errorMessage?: string | null;
  modelThresholdPercent: number;
  page: number;
  pageSize: number;
  probabilityMax: number;
  probabilityMin: number;
  recordId: string;
  requiresReview: boolean;
  riskLevel: RiskLevel | "all";
  rows: AnalysisRow[];
  relatedTransactions?: TransactionRow[];
  thresholdPercent: number;
  total: number;
  onFiltersApply: () => void;
  onFiltersReset: () => void;
  onPageChange: (page: number) => void;
  onProbabilityMaxChange: (value: number) => void;
  onProbabilityMinChange: (value: number) => void;
  onRecordIdChange: (value: string) => void;
  onRequiresReviewChange: (value: boolean) => void;
  onRiskLevelChange: (level: RiskLevel | "all") => void;
  onThresholdApply: () => void;
  onThresholdChange: (value: number) => void;
  onThresholdReset: () => void;
}

const allColumns = new Set<RiskTableColumn>(["record", "probability", "level", "decision", "factors"]);

export function RiskRecordsPage(props: RiskRecordsPageProps) {
  const [visibleColumns, setVisibleColumns] = useState(allColumns);
  const [selected, setSelected] = useState<AnalysisRow | null>(null);
  const [desktopDetails, setDesktopDetails] = useState(() =>
    typeof window.matchMedia !== "function" || window.matchMedia("(min-width: 1200px)").matches,
  );
  const selectedTrigger = useRef<HTMLButtonElement | null>(null);

  useEffect(() => {
    if (typeof window.matchMedia !== "function") return;
    const media = window.matchMedia("(min-width: 1200px)");
    const update = () => setDesktopDetails(media.matches);
    update();
    media.addEventListener?.("change", update);
    return () => media.removeEventListener?.("change", update);
  }, []);

  useEffect(() => {
    if (selected && !props.rows.some((row) => row.record_id === selected.record_id)) setSelected(null);
  }, [props.rows, selected]);

  const toggleColumn = (column: RiskTableColumn) => {
    setVisibleColumns((current) => {
      const next = new Set(current);
      if (next.has(column) && next.size > 1) next.delete(column);
      else next.add(column);
      return next;
    });
  };

  const selectRecord = (row: AnalysisRow, trigger: HTMLButtonElement) => {
    selectedTrigger.current = trigger;
    setSelected(row);
  };

  const closeDetails = () => {
    setSelected(null);
    const restoreFocus = () => selectedTrigger.current?.focus();
    if (typeof window.requestAnimationFrame === "function") window.requestAnimationFrame(restoreFocus);
    else window.setTimeout(restoreFocus, 0);
  };

  const clientIds = selected ? [selected.record_id, selected.client_id, selected.customer_id, selected.subject_id].filter(Boolean).map(String) : [];
  const related = (props.relatedTransactions ?? []).filter((transaction) => clientIds.includes(String(transaction.client_id ?? "")));
  const details = selected ? <RecordDetails key={selected.record_id} row={selected} analysisId={props.analysisId} relatedTransactions={related} onClose={closeDetails} /> : null;

  return (
    <div className="section-page records-page">
      <header className="section-page__intro records-page__intro">
        <div><p className="eyebrow">Клиентский профиль</p><h1>Рискованные клиенты</h1><p>Клиенты отсортированы сервером от наиболее подозрительных к наименее рискованным.</p></div>
        <ColumnPicker visible={visibleColumns} onToggle={toggleColumn} />
      </header>
      <AnalystBrief
        title="Как проверить клиента"
        description="Список уже отсортирован по риску: двигайтесь сверху вниз и проверяйте объяснение модели."
        steps={[
          { label: "Риск", text: "Начните с критических клиентов" },
          { label: "Факторы", text: "Сопоставьте причины с исходными данными" },
          { label: "Решение", text: "Зафиксируйте итог ручной проверки" },
        ]}
      />
      {props.distribution && <RiskDistributionCharts distribution={props.distribution} />}
      {props.errorMessage && <div className="inline-alert" role="alert">{props.errorMessage}</div>}
      <RiskFilters
        busy={props.busy}
        modelThresholdPercent={props.modelThresholdPercent}
        probabilityMax={props.probabilityMax}
        probabilityMin={props.probabilityMin}
        recordId={props.recordId}
        requiresReview={props.requiresReview}
        riskLevel={props.riskLevel}
        thresholdPercent={props.thresholdPercent}
        onFiltersApply={props.onFiltersApply}
        onFiltersReset={props.onFiltersReset}
        onProbabilityMaxChange={props.onProbabilityMaxChange}
        onProbabilityMinChange={props.onProbabilityMinChange}
        onRecordIdChange={props.onRecordIdChange}
        onRequiresReviewChange={props.onRequiresReviewChange}
        onRiskLevelChange={props.onRiskLevelChange}
        onThresholdApply={props.onThresholdApply}
        onThresholdChange={props.onThresholdChange}
        onThresholdReset={props.onThresholdReset}
      />
      <div className={`records-master-detail${selected ? " has-selection" : ""}`}>
        <RiskTable
          page={props.page}
          pageSize={props.pageSize}
          rows={props.rows}
          selectedRecordId={selected?.record_id}
          selectedDetails={!desktopDetails ? details : undefined}
          total={props.total}
          visibleColumns={visibleColumns}
          onPageChange={props.onPageChange}
          onSelect={selectRecord}
        />
        {desktopDetails && details}
      </div>
    </div>
  );
}
