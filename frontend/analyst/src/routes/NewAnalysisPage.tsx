import { useState } from "react";
import { AnalysisPlanPanel } from "../components/AnalysisPlanPanel";
import { AnalystBrief } from "../components/AnalystBrief";
import { ConfirmationDialog } from "../components/ConfirmationDialog";
import { ProgressPanel } from "../components/ProgressPanel";
import { UploadPanel } from "../components/UploadPanel";
import type { AnalysisPlan, AnalysisStatus, SourceInventory } from "../types/analysis";
import { formatErrorDetail } from "../utils/dataQuality";

interface VisibleError {
  message: string;
  details: string[];
}

interface NewAnalysisPageProps {
  error: VisibleError | null;
  hasActiveAnalysis: boolean;
  processing: boolean;
  status: AnalysisStatus | null;
  inventory: SourceInventory | null;
  plan: AnalysisPlan | null;
  cancelling: boolean;
  starting: boolean;
  onClearError: () => void;
  onCancel: () => void;
  onRun: (period: { start: string | null; end: string | null }) => void;
  onStart: (file: File) => void;
}

export function NewAnalysisPage({
  error,
  hasActiveAnalysis,
  processing,
  status,
  inventory,
  plan,
  cancelling,
  starting,
  onClearError,
  onCancel,
  onRun,
  onStart,
}: NewAnalysisPageProps) {
  const [replacementFile, setReplacementFile] = useState<File | null>(null);

  const start = (file: File) => {
    if (hasActiveAnalysis) {
      setReplacementFile(file);
      return;
    }
    onStart(file);
  };

  return (
    <div className="new-analysis-page">
      <header className="page-intro">
        <p className="eyebrow">Локальная проверка выборки</p>
        <h1>Новый анализ</h1>
        <p>
          Передайте банковский источник модели. Файл и результаты остаются на этом
          компьютере и удаляются после завершения сессии.
        </p>
      </header>
      <p className="active-session-note">Анализ операций доступен по объяснимым правилам без ML-моделей. Если клиентская модель отсутствует, клиентский ML-профиль не рассчитывается; причины пропуска видны в плане анализа.</p>

      <AnalystBrief
        title="Перед запуском"
        description="Система сама распознает структуру источника, а вы подтверждаете найденные профили и период анализа."
        steps={[
          { label: "Источник", text: "Загрузите локальный файл с данными" },
          { label: "План", text: "Проверьте найденные таблицы и профили" },
          { label: "Анализ", text: "Запустите расчёт и дождитесь результата" },
        ]}
      />

      <section className="requirements-strip" aria-label="Требования к источнику">
        <div><strong>9 расширений</strong><span>CSV, JSON, SQL, SQLite и BSON</span></div>
        <div><strong>500 МБ</strong><span>Максимальный размер файла</span></div>
        <div><strong>1 000 000</strong><span>Максимальное число записей</span></div>
        <div><strong>Локально</strong><span>Без передачи во внешние сервисы</span></div>
      </section>

      {status && (
        <ProgressPanel cancelling={cancelling} status={status} onCancel={onCancel} />
      )}

      {error && (
        <section className="error-panel" role="alert">
          <p className="eyebrow">Анализ остановлен</p>
          <h2>{error.message}</h2>
          {error.details.length > 0 && (
            <ul>{error.details.map((detail) => <li key={detail}>{formatErrorDetail(detail)}</li>)}</ul>
          )}
          <button className="ui-button ui-button--secondary" type="button" onClick={onClearError}>
            Выбрать другой файл
          </button>
        </section>
      )}

      {status?.status === "planned" && inventory && plan ? (
        <AnalysisPlanPanel busy={starting} inventory={inventory} plan={plan} onRun={onRun} />
      ) : status?.status === "cancelled" ? (
        <section className="cancelled-panel">
          <h2>Расчёт остановлен</h2>
          <p>Можно выбрать другой источник и начать новую проверку.</p>
          <button className="ui-button ui-button--secondary" type="button" onClick={onClearError}>Выбрать другой файл</button>
        </section>
      ) : !processing ? (
        <>
          {hasActiveAnalysis && !error && (
            <aside className="active-session-note">
              <strong>Сейчас открыт другой анализ.</strong>
              <span>При запуске нового файла его временная сессия будет удалена после подтверждения.</span>
            </aside>
          )}

          <UploadPanel disabled={false} onUpload={start} />
        </>
      ) : null}

      <ConfirmationDialog
        confirmLabel="Удалить и продолжить"
        description="Текущий результат и его временные файлы будут удалены. Отменить это действие после запуска нового анализа будет нельзя."
        open={replacementFile !== null}
        title="Заменить текущий анализ?"
        onCancel={() => setReplacementFile(null)}
        onConfirm={() => {
          const file = replacementFile;
          setReplacementFile(null);
          if (file) onStart(file);
        }}
      />
    </div>
  );
}
