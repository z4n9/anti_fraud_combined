import type { RiskTableColumn } from "./RiskTable";

const columns: Array<{ id: RiskTableColumn; label: string }> = [
  { id: "record", label: "Запись" }, { id: "probability", label: "Вероятность" },
  { id: "level", label: "Уровень" }, { id: "decision", label: "Решение" },
  { id: "factors", label: "Факторы" },
];
export function ColumnPicker({ visible, onToggle }: { visible: Set<RiskTableColumn>; onToggle: (column: RiskTableColumn) => void }) {
  return <details className="column-picker"><summary className="ui-button ui-button--secondary">Колонки</summary><div className="column-picker__panel"><strong>Показывать в таблице</strong>{columns.map((column) => <label key={column.id}><input checked={visible.has(column.id)} type="checkbox" onChange={() => onToggle(column.id)} /><span>{column.label}</span></label>)}</div></details>;
}
