import { useState } from "react";
import { ConfirmationDialog } from "./ConfirmationDialog";

export function SessionControls({ onCloseSession }: { onCloseSession: () => Promise<boolean> }) {
  const [confirming, setConfirming] = useState(false);
  const [closing, setClosing] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const close = async () => {
    setClosing(true);
    setError(null);
    const succeeded = await onCloseSession();
    if (!succeeded) {
      setClosing(false);
      setConfirming(false);
      setError("Не удалось завершить сессию. Текущий результат сохранён; повторите попытку.");
    }
  };

  return (
    <div className="session-controls">
      <button className="ui-button ui-button--ghost" disabled={closing} type="button" onClick={() => setConfirming(true)}>
        {closing ? "Удаление…" : "Завершить сессию"}
      </button>
      {error && <p className="session-controls__error" role="alert">{error}</p>}
      <ConfirmationDialog
        confirmLabel={closing ? "Удаление…" : "Удалить сессию"}
        description="Временный CSV, результаты анализа и уведомления будут удалены с этого компьютера. Отменить действие после подтверждения нельзя."
        open={confirming}
        title="Завершить локальную сессию?"
        onCancel={() => { if (!closing) setConfirming(false); }}
        onConfirm={() => { if (!closing) void close(); }}
      />
    </div>
  );
}
