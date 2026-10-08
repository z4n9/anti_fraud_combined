import type { AnalysisStatus } from "../types/analysis";

const stageLabels: Record<string, string> = {
  queued: "Ожидание обработки",
  uploaded: "Файл принят",
  inspecting: "Изучаем структуру источника",
  mapping: "Определяем назначение данных",
  planning: "Готовим план анализа",
  planned: "План готов",
  validating: "Проверяем данные",
  transforming: "Подготавливаем данные",
  client_scoring: "Оцениваем риски клиентов",
  transaction_features: "Изучаем поведение операций",
  transaction_scoring: "Ищем подозрительные операции",
  relationships: "Проверяем связи",
  explaining: "Готовим понятные объяснения",
  exporting: "Сохраняем результат",
  ready: "Анализ завершён",
  cancelled: "Анализ отменён",
  reading_csv: "Чтение CSV",
  inference: "Расчёт риска и объяснений",
  completed: "Анализ завершён",
  schema_rejected: "Файл несовместим",
  failed: "Обработка остановлена",
  storage_failed: "Недостаточно временного места",
};

const steps = [
  { label: "Загрузка", threshold: 1 },
  { label: "Структура", threshold: 20 },
  { label: "Распознавание", threshold: 35 },
  { label: "План", threshold: 45 },
  { label: "Анализ", threshold: 100 },
];

interface ProgressPanelProps {
  status: AnalysisStatus;
  cancelling?: boolean;
  onCancel?: () => void;
}

export function ProgressPanel({ status, cancelling = false, onCancel }: ProgressPanelProps) {
  const cancelled = status.status === "cancelled";
  return (
    <section className="progress-panel" aria-live="polite" aria-label="Прогресс анализа">
      <div className="section-heading section-heading--inline">
        <div>
          <p className="eyebrow">Состояние модели</p>
          <h2>{stageLabels[status.stage] ?? status.stage}</h2>
        </div>
        <strong className="progress-value">{status.progress}%</strong>
      </div>
      <div
        aria-label={`Выполнено ${status.progress}%`}
        aria-valuemax={100}
        aria-valuemin={0}
        aria-valuenow={status.progress}
        className="progress-track"
        role="progressbar"
      >
        <span style={{ width: `${status.progress}%` }} />
      </div>
      <p className="muted">{status.filename}</p>
      <ol className="analysis-steps" aria-label="Этапы обработки">
        {steps.map((step, index) => {
          const complete = status.progress >= step.threshold;
          const previousThreshold = index === 0 ? 0 : steps[index - 1].threshold;
          const current = !complete && status.progress >= previousThreshold;
          return (
            <li className={complete ? "is-complete" : current ? "is-current" : ""} key={step.label}>
              <span aria-hidden="true">{complete ? "✓" : index + 1}</span>
              {step.label}
            </li>
          );
        })}
      </ol>
      <p className="progress-note">
        {cancelled
          ? "Временные данные остановленного расчёта не будут использованы."
          : "Большой источник может обрабатываться несколько минут. Можно отменить операцию на любом этапе."}
      </p>
      {onCancel && status.can_cancel && !cancelled && (
        <button className="ui-button ui-button--secondary progress-cancel" disabled={cancelling} type="button" onClick={onCancel}>
          {cancelling ? "Отменяем…" : "Отменить"}
        </button>
      )}
    </section>
  );
}
