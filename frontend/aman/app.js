'use strict';
// Local educational API; all balances and recipients are test data.
const paths = {
 home:'M3 10 12 3l9 7M5 9v11h5v-6h4v6h5V9', arrows:'M4 7h15l-4-4m4 4-4 4M20 17H5l4-4m-4 4 4 4',
 card:'M3 5h18v14H3zM3 10h18M7 15h4', plus:'M12 5v14M5 12h14', qr:'M3 3h6v6H3zM15 3h6v6h-6zM3 15h6v6H3zM15 15h2v2h-2zM21 14v4h-3v3M14 20v1',
 shield:'M12 3 4 6v6c0 5 8 9 8 9s8-4 8-9V6zM8 12l3 3 5-6', history:'M3 11a9 9 0 1 1 2 7M3 4v7h7M12 7v5l3 2', user:'M16 7a4 4 0 1 1-8 0 4 4 0 0 1 8 0M4 21v-2a8 8 0 0 1 16 0v2',
 settings:'M12 8a4 4 0 1 0 0 8 4 4 0 0 0 0-8M9 3h6l1 3 3 1 2 5-2 5-3 1-1 3H9l-1-3-3-1-2-5 2-5 3-1z', chevron:'m9 5 7 7-7 7', back:'m15 5-7 7 7 7', close:'m6 6 12 12M6 18 18 6', check:'m5 12 4 4L19 6',
 sun:'M16 12a4 4 0 1 1-8 0 4 4 0 0 1 8 0M12 2v2M12 20v2M2 12h2M20 12h2M5 5l1 1M18 18l1 1M5 19l1-1M18 6l1-1', moon:'M20 15A9 9 0 0 1 9 4a9 9 0 1 0 11 11', bell:'M5 17h14l-2-4V9a5 5 0 0 0-10 0v4zM10 21h4M12 2v2',
 phone:'M7 2h10v20H7zM11 18h2', globe:'M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0M3 12h18M12 3c-5 5-5 13 0 18 5-5 5-13 0-18', building:'M4 21V7l8-4 8 4v14M2 21h20M8 9v2M12 9v2M16 9v2M8 14v2M12 14v2M16 14v2',
 book:'M12 5C9 3 5 3 3 4v15c3-1 6-1 9 1 3-2 6-2 9-1V4c-2-1-6-1-9 1v15', bus:'M5 17V4h14v13zM5 10h14M8 14h1M15 14h1M7 17v3M17 17v3', game:'M7 7h10c4 0 6 12 2 12l-4-3H9l-4 3C1 19 3 7 7 7M7 10v5M5 12h4M16 11h.1M18 14h.1',
 grid:'M3 3h7v7H3zM14 3h7v7h-7zM3 14h7v7H3zM14 14h7v7h-7z', face:'M8 3H3v5M16 3h5v5M3 16v5h5M21 16v5h-5M8 8v2M16 8v2M12 9v5h-2M8 16q4 4 8 0', lock:'M5 10h14v11H5zM8 10V6a4 4 0 0 1 8 0v4M12 14v3', receipt:'M6 3h12v18l-3-2-3 2-3-2-3 2zM9 8h6M9 12h6', exit:'M9 3H4v18h5M10 12h11l-4-4m4 4-4 4', wallet:'M3 5h17v15H3zM3 5V3h14v2M15 10h6v5h-6z', gift:'M3 8h18v4H3zM5 12v9h14v-9M12 8v13M12 8C2 8 7-2 12 8c5-10 10 0 0 0'
};
const icon = (name, cls='') => `<svg class="${cls}" viewBox="0 0 24 24" aria-hidden="true"><path d="${paths[name] || paths.grid}"/></svg>`;
const money = n => new Intl.NumberFormat('ru-RU').format(n) + ' ₸';
const escapeText = value => String(value).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const state = {page:'login',signedIn:false,trail:[],amount:'',phone:'',message:'',transferType:'phone',filter:'all',search:'',dark:false};
try { state.dark = localStorage.getItem('aman-theme') === 'dark'; } catch {}
const main = document.getElementById('main');
const modal = document.getElementById('modal');
const navItems = [['home','home','Главная'],['transfer','arrows','Переводы'],['payments','wallet','Платежи'],['history','history','История'],['profile','user','Профиль']];
let transactions = [];
let userData = null;
let cardsData = [];
let trustedData = null;
let currentInvitation = null;
let incomingInvitations = [];
let ownRequests = [];
let approvalRequests = [];
let protectionChanges = [];
let ownInvitations = [];
state.antiScam={pressure:null,secrecy:null,stranger:null};
const familyReady = () => Boolean(trustedData?.protection_active && trustedData?.relationship_verified && trustedData?.invitation_status === 'accepted');
const invitationLabel = status => ({pending:'Ожидает подтверждения',accepted:'Приглашение принято',rejected:'Приглашение отклонено'})[status] || 'Не отправлено';
Object.assign(state, {loading:false, loaded:false, sending:false, error:'', transferError:'', requestKey:null, completed:null, familyBusy:false, familyError:'', candidate:null, authEpoch:0});
Object.assign(state, {requestBusy:false, requestError:'', requestNotice:'', transferOutcome:null});
state.protectionNotice='';
const currentCard = () => cardsData[0];
const cardLabel = () => escapeText(currentCard().card_name + ' · ' + currentCard().last_four);
const initials = name => escapeText(name.trim().split(/\s+/).slice(0,2).map(part=>part[0]).join(''));
function normalizeTransaction(t) {
 const date = new Date(t.created_at);
 const today = new Date(); const yesterday = new Date(); yesterday.setDate(today.getDate()-1);
 const day = date.toDateString()===today.toDateString()?'Сегодня':date.toDateString()===yesterday.toDateString()?'Вчера':date.toLocaleDateString('ru-RU',{day:'numeric',month:'long',year:'numeric'});
 return {...t,name:t.title,day,time:date.toLocaleTimeString('ru-RU',{hour:'2-digit',minute:'2-digit'}),symbol:t.type==='income'?'₸':t.title.slice(0,1),color:t.type==='income'?'mint':t.type==='transfer'?'peach':'rose'};
}
async function api(path, options={}) {
 const epoch=state.authEpoch,accountId=userData?.id??null;
 let response;
 try { response = await fetch('/api'+path,{...options,credentials:'same-origin',headers:{'Content-Type':'application/json',...(userData?{'X-Account-ID':String(userData.id)}:{}),...options.headers},signal:AbortSignal.timeout(15000)}); }
 catch { throw new Error('Ошибка соединения с сервером. Проверьте, что FastAPI запущен.'); }
 let data;
 try { data = await response.json(); } catch { throw new Error('Сервер вернул некорректный ответ. Откройте приложение через FastAPI.'); }
 if(epoch!==state.authEpoch||(userData?.id??null)!==accountId){const error=new Error('Аккаунт изменился во время запроса.');error.status=409;throw error;}
 const accountChanged=response.status===409&&data.detail==='В другой вкладке сменился аккаунт. Обновите страницу и войдите заново.';
 if(!response.ok){if(response.status===401||accountChanged)resetAccount();const error=new Error(typeof data.detail==='string'?data.detail:'Не удалось сохранить данные.');error.status=response.status;throw error;}
 return data;
}
async function loadData() {
 const epoch=state.authEpoch;
 // Pin the restored cookie identity before requesting any private financial data.
 const user=await api('/user');
 if(epoch!==state.authEpoch)throw new Error('Аккаунт изменился. Войдите заново.');
 userData=user;
 const [cards,history,trusted,invitation,incoming,requests,approvals,changes,relatives] = await Promise.all([api('/cards'),api('/transactions'),api('/trusted-person'),api('/trusted-invitations/current'),api('/trusted-invitations/incoming'),api('/transfers/requests'),api('/transfers/pending-requests'),api('/protection-change-requests'),api('/trusted-invitations')]);
 if(epoch!==state.authEpoch)throw new Error('Аккаунт изменился. Войдите заново.');
 if(!cards.length) throw new Error('Тестовая карта не найдена.');
 userData=user; cardsData=cards; trustedData=trusted; currentInvitation=invitation; incomingInvitations=incoming; transactions=history.map(normalizeTransaction); state.loaded=true;
 ownRequests=requests;approvalRequests=approvals;reconcileOutcome();
 protectionChanges=changes;ownInvitations=relatives;
}
async function refreshData() {
 if(state.loading||state.requestBusy||state.sending)return;
 state.loading=true; state.error=''; render(false);
 try { await loadData(); } catch(error) { state.error=error.message; }
 finally { state.loading=false; render(false); }
}
const services = [['phone','Мобильная связь'],['globe','Интернет'],['building','Коммунальные услуги'],['receipt','Штрафы'],['book','Образование'],['bus','Транспорт'],['game','Игры'],['grid','Другое']];
const title = text => `<div class="page-title"><button class="icon-btn" data-back aria-label="Назад">${icon('back')}</button><h1>${text}</h1></div>`;
const action = (symbol,label,attribute) => `<button class="action-item" ${attribute}><span class="action-icon">${icon(symbol)}</span><span>${label}</span></button>`;
const row = (symbol,label,attribute) => `<button class="list-button" ${attribute}>${icon(symbol)}<span>${label}</span>${icon('chevron','chevron')}</button>`;
const details = (label,value) => `<div class="detail-row"><span>${label}</span><strong>${value}</strong></div>`;
const demoNote = '<p class="note">Учебный счёт · Все данные вымышлены</p>';
function bankCard(large=false){const c=currentCard();return `<${large?'div':'button'} class="bank-card ${large?'card-large':''}" ${large?'':'data-page="card" aria-label="Открыть карту"'}><div class="card-top"><strong>${escapeText(c.card_name.toUpperCase())}</strong>${icon('card')}</div><div><span class="card-label">Доступный баланс</span><div class="balance">${new Intl.NumberFormat('ru-RU').format(c.balance)} <small>${c.currency==='KZT'?'₸':escapeText(c.currency)}</small></div></div>${large?`<div class="card-number">•••• •••• •••• ${escapeText(c.last_four)}</div>`:''}<div class="card-bottom"><span>${large?'VALID THRU &nbsp; '+escapeText(c.expiry):'•••• &nbsp;'+escapeText(c.last_four)}</span><b>AMAN</b></div></${large?'div':'button'}>`;}
function transactionList(items,showDay=true){return items.map(t=>`<div class="transaction"><span class="transaction-icon ${t.color}">${t.symbol==='qr'?icon('qr'):escapeText(t.symbol)}</span><div class="transaction-info"><strong>${escapeText(t.name)}</strong><span>${showDay?t.day+', ':''}${t.time}</span></div><span class="transaction-amount ${t.amount>0?'positive':''}">${t.amount>0?'+':'−'}${money(Math.abs(t.amount))}</span></div>`).join('');}
function login(){return `<section class="screen login-screen"><div class="login-hero"><div class="orbit"></div><i class="orbit-dot"></i><div class="hero-mark"><span>A</span></div><div class="hero-badge">${icon('shield')}</div></div><div class="login-copy"><span class="eyebrow">Больше чем банк</span><h1>Войти<br>в AMAN Bank</h1><p>У каждого члена семьи — свой аккаунт.</p></div><form id="loginForm"><label class="field">Тестовый ИИН<input name="test_iin" placeholder="TEST0006" pattern="TEST[0-9]{4}" maxlength="8" autocomplete="username" required></label><label class="field">Пароль<input name="password" type="password" placeholder="Пароль аккаунта" autocomplete="current-password" maxlength="128" required></label><button class="primary" type="submit">Войти ${icon('chevron')}</button></form><p class="note">Для локальной проверки используйте тестовые аккаунты из README. Настоящие ИИН и личные пароли не вводите.</p></section>`;}
function home(){return `<section class="screen"><div class="welcome"><p>Доброе утро,</p><h1>${escapeText(userData.name)} <span style="color:var(--primary)">.</span></h1></div><div class="section-head"><h2>Мои продукты</h2><span class="small muted">${cardsData.length} карта</span></div>${bankCard()}<div class="quick-actions">${action('arrows','Переводы','data-page="transfer"')}${action('wallet','Платежи','data-page="payments"')}${action('plus','Пополнить','data-modal="topup"')}${action('qr','QR','data-modal="qr"')}</div>${familyReady()?`<button class="family-banner" data-page="family"><span class="shield-icon">${icon('shield')}</span><div><strong>Рядом, даже на расстоянии</strong><p>Семейная защита ваших финансов</p></div>${icon('chevron','chevron')}</button>`:''}<div class="section-head"><h2>Сервисы</h2></div><div class="service-grid">${[['shield','Семейная защита','family'],['history','История операций','history'],['card','Мои карты','card'],['settings','Настройки','profile']].map(([i,n,p])=>`<button class="service" data-page="${p}">${icon(i)}${n}</button>`).join('')}</div><div class="section-head"><h2>Последние операции</h2><button class="text-btn" data-page="history">Все операции →</button></div><div class="panel" style="margin-top:0;padding-top:3px;padding-bottom:3px">${transactionList(transactions.slice(0,4))}</div>${demoNote}</section>`;}
function card(){return `<section class="screen">${title('Моя карта')}${bankCard(true)}<div class="quick-actions">${action('receipt','Реквизиты','data-modal="requisites"')}${action('plus','Пополнить','data-modal="topup"')}${action('arrows','Перевести','data-page="transfer"')}${action('settings','Настройки','data-modal="cardSettings"')}</div><div class="panel">${details('Доступный баланс',money(currentCard().balance))}${details('Валюта счёта',escapeText(currentCard().currency))}</div><div class="panel"><div class="section-head" style="margin:0"><h2>Лимиты</h2><span class="small muted">В этом месяце</span></div><div class="detail-row"><span>Покупки и платежи</span><strong>240 000 / 1 000 000 ₸</strong></div><div class="limit-track"><span></span></div><p class="small">Осталось 760 000 ₸ · пример лимита</p></div><div class="section-head"><h2>Последние операции</h2><button class="text-btn" data-page="history">Все →</button></div><div class="panel">${transactionList(transactions.slice(0,3))}</div>${demoNote}</section>`;}
function transfer(){const modes=[['phone','phone','По номеру телефона'],['own','arrows','Между своими счетами'],['card','card','На карту'],['account','receipt','По реквизитам']];return `<section class="screen">${title('Переводы')}<div class="tabs" aria-label="Способ перевода">${modes.map(([id,i,n])=>`<button data-transfer-type="${id}" class="${state.transferType===id?'active':''}" aria-pressed="${state.transferType===id}">${icon(i)}${n}</button>`).join('')}</div>${state.transferType==='own'?`<div class="panel"><h2>Ваши счета</h2>${details('Откуда',cardLabel())}${details('Куда','Накопительный счёт · 0012')}<p class="small">Демонстрационный накопительный счёт</p></div>`:`<label class="field">${state.transferType==='phone'?'Номер телефона':state.transferType==='card'?'Номер карты':'Номер счёта'}<input id="recipientInput" type="text" inputmode="${state.transferType==='account'?'text':'numeric'}" autocomplete="off" placeholder="${state.transferType==='phone'?'+7 ___ ___ __ __':state.transferType==='card'?'0000 0000 0000 0000':'KZ00 •••• •••• ••••'}" value="${escapeText(state.phone)}"></label><div class="recipient" id="recipientPreview" aria-live="polite" ${state.recipient?'':'hidden'}>${state.recipient?recipientMarkup(state.recipient):''}</div>`}<form id="transferForm"><label class="field">Сумма перевода<div class="amount-field"><input id="amountInput" type="text" inputmode="decimal" placeholder="0" value="${escapeText(state.amount)}" autocomplete="off"><span>₸</span></div></label><div class="chips">${[10000,50000,100000].map(n=>`<button type="button" class="chip" data-add="${n}">+${money(n).replace(' ₸','')}</button>`).join('')}</div><label class="field">Сообщение получателю <span class="muted">· необязательно</span><input id="messageInput" maxlength="140" placeholder="Например, на подарок" value="${escapeText(state.message)}"></label>${antiScamForm()}<div class="info-box">${icon('shield')}<span>Учебный перевод без комиссии.<br>Сумма поступит на тестовую карту получателя.</span></div><p id="transferError" class="error" role="alert" hidden></p><button type="submit" class="primary">Продолжить ${icon('chevron')}</button></form>${demoNote}</section>`;}
function confirm(){const recipient=state.recipient?.name||'';return `<section class="screen">${title('Подтверждение перевода')}<div class="confirm-person"><div class="avatar">${initials(recipient)}</div><h2>${escapeText(recipient)}</h2><p class="small">${state.transferType==='own'?'Между своими счетами':'Клиент AMAN Bank'}</p><div class="amount">${money(Number(state.amount))}</div></div><div class="panel">${details('С карты',cardLabel())}${details('Комиссия','0 ₸')}${state.message?details('Сообщение',escapeText(state.message)):''}${details('Итого',money(Number(state.amount)))}</div>${antiScamSummary(state.antiScam)}<button class="primary" data-send-transfer ${state.sending?'disabled':''}>${state.sending?'Сохраняем…':'Перевести'} ${money(Number(state.amount))}</button><button class="secondary" data-back>Изменить перевод</button><p class="error" role="alert">${escapeText(state.transferError)}</p><p class="note">Перевод сохранится на обоих учебных счетах. Реальные деньги не переводятся.</p></section>`;}
function success(){const result=state.completed;return `<section class="screen success-screen"><div class="success-mark">${icon('check')}</div><h1>Перевод выполнен</h1><p>${escapeText(result.recipient_name)} · ${money(result.amount)}</p><div class="panel">${details('Статус','Сохранён на учебном счёте')}${details('Операция','№ '+result.transaction_id)}${details('Комиссия','0 ₸')}${details('Списано с карты',money(result.amount))}${details('Остаток',money(result.new_balance))}</div><p class="note">Перевод между тестовыми счетами сохранён.<br>Реальные деньги не используются.</p><button class="primary" data-page="home">На главную</button><button class="secondary" data-new-transfer>Ещё один перевод</button></section>`;}
function payments(){return `<section class="screen">${title('Платежи')}<input class="search" id="paymentSearch" type="search" placeholder="Поиск услуги" aria-label="Поиск услуги" value="${escapeText(state.search)}"><div class="payment-grid" id="paymentGrid">${paymentTiles()}</div><p class="empty" id="paymentEmpty" ${services.some(([,n])=>n.toLowerCase().includes(state.search.toLowerCase()))?'hidden':''}>Ничего не найдено. Попробуйте другое название.</p><div class="info-box">${icon('wallet')}<span>Всё важное — в одном месте.<br>Оплачивайте повседневные услуги с AMAN.</span></div>${demoNote}</section>`;}
function paymentTiles(){return services.filter(([,n])=>n.toLowerCase().includes(state.search.toLowerCase())).map(([i,n])=>`<button class="payment-tile" data-service="${n}">${icon(i)}<span>${n}</span></button>`).join('');}
function history(){const filtered=transactions.filter(t=>state.filter==='all'||(state.filter==='expense'?t.amount<0:t.type===state.filter));const spent=transactions.reduce((sum,t)=>sum+(t.amount<0?Math.round(-t.amount*100):0),0)/100;return `<section class="screen">${title('История')}<div class="history-total"><span class="small muted">Расходы за всё время</span><strong>${money(spent)}</strong><p class="small">Покупки, платежи и переводы</p></div><div class="chips" aria-label="Фильтр операций">${[['all','Все'],['expense','Расходы'],['income','Пополнения'],['transfer','Переводы']].map(([id,n])=>`<button class="chip ${state.filter===id?'active':''}" data-filter="${id}" aria-pressed="${state.filter===id}">${n}</button>`).join('')}</div>${[...new Set(filtered.map(t=>t.day))].map(day=>`<h3 class="history-date">${day}</h3><div class="panel" style="padding-top:3px;padding-bottom:3px">${transactionList(filtered.filter(t=>t.day===day),false)}</div>`).join('')}${!filtered.length?'<p class="empty">Операций пока нет</p>':''}${demoNote}</section>`;}
function antiScamForm(){return `<fieldset class="antiscam-form"><legend>Проверка AntiScam</legend><p class="small">Ответьте перед переводом. При давлении остановитесь и свяжитесь с банком или близким. Ответы «Нет» не отменяют проверку риска и решения родственников.</p>${[['pressure','Вас торопят или оказывают давление?'],['secrecy','Вас просят скрыть перевод от близких или банка?'],['stranger','Вы переводите по указанию незнакомого человека?']].map(([key,label])=>`<label class="field">${label}<select data-antiscam="${key}"><option value="" ${state.antiScam[key]===null?'selected':''}>Не отвечено</option><option value="true" ${state.antiScam[key]===true?'selected':''}>Да</option><option value="false" ${state.antiScam[key]===false?'selected':''}>Нет</option></select></label>`).join('')}</fieldset>`;}
function antiScamSummary(answers){return answers?`<div class="antiscam-summary"><h3>Ответы AntiScam</h3>${[['pressure','Давление или срочность'],['secrecy','Просьба о тайне'],['stranger','Указания незнакомого человека']].map(([key,label])=>details(label,answers[key]===true?'Да':answers[key]===false?'Нет':'Неизвестно')).join('')}</div>`:'';}
function participantMarkup(request){return request.participants?.length?`<div class="participant-votes"><h3>Решения родственников</h3>${request.participants.map(person=>`<div class="detail-row"><span>${escapeText(person.name)}</span><strong>${({pending:'Ожидается ответ',approve:'Одобрено',reject:'Отклонено'})[person.response]||'Неизвестно'}</strong></div>`).join('')}${request.my_vote?`<p class="note">Ваш ответ: ${request.my_vote==='approve'?'одобрено':'отклонено'}.</p>`:''}<p class="note">Для исполнения требуется единогласное одобрение. Единогласный отказ отклоняет перевод; разные решения после ответа всех родственников передают запрос в банк.</p></div>`:'';}
function relativesMarkup(){
 const active=ownInvitations.filter(invite=>invite.active&&invite.status!=='rejected');
 return `<div class="section-head"><h2>Доверенные родственники (${active.length}/3)</h2></div>${active.map(invite=>`<div class="panel"><h3>${escapeText(invite.trusted_person_name)}</h3>${details('Родство',escapeText(invite.relationship))}${details('Приглашение',invitationLabel(invite.status))}${details('Проверка родства',invite.relationship_verified?'Подтверждено':'Не подтверждено')}<button class="danger" data-protection-change="remove" data-invitation-id="${escapeText(invite.id)}" ${state.familyBusy||state.loading||protectionChanges.some(change=>change.action==='remove'&&change.status==='pending'&&change.invitation_id===invite.id)?'disabled':''}>Удалить родственника (через 24 часа)</button></div>`).join('')}${!active.length?'<p class="small">Пригласите родственника и дождитесь его согласия.</p>':''}<button class="primary" data-modal="trustedPerson" ${active.length>=3||state.familyBusy?'disabled':''}>Добавить доверенного родственника</button><p class="note">До трёх родственников с проверенным родством. Новое приглашение не заменяет уже принятые.</p>`;
}
function protectionChangeMarkup(){
 const pending=protectionChanges.some(change=>change.status==='pending');
 const selected=Boolean(trustedData?.trusted_person_test_iin)&&!ownInvitations.length;
 const label=action=>action==='remove'?'Удаление родственника':'Отключение защиты';
 const statusLabel=status=>({pending:'Запланировано',cancelled:'Отменено',executed:'Исполнено'})[status]||'Обновите статус';
 if(!selected&&!trustedData?.protection_active&&!protectionChanges.length)return '';
 return `<div class="panel protection-changes"><h3>Изменение защиты</h3><p class="small">Отключение защиты и удаление родственника вступают в силу через 24 часа. До этого защита и подтверждение переводов сохраняются. Изменение можно отменить.</p>${trustedData?.protection_active?`<button class="secondary" data-protection-change="disable" ${state.familyBusy||state.loading||pending?'disabled':''}>Отключить защиту (через 24 часа)</button>`:''}${selected?`<button class="danger" data-protection-change="remove" ${state.familyBusy||state.loading||pending?'disabled':''}>Удалить родственника (через 24 часа)</button>`:''}${pending?'<p class="note">Уже есть ожидающее изменение. Для другого действия сначала отмените его.</p>':''}${[...protectionChanges].sort((a,b)=>Number(b.status==='pending')-Number(a.status==='pending')||b.id-a.id).slice(0,5).map(change=>`<div class="protection-change-item"><strong>${label(change.action)}</strong>${details('Статус',statusLabel(change.status))}${change.invitation_id?details('Родственник',escapeText(ownInvitations.find(invite=>invite.id===change.invitation_id)?.trusted_person_name||'Приглашение № '+change.invitation_id)):''}${details('Дата исполнения',requestTime(change.effective_at))}${change.status==='pending'&&change.can_cancel?`<button class="secondary" data-cancel-protection-change="${escapeText(change.id)}" ${state.familyBusy||state.loading?'disabled':''}>Отменить ${change.action==='remove'?'удаление родственника':'отключение защиты'}</button>`:''}</div>`).join('')}</div>`;
}
function family(){
 const active=familyReady();const status=trustedData.invitation_status;
 const heading=active?'Защита активна':status==='rejected'?'Приглашение отклонено':status==='accepted'&&!trustedData.protection_active?'Защита выключена':'Настройка защиты';
 return `<section class="screen">${title('Семейная защита')}
 <div class="family-hero"><span class="shield-icon">${icon('shield')}</span><h2>${heading}</h2>
 <p>${active?'Родство подтверждено, близкий человек принял приглашение.':status==='pending'?'Родство подтверждено. Дождитесь согласия вашего близкого.':status==='rejected'?'Можно выбрать другого родственника и отправить новое приглашение.':status==='accepted'?'Приглашение принято. Включите функцию в настройках защиты.':'Включите функцию, подтвердите родство и пригласите близкого человека.'}</p>
 <span class="status ${active?'':'status-neutral'}">${icon(active?'check':'settings')} ${active?'Всё готово':'Требуется настройка'}</span></div>
 ${state.familyError?`<p class="error" role="alert">${escapeText(state.familyError)}</p>`:''}
 ${state.protectionNotice?`<p class="info-box" role="status">${escapeText(state.protectionNotice)}</p>`:''}
 ${protectionChangeMarkup()}
 <div class="panel"><h3>Семейная защита</h3>${details('Функция',trustedData.protection_active?'Включена':'Выключена')}<button class="secondary" data-modal="protectionSettings">${icon('settings')} Настройки защиты</button></div>
 ${relativesMarkup()}
 ${currentInvitation?.status==='pending'?'<p class="note">Приглашение отправлено. Родственник должен войти в свой аккаунт и ответить во входящих приглашениях.</p>':''}
 <button class="secondary" data-page="invitations">${icon('bell')} Входящие приглашения (${incomingInvitations.filter(i=>i.status==='pending').length})</button>
 <div class="panel"><h2>Как включить защиту?</h2><div class="steps"><div class="step"><span>1</span><div><strong>Включите функцию</strong>Вы управляете ею в настройках защиты.</div></div><div class="step"><span>2</span><div><strong>Проверьте родство</strong>Введите TEST-код одного родственника.</div></div><div class="step"><span>3</span><div><strong>Получите согласие</strong>Защита будет активна после принятия приглашения.</div></div></div></div>
 <p class="note">Mock eGov — локальная демонстрация на вымышленных данных. При высоком риске перевод требует решения доверенного родственника.</p></section>`;
}
function invitations(){return `<section class="screen">${title('Входящие приглашения')}<p class="muted small">Аккаунт: ${escapeText(userData.name)}</p>${incomingInvitations.length?incomingInvitations.map(invite=>`<div class="panel"><h2>${invite.status==='pending'?'Вам пришло приглашение':invitationLabel(invite.status)}</h2><p class="small" style="margin-top:10px">${escapeText(invite.owner_name)} приглашает вас стать доверенным лицом.</p>${details('Кем вам приходится',escapeText(invite.reverse_relationship))}${details('Ваше родство',escapeText(invite.relationship))}${details('Статус',invitationLabel(invite.status))}${invite.status==='pending'?`<button class="primary" data-invitation-response="accept" data-invitation-id="${invite.id}" ${state.familyBusy?'disabled':''}>${icon('check')} Принять</button><button class="danger" data-invitation-response="reject" data-invitation-id="${invite.id}" ${state.familyBusy?'disabled':''}>${icon('close')} Отклонить</button>`:''}</div>`).join(''):'<div class="panel"><h2>Приглашений пока нет</h2><p class="small">Здесь появятся приглашения, адресованные именно вам.</p></div>'}<p class="error" role="alert">${escapeText(state.familyError)}</p><button class="secondary" data-retry>${icon('history')} Обновить</button><p class="note">Принятие приглашения не даёт доступа к деньгам и карте родственника.</p></section>`;}
function profile(){return `<section class="screen">${title('Профиль')}<div class="profile-hero"><div class="avatar">${initials(userData.name)}</div><h1>${escapeText(userData.name)}</h1><p>${escapeText(userData.phone)}</p></div><div class="panel">${row('bell','Входящие приглашения','data-page="invitations"')}${row('user','Личные данные','data-modal="personal"')}${row('lock','Безопасность','data-modal="security"')}${row('bell','Уведомления','data-modal="notificationsSettings"')}${row('settings','Настройки','data-modal="settings"')}${row('globe','Язык · Русский','data-modal="language"')}</div><div class="panel"><h3>Тема оформления</h3><div class="theme-control"><button data-theme="light" class="${!state.dark?'active':''}" aria-pressed="${!state.dark}">${icon('sun')} Светлая</button><button data-theme="dark" class="${state.dark?'active':''}" aria-pressed="${state.dark}">${icon('moon')} Тёмная</button></div></div><button class="danger" data-logout>${icon('exit')} Выйти</button><p class="demo-tag">AMAN BANK · Больше чем банк</p>${demoNote}</section>`;}
const requestLabel=status=>({completed:'Перевод выполнен',pending_approval:'Ожидает решений родственников',bank_review:'Ожидает проверки сотрудником банка',blocked_no_trusted:'Не исполнен: нет активного доверенного лица',rejected:'Отклонён родственником',cancelled:'Отменён отправителем',expired:'Срок подтверждения истёк'})[status]||'Статус требует обновления';
const riskLabel=level=>({low:'Низкий',medium:'Средний',high:'Высокий',critical:'Критический'})[level]||'Нет оценки';
function riskMarkup(risk){
 if(!risk)return '';
 const component=(label,value)=>!value?'':`<div class="risk-component"><strong>${label}: ${riskLabel(value.level)}</strong>${value.factors?.length?`<ul>${value.factors.map(f=>`<li><strong>${escapeText(f.label)}</strong><br>${escapeText(f.description)}</li>`).join('')}</ul>`:'<p class="small">Выраженных признаков не обнаружено.</p>'}</div>`;
 return `<div class="risk-summary"><h3>Причины проверки</h3>${component('Риск операции',risk.transaction_risk)}${component('Риск получателя',risk.recipient_risk)}${risk.data_uncertainty?`<p class="small">Неопределённость данных: ${riskLabel(risk.data_uncertainty.level)}</p>${risk.data_uncertainty.reasons?.length?`<ul>${risk.data_uncertainty.reasons.map(reason=>`<li>${escapeText(reason)}</li>`).join('')}</ul>`:''}`:''}<p class="note">Оценка риска не является доказательством мошенничества.</p></div>`;
}
function requestTime(value){const date=new Date(value);return Number.isNaN(date.getTime())?'Не указан':escapeText(date.toLocaleString('ru-RU'));}
function requestCard(request,relative=false){
 const pending=request.status==='pending_approval';const canCancel=['pending_approval','bank_review'].includes(request.status);
 const attributes=action=>`data-request-action="${action}" data-request-id="${escapeText(request.id)}" data-request-relative="${relative}" ${state.requestBusy||state.loading?'disabled':''}`;
 return `<article class="panel transfer-request"><h2>${escapeText(requestLabel(request.status))}</h2>${details('Запрос','№ '+escapeText(request.id))}${details('Отправитель',escapeText(request.sender_name))}${details('Получатель',escapeText(request.recipient_name))}${details('Телефон получателя',escapeText(request.recipient))}${details('Сумма',money(request.amount))}${request.message?details('Сообщение',escapeText(request.message)):''}${details('Создан',requestTime(request.created_at))}${details('Срок подтверждения',requestTime(request.expires_at))}${request.decided_at?details('Решение принято',requestTime(request.decided_at)):''}${riskMarkup(request.risk)}${participantMarkup(request)}${antiScamSummary(request.anti_scam)}${request.status==='bank_review'?'<p class="note">Деньги не списаны. Требуется проверка сотрудником банка; голоса родственников и ответы AntiScam сохранены.</p>':''}${pending?'<p class="note">Деньги ещё не списаны. Запрос действует 24 часа. Решение относится только к указанным сумме и получателю.</p>':''}${relative&&pending&&request.can_approve?`<p class="small">Свяжитесь с родственником перед подтверждением.</p><button class="primary" ${attributes('approve')}>${icon('check')} Одобрить перевод ${money(request.amount)}</button><button class="danger" ${attributes('reject')}>${icon('close')} Отклонить перевод</button>`:''}${!relative&&canCancel&&request.can_cancel?`<button class="secondary" ${attributes('cancel')}>Отменить запрос без списания</button>`:''}${request.status==='blocked_no_trusted'?'<p class="note">Подключите доверенного родственника и включите семейную защиту. Затем создайте новый перевод.</p><button class="secondary" data-page="family">Настроить защиту</button>':''}</article>`;
}
function requests(){return `<section class="screen">${title('Запросы на перевод')}<p class="small muted">Аккаунт: ${escapeText(userData.name)}</p><p class="error" role="alert">${escapeText(state.requestError)}</p>${state.requestNotice?`<p class="info-box" role="status">${escapeText(state.requestNotice)}</p>`:''}<button class="secondary" data-retry ${state.loading||state.requestBusy?'disabled':''}>${icon('history')} Обновить</button><div class="section-head"><h2>Решения для родственников</h2></div>${approvalRequests.length?approvalRequests.map(r=>requestCard(r,true)).join(''):'<p class="empty">Запросов на ваше решение нет.</p>'}<p class="note">Вы видите только реквизиты этих запросов. Балансы и история родственника недоступны.</p><div class="section-head"><h2>Мои запросы</h2></div>${ownRequests.length?ownRequests.map(r=>requestCard(r)).join(''):'<p class="empty">Вы ещё не создавали запросы.</p>'}${demoNote}</section>`;}
function transferResult(){const result=state.transferOutcome;return `<section class="screen">${title('Результат проверки')}<div class="panel"><h2>${escapeText(requestLabel(result.status))}</h2>${details('Получатель',escapeText(result.recipient_name))}${details('Сумма',money(result.amount))}${result.request_id?details('Запрос','№ '+escapeText(result.request_id)):''}<p class="note">${result.status==='completed'?'Перевод исполнен. Актуальный баланс доступен на главной.':result.status==='pending_approval'?'Деньги не списаны. Запрос действует 24 часа. Для исполнения нужно согласие всех назначенных родственников.':result.status==='bank_review'?'Деньги не списаны. Запрос рассматривает сотрудник банка из-за ответов AntiScam или разногласия родственников.':'Перевод не исполнен. Деньги не списаны.'}</p>${riskMarkup(result.risk)}</div><button class="primary" data-page="requests">Открыть запросы на перевод</button>${result.status==='blocked_no_trusted'?'<button class="secondary" data-page="family">Настроить семейную защиту</button>':''}<button class="secondary" data-page="home">На главную</button></section>`;}
function reconcileOutcome(){if(state.transferOutcome){const updated=ownRequests.find(r=>r.id===state.transferOutcome.request_id);if(updated)state.transferOutcome={...state.transferOutcome,status:updated.status,risk:updated.risk};}}
const screens={login,home,card,transfer,confirm,success,payments,history,family,profile,invitations,requests,transferResult};
function render(focus=true){main.innerHTML=(state.error?`<div class="panel error" role="alert">${escapeText(state.error)}<button class="text-btn" data-retry>Повторить загрузку</button></div>`:'')+(state.loading?'<p class="note" role="status">Загружаем данные…</p>':'')+screens[state.page]();if(state.signedIn&&['home','family','profile','invitations'].includes(state.page))main.querySelector('section')?.insertAdjacentHTML('beforeend',`<button class="secondary" data-page="requests">${icon('shield')} Запросы на перевод (${approvalRequests.length})</button>`);document.querySelectorAll('#loginForm button,[data-login]').forEach(b=>b.disabled=state.loading);if(userData)document.querySelector('.header .avatar').textContent=userData.name.slice(0,1);document.querySelector('.notification-dot').hidden=!(incomingInvitations.some(i=>i.status==='pending')||approvalRequests.length);document.querySelectorAll('.signed-in').forEach(el=>el.hidden=!state.signedIn);const nav=document.querySelector('.bottom-nav');nav.hidden=!state.signedIn;nav.innerHTML=navItems.map(([p,i,n])=>`<button class="nav-item ${((['confirm','success','requests','transferResult'].includes(state.page)?'transfer':['card','family','invitations'].includes(state.page)?'home':state.page)===p)?'active':''}" data-page="${p}" ${state.page===p?'aria-current="page"':''}>${icon(i)}<span>${n}</span></button>`).join('');document.title=`AMAN Bank — ${({login:'Добро пожаловать',home:'Главная',card:'Моя карта',transfer:'Переводы',confirm:'Подтверждение',success:'Перевод выполнен',payments:'Платежи',history:'История',family:'Семейная защита','invitations':'Входящие приглашения',requests:'Запросы на перевод',transferResult:'Результат проверки',profile:'Профиль'})[state.page]}`;if(focus){window.scrollTo(0,0);main.focus({preventScroll:true});}}
function go(page){if(state.sending||state.requestBusy||!screens[page]||(!state.signedIn&&page!=='login'))return;if(state.page!==page)state.trail.push(state.page);state.page=page;render();}
function setTheme(dark){state.dark=dark;document.body.classList.toggle('dark',dark);const button=document.getElementById('themeToggle');button.innerHTML=icon(dark?'sun':'moon');button.setAttribute('aria-label',dark?'Включить светлую тему':'Включить тёмную тему');document.querySelector('meta[name="theme-color"]').content=dark?'#0B1520':'#F4F6F8';try{localStorage.setItem('aman-theme',dark?'dark':'light');}catch{}if(state.page==='profile')render(false);}
async function enter(form){if(state.loading)return;const credentials=Object.fromEntries(new FormData(form));credentials.test_iin=credentials.test_iin.trim().toUpperCase();state.loading=true;state.error='';render(false);try{const loggedIn=await api('/auth/login',{method:'POST',body:JSON.stringify(credentials)});state.authEpoch++;userData={id:loggedIn.id};await loadData();state.signedIn=true;state.page='home';state.trail=[];}catch(error){state.error=error.message;}finally{state.loading=false;render();}}
function resetAccount(){state.authEpoch++;state.signedIn=false;state.page='login';state.trail=[];state.phone='';state.recipient=null;state.recipientVersion=(state.recipientVersion||0)+1;state.amount='';state.message='';state.requestKey=null;state.completed=null;state.transferOutcome=null;state.requestError='';state.requestNotice='';ownRequests=[];approvalRequests=[];state.candidate=null;state.familyError='';state.protectionNotice='';protectionChanges=[];ownInvitations=[];state.antiScam={pressure:null,secrecy:null,stranger:null};state.loaded=false;userData=null;cardsData=[];transactions=[];trustedData=null;currentInvitation=null;incomingInvitations=[];if(modal.open)modal.close();}
async function logout(){try{await api('/auth/logout',{method:'POST'});resetAccount();state.error='';}catch(error){state.error=error.message;}render();}
async function restoreAccount(){state.loading=true;render(false);try{await loadData();state.signedIn=true;state.page='home';}catch(error){if(error.status!==401)state.error=error.message;}finally{state.loading=false;render(false);}}

function showModal(heading,body){document.getElementById('modal-title').textContent=heading;document.getElementById('modal-content').innerHTML=body;modal.showModal();}
const toggleRow=(label,checked=true)=>`<label class="switch-row"><span>${label}</span><input type="checkbox" ${checked?'checked':''}></label>`;
function openModal(name){if(name==='trustedPerson')return trustedForm();if(name==='protectionSettings')return protectionForm();const content={
 notifications:['Уведомления',`<div class="info-box" style="margin:0">${icon('shield')}<span>${familyReady()?'Семейная защита активна':'Семейная защита пока не активна'}.<br>Доверенное лицо: ${escapeText(trustedData.name || 'не выбрано')}.</span></div><p class="note">Демонстрационное уведомление</p>`],
 topup:['Пополнить карту',`${row('card','С карты другого банка','data-modal="topupDemo"')}${row('arrows','Со своего счёта','data-modal="topupDemo"')}<p class="note">${cardLabel()}</p>`],
 topupDemo:['Пополнение карты','<p>Здесь будет пополнение AMAN Gold. В демонстрации баланс остаётся без изменений.</p>'],
 qr:['AMAN QR',`<div class="qr-demo">${icon('qr')}</div><p style="text-align:center">Демонстрация экрана QR.<br>Сканирование и оплата не выполняются.</p>`],
 requisites:['Реквизиты карты',`${details('Владелец',escapeText(userData.name))}${details('Карта','•••• '+escapeText(currentCard().last_four))}${details('Банк','AMAN Bank')}${details('Валюта',escapeText(currentCard().currency))}<p class="note">Вымышленные данные для демонстрации интерфейса</p>`],
 cardSettings:['Настройки карты',`${toggleRow('Покупки в интернете')}${toggleRow('Бесконтактная оплата')}<p class="note">Настройки показаны для демонстрации</p>`],
 personal:['Личные данные',`${details('Имя',escapeText(userData.name))}${details('Телефон',escapeText(userData.phone))}<p class="note">Демонстрационный профиль</p>`],
 security:['Безопасность',`${toggleRow('Вход по Face ID')}${toggleRow('Уведомления о входе')}<p class="note">Биометрия и настоящая авторизация не подключены</p>`],
 notificationsSettings:['Уведомления',`${toggleRow('Операции по карте')}${toggleRow('Семейная защита')}${toggleRow('Новости банка',false)}<p class="note">Настройки действуют только в открытом окне прототипа</p>`],
 settings:['Настройки','<p>AMAN Bank · Интерактивный прототип</p><p class="note">Язык: Русский<br>Валюта: Казахстанский тенге<br>Тема меняется на странице профиля.</p>'],
 language:['Язык приложения',`${details('Русский','✓ Выбран')}${details('Қазақша','Скоро')}${details('English','Скоро')}`]
};if(content[name]){if(modal.open)modal.close();showModal(...content[name]);}}
document.addEventListener('click',event=>{const b=event.target.closest('button,a.brand');if(!b)return;if(state.sending||state.familyBusy||state.requestBusy){event.preventDefault();return;}
 if(b.matches('.brand')){event.preventDefault();go(state.signedIn?'home':'login');}
 else if(b.id==='themeToggle')setTheme(!state.dark);
 else if(b.dataset.theme)setTheme(b.dataset.theme==='dark');
 else if(b.hasAttribute('data-retry'))refreshData();
 else if(b.hasAttribute('data-send-transfer'))sendTransfer();
 else if(b.dataset.requestAction)respondRequest(b.dataset.requestAction,Number(b.dataset.requestId),b.dataset.requestRelative==='true');
 else if(b.hasAttribute('data-send-invitation'))sendInvitation();
 else if(b.dataset.protectionChange)changeProtection(b.dataset.protectionChange,b.dataset.invitationId?Number(b.dataset.invitationId):undefined);
 else if(b.dataset.cancelProtectionChange)changeProtection('cancel',Number(b.dataset.cancelProtectionChange));
 else if(b.dataset.invitationResponse)respondInvitation(b.dataset.invitationResponse,Number(b.dataset.invitationId));
 else if(b.hasAttribute('data-logout'))logout();
 else if(b.hasAttribute('data-back')){state.page=state.trail.pop()||'home';render();}
 else if(b.dataset.page)go(b.dataset.page);
 else if(b.dataset.modal)openModal(b.dataset.modal);
 else if(b.id==='closeModal')modal.close();
 else if(b.dataset.transferType){state.transferType=b.dataset.transferType;state.phone='';state.recipient=null;state.recipientVersion=(state.recipientVersion||0)+1;render(false);}
 else if(b.dataset.add){state.amount=String((Number(state.amount)||0)+Number(b.dataset.add));document.getElementById('amountInput').value=state.amount;}
 else if(b.dataset.filter){state.filter=b.dataset.filter;render(false);}
 else if(b.hasAttribute('data-new-transfer')){state.amount='';state.message='';state.antiScam={pressure:null,secrecy:null,stranger:null};go('transfer');}
 else if(b.dataset.service)showModal(b.dataset.service,`<p>Оплата услуги «${escapeText(b.dataset.service)}» с карты AMAN Gold.</p><label class="field">Лицевой счёт или номер<input placeholder="Введите номер" autocomplete="off"></label><button class="primary" data-modal="paymentDemo">Продолжить</button><p class="note">Демонстрация · Оплата не выполняется</p>`);
 if(b.dataset.modal==='paymentDemo'){if(modal.open)modal.close();showModal('Демонстрация платежа','<p>Данные услуги готовы к проверке. Оплата в прототипе не выполняется, деньги не списываются.</p>');}
});
document.addEventListener('change',event=>{const key=event.target.dataset.antiscam;if(['pressure','secrecy','stranger'].includes(key))state.antiScam[key]=event.target.value===''?null:event.target.value==='true';});
document.addEventListener('input',event=>{const el=event.target;
 if(el.id==='testIin'){el.value=el.value.toUpperCase();state.candidate=null;document.getElementById('candidateResult').innerHTML='';}
 if(el.id==='recipientInput'){state.phone=el.value;lookupRecipient();}
 if(el.id==='amountInput'){el.value=el.value.replace(/[^0-9.,]/g,'').replace(',','.');state.amount=el.value;}
 if(el.id==='messageInput')state.message=el.value;
 if(el.id==='paymentSearch'){state.search=el.value;document.getElementById('paymentGrid').innerHTML=paymentTiles();document.getElementById('paymentEmpty').hidden=!!document.getElementById('paymentGrid').children.length;}
});
document.addEventListener('submit',async event=>{
 event.preventDefault(); if(state.sending||state.familyBusy||state.requestBusy)return;
 if(event.target.id==='loginForm')enter(event.target);
 if(event.target.id==='trustedForm')verifyCandidate(event.target);
 if(event.target.id==='protectionForm')saveProtection(event.target);
 if(event.target.id==='transferForm'){
  const error=document.getElementById('transferError'); const amount=Number(state.amount); let message='';
  if(state.transferType!=='phone'){showModal('Демонстрационный раздел','<p>Сейчас списание с учебного счёта доступно только по номеру телефона. Остальные способы пока представлены как макеты.</p>');return;}
  if(!/^\+7\d{10}$/.test(state.phone.replace(/[\s()\-]/g,'')))message='Введите тестовый номер: +7 и 10 цифр.';
  else if(!Number.isFinite(amount)||amount<=0)message='Введите корректную сумму больше 0 ₸.';
  else if(amount>currentCard().balance)message='Недостаточно средств';
  else if(!/^\d+(\.\d{1,2})?$/.test(state.amount))message='Укажите не более двух знаков после запятой.';
  if(message){error.textContent=message;error.hidden=false;return;}
  const recipient=await lookupRecipient();
  if(!recipient||state.page!=='transfer')return;
  state.requestKey=crypto.randomUUID();state.transferError='';go('confirm');
 }
});
modal.addEventListener('click',event=>{if(state.familyBusy)return;if(event.target===modal){const rect=modal.getBoundingClientRect();if(event.clientX<rect.left||event.clientX>rect.right||event.clientY<rect.top||event.clientY>rect.bottom)modal.close();}});
modal.addEventListener('cancel',event=>{if(state.familyBusy)event.preventDefault();});
document.querySelectorAll('[data-icon]').forEach(el=>el.innerHTML=icon(el.dataset.icon));
setTheme(state.dark);render(false);restoreAccount();

// Persisted demo mutations. No external banking services are called.
async function sendTransfer() {
 if(state.sending||!state.requestKey||!state.recipient||state.recipient.phone!==state.phone.replace(/[\s()\-]/g,''))return;
 state.sending=true;state.transferError='';render(false);
 const payload={recipient:state.phone.replace(/[\s()\-]/g,''),recipient_name:state.recipient.name,amount:state.amount,message:state.message,anti_scam:{...state.antiScam}};
 const epoch=state.authEpoch;
 try {
  const result=await api('/transfers',{method:'POST',headers:{'Idempotency-Key':state.requestKey},body:JSON.stringify(payload)});
  if(epoch!==state.authEpoch)return;
  if(result.status==='completed'&&result.success&&result.transaction_id!=null){
   state.completed=result;state.transferOutcome=null;
   currentCard().balance=result.new_balance;
   if(!transactions.some(t=>t.id===result.transaction_id))transactions.unshift(normalizeTransaction({id:result.transaction_id,title:result.recipient_name,type:'transfer',amount:-result.amount,status:result.status,created_at:new Date().toISOString()}));
   state.page='success';
  }else if(['pending_approval','bank_review','blocked_no_trusted','rejected','cancelled','expired'].includes(result.status)){
   state.completed=null;state.transferOutcome=result;state.page='transferResult';
  }else throw new Error('Не удалось определить статус перевода. Обновите запросы перед новой операцией.');
  state.trail=['home'];state.requestKey=null;
  try{await loadData();state.error='';}catch{if(epoch===state.authEpoch)state.error='Ответ сохранён. Не удалось обновить данные сервера. Повторите загрузку.';}
 }catch(error){if(epoch===state.authEpoch)state.transferError=error.message+' Если ответ потерян, повтор этой кнопкой не создаст второе списание.';}
 finally{state.sending=false;render(false);}
}
async function respondRequest(action,id,relative){
 if(state.requestBusy||state.loading||!state.signedIn||!['approve','reject','cancel'].includes(action))return;
 const request=(relative?approvalRequests:ownRequests).find(r=>r.id===id);
 if(!request||!(action==='cancel'?['pending_approval','bank_review'].includes(request.status):request.status==='pending_approval')||(action==='cancel'?relative||!request.can_cancel:!relative||!request.can_approve))return;
 const epoch=state.authEpoch;
 state.requestBusy=true;state.requestError='';state.requestNotice='';render(false);
 try{
  const updated=await api(`/transfers/requests/${id}/${action}`,{method:'POST'});
  if(epoch!==state.authEpoch)return;
  Object.assign(request,updated);
  if(relative)approvalRequests=approvalRequests.filter(r=>['pending_approval','bank_review'].includes(r.status));
  state.requestNotice=`Запрос № ${id}: ${requestLabel(updated.status)}.`;
  try{await loadData();state.error='';}catch{if(epoch===state.authEpoch)state.requestError='Решение сохранено. Не удалось обновить данные — нажмите «Обновить».';}
 }catch(error){
  if(epoch!==state.authEpoch)return;
  state.requestError=error.message+' Обновите список, чтобы проверить актуальный статус.';
  // Remove stale actions after a lost response or a competing decision.
  request.can_approve=false;request.can_cancel=false;
  try{await loadData();}catch{}
 }finally{state.requestBusy=false;render(false);}
}
function trustedForm(){
 state.candidate=null;
 showModal('Добавить родственника',`<form id="trustedForm"><span class="eyebrow">Mock eGov</span><p class="small muted" style="margin-top:10px">Проверим родство только с указанным человеком.</p><label class="field">Тестовый ИИН родственника<input id="testIin" name="trusted_iin" placeholder="TEST0002" pattern="TEST[0-9]{4}" maxlength="8" autocomplete="off" spellcheck="false" required aria-describedby="testIinHint"></label><p id="testIinHint" class="note">Например, TEST0002. Только вымышленные TEST-коды, не реальные ИИН.</p><p class="error" role="alert" data-form-error></p><button class="primary" type="submit">Проверить родство</button><div id="candidateResult" aria-live="polite"></div></form>`);
}
async function verifyCandidate(form){
 if(state.familyBusy)return;
 const input=form.querySelector('#testIin');const code=input.value.trim().toUpperCase();const button=form.querySelector('button[type="submit"]');const error=form.querySelector('[data-form-error]');const result=document.getElementById('candidateResult');
 state.familyBusy=true;state.candidate=null;button.disabled=true;input.disabled=true;error.textContent='';result.innerHTML='';button.textContent='Проверяем…';
 try{
  const checked=await api('/mock-egov/verify-relationship',{method:'POST',body:JSON.stringify({client_iin:userData.test_iin,trusted_iin:code})});
  if(!checked.verified){error.textContent=checked.reason==='citizen_not_found'?'Тестовый гражданин не найден. Проверьте TEST-код.':'Родство не найдено. Выберите другого человека.';return;}
  state.candidate={...checked,code};
  result.innerHTML=`<div class="recipient"><span class="avatar">${initials(checked.trusted_person.full_name)}</span><div><strong>${escapeText(checked.trusted_person.full_name)}</strong><p>${escapeText(checked.relationship)}</p><span class="status">${icon('check')} Родство подтверждено</span></div></div><p class="note">Защита станет активной после согласия родственника и включения функции.</p><button class="primary" type="button" data-send-invitation>Отправить приглашение</button>`;
 }catch(reason){error.textContent=reason.message;}
 finally{state.familyBusy=false;button.disabled=false;input.disabled=false;button.textContent='Проверить родство';}
}
async function sendInvitation(){
 if(state.familyBusy||!state.candidate)return;
 const form=document.getElementById('trustedForm');const input=form.querySelector('#testIin');
 if(input.value.trim().toUpperCase()!==state.candidate.code)return;
 const button=form.querySelector('[data-send-invitation]');const error=form.querySelector('[data-form-error]');
 state.familyBusy=true;button.disabled=true;input.disabled=true;error.textContent='';button.textContent='Отправляем…';
 try{
  const invited=await api('/trusted-invitations',{method:'POST',body:JSON.stringify({trusted_iin:state.candidate.code})});
  currentInvitation=invited;
  ownInvitations=[...ownInvitations.filter(i=>i.id!==invited.id),invited];
  // Additive invitations preserve the protection supplied by existing relatives.
  if(!ownInvitations.some(i=>i.id!==invited.id&&i.active&&i.status==='accepted'))Object.assign(trustedData,{name:invited.trusted_person_name,phone:state.candidate.trusted_person.phone,relationship:invited.relationship,relationship_verified:true,verified:true,invitation_status:invited.status,trusted_person_test_iin:invited.trusted_person_test_iin,current_invitation_id:invited.id,family_protection_ready:false});
  state.candidate=null;state.familyError='';modal.close();state.page='family';
  try{await loadData();state.error='';}catch{state.error='Приглашение сохранено. Повторите загрузку данных.';}
  render(false);
 }catch(reason){error.textContent=reason.message;}
 finally{state.familyBusy=false;button.disabled=false;input.disabled=false;button.textContent='Отправить приглашение';}
}
async function respondInvitation(answer,invitationId){
 const invitation=incomingInvitations.find(i=>i.id===invitationId);
 if(state.familyBusy||!invitation||invitation.status!=='pending')return;
 state.familyBusy=true;state.familyError='';render(false);
 try{
  const updated=await api(`/trusted-invitations/${invitationId}/${answer}`,{method:'POST'});
  Object.assign(invitation,updated);
  try{await loadData();state.error='';}catch{state.error='Ответ сохранён. Обновите данные.';}
 }catch(reason){state.familyError=reason.message;try{await loadData();}catch{}}
 finally{state.familyBusy=false;render(false);}
}
function protectionForm(){
 const field=(key,label)=>`<label class="switch-row"><span>${label}</span><input type="checkbox" name="${key}" ${trustedData[key]?'checked':''}></label>`;
 showModal('Настройки защиты',`<form id="protectionForm">${field('protection_active','Семейная защита')}${field('notifications_enabled','Уведомлять доверенное лицо')}${field('confirmation_enabled','Запрашивать подтверждение')}<p class="error" role="alert" data-form-error></p><button class="primary" type="submit">Сохранить</button><p class="note">Для активации нужны подтверждённое родство и принятое приглашение. Перевод высокого риска автоматически не исполняется. Отключение вступает в силу через 24 часа; его можно отменить на странице семейной защиты. Пока защита включена, подтверждение рискованных переводов обязательно. Уведомления настраиваются отдельно.</p></form>`);
}
async function saveForm(form,path,payload){
 const button=form.querySelector('button[type="submit"]');if(button.disabled||state.familyBusy)return;
 const epoch=state.authEpoch;
 state.familyBusy=true;
 button.disabled=true;button.textContent='Сохраняем…';const error=form.querySelector('[data-form-error]');error.textContent='';
 try{
  const updated=await api(path,{method:'PUT',body:JSON.stringify(payload)});
  if(epoch!==state.authEpoch)return;
  trustedData=updated;
  state.protectionNotice=payload.protection_active===false&&updated.protection_active?'Отключение запланировано через 24 часа. До даты исполнения защита остаётся включённой.':'Настройки защиты сохранены.';
  if(form.isConnected)modal.close();
  try{await loadData();state.error='';}catch{if(epoch===state.authEpoch)state.error='Настройки сохранены. Повторите загрузку, чтобы увидеть актуальную дату исполнения.';}
 }
 catch(reason){if(epoch===state.authEpoch)error.textContent=reason.message;}
 finally{state.familyBusy=false;button.disabled=false;button.textContent='Сохранить';render(false);}
}
function saveProtection(form){const data=new FormData(form);return saveForm(form,'/protection-settings',Object.fromEntries(['protection_active','notifications_enabled','confirmation_enabled'].map(key=>[key,data.has(key)])));}
async function changeProtection(action,id){
 if(state.familyBusy||state.loading||!state.signedIn||!['disable','remove','cancel'].includes(action))return;
 if(action==='cancel'&&!protectionChanges.some(change=>change.id===id&&change.status==='pending'&&change.can_cancel))return;
 const epoch=state.authEpoch;
 state.familyBusy=true;state.familyError='';state.protectionNotice='';render(false);
 try{
  const result=await api(action==='cancel'?`/protection-change-requests/${id}/cancel`:'/protection-change-requests',{method:'POST',...(action==='cancel'?{}:{body:JSON.stringify({action,...(action==='remove'&&id?{invitation_id:id}:{})})})});
  if(epoch!==state.authEpoch)return;
  protectionChanges=[result,...protectionChanges.filter(change=>change.id!==result.id)];
  state.protectionNotice=action==='cancel'?'Изменение отменено. Защита и родственник сохранены.':`${action==='remove'?'Удаление родственника':'Отключение защиты'} запланировано на ${new Date(result.effective_at).toLocaleString('ru-RU')}. До исполнения защита сохраняется.`;
  try{await loadData();state.error='';}catch{if(epoch===state.authEpoch)state.error='Ответ сохранён. Повторите загрузку данных.';}
 }catch(reason){if(epoch===state.authEpoch){state.familyError=reason.message;try{await loadData();}catch{}}}
 finally{state.familyBusy=false;render(false);}
}

// Refresh invitation delivery and owner consent without touching editable forms.
async function syncInvitations(){
 if(!state.signedIn||state.loading||state.sending||state.familyBusy||state.requestBusy||modal.open||document.hidden)return;
 const epoch=state.authEpoch;
 try{
  const [trusted,invitation,incoming,cards,history,requests,approvals,changes,relatives]=await Promise.all([api('/trusted-person'),api('/trusted-invitations/current'),api('/trusted-invitations/incoming'),api('/cards'),api('/transactions'),api('/transfers/requests'),api('/transfers/pending-requests'),api('/protection-change-requests'),api('/trusted-invitations')]);
  if(epoch!==state.authEpoch||state.sending||state.requestBusy||state.familyBusy)return;
  trustedData=trusted;currentInvitation=invitation;incomingInvitations=incoming;cardsData=cards;transactions=history.map(normalizeTransaction);
  ownRequests=requests;approvalRequests=approvals;reconcileOutcome();
  protectionChanges=changes;ownInvitations=relatives;
  if(['home','card','history','family','invitations','requests','transferResult'].includes(state.page))render(false);
  else document.querySelector('.notification-dot').hidden=!(incoming.some(i=>i.status==='pending')||approvalRequests.length);
 }catch(error){if(error.status===401)render(false);}
}
setInterval(syncInvitations,10000);
window.addEventListener('focus',syncInvitations);

function recipientMarkup(person){return `<span class="avatar">${initials(person.name)}</span><div><strong>${escapeText(person.name)}</strong><p>Клиент AMAN Bank · тестовый счёт</p></div><span class="check-badge">${icon('check')}</span>`;}
async function lookupRecipient(){
 const version=state.recipientVersion=(state.recipientVersion||0)+1;
 const epoch=state.authEpoch,phone=state.phone.replace(/[\s()\-]/g,'');
 state.recipient=null;
 const preview=document.getElementById('recipientPreview');
 if(!/^\+7[0-9]{10}$/.test(phone)){if(preview)preview.hidden=true;return null;}
 if(preview){preview.hidden=false;preview.textContent='Ищем получателя…';}
 try{
  const person=await api('/recipients?phone='+encodeURIComponent(phone));
  if(version!==state.recipientVersion||epoch!==state.authEpoch)return null;
  state.recipient=person;
  const current=document.getElementById('recipientPreview');
  if(current){current.hidden=false;current.innerHTML=recipientMarkup(person);}
  return person;
 }catch(error){
  if(version!==state.recipientVersion||epoch!==state.authEpoch)return null;
  const current=document.getElementById('recipientPreview');
  if(current){current.hidden=false;current.textContent=error.message;}
  return null;
 }
}
