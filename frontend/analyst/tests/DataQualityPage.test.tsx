import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { DataQualityPage } from "../src/routes/DataQualityPage";

afterEach(cleanup);

describe("DataQualityPage", () => {
  it("groups acceptable deviations and explains their impact in Russian", () => {
    const { container } = render(
      <DataQualityPage
        targetPresent
        targetValid={false}
        warnings={[
          "Extra columns will be preserved but ignored by the model: CNT_3D, NUM_CONTRACTS_OTHER",
          "Unknown categories in NEGATIVESTATUS: 116",
          "Optional features will be imputed: AGE, DTI3M",
          "Missing rate drift for GENDER: 20.0% versus 2.0% in training.",
          "Target will be ignored for metrics: target must contain only 0 and 1",
        ]}
      />,
    );

    expect(screen.getByText("Есть отклонения")).toBeInTheDocument();
    expect(screen.getByText("Новые значения в поле «Негативный статус»")).toBeInTheDocument();
    expect(screen.getByText(/сохранены в итоговом CSV, но не участвуют/)).toBeInTheDocument();
    expect(screen.getByText(/отсутствует 20.0% значений/)).toBeInTheDocument();
    expect(screen.getByText("Некорректен")).toBeInTheDocument();
    expect(screen.getByText(/Анализ завершён/)).toBeInTheDocument();
    expect(container).not.toHaveTextContent("Unknown categories in");
    expect(container).not.toHaveTextContent("Extra columns will be");

    const details = screen.getAllByText("Показать коды и значения");
    fireEvent.click(details[0]);
    expect(screen.getByText("CNT_3D")).toBeInTheDocument();
    expect(screen.getByText("NUM_CONTRACTS_OTHER")).toBeInTheDocument();
  });

  it("shows a calm successful state when no deviations were reported", () => {
    render(<DataQualityPage targetPresent={false} targetValid={false} warnings={[]} />);

    expect(screen.getByText("Отклонений не обнаружено")).toBeInTheDocument();
    expect(screen.getByText("Структура принята")).toBeInTheDocument();
    const summary = screen.getByLabelText("Сводка качества данных");
    expect(within(summary).getByText("Не передан")).toBeInTheDocument();
    expect(within(summary).getByText("Это не мешает оценке риска")).toBeInTheDocument();
  });
});
