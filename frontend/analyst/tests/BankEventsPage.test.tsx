import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { BankEventsPage } from "../src/routes/BankEventsPage";
import { ApiClientError } from "../src/api/client";

const api = vi.hoisted(() => ({ getBankEvents: vi.fn(), getBankEvent: vi.fn(), decideBankEvent: vi.fn() }));
vi.mock("../src/api/client", async (importOriginal) => ({ ...await importOriginal<typeof import("../src/api/client")>(), ...api }));
const event = { id: 1, status: "bank_review", sender_name: "Амина", recipient_name: "Получатель", recipient: "+77000000000", amount: 100, message: "", created_at: "2026-10-08T00:00:00Z", expires_at: "2026-10-09T00:00:00Z", participants: [{ invitation_id: 1, name: "Марат", response: "approve" }, { invitation_id: 2, name: "Дочь", response: "reject" }], anti_scam: { pressure: true, secrecy: null, stranger: false }, bank_decisions: [] };
beforeEach(() => { vi.resetAllMocks(); api.getBankEvents.mockResolvedValue({ items: [event], total: 21, page: 1, page_size: 20 }); api.getBankEvent.mockResolvedValue(event); });
afterEach(cleanup);

it("loads live bank events and paginates without a file analysis", async () => {
  const user = userEvent.setup();
  render(<BankEventsPage />);
  await screen.findByRole("button", { name: "Открыть запрос 1" });
  expect(api.getBankEvents).toHaveBeenCalledWith({ page: 1, status: "bank_review", senderName: "" });
  await user.click(screen.getByRole("button", { name: "Следующая страница" }));
  await waitFor(() => expect(api.getBankEvents).toHaveBeenLastCalledWith({ page: 2, status: "bank_review", senderName: "" }));
});
it("requires a note and records an employee decision with its audit trail", async () => {
  const user = userEvent.setup();
  api.decideBankEvent.mockResolvedValue({ ...event, status: "completed", bank_decisions: [{ action: "approve", note: "Проверено по звонку", actor_name: "Сотрудник", created_at: event.created_at }] });
  render(<BankEventsPage />);
  await user.click(await screen.findByRole("button", { name: "Открыть запрос 1" }));
  expect(await screen.findByRole("button", { name: "Разрешить перевод" })).toBeDisabled();
  expect(screen.getByText("Марат: Одобрен")).toBeInTheDocument();
  expect(screen.getByText("Дочь: Отклонён")).toBeInTheDocument();
  await user.type(screen.getByLabelText("Основание решения"), "Проверено по звонку");
  await user.click(screen.getByRole("button", { name: "Разрешить перевод" }));
  await waitFor(() => expect(api.decideBankEvent).toHaveBeenCalledWith(1, "approve", "Проверено по звонку"));
  expect(await screen.findByText("Проверено по звонку")).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Разрешить перевод" })).not.toBeInTheDocument();
});
it("never offers an employee override while family voting is pending", async () => {
  api.getBankEvent.mockResolvedValue({ ...event, status: "pending_approval" });
  const user = userEvent.setup();
  render(<BankEventsPage />);
  await user.click(await screen.findByRole("button", { name: "Открыть запрос 1" }));
  await screen.findByText("Для этого статуса решение сотрудника недоступно.");
  expect(screen.queryByRole("button", { name: "Разрешить перевод" })).not.toBeInTheDocument();
  expect(api.decideBankEvent).not.toHaveBeenCalled();
});
it("never fetches recovery data after the account session has ended", async () => {
  api.decideBankEvent.mockRejectedValue(new ApiClientError("session_ended", "Сессия завершена", [], true));
  const user = userEvent.setup();
  render(<BankEventsPage />);
  await user.click(await screen.findByRole("button", { name: "Открыть запрос 1" }));
  await user.type(await screen.findByLabelText("Основание решения"), "Проверено сотрудником");
  await user.click(screen.getByRole("button", { name: "Разрешить перевод" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("Сессия завершена");
  expect(api.getBankEvent).toHaveBeenCalledTimes(1);
  expect(screen.queryByLabelText("Основание решения")).not.toBeInTheDocument();
});
it("shows an expired action response without claiming execution or an audit decision", async () => {
  api.decideBankEvent.mockResolvedValue({ ...event, status: "expired", bank_decisions: [] });
  const user = userEvent.setup();
  render(<BankEventsPage />);
  await user.click(await screen.findByRole("button", { name: "Открыть запрос 1" }));
  await user.type(await screen.findByLabelText("Основание решения"), "Проверено сотрудником");
  await user.click(screen.getByRole("button", { name: "Разрешить перевод" }));
  expect(await screen.findByText("Запрос № 1: срок истёк; перевод не исполнен.")).toBeInTheDocument();
  expect(screen.queryByText(/Решение записано в журнал/)).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Разрешить перевод" })).not.toBeInTheDocument();
  expect(screen.getByText("Решений банка пока нет.")).toBeInTheDocument();
});
