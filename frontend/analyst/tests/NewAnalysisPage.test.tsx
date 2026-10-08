import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { NewAnalysisPage } from "../src/routes/NewAnalysisPage";

const baseProps = {
  error: null,
  hasActiveAnalysis: false,
  processing: false,
  status: null,
  inventory: null,
  plan: null,
  cancelling: false,
  starting: false,
  onCancel: vi.fn(),
  onClearError: vi.fn(),
  onRun: vi.fn(),
  onStart: vi.fn(),
};

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("new analysis page", () => {
  it("accepts every local source extension supported by the backend", () => {
    render(<NewAnalysisPage {...baseProps} />);
    const input = screen.getByLabelText("Выберите файл с данными");
    for (const extension of ["csv", "json", "jsonl", "ndjson", "sql", "sqlite", "sqlite3", "db", "bson"]) {
      fireEvent.change(input, {
        target: { files: [new File(["fixture"], `source.${extension}`)] },
      });
      expect(screen.queryByRole("alert")).not.toBeInTheDocument();
      expect(screen.getByRole("button", { name: "Проверить источник" })).toBeEnabled();
    }
  });

  it("rejects unsupported, empty and oversized files before API upload", () => {
    render(<NewAnalysisPage {...baseProps} />);
    const input = screen.getByLabelText("Выберите файл с данными");

    fireEvent.change(input, {
      target: { files: [new File(["x"], "clients.xlsx")] },
    });
    expect(screen.getByRole("alert")).toHaveTextContent("CSV, JSON, SQL, SQLite или BSON");

    fireEvent.change(input, {
      target: { files: [new File([], "empty.csv")] },
    });
    expect(screen.getByRole("alert")).toHaveTextContent("Файл пуст");

    const oversized = new File(["x"], "large.csv", { type: "text/csv" });
    Object.defineProperty(oversized, "size", { value: 501 * 1024 * 1024 });
    fireEvent.change(input, { target: { files: [oversized] } });
    expect(screen.getByRole("alert")).toHaveTextContent("500 МБ");
    expect(screen.getByRole("button", { name: "Проверить источник" })).toBeDisabled();
  });

  it("requires confirmation before replacing an active analysis", async () => {
    const user = userEvent.setup();
    const onStart = vi.fn();
    render(
      <NewAnalysisPage
        {...baseProps}
        hasActiveAnalysis
        onStart={onStart}
      />,
    );
    const file = new File(["signal;GB_flag\n9;1"], "clients.csv", {
      type: "text/csv",
    });
    await user.upload(screen.getByLabelText("Выберите файл с данными"), file);
    await user.click(screen.getByRole("button", { name: "Проверить источник" }));
    expect(screen.getByRole("dialog", { name: "Заменить текущий анализ?" })).toBeInTheDocument();
    expect(onStart).not.toHaveBeenCalled();

    await user.click(screen.getByRole("button", { name: "Отмена" }));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Проверить источник" }));
    await user.click(screen.getByRole("button", { name: "Удалить и продолжить" }));
    expect(onStart).toHaveBeenCalledWith(file);
  });
});
