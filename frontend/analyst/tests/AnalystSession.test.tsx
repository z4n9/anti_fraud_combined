import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { AnalystSession } from "../src/AnalystSession";

vi.mock("../src/App", () => ({ default: ({ analystId }: { analystId: number }) => <p>Рабочее место {analystId}</p> }));
const response = (status: number, data: unknown) => ({ status, ok: status === 200, json: async () => data });
beforeEach(() => window.localStorage.clear());
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

it("does not mount analyst data for a bank customer", async () => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(response(403, { detail: "Forbidden" })));
  render(<AnalystSession />);
  expect(await screen.findByRole("alert")).toHaveTextContent("не имеет доступа");
  expect(screen.queryByText(/Рабочее место/)).not.toBeInTheDocument();
});

it("clears only this analyst's local analysis when the cookie identity changes", async () => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(response(200, { id: 99, name: "Аналитик", role: "analyst" })));
  localStorage.setItem("risk-ledger-active-analysis:99", "analysis-99");
  localStorage.setItem("risk-ledger-active-analysis:100", "analysis-100");
  render(<AnalystSession />);
  await screen.findByText("Рабочее место 99");
  window.dispatchEvent(new CustomEvent("analyst-session-ended", { detail: 409 }));
  await waitFor(() => expect(screen.queryByText("Рабочее место 99")).not.toBeInTheDocument());
  expect(localStorage.getItem("risk-ledger-active-analysis:99")).toBeNull();
  expect(localStorage.getItem("risk-ledger-active-analysis:100")).toBe("analysis-100");
});

it("logs in through the bank session API before opening analyst data", async () => {
  const fetchMock = vi.fn().mockResolvedValueOnce(response(401, {})).mockResolvedValueOnce(response(200, { id: 99 })).mockResolvedValueOnce(response(200, { id: 99, name: "Аналитик", role: "analyst" }));
  vi.stubGlobal("fetch", fetchMock);
  const user = userEvent.setup();
  render(<AnalystSession />);
  await screen.findByRole("alert");
  await user.type(screen.getByLabelText("Тестовый ИИН"), "TEST0099");
  await user.type(screen.getByLabelText("Пароль"), "Aman-Test-0099!");
  await user.click(screen.getByRole("button", { name: "Войти" }));
  await screen.findByText("Рабочее место 99");
  expect(fetchMock.mock.calls[1][0]).toBe("/api/auth/login");
  expect(fetchMock.mock.calls[1][1].credentials).toBe("same-origin");
  expect(new Headers(fetchMock.mock.calls[2][1].headers).get("X-Account-ID")).toBe("99");
});
