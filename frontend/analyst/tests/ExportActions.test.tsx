import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ExportActions } from "../src/components/ExportActions";

const api = vi.hoisted(() => ({ downloadAnalysisReport: vi.fn(), downloadUniversalExport: vi.fn() }));

vi.mock("../src/api/client", () => api);

describe("ExportActions", () => {
  beforeEach(() => {
    api.downloadAnalysisReport.mockReset();
    api.downloadAnalysisReport.mockResolvedValue({
      blob: new Blob(["record_id,risk_probability\nrow-1,0.9"]),
      filename: "analysis-test.csv",
    });
    api.downloadUniversalExport.mockResolvedValue({ blob: new Blob(["data"]), filename: "transactions.csv" });
    vi.stubGlobal("URL", {
      ...URL,
      createObjectURL: vi.fn(() => "blob:report"),
      revokeObjectURL: vi.fn(),
    });
    vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => undefined);
  });

  it("keeps identifiers masked by default and requires confirmation to reveal them", async () => {
    const user = userEvent.setup();
    render(<ExportActions analysisId="analysis-test" reviewCount={2} threshold={0.5} />);
    await user.click(screen.getByText("Другие варианты выгрузки"));
    await user.selectOptions(screen.getByLabelText("Состав данных"), "relationships");
    await user.click(screen.getByRole("button", { name: "Скачать выбранную выгрузку" }));
    await waitFor(() => expect(api.downloadUniversalExport).toHaveBeenCalledWith("analysis-test", "relationships", true));
    await user.click(screen.getByRole("checkbox", { name: "Включить исходные идентификаторы" }));
    expect(screen.getByRole("dialog", { name: "Раскрыть идентификаторы?" })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Раскрыть для выгрузки" }));
    expect(screen.getByText(/файл будет содержать исходные идентификаторы/)).toBeInTheDocument();
  });

  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it("downloads full and review-only CSV with the current threshold", async () => {
    const user = userEvent.setup();
    render(<ExportActions analysisId="analysis-test" reviewCount={12} threshold={0.73} />);

    await user.click(screen.getByRole("button", { name: "Скачать CSV" }));
    await waitFor(() => expect(api.downloadAnalysisReport).toHaveBeenCalledWith("analysis-test", 0.73, false));
    await user.click(screen.getByRole("button", { name: "Только ручная проверка (12)" }));
    await waitFor(() => expect(api.downloadAnalysisReport).toHaveBeenCalledWith("analysis-test", 0.73, true));
    expect(screen.getByText(/учитывает порог 73%/)).toBeInTheDocument();
  });

  it("shows a local error without removing export actions", async () => {
    const user = userEvent.setup();
    api.downloadAnalysisReport.mockRejectedValueOnce(new Error("Не удалось подготовить CSV."));
    render(<ExportActions analysisId="analysis-test" reviewCount={2} threshold={0.5} />);

    await user.click(screen.getByRole("button", { name: "Скачать CSV" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Не удалось подготовить CSV");
    expect(screen.getByRole("button", { name: "Только ручная проверка (2)" })).toBeEnabled();
  });
});
