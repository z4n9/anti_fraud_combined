import { act, cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { RecipientMonitoringPage } from "../src/routes/RecipientMonitoringPage";
import type { RecipientMonitoringPage as MonitoringResponse } from "../src/types/recipientMonitoring";

const api = vi.hoisted(() => ({ getRecipientMonitoring: vi.fn() }));
vi.mock("../src/api/client", () => api);
const fixture = (): MonitoringResponse => ({
  source: { kind: "bank", analysis_id: null, records_scanned: 120, truncated: false, warnings: ["Более ранняя история может быть неизвестна."], as_of: "2026-10-15T12:00:00Z" },
  config: { window_minutes: 60, max_small_amount: 10000, currency: "KZT", min_small_transfers: 10, min_unique_senders: 8, min_new_senders: 8, min_new_sender_share: 0.8, detail_limit: 200 },
  total: 1, page: 1, page_size: 20,
  items: [{ recipient_id: "receiver-1", label: "Получатель А", window_start: "2026-10-15T11:00:00Z", window_end: "2026-10-15T12:00:00Z", small_transfer_count: 12, total_amount: 24000, unique_senders: 10, new_senders: 8, new_sender_share: 0.8, level: "high", reasons: ["Большая доля новых отправителей"], details_truncated: false,
    sender_breakdown: [{ sender_id: "sender-new", count: 8, total_amount: 16000, new_to_recipient: true }, { sender_id: "sender-repeat", count: 4, total_amount: 8000, new_to_recipient: false }],
    transactions: [{ id: "tx-1", timestamp: "2026-10-15T11:20:00Z", sender_id: "sender-new", recipient_id: "receiver-1", amount: 2000 }] }],
});
beforeEach(() => { vi.resetAllMocks(); api.getRecipientMonitoring.mockResolvedValue(fixture()); });
afterEach(cleanup);

it("opens bank monitoring without an uploaded analysis and explains uncertainty", async () => {
  render(<RecipientMonitoringPage completedAnalysisId={null} />);
  await screen.findByRole("button", { name: /Получатель А/ });
  expect(api.getRecipientMonitoring).toHaveBeenCalledWith({ page: 1, analysisId: undefined, recipientId: undefined }, expect.any(AbortSignal));
  expect(screen.getByRole("option", { name: "Текущий завершённый анализ" })).toBeDisabled();
  expect(screen.getByText(/не доказательство мошенничества/)).toBeInTheDocument();
  expect(screen.getByText("Более ранняя история может быть неизвестна.")).toBeInTheDocument();
});
it("shows loading, then an empty result without implying that accounts are safe", async () => {
  let resolve!: (response: MonitoringResponse) => void;
  api.getRecipientMonitoring.mockImplementation(() => new Promise<MonitoringResponse>((done) => { resolve = done; }));
  render(<RecipientMonitoringPage completedAnalysisId={null} />);
  expect(screen.getByRole("status")).toHaveTextContent("Загружаем сигналы");
  await act(async () => resolve({ ...fixture(), items: [], total: 0 }));
  expect(await screen.findByText("Сигналов по заданным условиям нет")).toBeInTheDocument();
  expect(screen.getByText(/Это не подтверждение безопасности/)).toBeInTheDocument();
});
it("shows a recoverable error and retries explicitly", async () => {
  api.getRecipientMonitoring.mockRejectedValueOnce(new Error("Источник недоступен"));
  const user = userEvent.setup();
  render(<RecipientMonitoringPage completedAnalysisId={null} />);
  expect(await screen.findByRole("alert")).toHaveTextContent("Источник недоступен");
  await user.click(screen.getByRole("button", { name: "Повторить загрузку" }));
  await screen.findByRole("button", { name: /Получатель А/ });
  expect(api.getRecipientMonitoring).toHaveBeenCalledTimes(2);
});
it("opens reasons, new versus repeat senders, timeline and capped evidence", async () => {
  const response = fixture(); response.items[0].details_truncated = true;
  api.getRecipientMonitoring.mockResolvedValue(response);
  const user = userEvent.setup();
  render(<RecipientMonitoringPage completedAnalysisId={null} />);
  await user.click(await screen.findByRole("button", { name: /Получатель А/ }));
  expect(screen.getByRole("heading", { name: "Получатель А" })).toBeInTheDocument();
  expect(screen.getByText("Новый в доступной истории")).toBeInTheDocument();
  expect(screen.getByText("Повторный")).toBeInTheDocument();
  expect(screen.getByText("Операция tx-1")).toBeInTheDocument();
  expect(screen.getByText(/Детализация ограничена 200/)).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: /Одобрить|Разрешить|Блокировать/ })).not.toBeInTheDocument();
});
it("uses only the supplied completed analysis and clears old recipient details", async () => {
  const user = userEvent.setup();
  render(<RecipientMonitoringPage completedAnalysisId="owned-analysis" />);
  await user.click(await screen.findByRole("button", { name: /Получатель А/ }));
  await user.selectOptions(screen.getByLabelText("Источник данных"), "analysis");
  await waitFor(() => expect(api.getRecipientMonitoring).toHaveBeenLastCalledWith({ page: 1, analysisId: "owned-analysis", recipientId: undefined }, expect.any(AbortSignal)));
  expect(screen.queryByRole("heading", { name: "Получатель А" })).not.toBeInTheDocument();
});
it("ignores an obsolete bank response after switching to an analysis", async () => {
  let resolveBank!: (response: MonitoringResponse) => void;
  api.getRecipientMonitoring.mockImplementationOnce(() => new Promise<MonitoringResponse>((done) => { resolveBank = done; })).mockResolvedValue({ ...fixture(), source: { ...fixture().source, kind: "analysis", analysis_id: "owned-analysis" }, items: [], total: 0 });
  const user = userEvent.setup();
  render(<RecipientMonitoringPage completedAnalysisId="owned-analysis" />);
  await user.selectOptions(screen.getByLabelText("Источник данных"), "analysis");
  await screen.findByText("Сигналов по заданным условиям нет");
  await act(async () => resolveBank(fixture()));
  expect(screen.queryByRole("button", { name: /Получатель А/ })).not.toBeInTheDocument();
  expect(screen.getByText("Загруженный анализ")).toBeInTheDocument();
});
it("filters recipients and paginates on the server", async () => {
  api.getRecipientMonitoring.mockResolvedValue({ ...fixture(), total: 21 });
  const user = userEvent.setup();
  render(<RecipientMonitoringPage completedAnalysisId={null} />);
  await user.click(await screen.findByRole("button", { name: "Следующая страница" }));
  await waitFor(() => expect(api.getRecipientMonitoring).toHaveBeenLastCalledWith({ page: 2, analysisId: undefined, recipientId: undefined }, expect.any(AbortSignal)));
  await user.type(screen.getByLabelText("Счёт или ID получателя"), "receiver-1");
  await user.click(screen.getByRole("button", { name: "Обновить мониторинг" }));
  await waitFor(() => expect(api.getRecipientMonitoring).toHaveBeenLastCalledWith({ page: 1, analysisId: undefined, recipientId: "receiver-1" }, expect.any(AbortSignal)));
});
