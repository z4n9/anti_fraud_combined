import { ExportActions } from "../components/ExportActions";
import { AnalystBrief } from "../components/AnalystBrief";
import { RiskDonutChart } from "../components/RiskDonutChart";
import { SessionControls } from "../components/SessionControls";
import { QualityHighlights } from "../components/QualityHighlights";
import { SummaryCards } from "../components/SummaryCards";
import { TopRiskRecords } from "../components/TopRiskRecords";
import { ProfileOverview } from "../components/ProfileOverview";
import type { AnalysisRow, AnalysisSummary, TransactionRow } from "../types/analysis";
import type { DashboardRouteId } from "../types/dashboard";

interface OverviewPageProps {
  analysisId: string;
  filename: string | null;
  rows: AnalysisRow[];
  clients?: AnalysisRow[];
  clientTotal?: number;
  transactions?: TransactionRow[];
  transactionTotal?: number;
  summary: AnalysisSummary;
  onCloseSession: () => Promise<boolean>;
  onNavigate: (route: DashboardRouteId) => void;
}

export function OverviewPage({
  analysisId,
  filename,
  rows,
  clients,
  clientTotal,
  transactions,
  transactionTotal,
  summary,
  onCloseSession,
  onNavigate,
}: OverviewPageProps) {
  const showProfiles = clientTotal !== undefined || transactionTotal !== undefined;
  return (
    <div className="overview-page">
      <header className="overview-hero">
        <div>
          <p className="eyebrow">Результат анализа</p>
          <h1>Карта риска выборки</h1>
          <p>{filename ?? "Загруженная выборка"} · модель {summary.model_version}</p>
        </div>
        <div className="overview-actions">
          <ExportActions
            analysisId={analysisId}
            reviewCount={summary.summary.requires_review}
            threshold={summary.threshold}
          />
          <SessionControls onCloseSession={onCloseSession} />
        </div>
      </header>

      <AnalystBrief
        title="Маршрут проверки"
        description="Сводка помогает быстро перейти от общего риска к конкретному решению аналитика."
        steps={[
          { label: "Оцените масштаб", text: "Посмотрите число критических записей и тревог" },
          { label: "Откройте лидеров", text: "Начните с клиентов и операций с максимальным риском" },
          { label: "Зафиксируйте вывод", text: "Изучите факторы и сохраните решение" },
        ]}
      />

      <SummaryCards summary={summary} />

      {showProfiles && <ProfileOverview clients={clients ?? rows} clientTotal={clientTotal ?? rows.length} transactions={transactions ?? []} transactionTotal={transactionTotal ?? 0} onNavigate={onNavigate} />}

      <div className="overview-grid">
        <RiskDonutChart counts={summary.summary.risk_counts} />
        <QualityHighlights summary={summary} onNavigate={onNavigate} />
      </div>

      <TopRiskRecords rows={rows} onOpenAll={() => onNavigate("risk-records")} />
    </div>
  );
}
