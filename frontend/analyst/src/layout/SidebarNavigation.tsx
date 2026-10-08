import { DASHBOARD_ROUTES, isDashboardRouteAvailable } from "../types/dashboard";
import type { DashboardRouteId, WorkspacePhase } from "../types/dashboard";

const routeMarks: Record<DashboardRouteId, string> = {
  "new-analysis": "+",
  "bank-events": "▣",
  overview: "◫",
  "risk-records": "↗",
  transactions: "⇄",
  relationships: "⌘",
  "model-quality": "◎",
  "data-quality": "◇",
};

interface SidebarNavigationProps {
  activeRoute: DashboardRouteId;
  mobileMode: boolean;
  mobileOpen: boolean;
  phase: WorkspacePhase;
  onCloseMobile: () => void;
  onNavigate: (route: DashboardRouteId) => void;
}

export function SidebarNavigation({
  activeRoute,
  mobileMode,
  mobileOpen,
  phase,
  onCloseMobile,
  onNavigate,
}: SidebarNavigationProps) {
  return (
    <>
      <button
        aria-hidden={!mobileOpen}
        aria-label="Закрыть меню"
        className={`sidebar-backdrop${mobileOpen ? " is-visible" : ""}`}
        tabIndex={mobileOpen ? 0 : -1}
        type="button"
        onClick={onCloseMobile}
      />
      <aside
        aria-hidden={mobileMode && !mobileOpen ? true : undefined}
        className={`dashboard-sidebar${mobileOpen ? " is-open" : ""}`}
        id="dashboard-sidebar"
        inert={mobileMode && !mobileOpen}
        onKeyDown={(event) => {
          if (event.key === "Escape" && mobileOpen) onCloseMobile();
        }}
      >
        <a
          className="dashboard-brand"
          href="/analyst/new-analysis"
          aria-label="Risk Ledger — новый анализ"
          onClick={(event) => {
            event.preventDefault();
            onNavigate("new-analysis");
            onCloseMobile();
          }}
        >
          <span className="dashboard-brand__mark">RL</span>
          <span className="dashboard-brand__name">Risk Ledger</span>
        </a>
        <nav aria-label="Основная навигация">
          {DASHBOARD_ROUTES.map((route) => {
            const available = isDashboardRouteAvailable(route, phase);
            return (
              <a
                aria-current={activeRoute === route.id ? "page" : undefined}
                aria-disabled={!available}
                className={activeRoute === route.id ? "is-active" : ""}
                data-tooltip={route.label}
                href={"/analyst" + route.path}
                key={route.id}
                tabIndex={available ? undefined : -1}
                onClick={(event) => {
                  event.preventDefault();
                  if (!available) return;
                  onNavigate(route.id);
                  onCloseMobile();
                }}
              >
                <span className="dashboard-nav__mark" aria-hidden="true">
                  {routeMarks[route.id]}
                </span>
                <span className="dashboard-nav__label">{route.label}</span>
                {!available && <span className="dashboard-nav__lock" aria-label="Раздел станет доступен после анализа">·</span>}
              </a>
            );
          })}
        </nav>
        <div className="dashboard-sidebar__footer">
          <span className="local-dot" aria-hidden="true" />
          <span>Локальный режим</span>
        </div>
      </aside>
    </>
  );
}
