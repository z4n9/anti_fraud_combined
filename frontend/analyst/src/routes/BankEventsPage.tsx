import { useEffect, useRef, useState } from "react";
import { ApiClientError, decideBankEvent, getBankEvent, getBankEvents } from "../api/client";
import type { BankEvent, BankEventPage } from "../types/bankEvents";

const statusLabels: Record<string, string> = { pending_approval: "Ожидает родственников", bank_review: "Проверка банка", completed: "Исполнен", rejected: "Отклонён", cancelled: "Отменён", expired: "Истёк", blocked_no_trusted: "Нет доверенного лица" };
const vote = (response: string) => ({ pending: "Нет ответа", approve: "Одобрен", reject: "Отклонён" })[response] ?? response;
const date = (value: string) => new Date(value).toLocaleString("ru-RU");
const failure = (reason: unknown) => reason instanceof Error ? reason.message : "Не удалось получить данные банка.";

export function BankEventsPage() {
  const [status, setStatus] = useState("bank_review");
  const [sender, setSender] = useState("");
  const [search, setSearch] = useState("");
  const [page, setPage] = useState(1);
  const [refresh, setRefresh] = useState(0);
  const [events, setEvents] = useState<BankEventPage | null>(null);
  const [selected, setSelected] = useState<BankEvent | null>(null);
  const [note, setNote] = useState("");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [loading, setLoading] = useState(false);
  const [busy, setBusy] = useState(false);
  const mounted = useRef(true);
  const lock = useRef(false);
  const detailGeneration = useRef(0);

  useEffect(() => { mounted.current = true; return () => { mounted.current = false; detailGeneration.current += 1; }; }, []);
  useEffect(() => {
    let active = true;
    setLoading(true); setEvents(null); setError("");
    void getBankEvents({ page, status, senderName: sender }).then((value) => { if (active) setEvents(value); }).catch((reason) => { if (active) setError(failure(reason)); }).finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [page, status, sender, refresh]);

  async function open(id: number) {
    if (lock.current) return;
    const generation = ++detailGeneration.current;
    setSelected(null); setNote(""); setNotice(""); setError(""); setBusy(true);
    try { const result = await getBankEvent(id); if (mounted.current && generation === detailGeneration.current) setSelected(result); }
    catch (reason) { if (mounted.current && generation === detailGeneration.current) setError(failure(reason)); }
    finally { if (mounted.current && generation === detailGeneration.current) setBusy(false); }
  }
  async function decide(action: "approve" | "reject") {
    if (lock.current || !selected || selected.status !== "bank_review") return;
    const comment = note.trim();
    if (comment.length < 5 || comment.length > 500) { setError("Укажите основание решения: от 5 до 500 символов."); return; }
    lock.current = true; setBusy(true); setError(""); setNotice("");
    const id = selected.id;
    try {
      const updated = await decideBankEvent(id, action, comment);
      if (!mounted.current) return;
      setSelected(updated); setNote("");
      setNotice(updated.status === "expired"
        ? `Запрос № ${id}: срок истёк; перевод не исполнен.`
        : `Запрос № ${id}: ${statusLabels[updated.status] ?? updated.status}.${updated.bank_decisions?.length || ["completed", "rejected"].includes(updated.status) ? " Решение записано в журнал." : ""}`);
      setRefresh((value) => value + 1);
    } catch (reason) {
      if (!mounted.current) return;
      setError(failure(reason));
      // A competing decision or a lost response must not leave actionable stale data.
      setSelected(null);
      if (reason instanceof ApiClientError && (reason.sessionEnded || reason.code === "account_changed")) return;
      try { const updated = await getBankEvent(id); if (mounted.current) setSelected(updated); } catch { /* Visible error remains; the list can be refreshed. */ }
    } finally { lock.current = false; if (mounted.current) setBusy(false); }
  }

  return <div className="section-page bank-events-page"><header className="section-page__intro"><h1>Банковские события</h1><p>Живые запросы банка. Загрузка файла и наличие ML-модели не требуются.</p></header><form className="bank-event-filters" onSubmit={(event) => { event.preventDefault(); setSender(search.trim()); setPage(1); setRefresh((value) => value + 1); }}><label>Статус<select value={status} disabled={busy} onChange={(event) => { setStatus(event.target.value); setPage(1); }}><option value="">Все статусы</option>{Object.entries(statusLabels).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label><label>Отправитель<input value={search} maxLength={100} disabled={busy} onChange={(event) => setSearch(event.target.value)} /></label><button className="ui-button ui-button--secondary" disabled={busy || loading}>Обновить события</button></form>{error && <p className="error-panel" role="alert">{error}</p>}{notice && <p role="status">{notice}</p>}{loading && <p role="status">Загружаем события…</p>}{events && <><p>Найдено: {events.total}</p><div className="transaction-table-wrap"><table className="transaction-table"><caption className="sr-only">Запросы банковских переводов</caption><thead><tr><th>Запрос</th><th>Отправитель</th><th>Получатель</th><th>Сумма</th><th>Статус</th><th>Действие</th></tr></thead><tbody>{events.items.map((item) => <tr key={item.id}><td>№ {item.id}</td><td>{item.sender_name}</td><td>{item.recipient_name}</td><td>{item.amount.toLocaleString("ru-RU")} ₸</td><td>{statusLabels[item.status]}</td><td><button disabled={busy} onClick={() => void open(item.id)}>Открыть запрос {item.id}</button></td></tr>)}</tbody></table></div>{!events.items.length && <p>Событий по этому фильтру нет.</p>}<div className="bank-event-pagination"><button disabled={busy || loading || page <= 1} onClick={() => setPage((value) => value - 1)}>Предыдущая страница</button><span>Страница {page}</span><button disabled={busy || loading || page * events.page_size >= events.total} onClick={() => setPage((value) => value + 1)}>Следующая страница</button></div></>}{selected && <section className="panel bank-event-detail"><h2>Запрос № {selected.id}</h2><dl><dt>Отправитель</dt><dd>{selected.sender_name}</dd><dt>Получатель</dt><dd>{selected.recipient_name} · {selected.recipient}</dd><dt>Сумма</dt><dd>{selected.amount.toLocaleString("ru-RU")} ₸</dd><dt>Статус</dt><dd>{statusLabels[selected.status]}</dd><dt>Создан</dt><dd>{date(selected.created_at)}</dd><dt>Срок</dt><dd>{date(selected.expires_at)}</dd><dt>Сообщение</dt><dd>{selected.message || "Не указано"}</dd></dl><h3>Решения родственников</h3><ul>{selected.participants?.map((person) => <li key={person.invitation_id}>{person.name}: {vote(person.response)}</li>)}</ul><h3>Проверка AntiScam</h3><ul>{([['pressure', 'Давление или срочность'], ['secrecy', 'Просьба сохранить перевод в тайне'], ['stranger', 'Указания незнакомого человека']] as const).map(([key, label]) => <li key={key}>{label}: {selected.anti_scam?.[key] === true ? "Да" : selected.anti_scam?.[key] === false ? "Нет" : "Неизвестно"}</li>)}</ul><h3>Причины риска</h3><ul>{[...(selected.risk?.transaction_risk?.factors ?? []), ...(selected.risk?.recipient_risk?.factors ?? [])].map((factor, index) => <li key={index}><strong>{factor.label}</strong>: {factor.description}</li>)}</ul><p>До исполнения деньги не списываются. Сотрудник не может обойти незавершённое голосование родственников.</p>{selected.status === "bank_review" ? <><label>Основание решения<textarea value={note} disabled={busy} minLength={5} maxLength={500} onChange={(event) => setNote(event.target.value)} /></label><button className="ui-button ui-button--primary" disabled={busy || note.trim().length < 5} onClick={() => void decide("approve")}>Разрешить перевод</button><button className="ui-button ui-button--secondary" disabled={busy || note.trim().length < 5} onClick={() => void decide("reject")}>Отклонить перевод</button></> : <p>Для этого статуса решение сотрудника недоступно.</p>}<h3>Журнал решений банка</h3>{selected.bank_decisions?.length ? <ul>{selected.bank_decisions.map((entry, index) => <li key={index}>{date(entry.created_at)} · {entry.actor_name} · {entry.action === "approve" ? "Одобрение" : "Отклонение"}<p>{entry.note}</p></li>)}</ul> : <p>Решений банка пока нет.</p>}</section>}</div>;
}
