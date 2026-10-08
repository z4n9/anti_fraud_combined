import { afterEach, describe, expect, it, vi } from "vitest";

import { getAnalysisSummary, getHealthStatus, setAnalystIdentity, downloadUniversalExport, deleteAnalysis } from "../src/api/client";

const jsonResponse = (payload: unknown) =>
  Promise.resolve({
    ok: true,
    json: async () => payload,
  } as Response);

describe("analysis summary compatibility", () => {
  afterEach(() => { vi.unstubAllGlobals(); setAnalystIdentity(null); });

  it("completes an older universal summary before the dashboard renders", async () => {
    const fetchMock = vi.fn()
      .mockImplementationOnce(() => jsonResponse({
        analysis_id: "analysis-1",
        model_version: "universal-1.0",
        threshold: 0.5,
        summary: { rows: 4, requires_review: 2 },
        metrics: { available: true },
      }))
      .mockImplementationOnce(() => jsonResponse({
        analysis_id: "analysis-1",
        threshold: 0.5,
        risk_counts: { low: 1, medium: 1, high: 1, critical: 1 },
        probability_histogram: [],
      }))
      .mockImplementationOnce(() => jsonResponse({
        warnings: ["Обнаружено новое поле"],
      }));
    vi.stubGlobal("fetch", fetchMock);

    const summary = await getAnalysisSummary("analysis-1");

    expect(summary.summary.risk_counts.critical).toBe(1);
    expect(summary.summary.warnings).toEqual(["Обнаружено новое поле"]);
    expect(summary.summary.target_present).toBe(true);
    expect(summary.summary.target_valid).toBe(true);
    expect(fetchMock).toHaveBeenCalledTimes(3);
  });
});

describe("authenticated analyst API", () => {
  afterEach(() => { vi.unstubAllGlobals(); setAnalystIdentity(null); });
  it("pins ordinary requests, exports and deletion to the signed in analyst", async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, status: 200, json: async () => ({}), headers: new Headers(), blob: async () => new Blob() });
    vi.stubGlobal("fetch", fetchMock);
    setAnalystIdentity(99);
    await getHealthStatus();
    await downloadUniversalExport("analysis-1", "transactions");
    await deleteAnalysis("analysis-1");
    for (const [url, options] of fetchMock.mock.calls) {
      expect(url).toMatch(/^\/api\/analyst\//);
      expect(options.credentials).toBe("same-origin");
      expect(new Headers(options.headers).get("X-Account-ID")).toBe("99");
    }
  });
  it("invalidates a stale account cookie instead of crashing on a detail error", async () => {
    const ended = vi.fn();
    window.addEventListener("analyst-session-ended", ended);
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({ detail: "В другой вкладке сменился аккаунт. Обновите страницу и войдите заново." }), { status: 409 })));
    await expect(getHealthStatus()).rejects.toThrow("Ошибка запроса (409)");
    expect(ended).toHaveBeenCalledTimes(1);
    expect((ended.mock.calls[0][0] as CustomEvent).detail).toBe(409);
    window.removeEventListener("analyst-session-ended", ended);
  });
  it("keeps the analyst session for a normal analysis conflict", async () => {
    const ended = vi.fn();
    window.addEventListener("analyst-session-ended", ended);
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({ error: { code: "plan_not_editable", message: "План уже запущен", details: [] } }), { status: 409 })));
    await expect(getHealthStatus()).rejects.toThrow("План уже запущен");
    expect(ended).not.toHaveBeenCalled();
    window.removeEventListener("analyst-session-ended", ended);
  });
  it("ignores a delayed rejection from a previous analyst", async () => {
    let resolve!: (value: Response) => void;
    vi.stubGlobal("fetch", vi.fn().mockImplementation(() => new Promise<Response>((done) => { resolve = done; })));
    const ended = vi.fn();
    window.addEventListener("analyst-session-ended", ended);
    setAnalystIdentity(99);
    const pending = getHealthStatus();
    setAnalystIdentity(100);
    resolve({ ok: false, status: 409 } as Response);
    await expect(pending).rejects.toThrow("Аккаунт изменился во время запроса");
    expect(ended).not.toHaveBeenCalled();
    window.removeEventListener("analyst-session-ended", ended);
  });
});
