const $ = (s) => document.querySelector(s);
const state = { scope: null, pricing: null, quote: null, invoices: [], position: "market", rate: null };
const SAMPLE = `Hi! I'm Sarah from Bloom Pilates Studio in Austin. We want an iOS app for our members.

Members should be able to sign up, see the weekly class schedule, book or cancel a class, and get reminder notifications an hour before class. We'd also like to sell 10-class packs and a monthly membership inside the app (subscription).

On our side we need a simple admin dashboard to manage classes, instructors and see bookings. Bonus: an AI assistant that answers common questions (parking, what to bring, cancellation policy).

We'd love to launch before our spring promo in March. Budget is flexible but we're a small studio. Can you send a proposal?`;

const money = (n, c = "USD") => new Intl.NumberFormat("en-US", { style: "currency", currency: c, maximumFractionDigits: 0 }).format(n);
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

async function api(path, body, method) {
  const r = await fetch(path, { method: method || (body ? "POST" : "GET"), headers: { "Content-Type": "application/json" }, body: body ? JSON.stringify(body) : undefined });
  if (!r.ok) { const t = await r.text(); toast("Error: " + t.slice(0, 160)); throw new Error(t); }
  return r.json();
}
function toast(msg) { const t = $("#toast"); t.textContent = msg; t.classList.remove("hidden"); clearTimeout(t._h); t._h = setTimeout(() => t.classList.add("hidden"), 2600); }
function setStep(n) {
  document.querySelectorAll(".step").forEach((el) => {
    const s = +el.dataset.step; el.classList.toggle("active", s === n); el.classList.toggle("done", s < n);
  });
}
function addTrace(steps) {
  const ol = $("#trace");
  steps.forEach((t) => {
    const li = document.createElement("li");
    const tier = t.model.includes("Nano") ? "fast" : t.model.includes("Ultra") ? "deep" : t.model.includes("super") ? "main" : "tool";
    if (tier === "tool") li.className = "tool";
    li.innerHTML = `<b>${esc(t.step)}</b><span class="tier">${tier}</span><code>${esc(t.model)}</code><br><span class="muted">${t.ms} ms · ${t.mode}${t.note ? " · " + esc(t.note) : ""}</span>`;
    ol.appendChild(li);
  });
  ol.scrollTop = ol.scrollHeight;
}
async function busy(btn, fn) {
  const label = btn.innerHTML; btn.disabled = true; btn.innerHTML = `<span class="spinner"></span>Working…`;
  try { await fn(); } finally { btn.disabled = false; btn.innerHTML = label; }
}
function show(id) { const el = $(id); el.classList.remove("hidden"); setTimeout(() => el.scrollIntoView({ behavior: "smooth", block: "start" }), 50); }

// ---------- health
(async () => {
  const h = await api("/api/health");
  const b = (label, a) => `<span class="badge" title="${esc(a.provider)}"><span class="dot ${a.mode === "live" ? "live" : ""}"></span>${label} · ${a.mode}</span>`;
  $("#badges").innerHTML = b("Nemotron @ Nebius", h.adapters.llm) + b("Tavily", h.adapters.search) + b("PayPal", h.adapters.payments);
})();

// ---------- step 1
$("#sample").onclick = () => { $("#brief").value = SAMPLE; };
$("#brief").addEventListener("keydown", (e) => { if ((e.metaKey || e.ctrlKey) && e.key === "Enter") $("#go-scope").click(); });
$("#go-scope").onclick = (e) => busy(e.currentTarget, async () => {
  const requirement = $("#brief").value.trim();
  if (requirement.length < 20) return toast("Paste a longer brief (20+ characters).");
  $("#trace").innerHTML = ""; ["#s3", "#s4", "#s5"].forEach((s) => $(s).classList.add("hidden")); $("#totals").classList.add("hidden");
  const r = await api("/api/scope", { requirement });
  state.scope = r.scope; addTrace(r.trace); renderScope(); setStep(2); show("#s2");
});

// ---------- step 2
function renderScope() {
  const s = state.scope;
  const total = s.milestones.reduce((a, m) => a + Number(m.est_hours), 0);
  $("#s2").innerHTML = `
    <div class="card-head"><h2>2 · Scope: ${esc(s.project_title)}</h2><div class="chips"><span class="chip">${esc(s.project_type)}</span><span class="chip">${esc(s.client_name)}</span><span class="chip" id="hrs-total">${total} h</span></div></div>
    <div class="summary">${esc(s.summary)}</div>
    <div class="ms-grid">${s.milestones.map((m, i) => `
      <div class="ms"><div class="n">MILESTONE ${i + 1}</div><h4>${esc(m.title)}</h4>
        <ul>${m.deliverables.map((d) => `<li>${esc(d)}</li>`).join("")}</ul>
        <div class="hrs">Est. <input type="number" min="1" value="${m.est_hours}" data-i="${i}" class="hrs-in"> hours · ${m.weeks} wk</div></div>`).join("")}
    </div>
    <div class="cols3">
      <div><h5>Assumptions</h5><ul>${s.assumptions.map((x) => `<li>${esc(x)}</li>`).join("")}</ul></div>
      <div><h5>Risks</h5><ul>${s.risks.map((x) => `<li>${esc(x)}</li>`).join("")}</ul></div>
      <div><h5>Ask the client</h5><ul>${s.open_questions.map((x) => `<li>${esc(x)}</li>`).join("")}</ul></div>
    </div>
    <div class="row end"><button class="primary" id="go-price">Research market price →</button></div>`;
  document.querySelectorAll(".hrs-in").forEach((inp) => inp.oninput = () => {
    s.milestones[+inp.dataset.i].est_hours = Number(inp.value) || 1;
    $("#hrs-total").textContent = s.milestones.reduce((a, m) => a + Number(m.est_hours), 0) + " h";
  });
  $("#go-price").onclick = (e) => busy(e.currentTarget, async () => {
    const r = await api("/api/pricing", { scope: state.scope });
    state.pricing = r.pricing; addTrace(r.trace); await makeQuote(); renderPricing(); setStep(3); show("#s3");
  });
}

// ---------- step 3
function renderPricing() {
  const p = state.pricing, q = state.quote;
  const span = Math.max(p.high - p.low, 1);
  const pos = (v) => Math.min(100, Math.max(0, ((v - p.low) / span) * 100));
  $("#s3").innerHTML = `
    <div class="card-head"><h2>3 · Market price reference</h2><span class="chip">${money(p.hourly_low)}–${money(p.hourly_high)}/h</span></div>
    <p class="muted">${esc(p.rationale)}</p>
    <div class="range"><div class="mk" style="left:${pos(p.median)}%">Market ${money(p.median)}</div><div class="mk you" style="left:${pos(q.total)}%;top:24px">You ${money(q.total)}</div></div>
    <div class="range-labels" style="margin-top:34px"><span>Low ${money(p.low)}</span><span>High ${money(p.high)}</span></div>
    <div class="sources">${p.sources.map((s) => `<div class="src"><a href="${esc(s.url)}" target="_blank" rel="noopener">${esc(s.title)}</a><div class="muted">${esc(s.snippet)}</div></div>`).join("")}</div>
    ${p.mock ? `<div class="mocknote">Sample sources (mock mode) — add TAVILY_API_KEY for live market research.</div>` : ""}
    <div class="row end"><button class="primary" id="go-quote">Build the quote →</button></div>`;
  $("#go-quote").onclick = () => { renderQuote(); setStep(4); show("#s4"); };
}
async function makeQuote() {
  const r = await api("/api/quote", { scope: state.scope, pricing: state.pricing, hourly_rate: state.rate, position: state.position });
  state.quote = r.quote; addTrace(r.trace);
}

// ---------- step 4
function renderQuote() {
  const q = state.quote;
  $("#s4").innerHTML = `
    <div class="card-head"><h2>4 · Quote</h2>
      <div class="row" style="margin:0"><div class="seg" id="seg">${["value", "market", "premium"].map((p) => `<button data-p="${p}" class="${state.position === p ? "on" : ""}">${p[0].toUpperCase() + p.slice(1)}</button>`).join("")}</div>
      <input id="rate" type="number" style="width:110px" placeholder="$/h" value="${q.hourly_rate}"></div></div>
    <table><thead><tr><th>#</th><th>Milestone</th><th class="num">Hours</th><th>Due</th><th class="num">%</th><th class="num">Amount</th></tr></thead>
      <tbody>${q.line_items.map((li, i) => `<tr><td>${i + 1}</td><td>${esc(li.milestone)}</td><td class="num">${li.hours}</td><td>${li.due_date}</td><td class="num">${li.percent}%</td><td class="num">${money(li.amount, q.currency)}</td></tr>`).join("")}</tbody>
      <tfoot><tr><td></td><td>Total @ ${money(q.hourly_rate)}/h</td><td class="num">${q.line_items.reduce((a, l) => a + l.hours, 0)}</td><td></td><td></td><td class="num">${money(q.total, q.currency)}</td></tr></tfoot></table>
    <div class="grid2">
      <div><h5 class="muted" style="margin:0">PROPOSAL MESSAGE</h5><div class="letter">${esc(q.cover_message)}</div></div>
      <div><h5 class="muted" style="margin:0">NEGOTIATION COACH <span class="chip">deep model</span></h5><ul class="tips">${q.negotiation_tips.map((t) => `<li>${esc(t)}</li>`).join("")}</ul>
        <h5 class="muted" style="margin:14px 0 0">TERMS</h5><ul class="tips">${q.terms.map((t) => `<li>${esc(t)}</li>`).join("")}</ul></div>
    </div>
    <div class="row between"><button class="ghost" id="copy-quote">Copy proposal</button>
      <div class="row" style="margin:0;flex:1;justify-content:flex-end"><input id="email" type="email" style="max-width:260px" value="sarah@bloompilates.example" placeholder="client@email.com">
      <button class="pp" id="go-inv">Create PayPal invoice drafts</button></div></div>`;
  document.querySelectorAll("#seg button").forEach((b) => b.onclick = async () => {
    state.position = b.dataset.p; state.rate = null; await makeQuote(); renderPricing(); renderQuote();
  });
  $("#rate").onchange = async (e) => { state.rate = Number(e.target.value) || null; await makeQuote(); renderPricing(); renderQuote(); };
  $("#copy-quote").onclick = () => { navigator.clipboard?.writeText(q.cover_message); toast("Proposal copied"); };
  $("#go-inv").onclick = (e) => busy(e.currentTarget, async () => {
    const client_email = $("#email").value.trim();
    if (!client_email.includes("@")) return toast("Enter the client's email");
    state.invoices = await api("/api/invoices", { scope: state.scope, quote: state.quote, client_email });
    addTrace([{ step: "Create PayPal invoice drafts", model: "paypal-invoicing-v2", mode: state.invoices[0]?.provider === "mock" ? "mock" : "live", ms: 0, note: `${state.invoices.length} drafts` }]);
    renderInvoices(); setStep(5); show("#s5");
  });
}

// ---------- step 5
function renderInvoices() {
  const inv = state.invoices, cur = state.quote.currency;
  const sum = (f) => inv.filter(f).reduce((a, i) => a + i.amount, 0);
  const paid = sum((i) => i.status === "PAID"), sent = sum((i) => ["SENT", "OVERDUE"].includes(i.status)), draft = sum((i) => i.status === "DRAFT");
  const demo = inv[0]?.provider === "mock";
  $("#s5").innerHTML = `
    <div class="card-head"><h2>5 · Get paid</h2><span class="chip">${demo ? "PayPal mock" : "PayPal " + inv[0]?.provider}</span></div>
    <div class="stat-row"><div class="stat"><small>Paid</small><b style="color:var(--ok)">${money(paid, cur)}</b></div>
      <div class="stat"><small>Awaiting payment</small><b style="color:var(--pp)">${money(sent, cur)}</b></div>
      <div class="stat"><small>Drafts to approve</small><b>${money(draft, cur)}</b></div></div>
    <table><thead><tr><th>Invoice</th><th>Milestone</th><th>Due</th><th class="num">Amount</th><th>Status</th><th class="num">Actions</th></tr></thead><tbody>
    ${inv.map((i) => `<tr><td><b>${esc(i.number)}</b><br><span class="muted small">${esc(i.id)}</span></td><td>${esc(i.milestone)}</td><td>${i.due_date}</td>
      <td class="num">${money(i.amount, cur)}</td><td><span class="pill ${i.status}">${i.status}</span>${i.status === "OVERDUE" && i.days_overdue ? `<br><span class="muted small">${i.days_overdue} days late (simulated)</span>` : ""}</td><td><div class="acts">
      ${i.status === "DRAFT" ? `<button class="pp tiny" data-a="send" data-id="${i.id}">Approve & send</button>` : ""}
      ${["SENT", "OVERDUE"].includes(i.status) ? `<button class="ghost tiny" data-a="remind" data-id="${i.id}">Draft reminder</button>` : ""}
      ${demo && i.status === "SENT" ? `<button class="ghost tiny" data-a="overdue" data-id="${i.id}">⏩ overdue</button><button class="ghost tiny" data-a="pay" data-id="${i.id}">⏩ paid</button>` : ""}
      ${demo && i.status === "OVERDUE" ? `<button class="ghost tiny" data-a="pay" data-id="${i.id}">⏩ paid</button>` : ""}
      </div></td></tr>`).join("")}</tbody></table>
    <p class="muted small">${demo ? "Demo mode: ⏩ buttons simulate client actions. With PayPal sandbox keys, statuses sync from PayPal." : "Statuses sync live from PayPal Invoicing."}</p>
    <div class="row end"><button class="ghost" id="refresh">Refresh statuses</button></div>`;
  $("#totals").classList.remove("hidden");
  $("#totals").innerHTML = `<div><span>Quoted</span><b>${money(state.quote.total, cur)}</b></div><div><span>Paid</span><b style="color:var(--ok)">${money(paid, cur)}</b></div><div><span>Outstanding</span><b>${money(state.quote.total - paid, cur)}</b></div>`;
  document.querySelectorAll("#s5 [data-a]").forEach((b) => b.onclick = (e) => busy(e.currentTarget, () => act(b.dataset.a, b.dataset.id)));
  $("#refresh").onclick = async () => { state.invoices = await api("/api/invoices"); renderInvoices(); toast("Statuses refreshed"); };
}
async function act(a, id) {
  const replace = (inv) => { state.invoices = state.invoices.map((x) => (x.id === inv.id ? inv : x)); renderInvoices(); };
  if (a === "send") { replace(await api(`/api/invoices/${id}/send`, {})); toast("Invoice sent via PayPal"); }
  if (a === "pay" || a === "overdue") replace(await api(`/api/demo/${id}/${a}`, {}));
  if (a === "remind") {
    const r = await api(`/api/invoices/${id}/reminder`, {}); addTrace(r.trace);
    $("#modal-card").innerHTML = `<div class="card-head"><h2>Reminder draft</h2><span class="chip">${esc(r.tone)}</span></div>
      <p class="muted small">Review before sending — the agent never contacts clients on its own.</p>
      <input value="${esc(r.subject)}" style="margin-top:8px"><div class="letter" contenteditable="true">${esc(r.body)}</div>
      <div class="row end"><button class="ghost" id="m-close">Close</button><button class="primary" id="m-copy">Copy reminder</button></div>`;
    $("#modal").classList.remove("hidden");
    $("#m-close").onclick = () => $("#modal").classList.add("hidden");
    $("#m-copy").onclick = () => { navigator.clipboard?.writeText(r.subject + "\n\n" + r.body); toast("Reminder copied"); $("#modal").classList.add("hidden"); };
  }
}
