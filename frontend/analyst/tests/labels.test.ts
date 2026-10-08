import { describe, expect, it } from "vitest";
import {
  formatDataWarning,
  formatFeatureValue,
  getFeaturePresentation,
} from "../src/utils/labels";

describe("front-end labels", () => {
  it("translates known and monthly model features", () => {
    expect(getFeaturePresentation("EMPLOYMENTNATURE").label).toBe("Вид деятельности");
    expect(getFeaturePresentation("MONTH_OVERDUE_C19").label).toBe(
      "Макс. число просрочек · 20 мес. назад",
    );
    expect(getFeaturePresentation("MONTH_OVERDUE_A1").label).toBe(
      "Макс. просрочка · 2 мес. назад",
    );
  });

  it("keeps an unknown technical name and formats values", () => {
    expect(getFeaturePresentation("NEW_SIGNAL").label).toBe("NEW_SIGNAL");
    expect(formatFeatureValue(null)).toBe("нет данных");
    expect(formatFeatureValue(12.3456)).toBe("12,346");
  });

  it("adds business units and identifies source category codes", () => {
    expect(formatFeatureValue("28", "EMPLOYMENTNATURE")).toBe("Код 28");
    expect(formatFeatureValue(0, "CNT_6M")).toBe("0 запросов за последние 6 мес.");
    expect(formatFeatureValue(12_987_274, "MONTH_OVERDUE_A1")).toBe("Максимальная сумма просрочки: 12 987 274 ₸");
    expect(formatFeatureValue(2, "MONTH_OVERDUE_C19")).toBe("Максимум: 2 просрочки");
    expect(formatFeatureValue(2, "NUM_CONTRACT_BVU")).toBe("2 договора с БВУ");
    expect(formatFeatureValue(2, "NUM_PHONENUMBERS")).toBe("2 телефонных номера");
    expect(formatFeatureValue(1_917, "term")).toBe("Срок договора: 1 917 дней");
  });

  it("translates schema warnings without losing codes", () => {
    expect(formatDataWarning("Unknown categories in NEGATIVESTATUS: 116")).toBe(
      "Новые значения в поле «Негативный статус» (NEGATIVESTATUS): 116",
    );
    expect(
      formatDataWarning(
        "Extra columns will be preserved but ignored by the model: CNT_3D, NUM_CONTRACTS_OTHER",
      ),
    ).toContain("CNT_3D, NUM_CONTRACTS_OTHER");
  });
});
