import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { DashboardShell } from "../src/layout/DashboardShell";

describe("dashboard shell", () => {
  beforeEach(() => window.localStorage.clear());
  afterEach(cleanup);

  it("locks analytical routes until an analysis is ready", async () => {
    const user = userEvent.setup();
    const navigate = vi.fn();
    render(
      <DashboardShell
        activeRoute="new-analysis"
        phase="empty"
        onNavigate={navigate}
      >
        <p>Загрузка</p>
      </DashboardShell>,
    );

    const overview = screen.getByRole("link", { name: /Обзор/ });
    expect(overview).toHaveAttribute("aria-disabled", "true");
    await user.click(overview);
    expect(navigate).not.toHaveBeenCalled();
    expect(screen.getByText("Загрузка")).toBeInTheDocument();
  });

  it("navigates between ready routes and switches display modes", async () => {
    const user = userEvent.setup();
    const navigate = vi.fn();
    const { container } = render(
      <DashboardShell activeRoute="overview" phase="ready" onNavigate={navigate}>
        <p>Обзор готов</p>
      </DashboardShell>,
    );

    await user.click(screen.getByRole("link", { name: "Клиенты" }));
    expect(navigate).toHaveBeenCalledWith("risk-records");

    await user.click(screen.getByRole("button", { name: "Настройки отображения" }));
    await user.click(screen.getByRole("button", { name: "Компактная" }));
    await user.click(screen.getByRole("button", { name: "Экспертные" }));
    await user.click(screen.getByRole("checkbox", { name: "Повышенный контраст" }));

    const root = container.querySelector(".dashboard-root");
    expect(root).toHaveClass("density-compact", "mode-expert", "contrast-high");
    expect(window.localStorage.getItem("risk-ledger-display-preferences:0")).toContain(
      '"density":"compact"',
    );
  });

  it("shows current-session notifications with expandable technical details", async () => {
    const user = userEvent.setup();
    render(
      <DashboardShell
        activeRoute="overview"
        notifications={[{
          id: "unknown-gender",
          level: "warning",
          title: "Новые значения в поле «Пол»",
          description: "Модель обработала значения как неизвестные.",
          technicalCode: "GENDER",
          items: ["999"],
        }]}
        phase="ready"
        onNavigate={vi.fn()}
      >
        <p>Результат</p>
      </DashboardShell>,
    );

    await user.click(screen.getByLabelText("Уведомления текущей сессии: 1"));
    expect(screen.getByText("Новые значения в поле «Пол»")).toBeInTheDocument();
    await user.click(screen.getByText("Технические детали"));
    expect(screen.getByText("GENDER")).toBeInTheDocument();
    expect(screen.getByText("999")).toBeInTheDocument();
  });
});
