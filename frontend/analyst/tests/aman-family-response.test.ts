import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { createContext, runInContext } from "node:vm";
import { afterEach, expect, it } from "vitest";

// Exercise the actual bank script: the server, rather than a count of UI votes,
// determines whether another relative may act after the first response.
const source = readFileSync(resolve(process.cwd(), "../aman/app.js"), "utf8");
function bankContext() {
  const element = () => ({ innerHTML: "", hidden: false, setAttribute() {}, addEventListener() {} });
  const context = createContext({
    Intl, Date, URL, AbortSignal,
    document: { getElementById: element, querySelector: element, querySelectorAll: () => [], addEventListener() {}, body: { classList: { toggle() {} } } },
    window: { addEventListener() {} },
    localStorage: { getItem: () => null, setItem() {} },
    setInterval: () => 0,
    fetch: () => new Promise(() => {}),
  });
  runInContext(source, context);
  runInContext("state.loading=false;state.signedIn=true;userData={id:3,name:'Марат'};", context);
  return context;
}

function bankUi() {
  const context = bankContext();
  return (request: object, relative = true) => {
    context.fixture = request;
    document.body.innerHTML = runInContext(`requestCard(fixture, ${relative})`, context);
    return document.body;
  };
}

const pending = {
  id: 1, status: "pending_approval", sender_name: "Амина", recipient_name: "Получатель", recipient: "+77000000000", amount: 100,
  created_at: "2026-11-04T00:00:00Z", expires_at: "2026-11-05T00:00:00Z", can_approve: true, can_cancel: true,
  participants: [{ name: "Марат", response: "pending" }, { name: "Сауле", response: "pending" }, { name: "Алихан", response: "pending" }],
};

afterEach(() => { document.body.innerHTML = ""; });

it("offers one relative a decision without asking for all three responses", () => {
  const body = bankUi()(pending);
  expect(body.textContent).toContain("Достаточно одного ответа");
  expect(body.textContent).toContain("Первый ответ доверенного родственника окончателен");
  expect(body.querySelectorAll("[data-request-action]")).toHaveLength(2);
  expect(body.textContent).not.toContain("единоглас");
});

it.each(["completed", "rejected", "bank_review"])("hides subsequent family actions after the first answer leads to %s", (status) => {
  const body = bankUi()({ ...pending, status, can_approve: false, participants: [{ name: "Марат", response: status === "rejected" ? "reject" : "approve" }, ...pending.participants.slice(1)] });
  expect(body.querySelector("[data-request-action]")).toBeNull();
  expect(body.textContent).toContain("Сауле");
  expect(body.textContent).toContain("Алихан");
  expect(body.textContent).toContain("Ответ не требуется");
});

it("does not offer a sender a family approval action", () => {
  const body = bankUi()(pending, false);
  expect(body.querySelector('[data-request-action="approve"]')).toBeNull();
  expect(body.querySelector('[data-request-action="reject"]')).toBeNull();
  expect(body.querySelector('[data-request-action="cancel"]')).not.toBeNull();
});

it("explains a safely cancelled legacy request and escapes the server note", () => {
  const body = bankUi()({ ...pending, status: "cancelled", can_approve: false, can_cancel: false, resolution_note: "Порядок согласования изменён. Создайте новый перевод. <img src=x onerror=alert(1)>" });
  expect(body.querySelector('[role="status"]')?.textContent).toContain("Создайте новый перевод.");
  expect(body.querySelector("img")).toBeNull();
  expect(body.querySelector("[data-request-action]")).toBeNull();
});

it("keeps the historical explanation for an existing bank review of conflicting votes", () => {
  const body = bankUi()({ ...pending, status: "bank_review", bank_review_reason: "conflicting_votes", can_approve: false, participants: [{ name: "Марат", response: "approve" }, { name: "Сауле", response: "reject" }] });
  expect(body.textContent).toContain("Этот запрос создан по прежним правилам");
  expect(body.textContent).not.toContain("Первый ответ доверенного родственника окончателен");
  expect(body.querySelector("[data-request-action]")).toBeNull();
});

const hybridRisk = {
  overall_level: "high", engine_mode: "hybrid_ml_rules",
  transaction_risk: { level: "high", factors: [{ code: "ml_review", source: "ml", label: "Нужна дополнительная проверка", description: "Модель выявила признаки, требующие проверки." }] },
  recipient_risk: { level: "low", factors: [] },
  ml: { status: "available", decision: "REVIEW", out_of_distribution: true, currency: "KZT", model_currency: "KZT", unit_version: "kzt-native-v1", model_id: "private-model-id", score: 0.7345123, features: { secret_feature: 123 }, path: "C:/private/model.txt" },
};

it("explains hybrid scoring to a customer without exposing the analyst-only score or internal fields", () => {
  const body = bankUi()({ ...pending, risk: hybridRisk });
  expect(body.textContent).toContain("Проверка безопасности: ML-модель и защитные правила");
  expect(body.textContent).toContain("Модель или защитные правила обнаружили признаки риска");
  expect(body.textContent).toContain("Данные операции отличаются от данных обучения");
  expect(body.textContent).toContain("Нужна дополнительная проверка");
  expect(body.textContent).toContain("Валюта расчёта: KZT");
  expect(body.textContent).toContain("не обучалась на казахстанских операциях");
  expect(body.textContent).not.toMatch(/EUR|курс/i);
  for (const privateValue of ["0.7345123", "private-model-id", "secret_feature", "C:/private/model.txt", "ml_review"]) {
    expect(body.textContent).not.toContain(privateValue);
  }
});

it.each([
  ["unavailable", "ML-модель временно недоступна. Проверка выполнена по правилам."],
  ["disabled", "Проверка безопасности: правила. ML-модель отключена."],
])("explains %s ML mode without claiming the model participated", (status, explanation) => {
  const body = bankUi()({ ...pending, risk: { ...hybridRisk, engine_mode: "rule_based_fallback", ml: { status, out_of_distribution: false } } });
  expect(body.textContent).toContain(explanation);
  expect(body.textContent).not.toContain("ML-модель и защитные правила");
  expect(body.textContent).not.toContain("Модель или защитные правила обнаружили признаки риска");
});

it("keeps the rules explanation for requests saved before ML was added", () => {
  const body = bankUi()({ ...pending, risk: { overall_level: "low", engine_mode: "rule_based_fallback" } });
  expect(body.textContent).toContain("Проверка безопасности: правила.");
  expect(body.textContent).not.toContain("ML-модель и защитные правила");
});

it("does not describe a historical model assessment as KZT-native when unit metadata was not saved", () => {
  const body = bankUi()({ ...pending, risk: { ...hybridRisk, ml: { status: "available", decision: "REVIEW", out_of_distribution: false } } });
  expect(body.textContent).toContain("Проверка безопасности: ML-модель и защитные правила");
  expect(body.textContent).not.toContain("Валюта расчёта: KZT");
  expect(body.textContent).not.toContain("адаптирована к суммам в тенге");
});

function demoTransfer(cards: object[], testIin = "TEST0006") {
  const context = bankContext();
  context.demoCards = cards;
  context.demoIin = testIin;
  runInContext("userData={id:6,name:'Амина',test_iin:demoIin};cardsData=demoCards;state.transferType='phone';", context);
  return {
    context,
    render() {
      document.body.innerHTML = runInContext("transfer()", context);
      return document.body.querySelector("[data-transfer-demo]");
    },
  };
}

it("shows Amina's reference contacts only for her phone transfer without changing the form", () => {
  const ui = demoTransfer([{ balance: 1200000, currency: "KZT", status: "active" }]);
  runInContext("state.phone='+77000000000';state.amount='25';state.antiScam={pressure:null,secrecy:false,stranger:null};", ui.context);
  const before = runInContext("JSON.stringify([state.phone,state.amount,state.antiScam])", ui.context);
  const panel = ui.render();
  expect(panel).toHaveTextContent("+77020000044");
  expect(panel).toHaveTextContent("+77010000043");
  expect(panel).toHaveTextContent("+77040000066");
  expect(panel?.querySelector("input,button,select,a")).toBeNull();
  expect(runInContext("JSON.stringify([state.phone,state.amount,state.antiScam])", ui.context)).toBe(before);
  runInContext("state.transferType='card'", ui.context);
  expect(ui.render()).toBeNull();
  for (const testIin of ["TEST0003", "TEST0099", ""]) {
    expect(demoTransfer([{ balance: 1200000, currency: "KZT", status: "active" }], testIin).render()).toBeNull();
  }
});

it("uses the first active KZT balance and offers the family demo only when funds cover it", () => {
  const cards = [{ balance: 2000000, currency: "KZT", status: "blocked" }, { balance: 5000000, currency: "USD", status: "active" }, { balance: 100.01, currency: "KZT", status: "active" }];
  const ui = demoTransfer(cards);
  expect(ui.render()).toHaveTextContent("Для примера нужно 1 100 000 ₸ на счёте");
  expect(ui.render()).toHaveTextContent("100,01 ₸");
  cards[2].balance = 1099999.99;
  expect(ui.render()).toHaveTextContent("Для примера нужно 1 100 000 ₸ на счёте");
  cards[2].balance = 1100000;
  expect(ui.render()?.querySelector("li b")).toHaveTextContent(/^1 100 000 ₸$/);
  expect(ui.render()).toHaveTextContent("1 000 ₸");
  cards[2].balance = 0.01;
  expect(ui.render()).toHaveTextContent("0,01 ₸");
  expect(ui.render()).not.toHaveTextContent("0,02 ₸");
});

it("keeps contacts available as references without suggesting an invalid amount when funds are unavailable", () => {
  for (const cards of [[], [{ balance: 1000, currency: "USD", status: "active" }], ...[0, -1, Number.NaN].map(balance => [{ balance, currency: "KZT", status: "active" }])]) {
    const panel = demoTransfer(cards).render();
    expect(panel).toHaveTextContent("+77020000044");
    expect(panel).toHaveTextContent(/нет доступных средств/i);
    expect(panel?.textContent).not.toMatch(/NaN|(?:^|\s)0 ₸|1 000 ₸/);
  }
});
