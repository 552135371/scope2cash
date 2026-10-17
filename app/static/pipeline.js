// Pipeline view: AG Grid Community table of every invoice across clients, the collections agent
// (PayPal Agent Toolkit tools, human approval), and the verified PayPal webhook feed.
const P = { rows: [], filter: "all", grid: null };
const UNPAID = ["SENT", "OVERDUE", "PARTIALLY_PAID"];
const FILTERS = [["all", "All"], ["unpaid", "Unpaid"], ["overdue", "Overdue"], ["paid", "Paid"], ["draft", "Drafts"]];
const SUGGEST = ["Who owes me money?", "Nudge everyone more than 3 days late", "Chase Harbor Yoga", "Cancel invoice S2C-SAMPLE-03"];
const due = (i) => (i.amount_due ?? (["PAID", "CANCELLED", "REFUNDED"].includes(i.status) ? 0 : i.amount));
const lateDays = (i) => {
  if (!UNPAID.includes(i.status)) return 0;
  const d = Math.floor((Date.now() - new Date(i.due_date + "T23:59:59").getTime()) / 864e5);
  return Math.max(i.days_overdue || 0, d, 0);
};
const passes = (i) => ({ all: true, unpaid: UNPAID.includes(i.status), overdue: lateDays(i) > 0 && UNPAID.includes(i.status),
  paid: i.status === "PAID", draft: i.status === "DRAFT" })[P.filter];

function kpis() {
  const r = P.rows, sum = (f) => r.filter(f).reduce((a, i) => a + due(i), 0);
  const paid = r.filter((i) => i.status === "PAID").reduce((a, i) => a + i.amount, 0);
  const late = r.filter((i) => UNPAID.includes(i.status) && lateDays(i) > 0);
  const clients = new Set(r.map((i) => i.client_name || i.client_email)).size;
  $("#kpis").innerHTML = [
    ["Collected", money(paid), "ok", `${r.filter((i) => i.status === "PAID").length} paid`],
    ["Awaiting payment", money(sum((i) => UNPAID.includes(i.status))), "pp", `${r.filter((i) => UNPAID.includes(i.status)).length} open`],
    ["Overdue", money(late.reduce((a, i) => a + due(i), 0)), "bad", late.length ? `${late.length} late · oldest ${Math.max(...late.map(lateDays))}d` : "nobody is late 🎉"],
    ["Drafts to approve", money(sum((i) => i.status === "DRAFT")), "", `${clients} client${clients === 1 ? "" : "s"}`],
  ].map(([k, v, c, sub]) => `<div class="kpi ${c}"><small>${k}</small><b>${v}</b><span>${sub}</span></div>`).join("");
}

function actionsHtml(i) {
  const mock = i.provider === "mock", b = (a, label, cls = "ghost") => `<button class="${cls} tiny" data-a="${a}" data-id="${esc(i.id)}">${label}</button>`;
  return [
    i.status === "DRAFT" ? b("send", "Approve & send", "pp") : "",
    UNPAID.includes(i.status) ? b("remind", "Draft reminder") : "",
    mock && UNPAID.includes(i.status) ? b("pay", "⏩ paid") : "",
    !mock && i.payer_link && i.status !== "DRAFT" ? `<a class="ghost tiny" href="${esc(i.payer_link)}" target="_blank" rel="noopener">Payer view ↗</a>` : "",
    !mock && UNPAID.includes(i.status) && !i.checkout_link ? b("checkout", "Checkout link") : "",
    !mock && i.checkout_link && i.status !== "PAID" ? `<a class="ghost tiny" href="${esc(i.checkout_link)}" target="_blank" rel="noopener">Pay with PayPal ↗</a>` : "",
    !mock && UNPAID.includes(i.status) ? b("cardpay", "⏩ client pays (sandbox card)") : "",
  ].join("");
}
const pill = (s) => `<span class="pill ${esc(s)}">${esc(s.replace("_", " "))}</span>`;
const short = (e) => (e || "").replace("INVOICING.INVOICE.", "");

function columns() {
  return [
    { field: "client_name", headerName: "Client", minWidth: 200, flex: 1.4, filter: true, pinned: "left",
      cellRenderer: (p) => p.node.rowPinned ? `<b>${esc(p.value)}</b>` :
        `<div class="cell2"><b>${esc(p.value || p.data.client_email)}</b><small>${esc(p.data.project || "")}${p.data.sample ? ' <span class="tag">sample</span>' : ""}</small></div>` },
    { field: "number", headerName: "Invoice", width: 150, filter: true },
    { field: "milestone", headerName: "Milestone", minWidth: 160, flex: 1, filter: true },
    { field: "amount", headerName: "Amount", width: 110, type: "rightAligned", valueFormatter: (p) => money(p.value || 0, p.data?.currency) },
    { colId: "due", headerName: "Due now", width: 110, type: "rightAligned", valueGetter: (p) => p.node.rowPinned ? p.data.due : due(p.data),
      valueFormatter: (p) => money(p.value || 0, p.data?.currency) },
    { field: "due_date", headerName: "Due date", width: 120, sort: "asc" },
    { colId: "late", headerName: "Days late", width: 105, type: "rightAligned", valueGetter: (p) => p.node.rowPinned ? null : lateDays(p.data),
      cellClassRules: { "late-mild": (p) => p.value > 0 && p.value <= 7, "late-bad": (p) => p.value > 7 }, valueFormatter: (p) => (p.value ? `${p.value}d` : "") },
    { field: "status", headerName: "Status", width: 135, filter: true, cellRenderer: (p) => (p.node.rowPinned ? "" : pill(p.value)) },
    { field: "last_event", headerName: "Last PayPal event", width: 150, valueFormatter: (p) => short(p.value) },
    { colId: "actions", headerName: "Actions", minWidth: 250, flex: 1.2, sortable: false, pinned: "right", cellRenderer: (p) => (p.node.rowPinned ? "" : `<div class="acts left">${actionsHtml(p.data)}</div>`) },
  ];
}
function totals(rows) {
  return [{ client_name: `Total · ${rows.length} invoice${rows.length === 1 ? "" : "s"}`, amount: rows.reduce((a, i) => a + i.amount, 0), due: rows.reduce((a, i) => a + due(i), 0), currency: "USD" }];
}

function renderGrid() {
  const el = $("#grid");
  if (window.agGrid) {
    if (!P.grid) {
      const theme = agGrid.themeQuartz.withParams({ accentColor: "#0070e0", fontFamily: "inherit", fontSize: 13, headerBackgroundColor: "#f6f8fb",
        headerTextColor: "#5b6478", borderColor: "#e7eaf0", rowHoverColor: "#f4f8ff", wrapperBorderRadius: 12, spacing: 7 });
      P.grid = agGrid.createGrid(el, {
        theme, columnDefs: columns(), rowData: [], getRowId: (p) => p.data.id, animateRows: true, rowHeight: 50,
        defaultColDef: { sortable: true, resizable: true },
        isExternalFilterPresent: () => P.filter !== "all", doesExternalFilterPass: (n) => passes(n.data),
        rowClassRules: { "row-late": (p) => !p.node.rowPinned && lateDays(p.data) > 0, "row-paid": (p) => p.data?.status === "PAID" },
        onFilterChanged: () => P.grid && P.grid.setGridOption("pinnedBottomRowData", totals(visible())),
        overlayNoRowsTemplate: "<span class='muted'>No invoices yet: draft some in step 5, or click “Load sample clients”.</span>",
      });
      $("#grid-engine").textContent = `AG Grid Community ${agGrid.VERSION || ""}`.trim();
    }
    P.grid.setGridOption("rowData", P.rows);
    P.grid.onFilterChanged();
    P.grid.setGridOption("pinnedBottomRowData", totals(visible()));
  } else {  // CDN unreachable: same columns as a plain table
    $("#grid-engine").textContent = "plain table (AG Grid CDN unreachable)";
    const rows = P.rows.filter(passes).filter((i) => !P.q || JSON.stringify(i).toLowerCase().includes(P.q));
    el.classList.add("plain");
    el.innerHTML = `<table><thead><tr><th>Client</th><th>Invoice</th><th>Milestone</th><th class="num">Amount</th><th>Due date</th><th class="num">Late</th><th>Status</th><th>Actions</th></tr></thead><tbody>${rows.map((i) =>
      `<tr><td><b>${esc(i.client_name || i.client_email)}</b><br><small class="muted">${esc(i.project)}</small></td><td>${esc(i.number)}</td><td>${esc(i.milestone)}</td><td class="num">${money(i.amount)}</td><td>${i.due_date}</td><td class="num">${lateDays(i) || ""}</td><td>${pill(i.status)}</td><td><div class="acts left">${actionsHtml(i)}</div></td></tr>`).join("")}</tbody></table>`;
  }
}
function visible() { const out = []; P.grid?.forEachNodeAfterFilter((n) => n.data && out.push(n.data)); return out; }

async function loadPipeline() {
  P.rows = await api("/api/invoices");
  kpis(); renderGrid(); loadEvents();
}
async function loadEvents() {
  const ev = await api("/api/webhooks/events");
  const num = (id) => (P.rows.find((r) => r.id === id) || {}).number || id;
  $("#events").innerHTML = ev.length ? ev.slice(0, 8).map((e) => `<li><b>${esc(short(e.event_type))}</b> <code>${esc(num(e.invoice_id))}</code>
      <span class="vb ${e.verified_by === "paypal-api" ? "live" : ""}">${e.verified_by === "paypal-api" ? "✓ verified by PayPal" : "✓ mock-signed"}</span>
      <br><span class="muted small">${new Date(e.ts).toLocaleTimeString()} · ${esc(e.result)}</span></li>`).join("")
    : `<li class="muted small">No webhook deliveries yet. Mark an invoice paid (⏩) or pay a sandbox invoice: PayPal calls <code>/api/paypal/webhook</code>.</li>`;
}

// ---------- collections agent
function renderAgent(r) {
  const calls = r.tool_calls.map((t) => `<span class="toolcall ${t.kind}" title="${esc(JSON.stringify(t.args))}"><code>${esc(t.tool)}</code> ${esc(t.summary)}</span>`).join("");
  $("#agent-out").innerHTML = `<div class="letter agent-answer">${esc(r.answer)}</div>
    <div class="toolcalls"><span class="muted small">${esc(r.toolkit)} calls:</span>${calls}</div>
    ${r.proposals.length ? `<h5 class="muted" style="margin:12px 0 6px">WAITING FOR YOUR APPROVAL</h5>` : ""}
    <div id="props">${r.proposals.map(propHtml).join("")}</div>`;
  bindProps();
}
const propHtml = (p) => `<div class="prop ${p.status}" data-pid="${esc(p.id)}"><div><b>${esc(p.summary)}</b><br>
  <span class="muted small"><code>${esc(p.tool)}</code>${p.reason ? " · " + esc(p.reason) : ""}</span></div>
  <div class="acts">${p.status === "pending" ? `<button class="ghost tiny" data-pa="reject">Reject</button><button class="pp tiny" data-pa="approve">Approve</button>` : `<span class="pill ${p.status === "executed" ? "PAID" : "CANCELLED"}">${esc(p.status)}</span>`}</div></div>`;
function bindProps() {
  document.querySelectorAll("#props [data-pa]").forEach((b) => b.onclick = (e) => busy(e.currentTarget, async () => {
    const card = b.closest(".prop"), pid = card.dataset.pid;
    const r = await api(`/api/agent/actions/${pid}/${b.dataset.pa}`, {});
    card.outerHTML = propHtml(r.action);
    bindProps();
    if (r.action.status === "executed") {
      toast((r.invoice?.provider === "mock" ? "Mock: " : "PayPal: ") + r.action.summary.split(" · ")[0] + " — done");
      addTrace([{ step: "Approved agent action", model: "paypal-agent-toolkit", tier: "tool", mode: r.invoice?.provider === "mock" ? "mock" : "live", ms: 0, note: r.action.tool }]);
    } else if (r.action.status === "failed") toast("Blocked: " + r.action.result);
    await loadPipeline();
  }));
}
async function ask(text) {
  const btn = $("#ask-go");
  await busy(btn, async () => {
    const r = await api("/api/agent/collections", { instruction: text });
    addTrace(r.trace); renderAgent(r); await loadPipeline();
  });
}

// ---------- wiring
$("#pipe-filter").innerHTML = FILTERS.map(([k, l]) => `<button data-f="${k}" class="${k === "all" ? "on" : ""}">${l}</button>`).join("");
$("#pipe-filter").onclick = (e) => {
  const f = e.target.dataset.f; if (!f) return;
  P.filter = f; document.querySelectorAll("#pipe-filter button").forEach((b) => b.classList.toggle("on", b.dataset.f === f));
  if (P.grid) P.grid.onFilterChanged(); else renderGrid();
};
$("#qf").oninput = (e) => { P.q = e.target.value.toLowerCase(); if (P.grid) { P.grid.setGridOption("quickFilterText", e.target.value); } else renderGrid(); };
$("#grid").addEventListener("click", (e) => {
  const b = e.target.closest("button[data-a]"); if (!b) return;
  busy(b, () => act(b.dataset.a, b.dataset.id));
});
$("#seed").onclick = (e) => busy(e.currentTarget, async () => { await api("/api/demo/seed", {}); await loadPipeline(); toast("Added sample clients (fictional, mock PayPal)"); });
$("#pipe-refresh").onclick = (e) => busy(e.currentTarget, loadPipeline);
$("#nav-pipe").onclick = () => $("#pipe").scrollIntoView({ behavior: "smooth", block: "start" });
$("#suggest").innerHTML = SUGGEST.map((s) => `<button class="chipbtn">${esc(s)}</button>`).join("");
$("#suggest").onclick = (e) => { if (e.target.classList.contains("chipbtn")) { $("#ask").value = e.target.textContent; ask(e.target.textContent); } };
$("#ask-go").onclick = () => { const t = $("#ask").value.trim(); if (t.length < 3) return toast("Ask the agent something, e.g. “Who owes me money?”"); ask(t); };
$("#ask").addEventListener("keydown", (e) => { if (e.key === "Enter") $("#ask-go").click(); });
window.addEventListener("s2c:invoices", loadPipeline);
window.addEventListener("s2c:health", (e) => {
  const tk = e.detail.adapters.agent_toolkit;
  if (tk) $("#tk-chip").textContent = tk.available ? tk.provider : "PayPal Agent Toolkit not installed";
});
loadPipeline();
