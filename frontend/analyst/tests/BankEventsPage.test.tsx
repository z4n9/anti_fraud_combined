import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { BankEventsPage } from "../src/routes/BankEventsPage";
import { ApiClientError } from "../src/api/client";

const api = vi.hoisted(() => ({ getBankEvents: vi.fn(), getBankEvent: vi.fn(), decideBankEvent: vi.fn() }));
vi.mock("../src/api/client", async (importOriginal) => ({ ...await importOriginal<typeof import("../src/api/client")>(), ...api }));
const event = { id: 1, status: "bank_review", sender_name: "Амина", recipient_name: "Получатель", recipient: "+77000000000", amount: 100, message: "", created_at: "2026-10-08T00:00:00Z", expires_at: "2026-10-09T00:00:00Z", participants: [{ invitation_id: 1, name: "Марат", response: "approve" }, { invitation_id: 2, name: "Дочь", response: "pending" }, { invitation_id: 3, name: "Сын", response: "pending" }], bank_review_reason: "anti_scam", anti_scam: { pressure: true, secrecy: null, stranger: false }, bank_decisions: [] };
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
  expect(screen.getByText("Дочь: Нет ответа")).toBeInTheDocument();
  expect(screen.getByText("Сын: Нет ответа")).toBeInTheDocument();
  expect(screen.getByText(/достаточно одного ответа родственника; первый ответ окончателен/)).toBeInTheDocument();
  expect(screen.queryByText(/по прежним правилам/)).not.toBeInTheDocument();
  await user.type(screen.getByLabelText("Основание решения"), "Проверено по звонку");
  await user.click(screen.getByRole("button", { name: "Разрешить перевод" }));
  await waitFor(() => expect(api.decideBankEvent).toHaveBeenCalledWith(1, "approve", "Проверено по звонку"));
  expect(await screen.findByText("Проверено по звонку")).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Разрешить перевод" })).not.toBeInTheDocument();
});
it("never offers an employee override while the first family response is pending", async () => {
  api.getBankEvent.mockResolvedValue({ ...event, status: "pending_approval" });
  const user = userEvent.setup();
  render(<BankEventsPage />);
  await user.click(await screen.findByRole("button", { name: "Открыть запрос 1" }));
  await screen.findByText("Для этого статуса решение сотрудника недоступно.");
  expect(screen.queryByRole("button", { name: "Разрешить перевод" })).not.toBeInTheDocument();
  expect(api.decideBankEvent).not.toHaveBeenCalled();
});
it("preserves the explanation and answers of a legacy family conflict", async () => {
  api.getBankEvent.mockResolvedValue({ ...event, bank_review_reason: "conflicting_votes", participants: [{ invitation_id: 1, name: "Марат", response: "approve" }, { invitation_id: 2, name: "Дочь", response: "reject" }] });
  const user = userEvent.setup();
  render(<BankEventsPage />);
  await user.click(await screen.findByRole("button", { name: "Открыть запрос 1" }));
  expect(await screen.findByText(/создан по прежним правилам/)).toBeInTheDocument();
  expect(screen.getByText("Марат: Одобрен")).toBeInTheDocument();
  expect(screen.getByText("Дочь: Отклонён")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Разрешить перевод" })).toBeDisabled();
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

const mlAssessment = {
  status: "available", model_id: "synsepa_s0_clean_kzt", model_version: "1.1-kzt-units", score: 0.7345,
  ml_decision: "ALLOW", rule_decision: "REVIEW", decision: "REVIEW", out_of_distribution: true,
  currency: "KZT", model_currency: "KZT", unit_version: "kzt-native-v1", amount_kzt: 100,
  monetary_thresholds_kzt: { cold_start_review: 1075500, cold_start_high_risk: 18863000, velocity_amount_sum_1h_review: 27181500, velocity_amount_sum_1h_high_risk: 58017500, high_amount_indicator: 25000000 },
  thresholds: { review: 0.7, high_risk: 0.9 },
};

it("shows the analyst the model score, separate decisions and limits of applicability", async () => {
  api.getBankEvent.mockResolvedValue({ ...event, ml_assessment: mlAssessment });
  const user = userEvent.setup();
  render(<BankEventsPage />);
  await user.click(await screen.findByRole("button", { name: "Открыть запрос 1" }));
  const panel = within(await screen.findByRole("region", { name: "Проверка ML-моделью" }));
  expect(panel.getByText("Оценка модели")).toBeInTheDocument();
  expect(panel.getByText("0,7345")).toBeInTheDocument();
  expect(panel.getByText("synsepa_s0_clean_kzt")).toBeInTheDocument();
  expect(panel.getByText("1.1-kzt-units")).toBeInTheDocument();
  expect(panel.getByText(/ALLOW/)).toBeInTheDocument();
  expect(panel.getAllByText(/REVIEW/)).toHaveLength(2);
  expect(panel.getByText("Валюта расчёта")).toBeInTheDocument();
  expect(panel.getAllByText("KZT")).toHaveLength(2);
  expect(panel.getByText("100 ₸")).toBeInTheDocument();
  expect(panel.getByText("Да — применимость модели ограничена")).toBeInTheDocument();
  expect(panel.getByText(/не является подтверждённой вероятностью мошенничества/)).toBeInTheDocument();
  expect(panel.getByText(/без переобучения на казахстанских операциях/)).toBeInTheDocument();
  expect(panel.queryByText(/EUR|курс/i)).not.toBeInTheDocument();
  await user.click(panel.getByText("Денежные пороги правил в тенге"));
  expect(panel.getByText("1 075 500 ₸")).toBeInTheDocument();
  expect(panel.getByText("18 863 000 ₸")).toBeInTheDocument();
  expect(panel.getByText("27 181 500 ₸")).toBeInTheDocument();
  expect(panel.getByText("58 017 500 ₸")).toBeInTheDocument();
  expect(panel.getByText("25 000 000 ₸")).toBeInTheDocument();
  expect(panel.getByText(/не банковские лимиты/)).toBeInTheDocument();
});

it("preserves the archived currency conversion instead of relabelling a historical score as KZT-native", async () => {
  api.getBankEvent.mockResolvedValue({ ...event, ml_assessment: {
    ...mlAssessment, model_id: "synsepa_s0_clean", model_version: "1.0", currency: undefined, model_currency: undefined,
    unit_version: undefined, amount_kzt: undefined, monetary_thresholds_kzt: undefined, demo_kzt_per_eur: 500,
  } });
  const user = userEvent.setup();
  render(<BankEventsPage />);
  await user.click(await screen.findByRole("button", { name: "Открыть запрос 1" }));
  const panel = within(await screen.findByRole("region", { name: "Проверка ML-моделью" }));
  const archive = within(panel.getByRole("region", { name: "Архивный расчёт прежней версии" }));
  expect(archive.getByText(/признаки рассчитывались в EUR/)).toBeInTheDocument();
  expect(archive.getByText(/500 ₸ за 1 EUR/)).toBeInTheDocument();
  expect(archive.getByText(/Исторический расчёт не изменён/)).toBeInTheDocument();
  expect(panel.getByText("0,7345")).toBeInTheDocument();
  expect(panel.queryByText("Валюта расчёта")).not.toBeInTheDocument();
  expect(panel.queryByText("Денежные пороги правил в тенге")).not.toBeInTheDocument();
});

it("does not invent current monetary rule thresholds when an assessment did not save them", async () => {
  api.getBankEvent.mockResolvedValue({ ...event, ml_assessment: { ...mlAssessment, monetary_thresholds_kzt: undefined } });
  const user = userEvent.setup();
  render(<BankEventsPage />);
  await user.click(await screen.findByRole("button", { name: "Открыть запрос 1" }));
  const panel = within(await screen.findByRole("region", { name: "Проверка ML-моделью" }));
  expect(panel.getByText("Валюта расчёта")).toBeInTheDocument();
  expect(panel.queryByText("Денежные пороги правил в тенге")).not.toBeInTheDocument();
  expect(panel.queryByText(/EUR|курс/i)).not.toBeInTheDocument();
});

it.each(["unavailable", "disabled"])("shows rules fallback for %s ML without presenting a stale model score", async (status) => {
  api.getBankEvent.mockResolvedValue({ ...event, ml_assessment: { ...mlAssessment, status } });
  const user = userEvent.setup();
  render(<BankEventsPage />);
  await user.click(await screen.findByRole("button", { name: "Открыть запрос 1" }));
  const panel = within(await screen.findByRole("region", { name: "Проверка ML-моделью" }));
  expect(panel.getByText(/Проверка выполнена по правилам/)).toBeInTheDocument();
  expect(panel.queryByText("0,7345")).not.toBeInTheDocument();
  expect(panel.queryByText("Оценка модели")).not.toBeInTheDocument();
});

it("keeps events without a saved ML assessment readable", async () => {
  const user = userEvent.setup();
  render(<BankEventsPage />);
  await user.click(await screen.findByRole("button", { name: "Открыть запрос 1" }));
  await screen.findByText("Для новых запросов достаточно одного ответа родственника; первый ответ окончателен.", { exact: false });
  expect(screen.queryByRole("region", { name: "Проверка ML-моделью" })).not.toBeInTheDocument();
});
