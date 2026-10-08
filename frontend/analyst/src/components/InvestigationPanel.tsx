import { useEffect, useState } from "react";
import { getInvestigation, updateInvestigation } from "../api/client";
import type { InvestigationDetails, InvestigationStatus } from "../types/analysis";

const labels: Record<InvestigationStatus, string> = {
  new: "Новое",
  in_review: "В работе",
  confirmed: "Мошенничество подтверждено",
  dismissed: "Риск не подтвердился",
};

export function InvestigationPanel({ analysisId, entityId }: { analysisId: string; entityId: string }) {
  const [details, setDetails] = useState<InvestigationDetails | null>(null);
  const [status, setStatus] = useState<InvestigationStatus>("new");
  const [comment, setComment] = useState("");
  const [busy, setBusy] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    setBusy(true);
    getInvestigation(analysisId, entityId)
      .then((next) => { if (active) { setDetails(next); setStatus(next.status); setComment(next.comment); } })
      .catch(() => { if (active) setError("Не удалось загрузить расследование."); })
      .finally(() => { if (active) setBusy(false); });
    return () => { active = false; };
  }, [analysisId, entityId]);

  const save = async () => {
    setBusy(true);
    setError(null);
    try {
      const next = await updateInvestigation(analysisId, entityId, { status, comment: comment.trim() });
      setDetails(next);
    } catch {
      setError("Не удалось сохранить решение аналитика.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <section className="investigation-panel" aria-labelledby="investigation-title">
      <div><p className="eyebrow">Решение человека</p><h3 id="investigation-title">Расследование</h3></div>
      <p className="investigation-panel__notice">Прогноз модели не становится обучающей меткой автоматически. Метка создаётся только после явного подтверждения аналитиком.</p>
      <label>Статус<select value={status} disabled={busy} onChange={(event) => setStatus(event.target.value as InvestigationStatus)}>{Object.entries(labels).map(([key, label]) => <option key={key} value={key}>{label}</option>)}</select></label>
      <label>Комментарий<textarea maxLength={1000} rows={3} value={comment} disabled={busy} placeholder="Укажите основание решения" onChange={(event) => setComment(event.target.value)} /></label>
      <button className="ui-button ui-button--primary" disabled={busy} type="button" onClick={() => { void save(); }}>{busy ? "Сохраняем…" : "Сохранить решение"}</button>
      {error && <p role="alert">{error}</p>}
      {details?.confirmed_label && <p className="investigation-panel__confirmed">Подтверждённая метка сохранена обезличенно: {details.confirmed_label.human_label ? "мошенничество" : "ложная тревога"}.</p>}
      {details?.history.length ? <details className="investigation-history"><summary>Журнал изменений ({details.history.length})</summary><ol>{details.history.map((event) => <li key={event.event_id}><strong>{labels[event.status]}</strong><time dateTime={new Date(event.occurred_at * 1000).toISOString()}>{new Date(event.occurred_at * 1000).toLocaleString("ru-RU")}</time>{event.comment && <p>{event.comment}</p>}</li>)}</ol></details> : null}
    </section>
  );
}
