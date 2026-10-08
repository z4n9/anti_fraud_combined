import { describe, expect, it } from "vitest";
import {
  DASHBOARD_ROUTES,
  DEFAULT_DASHBOARD_PREFERENCES,
  DEFAULT_RISK_RECORD_FILTERS,
  INITIAL_ANALYSIS_WORKSPACE_STATE,
  isDashboardRouteAvailable,
} from "../src/types/dashboard";

describe("dashboard contract", () => {
  it("defines unique routes and keeps only the upload route open without a result", () => {
    expect(new Set(DASHBOARD_ROUTES.map((route) => route.id)).size).toBe(
      DASHBOARD_ROUTES.length,
    );
    expect(new Set(DASHBOARD_ROUTES.map((route) => route.path)).size).toBe(
      DASHBOARD_ROUTES.length,
    );

    const availableWithoutAnalysis = DASHBOARD_ROUTES.filter((route) =>
      isDashboardRouteAvailable(route, "empty"),
    );
    expect(availableWithoutAnalysis.map((route) => route.id)).toEqual(["new-analysis"]);
    expect(
      DASHBOARD_ROUTES.every((route) => isDashboardRouteAvailable(route, "ready")),
    ).toBe(true);
  });

  it("uses the approved default view and filter settings", () => {
    expect(DEFAULT_DASHBOARD_PREFERENCES).toEqual({
      density: "comfortable",
      explanationMode: "simple",
      contrast: "standard",
      textScale: 100,
    });
    expect(DEFAULT_RISK_RECORD_FILTERS).toMatchObject({
      riskLevel: "all",
      requiresReview: null,
      probabilityMin: 0,
      probabilityMax: 1,
      page: 1,
      pageSize: 25,
    });
    expect(INITIAL_ANALYSIS_WORKSPACE_STATE).toMatchObject({
      phase: "empty",
      activeRoute: "new-analysis",
      analysisId: null,
    });
  });
});
