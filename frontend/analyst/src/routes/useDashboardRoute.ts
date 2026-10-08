import { useCallback, useEffect, useState } from "react";
import {
  DASHBOARD_ROUTES,
  isDashboardRouteAvailable,
} from "../types/dashboard";
import type { DashboardRouteId, WorkspacePhase } from "../types/dashboard";

function routeFromPath(pathname: string): DashboardRouteId {
  const localPath = pathname.replace(/^\/analyst(?=\/|$)/, "") || "/";
  return DASHBOARD_ROUTES.find((route) => route.path === localPath)?.id ?? "new-analysis";
}

function pathForRoute(routeId: DashboardRouteId): string {
  return "/analyst" + DASHBOARD_ROUTES.find((route) => route.id === routeId)!.path;
}

export function useDashboardRoute(phase: WorkspacePhase) {
  const [activeRoute, setActiveRoute] = useState<DashboardRouteId>(() =>
    routeFromPath(window.location.pathname),
  );

  const navigate = useCallback((routeId: DashboardRouteId, replace = false) => {
    const path = pathForRoute(routeId);
    window.history[replace ? "replaceState" : "pushState"]({}, "", path);
    setActiveRoute(routeId);
  }, []);

  useEffect(() => {
    const handlePopState = () => setActiveRoute(routeFromPath(window.location.pathname));
    window.addEventListener("popstate", handlePopState);
    return () => window.removeEventListener("popstate", handlePopState);
  }, []);

  useEffect(() => {
    const route = DASHBOARD_ROUTES.find((item) => item.id === activeRoute)!;
    if (!isDashboardRouteAvailable(route, phase)) navigate("new-analysis", true);
  }, [activeRoute, navigate, phase]);

  return { activeRoute, navigate };
}
