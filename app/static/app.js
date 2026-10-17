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
    const tier = t.tier || "tool";
    li.className = [tier === "tool" ? "tool" : "", t.mode === "fallback" || t.mode === "error" ? t.mode : ""].join(" ").trim();
    const mode = t.mode === "fallback" ? "⚠ fallback" : t.mode;
    li.innerHTML = `<b>${esc(t.step)}</b><span class="tier ${esc(tier)}">${esc(tier)}</span><code>${esc(t.model)}</code><br>` +
      `<span class="muted">${t.ms} ms · ${esc(mode)}${t.note ? " · " + esc(t.note) : ""}</span>` +
      (t.tokens ? ` <span class="tokens">· ${t.tokens.toLocaleString()} tok</span>` : "") +
      (t.warning ? `<span class="warn">${esc(t.warning)}</span>` : "");
    ol.appendChild(li);
    if (t.warning) toast("⚠ " + t.warning.slice(0, 140));
  });
  ol.scrollTop = ol.scrollHeight;
}
async function busy(btn, fn) {
  const label = btn.innerHTML; btn.disabled = true; btn.innerHTML = `<span class="spinner"></span>Working…`;
  // Live model steps take 10-40 s (and the first request after the free host wakes up can take longer): say so.
  const slow = setTimeout(() => { if (btn.disabled) btn.innerHTML = `<span class="spinner"></span>Working…<span class="slowhint">live AI step, usually 10–40 s</span>`; }, 6000);
  try { await fn(); } finally { clearTimeout(slow); btn.disabled = false; btn.innerHTML = label; }
}
function show(id) { const el = $(id); el.classList.remove("hidden"); setTimeout(() => el.scrollIntoView({ behavior: "smooth", block: "start" }), 50); }

// ---------- health (with a visible wake-up note: free hosts sleep when idle and cold-start on the first request)
let SAMPLES = [];
async function healthWithWakeup() {
  const note = setTimeout(() => $("#wakeup").classList.remove("hidden"), 1500);
  for (let i = 0; ; i++) {
    try {
      const r = await fetch("/api/health", { cache: "no-store" });
      if (r.ok) { clearTimeout(note); $("#wakeup").classList.add("hidden"); return r.json(); }
    } catch (e) { /* server still waking up */ }
    $("#wakeup").classList.remove("hidden");
    $("#wakeup-msg").textContent = `Waking up the demo server (attempt ${i + 2})… The free host can take up to a minute after being idle.`;
    await new Promise((res) => setTimeout(res, Math.min(2000 + i * 1000, 6000)));
  }
}
(async () => {
  const h = await healthWithWakeup();
  const m = h.adapters.llm.models || {};
  const b = (label, a, extra = "") => `<span class="badge ${extra}" title="${esc(a.provider)}${a.models ? "\n" + esc(Object.entries(a.models).map(([k, v]) => k + ": " + v).join("\n")) : ""}"><span class="dot ${a.mode === "live" ? "live" : ""}"></span>${label} · ${a.mode}</span>`;
  $("#badges").innerHTML = b("LLM · " + (h.adapters.llm.provider || "?"), h.adapters.llm, h.warnings.length ? "warn" : "") + b("Tavily", h.adapters.search) + b("PayPal", h.adapters.payments)
    + (h.adapters.webhooks ? b("Webhooks", h.adapters.webhooks) : "");
  state.health = h; window.dispatchEvent(new CustomEvent("s2c:health", { detail: h }));
  state.demoEmail = h.demo_client_email;
  const banner = h.warnings.map((w) => `<div>⚠ ${esc(w)}</div>`).concat((h.notices || []).map((n) => `<div>ℹ ${esc(n)}</div>`));
  if (banner.length) { $("#healthwarn").innerHTML = banner.join(""); $("#healthwarn").classList.remove("hidden"); }
  SAMPLES = await api("/api/samples");
  $("#samples").innerHTML = SAMPLES.map((x, i) => `<option value="${i}">${esc(x.title)}</option>`).join("");
})();

// ---------- step 1
$("#instant").onclick = (e) => busy(e.currentTarget, async () => {
  const r = await api("/api/demo/sample-project", {});
  $("#brief").value = r.brief; $("#trace").innerHTML = ""; $("#totals").classList.add("hidden"); $("#s5").classList.add("hidden");
  state.scope = r.scope; state.pricing = r.pricing; state.quote = r.quote; state.position = "market"; state.rate = null;
  addTrace(r.trace); renderScope(); renderPricing(); renderQuote();
  ["#s2", "#s3"].forEach((s) => $(s).classList.remove("hidden")); setStep(4); show("#s4");
  toast("Sample project loaded (recorded DeepSeek run). Next: Create PayPal invoice drafts");
});
$("#sample").onclick = () => { const x = SAMPLES[+$("#samples").value]; $("#brief").value = x ? x.brief : SAMPLE; };
$("#samples").onchange = () => $("#sample").click();
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
    <div class="card-head"><h2>2 · Scope: ${esc(s.project_title)}</h2><div class="chips"><span class="chip">${esc(s.project_type)}</span><span class="chip">${esc(s.client_name)}</span><span class="chip" id="hrs-total">${total} h</span>${s.client_budget ? `<span class="chip">budget ${money(s.client_budget)}</span>` : ""}${s.deadline ? `<span class="chip">⏱ ${esc(s.deadline)}</span>` : ""}${s.language && s.language !== "en" ? `<span class="chip">brief: ${esc(s.language)}</span>` : ""}</div></div>
    <div class="summary">${esc(s.summary)}</div>
    <div class="row between" style="margin-top:8px"><span class="conf ${s.confidence < 0.5 ? "low" : ""}">Brief completeness <span class="bar"><i style="width:${Math.round(s.confidence * 100)}%"></i></span> ${Math.round(s.confidence * 100)}%</span></div>
    ${s.confidence < 0.5 ? `<div class="callout warn">This brief is thin. Send the client the questions below before committing to a fixed price, or quote milestone 1 as paid discovery.</div>` : ""}
    <div class="ms-grid">${s.milestones.map((m, i) => `
      <div class="ms"><div class="n">MILESTONE ${i + 1}</div><h4>${esc(m.title)}</h4>
        <ul>${m.deliverables.map((d) => `<li>${esc(d)}</li>`).join("")}</ul>
        <div class="hrs">Est. <input type="number" min="1" value="${m.est_hours}" data-i="${i}" class="hrs-in"> hours · ${m.weeks} wk</div></div>`).join("")}
    </div>
    <div class="cols3">
      <div><h5>Assumptions</h5><ul>${s.assumptions.map((x) => `<li>${esc(x)}</li>`).join("")}</ul></div>
      <div><h5>Risks</h5><ul>${s.risks.map((x) => `<li>${esc(x)}</li>`).join("")}</ul></div>
      <div><h5>Ask the client</h5><ul class="${s.confidence < 0.5 ? "ask-first" : ""}">${s.open_questions.map((x) => `<li>${esc(x)}</li>`).join("")}</ul></div>
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
    <div class="card-head"><h2>3 · Market price reference</h2><div class="chips"><span class="ground ${p.grounded ? "ok" : "no"}">${p.grounded ? "✓ grounded in sources" : "✗ unverified placeholder"}</span><span class="chip">${money(p.hourly_low)}–${money(p.hourly_high)}/h</span></div></div>
    <p class="muted">${esc(p.rationale)}</p>
    ${p.evidence?.length ? `<div class="evidence"><span class="muted" style="background:none">Figures found in sources:</span>${p.evidence.map((e) => `<span>${esc(e)}</span>`).join("")}</div>` : ""}
    ${p.queries?.length ? `<div class="queries">Tavily queries: ${p.queries.map((q) => `<code>${esc(q)}</code>`).join(" ")}</div>` : ""}
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
    ${q.budget_note ? `<div class="callout ${q.budget_note.includes("above") ? "warn" : ""}">${esc(q.budget_note)}</div>` : ""}
    <div class="grid2">
      <div><h5 class="muted" style="margin:0">PROPOSAL MESSAGE</h5><div class="letter">${esc(q.cover_message)}</div></div>
      <div><h5 class="muted" style="margin:0">NEGOTIATION COACH <span class="chip">deep model</span></h5><ul class="tips">${q.negotiation_tips.map((t) => `<li>${esc(t)}</li>`).join("")}</ul>
        <h5 class="muted" style="margin:14px 0 0">TERMS</h5><ul class="tips">${q.terms.map((t) => `<li>${esc(t)}</li>`).join("")}</ul></div>
    </div>
    <div class="row between"><button class="ghost" id="copy-quote">Copy proposal</button>
      <div class="row" style="margin:0;flex:1;justify-content:flex-end"><input id="email" type="email" style="max-width:260px" value="${esc(state.demoEmail || "sarah@bloompilates.example.com")}" placeholder="client@email.com">
      <button class="pp" id="go-inv">Create PayPal invoice drafts</button></div></div>`;
  document.querySelectorAll("#seg button").forEach((b) => b.onclick = async () => {
    state.position = b.dataset.p; state.rate = null; await makeQuote(); renderPricing(); renderQuote();
  });
  $("#rate").onchange = async (e) => { state.rate = Number(e.target.value) || null; await makeQuote(); renderPricing(); renderQuote(); };
  $("#copy-quote").onclick = () => { navigator.clipboard?.writeText(q.cover_message); toast("Proposal copied"); };
  $("#go-inv").onclick = (e) => busy(e.currentTarget, async () => {
    const client_email = $("#email").value.trim();
    if (!client_email.includes("@")) return toast("Enter the client's email");
    const r = await api("/api/invoices", { scope: state.scope, quote: state.quote, client_email });
    state.invoices = r.invoices; addTrace(r.trace);
    renderInvoices(); setStep(5); show("#s5"); window.dispatchEvent(new Event("s2c:invoices"));
  });
}

// ---------- step 5
function renderInvoices() {
  const inv = state.invoices, cur = state.quote.currency;
  const sum = (f) => inv.filter(f).reduce((a, i) => a + i.amount, 0);
  const paid = sum((i) => i.status === "PAID"), sent = sum((i) => ["SENT", "OVERDUE", "PARTIALLY_PAID"].includes(i.status)), draft = sum((i) => i.status === "DRAFT");
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
      ${["SENT", "OVERDUE", "PARTIALLY_PAID"].includes(i.status) ? `<button class="ghost tiny" data-a="remind" data-id="${i.id}">Draft reminder</button>` : ""}
      ${!demo && i.payer_link && i.status !== "DRAFT" ? `<a class="ghost tiny" style="text-decoration:none;color:var(--pp)" href="${esc(i.payer_link)}" target="_blank" rel="noopener">Payer view ↗</a>` : ""}
      ${!demo && ["SENT", "OVERDUE", "PARTIALLY_PAID"].includes(i.status) && !i.checkout_link ? `<button class="ghost tiny" data-a="checkout" data-id="${i.id}">Checkout link</button>` : ""}
      ${!demo && i.checkout_link && i.status !== "PAID" ? `<a class="ghost tiny" style="text-decoration:none;color:var(--pp)" href="${esc(i.checkout_link)}" target="_blank" rel="noopener">Pay with PayPal ↗</a>` : ""}
      ${!demo && ["SENT", "OVERDUE", "PARTIALLY_PAID"].includes(i.status) ? `<button class="ghost tiny" data-a="cardpay" data-id="${i.id}">⏩ client pays (sandbox card)</button>` : ""}
      ${demo && i.status === "SENT" ? `<button class="ghost tiny" data-a="overdue" data-id="${i.id}">⏩ overdue</button><button class="ghost tiny" data-a="pay" data-id="${i.id}">⏩ paid</button>` : ""}
      ${demo && i.status === "OVERDUE" ? `<button class="ghost tiny" data-a="pay" data-id="${i.id}">⏩ paid</button>` : ""}
      </div></td></tr>`).join("")}</tbody></table>
    <p class="muted small">${demo ? "Demo mode: ⏩ buttons simulate client actions. With PayPal sandbox keys, statuses sync from PayPal." : "Statuses sync live from PayPal Invoicing. Checkout links use PayPal Orders v2; ⏩ sandbox card pays with a PayPal sandbox test card (no real money)."}</p>
    <div class="row end"><button class="ghost" id="refresh">Refresh statuses</button></div>`;
  $("#totals").classList.remove("hidden");
  $("#totals").innerHTML = `<div><span>Quoted</span><b>${money(state.quote.total, cur)}</b></div><div><span>Paid</span><b style="color:var(--ok)">${money(paid, cur)}</b></div><div><span>Outstanding</span><b>${money(state.quote.total - paid, cur)}</b></div>`;
  document.querySelectorAll("#s5 [data-a]").forEach((b) => b.onclick = (e) => busy(e.currentTarget, () => act(b.dataset.a, b.dataset.id)));
  $("#refresh").onclick = async () => {
    const ids = new Set(state.invoices.map((i) => i.id));
    state.invoices = (await api("/api/invoices")).filter((i) => ids.has(i.id)); renderInvoices(); toast("Statuses refreshed");
    window.dispatchEvent(new Event("s2c:invoices"));
  };
}
async function act(a, id) {
  const replace = (inv) => {
    state.invoices = state.invoices.map((x) => (x.id === inv.id ? inv : x));
    if (state.quote && state.invoices.some((x) => x.id === inv.id)) renderInvoices();
    window.dispatchEvent(new Event("s2c:invoices"));
  };
  if (a === "send") { const inv = await api(`/api/invoices/${id}/send`, {}); replace(inv); toast(inv.provider === "mock" ? "Invoice marked sent (mock: nothing left this machine)" : "Invoice sent via PayPal " + inv.provider.replace("paypal-", "")); }
  if (a === "checkout") { const r = await api(`/api/invoices/${id}/checkout`, {}); addTrace(r.trace); replace(r.invoice); toast("PayPal checkout link ready: order " + r.invoice.order_id); }
  if (a === "cardpay") { const r = await api(`/api/demo/${id}/sandbox-card-pay`, {}); addTrace(r.trace); replace(r.invoice); toast(`Sandbox payment captured (${r.invoice.capture_id || "mock"}) → invoice ${r.invoice.status}`); }
  if (a === "pay" || a === "overdue" || a === "cancel") {
    const inv = await api(`/api/demo/${id}/${a}`, {}); replace(inv);
    if (a !== "overdue") toast(`Simulated PayPal webhook ${inv.last_event || ""} → ${inv.status} (mock-signed, verified)`);
  }
  if (a === "remind") {
    const r = await api(`/api/invoices/${id}/reminder`, {}); addTrace(r.trace);
    $("#modal-card").innerHTML = `<div class="card-head"><h2>Reminder draft</h2><span class="chip">${esc(r.tone)}</span></div>
      <p class="muted small">Review before anything goes out. The agent never contacts clients on its own.</p>
      <input id="m-subj" value="${esc(r.subject)}" style="margin-top:8px"><div class="letter" id="m-body" contenteditable="true">${esc(r.body)}</div>
      ${r.provider_note ? `<p class="muted small">ℹ ${esc(r.provider_note)}</p>` : ""}
      <div class="row end"><button class="ghost" id="m-close">Close</button><button class="ghost" id="m-copy">Copy personal note</button><button class="pp" id="m-send">Approve & send PayPal reminder</button></div>`;
    $("#modal").classList.remove("hidden");
    $("#m-close").onclick = () => $("#modal").classList.add("hidden");
    $("#m-copy").onclick = () => { navigator.clipboard?.writeText($("#m-subj").value + "\n\n" + $("#m-body").innerText); toast("Reminder copied"); };
    $("#m-send").onclick = (e) => busy(e.currentTarget, async () => {
      const s = await api(`/api/invoices/${id}/remind`, {}); addTrace(s.trace); replace(s.invoice);
      $("#modal").classList.add("hidden"); toast((s.invoice.provider === "mock" ? "Mock reminder recorded for " : "PayPal reminder sent for ") + s.invoice.number);
    });
  }
}
// Back from PayPal checkout (return_url / cancel_url): say what happened.
{ const c = new URLSearchParams(location.search).get("checkout");
  if (c) setTimeout(() => toast({ paid: "Payment captured via PayPal Checkout: invoice marked PAID", pending: "PayPal order not completed yet",
    cancelled: "Checkout cancelled by the buyer", failed: "PayPal capture failed (see audit log)" }[c] || "Unknown checkout"), 600); }
