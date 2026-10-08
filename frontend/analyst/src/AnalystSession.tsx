import { useEffect, useRef, useState } from "react";
import type { FormEvent } from "react";
import App from "./App";
import { setAnalystIdentity } from "./api/client";

interface Analyst { id: number; name: string; role: string }

async function authRequest(path: string, options?: RequestInit) {
  const response = await fetch(path, { ...options, credentials: "same-origin" });
  const data = await response.json();
  if (!response.ok) throw new Error(response.status === 403
    ? "Этот аккаунт не имеет доступа к кабинету аналитика. Войдите под аккаунтом сотрудника."
    : response.status === 409 ? "Аккаунт изменился в другом окне. Войдите снова."
    : response.status === 401 ? "Войдите в аккаунт аналитика."
    : typeof data.detail === "string" ? data.detail : "Не удалось выполнить запрос. Попробуйте ещё раз.");
  return data;
}

export function AnalystSession() {
  const [analyst, setAnalyst] = useState<Analyst | null>(null);
  const [busy, setBusy] = useState(true);
  const [error, setError] = useState("");
  const generation = useRef(0);
  const identity = useRef<Analyst | null>(null);
  const locked = useRef(false);

  useEffect(() => {
    let active = true;
    void authRequest("/api/analyst/me").then((user: Analyst) => {
      if (active) { identity.current = user; setAnalystIdentity(user.id); setAnalyst(user); }
    }).catch((reason: unknown) => {
      if (active) setError(reason instanceof Error ? reason.message : "Сервис недоступен.");
    }).finally(() => { if (active) setBusy(false); });
    const ended = (event: Event) => {
      generation.current += 1;
      if (identity.current) localStorage.removeItem(`risk-ledger-active-analysis:${identity.current.id}`);
      identity.current = null; setAnalystIdentity(null); setAnalyst(null);
      setError((event as CustomEvent).detail === 409 ? "Аккаунт изменился в другом окне. Войдите снова." : (event as CustomEvent).detail === 403 ? "Доступ к кабинету аналитика закрыт." : "Сессия завершилась. Войдите снова.");
    };
    window.addEventListener("analyst-session-ended", ended);
    const verify = () => {
      const expected = identity.current;
      if (!expected || locked.current) return;
      void fetch("/api/analyst/me", { credentials: "same-origin", headers: { "X-Account-ID": String(expected.id) } }).then((response) => {
        if (active && identity.current?.id === expected.id && [401, 403, 409].includes(response.status)) ended(new CustomEvent("analyst-session-ended", { detail: response.status }));
      }).catch(() => { /* The next API request still enforces the expected identity. */ });
    };
    window.addEventListener("focus", verify);
    return () => { active = false; generation.current += 1; window.removeEventListener("analyst-session-ended", ended); window.removeEventListener("focus", verify); };
  }, []);

  async function login(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (locked.current) return;
    locked.current = true; setBusy(true); setError("");
    const form = event.currentTarget;
    const fields = new FormData(form);
    const current = ++generation.current;
    try {
      const loggedIn = await authRequest("/api/auth/login", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ test_iin: String(fields.get("test_iin")).trim().toUpperCase(), password: fields.get("password") }) }) as { id: number };
      const user = await authRequest("/api/analyst/me", { headers: { "X-Account-ID": String(loggedIn.id) } }) as Analyst;
      if (user.id !== loggedIn.id) throw new Error("Аккаунт изменился во время входа. Попробуйте снова.");
      if (current === generation.current) { identity.current = user; setAnalystIdentity(user.id); setAnalyst(user); form.reset(); }
    } catch (reason) { if (current === generation.current) setError(reason instanceof Error ? reason.message : "Сервис недоступен."); }
    finally { locked.current = false; setBusy(false); }
  }

  async function logout() {
    if (locked.current) return;
    locked.current = true; setBusy(true); setError(""); generation.current += 1;
    const expected = identity.current;
    if (expected) localStorage.removeItem(`risk-ledger-active-analysis:${expected.id}`);
    identity.current = null; setAnalystIdentity(null); setAnalyst(null);
    try {
      // Do not revoke a different account's shared cookie after another tab changed it.
      if (expected) {
        const currentUser = await authRequest("/api/analyst/me", { headers: { "X-Account-ID": String(expected.id) } }) as Analyst;
        if (currentUser.id !== expected.id) throw new Error("Аккаунт изменился в другом окне. Серверная сессия другого аккаунта сохранена.");
      }
      await authRequest("/api/auth/logout", { method: "POST" });
    }
    catch (reason) { setError(reason instanceof Error ? reason.message : "Не удалось завершить серверную сессию. Повторите выход."); }
    finally { locked.current = false; setBusy(false); }
  }

  if (analyst) return <><div className="analyst-session-bar"><span>Аналитик: {analyst.name}</span><a href="/">AMAN Bank</a><button className="ui-button ui-button--secondary" disabled={busy} onClick={() => void logout()}>Выйти</button></div><App key={analyst.id} analystId={analyst.id} /></>;
  return <main className="analyst-login"><p className="eyebrow">Risk Ledger · AMAN Bank</p><h1>Вход для аналитика</h1><p>Доступ к источникам и результатам предоставляется сотрудникам банка.</p>{error && <p role="alert">{error}</p>}<form onSubmit={(event) => void login(event)}><label>Тестовый ИИН<input name="test_iin" required pattern="TEST[0-9]{4}" maxLength={8} autoComplete="username" placeholder="TEST0099" disabled={busy} /></label><label>Пароль<input name="password" type="password" required maxLength={128} autoComplete="current-password" disabled={busy} /></label><button className="ui-button ui-button--primary" type="submit" disabled={busy}>{busy ? "Проверяем доступ…" : "Войти"}</button></form><a href="/">Открыть AMAN Bank</a><button className="ui-button ui-button--secondary" disabled={busy} onClick={() => void logout()}>Выйти из текущего аккаунта</button><p>Используйте только вымышленные аккаунты из README.</p></main>;
}
