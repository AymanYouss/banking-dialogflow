// ---------------------------------------------------------------------------
// Dashboard rendering + live updates (Server-Sent Events)
// ---------------------------------------------------------------------------
const $ = (id) => document.getElementById(id);
let knownTxnIds = new Set();

function fmt(n) {
  return (n < 0 ? "-$" : "$") + Math.abs(n).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

function renderAccounts(accounts, changed) {
  const el = $("accounts");
  el.innerHTML = "";
  for (const a of accounts) {
    const div = document.createElement("div");
    div.className = "account" + (changed && changed.has(a.id) ? " flash" : "");
    div.innerHTML = `
      <div><div class="name">${a.name}</div><div class="type">${a.type}</div></div>
      <div class="balance ${a.balance < 0 ? "neg" : ""}">${fmt(a.balance)}</div>`;
    el.appendChild(div);
    if (changed && changed.has(a.id)) setTimeout(() => div.classList.remove("flash"), 1500);
  }
}

function renderTxns(txns) {
  const body = $("txns");
  body.innerHTML = "";
  for (const t of txns) {
    const tr = document.createElement("tr");
    if (!knownTxnIds.has(t.id) && knownTxnIds.size) tr.className = "new";
    tr.innerHTML = `
      <td>${t.date}</td>
      <td>${t.description}</td>
      <td class="amt ${t.amount < 0 ? "neg" : "pos"}">${fmt(t.amount)}</td>`;
    body.appendChild(tr);
  }
  knownTxnIds = new Set(txns.map((t) => t.id));
}

let lastBalances = {};
function render(state) {
  const changed = new Set();
  for (const a of state.accounts || []) {
    if (lastBalances[a.id] !== undefined && lastBalances[a.id] !== a.balance) changed.add(a.id);
    lastBalances[a.id] = a.balance;
  }
  renderAccounts(state.accounts || [], changed);
  renderTxns(state.transactions || []);
}

function connect() {
  const es = new EventSource("/api/events");
  es.onopen = () => { $("conn-dot").className = "dot on"; $("conn-label").textContent = "Live"; };
  es.onerror = () => { $("conn-dot").className = "dot off"; $("conn-label").textContent = "Reconnecting…"; };
  es.onmessage = (e) => { try { render(JSON.parse(e.data)); } catch (_) {} };
}

// ---------------------------------------------------------------------------
// Dialogflow CX Messenger — mount if configured, else local demo mode
// ---------------------------------------------------------------------------
function mountChat() {
  const c = window.CX_CONFIG || {};
  const configured = c.PROJECT_ID && !c.PROJECT_ID.startsWith("YOUR_");
  if (configured) {
    const df = document.createElement("df-messenger");
    df.setAttribute("project-id", c.PROJECT_ID);
    df.setAttribute("agent-id", c.AGENT_ID);
    df.setAttribute("location", c.LOCATION || "global");
    df.setAttribute("language-code", c.LANGUAGE || "en");
    df.setAttribute("max-query-length", "-1");
    df.innerHTML = `<df-messenger-chat-bubble chat-title="Banking Assistant"></df-messenger-chat-bubble>`;
    document.body.appendChild(df);
  } else {
    // No real agent yet: expose the local demo panel so the loop is still testable.
    $("demo-panel").hidden = false;
  }
}

// Local demo buttons -> /api/simulate (mimics a CX webhook call)
const DEMO = {
  check_balance: { tag: "check_balance", parameters: {} },
  list_transactions: { tag: "list_transactions", parameters: {} },
  transfer: { tag: "transfer_funds", parameters: { amount: 200, from_account: "checking", to_account: "savings" } },
  pay: { tag: "pay_bill", parameters: { amount: 75, from_account: "checking", payee: "Electric Company" } },
};
document.addEventListener("click", async (e) => {
  const btn = e.target.closest(".demo button");
  if (!btn) return;
  const payload = DEMO[btn.dataset.tag];
  const res = await fetch("/api/simulate", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
  const data = await res.json();
  const msg = data?.fulfillment_response?.messages?.[0]?.text?.text?.[0] || JSON.stringify(data);
  $("demo-out").textContent = msg;
});

// Boot
fetch("/api/state").then((r) => r.json()).then(render).catch(() => {});
connect();
mountChat();
