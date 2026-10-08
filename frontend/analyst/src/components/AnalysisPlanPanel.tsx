import { useEffect, useMemo, useState } from "react";
import type { AnalysisPlan, SourceInventory } from "../types/analysis";

const formatLabels = {
  csv: "Табличный файл CSV",
  json: "Документ JSON",
  jsonl: "Поток JSON",
  ndjson: "Поток NDJSON",
  sql_dump: "Выгрузка SQL",
  sqlite: "База SQLite",
  bson: "Выгрузка BSON",
} as const;

const profileCopy = {
  client_risk: {
    title: "Риск по клиентам",
    description: "Модель сможет ранжировать клиентов, которым требуется внимание.",
  },
  transaction_anomaly: {
    title: "Подозрительные операции",
    description: "Модель сможет искать необычные движения и связанные сигналы.",
  },
} as const;

function toInputValue(value: string | null): string {
  if (!value) return "";
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? "" : parsed.toISOString().slice(0, 16);
}

function toApiValue(value: string): string | null {
  return value ? new Date(`${value}:00Z`).toISOString() : null;
}

function formatCount(value: number): string {
  return new Intl.NumberFormat("ru-RU").format(value);
}

function formatBytes(value: number): string {
  if (value >= 1024 * 1024) return `${(value / (1024 * 1024)).toFixed(1)} МБ`;
  return `${Math.max(0.1, value / 1024).toFixed(1)} КБ`;
}

interface AnalysisPlanPanelProps {
  inventory: SourceInventory;
  plan: AnalysisPlan;
  busy: boolean;
  onRun: (period: { start: string | null; end: string | null }) => void;
}

export function AnalysisPlanPanel({ inventory, plan, busy, onRun }: AnalysisPlanPanelProps) {
  const [start, setStart] = useState(toInputValue(plan.time_range?.start ?? null));
  const [end, setEnd] = useState(toInputValue(plan.time_range?.end ?? null));

  useEffect(() => {
    setStart(toInputValue(plan.time_range?.start ?? null));
    setEnd(toInputValue(plan.time_range?.end ?? null));
  }, [plan]);

  const totalRows = useMemo(
    () => inventory.datasets.reduce((total, dataset) => total + dataset.row_count, 0),
    [inventory.datasets],
  );
  const runnable = plan.profiles.filter((profile) => profile.state === "planned");
  const invalidPeriod = Boolean(start && end && new Date(start) > new Date(end));

  return (
    <section className="analysis-plan" aria-labelledby="analysis-plan-title">
      <div className="analysis-plan__heading">
        <div>
          <p className="eyebrow">Проверка завершена</p>
          <h2 id="analysis-plan-title">Источник распознан</h2>
          <p className="muted">
            Мы определили структуру и подготовили подходящие виды анализа. Технические
            названия полей скрыты — проверьте только период и запустите расчёт.
          </p>
        </div>
        <span className="analysis-plan__ready">Готово к запуску</span>
      </div>

      <div className="source-summary" aria-label="Сводка загруженного источника">
        <div><span>Тип источника</span><strong>{formatLabels[inventory.source_format]}</strong></div>
        <div><span>Наборов данных</span><strong>{formatCount(inventory.datasets.length)}</strong></div>
        <div><span>Записей найдено</span><strong>{formatCount(totalRows)}</strong></div>
        <div><span>Размер</span><strong>{formatBytes(inventory.file_size_bytes)}</strong></div>
      </div>

      <div className="analysis-plan__profiles">
        <h3>Что будет проверено</h3>
        <div className="profile-plan-grid">
          {plan.profiles.map((profile) => {
            const copy = profileCopy[profile.profile];
            const active = profile.state === "planned";
            return (
              <article className={`profile-plan-card ${active ? "is-active" : "is-unavailable"}`} key={profile.profile}>
                <span aria-hidden="true">{active ? "✓" : "—"}</span>
                <div>
                  <strong>{copy.title}</strong>
                  <p>{active ? copy.description : "В этом источнике недостаточно подходящих данных."}</p>
                </div>
                <small>{active ? "Будет выполнено" : "Не включено"}</small>
              </article>
            );
          })}
        </div>
      </div>

      {plan.time_range && (
        <fieldset className="period-picker">
          <legend>Период анализа</legend>
          <p>По умолчанию выбран весь найденный период. При необходимости сузьте его.</p>
          <div className="period-picker__fields">
            <label>
              <span>Начало периода</span>
              <input type="datetime-local" value={start} onChange={(event) => setStart(event.target.value)} />
            </label>
            <label>
              <span>Конец периода</span>
              <input type="datetime-local" value={end} onChange={(event) => setEnd(event.target.value)} />
            </label>
          </div>
          {invalidPeriod && <p className="period-picker__error" role="alert">Начало периода должно быть раньше окончания.</p>}
        </fieldset>
      )}

      <div className="analysis-plan__actions">
        <p>{runnable.length > 0 ? `Будет запущено проверок: ${runnable.length}` : "Подходящие проверки не найдены."}</p>
        <button
          className="button button--primary"
          disabled={busy || runnable.length === 0 || invalidPeriod}
          type="button"
          onClick={() => onRun({ start: toApiValue(start), end: toApiValue(end) })}
        >
          {busy ? "Запускаем…" : "Запустить анализ"}
        </button>
      </div>
    </section>
  );
}
