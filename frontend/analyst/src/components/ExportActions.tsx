import { useEffect, useRef, useState } from "react";
import { downloadAnalysisReport, downloadUniversalExport } from "../api/client";
import type { UniversalExportKind } from "../types/analysis";
import { ConfirmationDialog } from "./ConfirmationDialog";

type ExportKind = "full" | "review";

interface ExportActionsProps {
  analysisId: string;
  reviewCount: number;
  threshold: number;
}

export function ExportActions({ analysisId, reviewCount, threshold }: ExportActionsProps) {
  const [active, setActive] = useState<ExportKind | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [kind, setKind] = useState<UniversalExportKind>("transactions");
  const [masked, setMasked] = useState(true);
  const [confirmUnmasked, setConfirmUnmasked] = useState(false);
  const mounted = useRef(true);

  useEffect(() => () => { mounted.current = false; }, []);

  const download = async (kind: ExportKind) => {
    setActive(kind);
    setError(null);
    try {
      const report = await downloadAnalysisReport(analysisId, threshold, kind === "review");
      const objectUrl = URL.createObjectURL(report.blob);
      const link = document.createElement("a");
      link.href = objectUrl;
      link.download = report.filename;
      link.click();
      URL.revokeObjectURL(objectUrl);
    } catch (caught) {
      if (!mounted.current) return;
      setError(caught instanceof Error ? caught.message : "Не удалось скачать CSV. Попробуйте ещё раз.");
    } finally {
      if (mounted.current) setActive(null);
    }
  };

  const downloadSelected = async () => {
    setActive("full");
    setError(null);
    try {
      const report = await downloadUniversalExport(analysisId, kind, masked);
      const objectUrl = URL.createObjectURL(report.blob);
      const link = document.createElement("a");
      link.href = objectUrl;
      link.download = report.filename;
      link.click();
      URL.revokeObjectURL(objectUrl);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Не удалось скачать выбранную выгрузку.");
    } finally {
      setActive(null);
    }
  };

  return (
    <div className="export-actions">
      <div className="export-actions__buttons">
        <button className="ui-button ui-button--primary" disabled={active !== null} type="button" onClick={() => { void download("full"); }}>
          {active === "full" ? <><span className="ui-spinner" aria-hidden="true" /> Подготовка CSV…</> : "Скачать CSV"}
        </button>
        <button className="ui-button ui-button--secondary" disabled={active !== null || reviewCount === 0} type="button" onClick={() => { void download("review"); }}>
          {active === "review" ? <><span className="ui-spinner" aria-hidden="true" /> Подготовка…</> : `Только ручная проверка (${reviewCount})`}
        </button>
      </div>
      <small>Выгрузка учитывает порог {(threshold * 100).toFixed(0)}% и содержит полный набор разрешённых полей.</small>
      <details className="export-options">
        <summary>Другие варианты выгрузки</summary>
        <div>
          <label>Состав данных<select value={kind} onChange={(event) => setKind(event.target.value as UniversalExportKind)}><option value="full">Клиенты и операции</option><option value="review">Только требующие проверки</option><option value="transactions">Только операции</option><option value="relationships">Связи</option><option value="mapping_quality">Качество распознавания</option></select></label>
          <label className="export-options__mask"><input type="checkbox" checked={!masked} onChange={(event) => event.target.checked ? setConfirmUnmasked(true) : setMasked(true)} />Включить исходные идентификаторы</label>
          <p>{masked ? "Идентификаторы будут замаскированы." : "Внимание: файл будет содержать исходные идентификаторы."}</p>
          <button className="ui-button ui-button--secondary" disabled={active !== null} type="button" onClick={() => { void downloadSelected(); }}>Скачать выбранную выгрузку</button>
        </div>
      </details>
      <ConfirmationDialog open={confirmUnmasked} title="Раскрыть идентификаторы?" description="Используйте такой файл только в защищённом контуре. По умолчанию Risk Ledger маскирует чувствительные значения." confirmLabel="Раскрыть для выгрузки" onCancel={() => setConfirmUnmasked(false)} onConfirm={() => { setMasked(false); setConfirmUnmasked(false); }} />
      {error && <p className="export-actions__error" role="alert">{error}</p>}
    </div>
  );
}
