import type { AnalysisRow, TransactionRow } from "../types/analysis";
import type { DashboardRouteId } from "../types/dashboard";
import { getRiskLevelLabel } from "../utils/labels";

interface ProfileOverviewProps {
  clients: AnalysisRow[];
  clientTotal: number;
  transactions: TransactionRow[];
  transactionTotal: number;
  onNavigate: (route: DashboardRouteId) => void;
}

function ProfileList({ rows, kind }: { rows: AnalysisRow[]; kind: "client" | "transaction" }) {
  if (!rows.length) return <p className="profile-overview__empty">Профиль не обнаружен в загруженных данных.</p>;
  return (
    <ol className="profile-overview__list">
      {rows.slice(0, 3).map((row) => (
        <li key={row.record_id}>
          <span><small>{kind === "client" ? "Клиент" : "Операция"}</small>{row.record_id}</span>
          <span className={`risk-badge risk-badge--${row.risk_level}`}>{getRiskLevelLabel(row.risk_level)}</span>
          <strong>{(row.risk_probability * 100).toFixed(1)}%</strong>
        </li>
      ))}
    </ol>
  );
}

export function ProfileOverview(props: ProfileOverviewProps) {
  const cards = [
    { title: "Риск клиентов", total: props.clientTotal, rows: props.clients, route: "risk-records" as const, kind: "client" as const },
    { title: "Подозрительные операции", total: props.transactionTotal, rows: props.transactions, route: "transactions" as const, kind: "transaction" as const },
  ];
  return (
    <section className="profile-overview" aria-labelledby="profiles-title">
      <div className="section-heading"><div><p className="eyebrow">Два профиля анализа</p><h2 id="profiles-title">Что требует внимания в первую очередь</h2></div></div>
      <div className="profile-overview__grid">
        {cards.map((card) => (
          <article className="profile-overview__card" key={card.title}>
            <header><div><h3>{card.title}</h3><p>{card.total.toLocaleString("ru-RU")} найдено</p></div><button type="button" onClick={() => props.onNavigate(card.route)}>Открыть все</button></header>
            <ProfileList rows={card.rows} kind={card.kind} />
          </article>
        ))}
      </div>
    </section>
  );
}
