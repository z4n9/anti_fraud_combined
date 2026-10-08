import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { InvestigationPanel } from "../src/components/InvestigationPanel";
import { SensitiveValue } from "../src/components/SensitiveValue";

const api = vi.hoisted(() => ({ getInvestigation: vi.fn(), updateInvestigation: vi.fn() }));
vi.mock("../src/api/client", () => api);

afterEach(() => { cleanup(); vi.clearAllMocks(); });

describe("investigation workflow", () => {
  it("stores an explicit human decision and shows the audit journal", async () => {
    const user = userEvent.setup();
    api.getInvestigation.mockResolvedValue({ analysis_id: "a", entity_id: "txn-1", status: "new", comment: "", updated_at: null, confirmed_label: null, history: [] });
    api.updateInvestigation.mockResolvedValue({ analysis_id: "a", entity_id: "txn-1", status: "confirmed", comment: "Подтверждено документами", updated_at: 10, confirmed_label: { human_label: 1, source: "human_confirmed", profile: "transaction_anomaly", model_version: "1", risk_probability: .9, confirmed_at: 10 }, history: [{ event_id: 1, previous_status: "new", status: "confirmed", comment: "Подтверждено документами", occurred_at: 10 }] });
    render(<InvestigationPanel analysisId="a" entityId="txn-1" />);
    await screen.findByRole("button", { name: "Сохранить решение" });
    await user.selectOptions(screen.getByLabelText("Статус"), "confirmed");
    await user.type(screen.getByLabelText("Комментарий"), "Подтверждено документами");
    await user.click(screen.getByRole("button", { name: "Сохранить решение" }));
    await waitFor(() => expect(api.updateInvestigation).toHaveBeenCalledWith("a", "txn-1", { status: "confirmed", comment: "Подтверждено документами" }));
    expect(await screen.findByText(/метка сохранена обезличенно: мошенничество/)).toBeInTheDocument();
    expect(screen.getByText("Журнал изменений (1)")).toBeInTheDocument();
  });

  it("masks an identifier until the user temporarily reveals it", async () => {
    const user = userEvent.setup();
    render(<SensitiveValue value="CLIENT-123456" label="ID клиента" />);
    expect(screen.queryByText("CLIENT-123456")).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Показать ID клиента на 30 секунд" }));
    expect(screen.getByText("CLIENT-123456")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Скрыть" }));
    expect(screen.queryByText("CLIENT-123456")).not.toBeInTheDocument();
  });
});
