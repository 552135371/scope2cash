// Pipeline dashboard: AG Grid Community table of every invoice across clients, an AG Charts Community cash-flow chart
// and a second AG Grid "who still owes what" scoreboard, all cross-filtered; saved views + CSV export; the collections
// agent (PayPal Agent Toolkit tools) with its proposed nudges inline in the grid, each waiting for an Approve click;
// collaborator payouts (PayPal Payouts) queued when a milestone is paid, approved in the same grid column;
// and the verified PayPal webhook feed. Community editions only (MIT), no build step.
// Currencies: amounts are shown in their own currency; any total over several currencies is marked "≈" and converted to
// USD with the dated ECB reference rates from /api/fx (the note under the KPIs says so). Nothing is summed silently.
const P = { rows: [], filter: "all", client: "", week: null, q: "", grid: null, board: null, chart: null, cash: null, pending: {}, hidden: new Set(["drafts"]) };
const UNPAID = ["SENT", "OVERDUE", "PARTIALLY_PAID"];
const FILTERS = [["all", "All"], ["unpaid", "Unpaid"], ["overdue", "Overdue"], ["paid", "Paid"], ["draft", "Drafts"]];
const SUGGEST = ["What's coming in this week?", "Nudge everyone more than 3 days late", "Chase Harbor Yoga", "Pay Mia 20% of the Lumen retainer", "Cancel invoice S2C-SAMPLE-03"];
const SERIES = [["collected", "Collected", "#1f9d55"], ["expected", "Expected (not due yet)", "#0070e0"], ["overdue", "Overdue", "#d64545"], ["drafts", "Drafts (not sent)", "#b8c0d0"]];
const due = (i) => (i.amount_due ?? (["PAID", "CANCELLED", "REFUNDED"].includes(i.status) ? 0 : i.amount));
const lateDays = (i) => {
  if (!UNPAID.includes(i.status)) return 0;
  const d = Math.floor((Date.now() - new Date(i.due_date + "T23:59:59").getTime()) / 864e5);
  return Math.max(i.days_overdue || 0, d, 0);
};
const clientOf = (i) => i.client_name || i.client_email;
const passes = (i) => ({ all: true, unpaid: UNPAID.includes(i.status), overdue: lateDays(i) > 0 && UNPAID.includes(i.status),
  paid: i.status === "PAID", draft: i.status === "DRAFT" })[P.filter]
  && (!P.client || clientOf(i) === P.client) && (!P.week || P.week.ids.has(i.id));
const debounce = (fn, ms) => { let h; return (...a) => { clearTimeout(h); h = setTimeout(() => fn(...a), ms); }; };

// Per-currency sums of f(i) over rows matching pred: { USD: 1200, EUR: 900 }
const byCur = (rows, pred, f = due) => rows.filter(pred).reduce((m, i) => { const c = i.currency || "USD"; m[c] = (m[c] || 0) + f(i); return m; }, {});
function kpis() {
  const r = P.rows, amt = (i) => i.amount;
  const late = r.filter((i) => UNPAID.includes(i.status) && lateDays(i) > 0);
  const clients = new Set(r.map(clientOf)).size;
  const pend = Object.values(P.pending), payouts = pend.filter((a) => a.tool === "create_payout").length, waiting = pend.length - payouts;
  const tiles = [
    ["Collected", moneyMulti(byCur(r, (i) => i.status === "PAID", amt)), "ok", `${r.filter((i) => i.status === "PAID").length} paid`],
    ["Awaiting payment", moneyMulti(byCur(r, (i) => UNPAID.includes(i.status))), "pp", `${r.filter((i) => UNPAID.includes(i.status)).length} open`],
    ["Overdue", moneyMulti(byCur(late, () => true)), "bad", late.length ? `${late.length} late · oldest ${Math.max(...late.map(lateDays))}d` : "nobody is late 🎉"],
    ["Drafts to approve", moneyMulti(byCur(r, (i) => i.status === "DRAFT")), "", `${clients} client${clients === 1 ? "" : "s"}${waiting ? ` · ${waiting} agent nudge${waiting === 1 ? "" : "s"} waiting` : ""}${payouts ? ` · ${payouts} payout${payouts === 1 ? "" : "s"} to approve` : ""}`],
  ];
  $("#kpis").innerHTML = tiles.map(([k, m, c, sub]) => `<div class="kpi ${c}" ${m.approx ? `title="${esc(m.parts)} (exact, per currency)"` : ""}><small>${k}</small><b>${esc(m.text)}</b>${m.approx ? `<em class="curparts">${esc(m.parts)}</em>` : ""}<span>${sub}</span></div>`).join("");
  const mixed = new Set(r.filter((i) => i.status !== "CANCELLED" && i.status !== "REFUNDED").map((i) => i.currency || "USD")).size > 1;
  $("#fx-note").classList.toggle("hidden", !mixed);
  if (mixed) $("#fx-note").innerHTML = `💱 Several currencies in this pipeline. Figures marked ≈ are the USD equivalent at the <a href="${esc(FX.url || "#")}" target="_blank" rel="noopener">${esc(FX.source || "ECB reference rates")}</a> of ${esc(FX.date || "?")} (a fixed snapshot for orientation, not PayPal's conversion rate). Each invoice is billed and paid in its own currency; the per-currency amounts are exact.`;
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
    splitHtml(i, b),
  ].join("");
}
// Collaborator split (PayPal Payouts): set it on any open or paid milestone; once paid out, show the payout status.
function splitHtml(i, b) {
  if (["CANCELLED", "REFUNDED"].includes(i.status)) return "";
  if (i.payout_batch_id) return `<span class="chip payout" title="PayPal Payouts batch ${esc(i.payout_batch_id)}">💸 ${esc(i.split_label)} · ${esc(i.payout_status || "sent")}</span>`
    + (i.provider !== "mock" ? b("payoutrefresh", "↻") : "");
  if (i.split_email) return b("split", `👥 ${esc(i.split_label)} · ${money(i.split_amount, i.currency)}`);
  if (!["PAID", "DRAFT"].includes(i.status)) return "";  // open rows already carry 2-3 buttons; their split lives in step 5
  return `<button class="ghost tiny" data-a="split" data-id="${esc(i.id)}" title="Split with a collaborator (PayPal Payouts after the client pays)" aria-label="Split with a collaborator">👥</button>`;
}
// Collections-agent column: the agent's queued proposal for this invoice, inline, with Approve / Reject.
function agentCellHtml(i) {
  const a = P.pending[i.id];
  if (a && a.tool === "create_payout") {
    const byAgent = a.params.source === "agent";
    return `<div class="nudge payout" title="${esc(a.reason)}"><div class="nudge-txt"><b>💸 Pay ${esc(a.params.label)} ${money(a.params.amount, i.currency)}</b><small>Client paid · PayPal Payouts to ${esc(a.params.receiver)}${byAgent ? " · 🤖 proposed by the agent" : " · your split"}</small></div>
      <button class="ghost tiny" data-pa="reject" data-pid="${esc(a.id)}" aria-label="Reject">✕</button><button class="pp tiny" data-pa="approve" data-pid="${esc(a.id)}">Approve</button></div>`;
  }
  if (a) {
    const cancel = a.tool === "cancel_sent_invoice";
    const head = cancel ? "🛑 Cancel proposed" : `💬 ${a.params?.subject || "Reminder drafted"}`;
    const note = a.params?.note || a.reason || a.summary;
    return `<div class="nudge ${cancel ? "cancel" : ""}" title="${esc(note)}"><div class="nudge-txt"><b>${esc(head)}</b><small>${esc(note)}</small></div>
      <button class="ghost tiny" data-pa="reject" data-pid="${esc(a.id)}" aria-label="Reject">✕</button><button class="pp tiny" data-pa="approve" data-pid="${esc(a.id)}">Approve</button></div>`;
  }
  if (UNPAID.includes(i.status)) return `<button class="ghost tiny agentbtn" data-nudge="${esc(i.number)}">🤖 Draft a nudge</button>`;
  if (i.status === "PAID" && i.payout_batch_id) return `<span class="muted small">✓ paid · 💸 ${esc(i.split_label)} paid out (${esc(i.payout_status || "sent")})</span>`;
  return `<span class="muted small">${i.status === "PAID" ? "✓ nothing to chase" : i.status === "DRAFT" ? "send it first" : ""}</span>`;
}
const pill = (s) => `<span class="pill ${esc(s)}">${esc(s.replace("_", " "))}</span>`;
const short = (e) => (e || "").replace("INVOICING.INVOICE.", "");

function columns() {
  return [
    { field: "client_name", headerName: "Client", minWidth: 175, flex: 1.2, filter: true, pinned: "left",
      cellRenderer: (p) => p.node.rowPinned ? `<b>${esc(p.value)}</b>` :
        `<div class="cell2"><b>${esc(p.value || p.data.client_email)}</b><small>${esc(p.data.project || "")}${p.data.sample ? ' <span class="tag">sample</span>' : ""}</small></div>` },
    { field: "project", headerName: "Project", hide: true },
    { field: "number", headerName: "Invoice · milestone", minWidth: 150, flex: 1, filter: true,
      cellRenderer: (p) => (p.node.rowPinned ? "" : `<div class="cell2"><b>${esc(p.value)}</b><small>${esc(p.data.milestone || "")}</small></div>`) },
    { field: "milestone", headerName: "Milestone", hide: true },
    { field: "amount", headerName: "Amount", width: 118, type: "rightAligned", valueFormatter: (p) => money(p.value || 0, p.data?.currency),
      cellRenderer: (p) => { const d = p.node.rowPinned ? p.data.due : due(p.data), c = p.data?.currency;
        if (p.node.rowPinned && p.data.approx) return `<div class="cell2 r" title="${esc(p.data.parts)}"><b>${esc(p.data.amountText)}</b><small>${esc(p.data.dueText)} due</small></div>`;
        return `<div class="cell2 r"><b>${money(p.value || 0, c)}</b><small>${p.node.rowPinned ? `${money(d, c)} due` : p.data.status === "DRAFT" ? "not sent" : d ? `${money(d, c)} due` : "settled"}</small></div>`; } },
    { field: "currency", headerName: "Currency", width: 92, filter: true, hide: true },
    { colId: "due", headerName: "Due now", hide: true, valueGetter: (p) => p.node.rowPinned ? p.data.due : due(p.data) },
    { field: "due_date", headerName: "Due date", width: 108, sort: "asc" },
    { colId: "late", headerName: "Days late", width: 86, type: "rightAligned", valueGetter: (p) => p.node.rowPinned ? null : lateDays(p.data),
      cellClassRules: { "late-mild": (p) => p.value > 0 && p.value <= 7, "late-bad": (p) => p.value > 7 }, valueFormatter: (p) => (p.value ? `${p.value}d` : "") },
    { field: "status", headerName: "Status", width: 132, filter: true, cellRenderer: (p) => (p.node.rowPinned ? "" : pill(p.value)) },
    { colId: "agent", headerName: "Collections agent", minWidth: 270, flex: 1.6, sortable: false,
      valueGetter: (p) => (p.node.rowPinned ? "" : P.pending[p.data.id] ? "proposal waiting" : ""),
      cellRenderer: (p) => (p.node.rowPinned ? "" : agentCellHtml(p.data)) },
    { field: "last_event", headerName: "Last PayPal event", width: 132, valueFormatter: (p) => short(p.value) },
    { colId: "actions", headerName: "Actions", minWidth: 235, flex: 0.8, sortable: false, pinned: "right", cellRenderer: (p) => (p.node.rowPinned ? "" : `<div class="acts left">${actionsHtml(p.data)}</div>`) },
  ];
}
// Totals row: exact when the visible rows share a currency; with several, "≈ $X" (USD at FX.date) + the exact breakdown.
function totals(rows) {
  const label = `Total · ${rows.length} invoice${rows.length === 1 ? "" : "s"}`;
  const a = moneyMulti(byCur(rows, () => true, (i) => i.amount)), d = moneyMulti(byCur(rows, () => true));
  const curs = Object.keys(byCur(rows, () => true, () => 1));
  if (curs.length <= 1) return [{ client_name: label, amount: rows.reduce((s, i) => s + i.amount, 0), due: rows.reduce((s, i) => s + due(i), 0), currency: curs[0] || "USD" }];
  // amount/due carry the USD equivalents so AG Grid sees a changed value and re-renders the pinned cell.
  return [{ client_name: `${label} · ${curs.length} currencies`, approx: true, amount: a.usd, due: d.usd, amountText: a.text, dueText: d.text, parts: `billed ${a.parts}; due ${d.parts}`, currency: "USD" }];
}
const theme = () => agGrid.themeQuartz.withParams({ accentColor: "#0070e0", fontFamily: "inherit", fontSize: 13, headerBackgroundColor: "#f6f8fb",
  headerTextColor: "#5b6478", borderColor: "#e7eaf0", rowHoverColor: "#f4f8ff", wrapperBorderRadius: 12, spacing: 7, selectedRowBackgroundColor: "#eaf3ff" });

function renderGrid() {
  const el = $("#grid");
  if (window.agGrid) {
    if (!P.grid) {
      P.grid = agGrid.createGrid(el, {
        theme: theme(), columnDefs: columns(), rowData: [], getRowId: (p) => p.data.id, animateRows: true, rowHeight: 54,
        defaultColDef: { sortable: true, resizable: true }, includeHiddenColumnsInQuickFilter: true,
        isExternalFilterPresent: () => P.filter !== "all" || !!P.client || !!P.week, doesExternalFilterPass: (n) => passes(n.data),
        rowClassRules: { "row-late": (p) => !p.node.rowPinned && lateDays(p.data) > 0, "row-paid": (p) => p.data?.status === "PAID",
          "row-nudge": (p) => !p.node.rowPinned && !!P.pending[p.data?.id] },
        onFilterChanged: gridFiltered, onGridSizeChanged: responsive,
        overlayNoRowsTemplate: "<span class='muted'>No invoices match. Draft some in step 5, click “Load sample clients”, or clear the filters.</span>",
      });
      $("#grid-engine").textContent = `AG Grid Community ${agGrid.VERSION || "36.2.0"}`;
    }
    P.grid.setGridOption("rowData", P.rows);
    P.grid.redrawRows();  // the agent column depends on P.pending, not only on row data
    P.grid.onFilterChanged();
  } else {  // CDN unreachable: same columns as a plain table
    $("#grid-engine").textContent = "plain table (AG Grid CDN unreachable)";
    const rows = P.rows.filter(passes).filter((i) => !P.q || JSON.stringify(i).toLowerCase().includes(P.q));
    el.classList.add("plain");
    el.innerHTML = `<table><thead><tr><th>Client</th><th>Invoice</th><th>Milestone</th><th class="num">Amount</th><th>Due date</th><th class="num">Late</th><th>Status</th><th>Collections agent</th><th>Actions</th></tr></thead><tbody>${rows.map((i) =>
      `<tr><td><b>${esc(clientOf(i))}</b><br><small class="muted">${esc(i.project)}</small></td><td>${esc(i.number)}</td><td>${esc(i.milestone)}</td><td class="num">${money(i.amount, i.currency)}</td><td>${i.due_date}</td><td class="num">${lateDays(i) || ""}</td><td>${pill(i.status)}</td><td>${agentCellHtml(i)}</td><td><div class="acts left">${actionsHtml(i)}</div></td></tr>`).join("")}</tbody></table>`;
    chips(); loadCash(P.rows.filter(passes).map((i) => i.id));
  }
}
function visible() { const out = []; P.grid?.forEachNodeAfterFilterAndSort((n) => n.data && out.push(n.data)); return out; }

// Any filter change except a chart click drops the week filter (its row ids came from the previous filter state),
// then re-asks the server for the cash-flow series of exactly the rows the grid now shows. AG Grid fires
// filterChanged asynchronously, so changes are told apart by a signature of the non-week filters.
const sig = () => JSON.stringify([P.filter, P.client, P.q, P.grid ? P.grid.getFilterModel() : null, P.rows.length]);
function gridFiltered() {
  if (!P.grid) return;
  if (P.week && P.week.sig !== sig()) { P.week = null; P.grid.onFilterChanged(); return; }
  P.grid.setGridOption("pinnedBottomRowData", totals(visible()));
  chips();
  if (P.week) drawChart(); else loadCashSoon();
}
function refilter() { if (P.grid) P.grid.onFilterChanged(); else renderGrid(); }
function setWeek(w) {
  P.week = w ? { ...w, sig: sig() } : null;
  refilter();
}
function chips() {
  const c = [];
  if (P.client) c.push(`<button class="fchip" data-clear="client">Client: ${esc(P.client)} ✕</button>`);
  if (P.week) c.push(`<button class="fchip" data-clear="week">${esc(P.week.text)} ✕</button>`);
  $("#active-filters").innerHTML = c.join("");
  P.board?.redrawRows();
}

// ---------- AG Charts Community: weekly cash flow (collected by paid date; expected / overdue / drafts by due date)
async function loadCash(ids) {
  try { P.cash = await api("/api/pipeline/cashflow", { invoice_ids: ids }); } catch { return; }
  drawChart();
}
const loadCashSoon = debounce(() => loadCash(P.grid ? visible().map((i) => i.id) : P.rows.filter(passes).map((i) => i.id)), 120);
function drawChart() {
  const el = $("#cashchart");
  if (!window.agCharts || !P.cash) { if (!window.agCharts) el.innerHTML = `<p class="muted small">AG Charts CDN unreachable: chart hidden; the grid still works.</p>`; return; }
  const data = P.cash.weeks.map((w) => ({ ...w, label: w.current ? `${w.label} · now` : w.label }));
  const sel = P.week ? P.week.week : "";
  const opts = {
    container: el, data, background: { fill: "#ffffff" }, padding: { top: 6, right: 8, bottom: 4, left: 4 },
    series: SERIES.map(([k, name, fill]) => ({ id: k, type: "bar", xKey: "label", yKey: k, yName: name, stacked: true, fill, stroke: fill, cornerRadius: 3,
      visible: !P.hidden.has(k),
      itemStyler: (p) => (sel && p.datum.week !== sel ? { fillOpacity: 0.28, strokeOpacity: 0.28 } : { fillOpacity: 1, strokeOpacity: 1 }),
      tooltip: { renderer: (p) => ({ heading: `Week of ${p.datum.week}`, title: name,
        data: [{ label: P.cash.mixed ? "≈ USD" : "Amount", value: money(p.datum[k], P.cash.currency) },
          ...(P.cash.mixed ? Object.entries(p.datum.native?.[k] || {}).map(([c, v]) => ({ label: c, value: money(v, c) })) : []),
          { label: "Invoices", value: String(p.datum.ids[k].length) }] }) } })),
    axes: { x: { type: "category", position: "bottom", label: { fontSize: 11, color: "#5b6478" } },
      y: { type: "number", position: "left", title: P.cash.mixed ? { text: `≈ USD (ECB ref. rates ${P.cash.fx?.date || ""})`, fontSize: 10.5, color: "#5b6478" } : undefined,
        label: { fontSize: 11, color: "#5b6478", formatter: (p) => { const sym = money(0, P.cash.currency).replace(/[\d.,\s]/g, ""); return p.value >= 1000 ? `${sym}${p.value / 1000}k` : `${sym}${p.value}`; } },
        gridLine: { style: [{ stroke: "#eef1f6" }] } } },
    legend: { position: "bottom", item: { label: { fontSize: 11.5, color: "#3a4256" } } },
    listeners: { seriesVisibilityChange: (e) => { if (e.visible) P.hidden.delete(e.seriesId); else P.hidden.add(e.seriesId); },
      seriesNodeClick: (e) => {
      const w = e.datum, ids = w.ids[e.yKey] || [];
      if (!ids.length) return;
      const name = (SERIES.find((s) => s[0] === e.yKey) || [])[1] || e.yKey;
      if (P.week && P.week.week === w.week && P.week.series === e.yKey) return setWeek(null);
      setWeek({ week: w.week, series: e.yKey, ids: new Set(ids), text: `${name} · week of ${w.week}` });
    } },
  };
  if (P.chart) P.chart.update(opts); else P.chart = agCharts.AgCharts.create(opts);
  $("#chart-engine").textContent = "AG Charts Community" + (agCharts.VERSION ? " " + agCharts.VERSION : "");
  const t = P.cash.totals, sum = t.collected + t.expected + t.overdue, cur = P.cash.currency, ap = P.cash.mixed ? "≈ " : "";
  $("#cash-note").textContent = data.length ? `${ap}${money(t.collected, cur)} in · ${ap}${money(t.expected, cur)} expected · ${ap}${money(t.overdue, cur)} late${P.hidden.has("drafts") && t.drafts ? ` · ${money(t.drafts, cur)} drafts hidden (legend)` : ""}${P.cash.mixed ? " · mixed currencies, USD equivalent" : cur !== "USD" ? ` · all in ${cur}` : ""}`
    : "Nothing to chart for this filter";
  $("#cash-note").title = P.cash.mixed ? Object.entries(P.cash.totals_by_currency).map(([c, v]) => `${c}: ${money(v.collected, c)} in, ${money(v.expected, c)} expected, ${money(v.overdue, c)} late`).join("\n") : "";
  el.classList.toggle("empty", !sum && !t.drafts);
}

// ---------- second AG Grid: friendly "who still owes what" scoreboard (Nudge-bot style), click a client to filter
const MOOD = { "all square": "m-ok", "on track": "m-pp", "gentle nudge": "m-mild", "friendly follow-up": "m-warn", "time for a call": "m-bad" };
function boardColumns() {
  return [
    { field: "client", headerName: "Client", minWidth: 150, flex: 1.3, cellRenderer: (p) =>
      `<div class="cell2"><b>${esc(p.value)}${p.data.currency && p.data.currency !== "USD" ? ` <span class="tag cur">${esc(p.data.currency)}</span>` : ""}</b><small><span class="mood ${MOOD[p.data.mood] || ""}">${esc(p.data.mood)}</span></small></div>` },
    { field: "paid_pct", headerName: "Paid so far", minWidth: 120, flex: 1, cellRenderer: (p) =>
      `<div class="paycell"><div class="paybar"><i style="width:${Math.min(100, p.value)}%"></i></div><small>${money(p.data.collected, p.data.currency)} of ${money(p.data.billed, p.data.currency)}</small></div>` },
    { field: "owed", headerName: "Still owes", width: 92, type: "rightAligned", valueFormatter: (p) => money(p.value, p.data.currency),
      comparator: (a, b, na, nb) => (na.data.owed_usd ?? a) - (nb.data.owed_usd ?? b) },
    { field: "overdue", headerName: "Late", width: 96, type: "rightAligned", comparator: (a, b, na, nb) => (na.data.overdue_usd ?? a) - (nb.data.overdue_usd ?? b),
      cellRenderer: (p) => p.value
      ? `<div class="cell2 r"><b class="late-bad">${money(p.value, p.data.currency)}</b><small>oldest ${p.data.oldest_late}d</small></div>`
      : `<div class="cell2 r"><b class="muted">—</b><small>${p.data.next_due ? "due " + esc(p.data.next_due.slice(5)) : ""}</small></div>` },
    { colId: "nudge", headerName: "Agent", width: 112, sortable: false, cellRenderer: (p) => p.data.pending_nudges
      ? `<span class="chip waiting" title="Agent proposals waiting for your Approve click in the grid">${p.data.pending_nudges} waiting</span>`
      : p.data.owed > 0 ? `<button class="ghost tiny agentbtn" data-chase="${esc(p.data.client)}">🤖 Nudge</button>` : "🎉" },
  ];
}
async function loadBoard() {
  let rows;
  try { rows = await api("/api/pipeline/clients"); } catch { return; }
  const el = $("#board");
  if (!window.agGrid) {
    el.innerHTML = `<table><tbody>${rows.map((c) => `<tr><td><b>${esc(c.client)}</b> <span class="mood ${MOOD[c.mood] || ""}">${esc(c.mood)}</span></td><td class="num">${money(c.owed, c.currency)}</td></tr>`).join("")}</tbody></table>`;
    return;
  }
  if (!P.board) {
    P.board = agGrid.createGrid(el, {
      theme: theme(), columnDefs: boardColumns(), rowData: [], getRowId: (p) => p.data.key || p.data.client, rowHeight: 50, headerHeight: 36,
      defaultColDef: { sortable: true, resizable: true }, domLayout: "autoHeight", suppressCellFocus: true,
      onGridSizeChanged: (e) => e.api.setColumnsVisible(["paid_pct"], e.clientWidth > 560),
      rowClassRules: { "row-picked": (p) => p.data.client === P.client },
      onRowClicked: (e) => {
        if (e.event?.target?.closest("button")) return;
        P.client = P.client === e.data.client ? "" : e.data.client;
        refilter();
      },
      overlayNoRowsTemplate: "<span class='muted'>No clients yet.</span>",
    });
  }
  P.board.setGridOption("domLayout", rows.length > 6 ? "normal" : "autoHeight");
  el.classList.toggle("tall", rows.length > 6);
  P.board.setGridOption("rowData", rows);
}

// ---------- saved views (localStorage) + CSV export
const PRESETS = [
  { name: "All invoices", filter: "all", sort: [["due_date", "asc"]] },
  { name: "Chase list · latest first", filter: "overdue", sort: [["late", "desc"]] },
  { name: "Cash coming in · by due date", filter: "unpaid", sort: [["due_date", "asc"]] },
  { name: "Drafts to approve", filter: "draft", sort: [["due_date", "asc"]] },
];
const VKEY = "s2c.views.v1";
const myViews = () => { try { return JSON.parse(localStorage.getItem(VKEY) || "[]"); } catch { return []; } };
function renderViews(selected = "") {
  const mine = myViews();
  $("#views").innerHTML = `<option value="">Views…</option><optgroup label="Built-in">${PRESETS.map((v, n) => `<option value="p${n}">${esc(v.name)}</option>`).join("")}</optgroup>`
    + (mine.length ? `<optgroup label="Saved by you">${mine.map((v, n) => `<option value="m${n}">${esc(v.name)}</option>`).join("")}</optgroup>` : "");
  $("#views").value = selected;
  $("#view-del").classList.toggle("hidden", !selected.startsWith("m"));
}
function setFilter(f) {
  P.filter = f; document.querySelectorAll("#pipe-filter button").forEach((b) => b.classList.toggle("on", b.dataset.f === f));
}
function applyView(v) {
  setFilter(v.filter || "all"); P.client = v.client || ""; P.q = (v.q || "").toLowerCase(); $("#qf").value = v.q || "";
  if (P.grid) {
    if (v.columns) P.grid.applyColumnState({ state: v.columns, applyOrder: true });
    else P.grid.applyColumnState({ state: (v.sort || []).map(([colId, sort]) => ({ colId, sort })), defaultState: { sort: null } });
    P.grid.setGridOption("quickFilterText", v.q || "");
  }
  refilter();
}
function saveView(name) {
  const v = { name, filter: P.filter, client: P.client, q: $("#qf").value, columns: P.grid ? P.grid.getColumnState() : null };
  const mine = myViews().filter((x) => x.name !== name).concat([v]).slice(-12);
  localStorage.setItem(VKEY, JSON.stringify(mine));
  renderViews("m" + (mine.length - 1)); toast(`Saved view “${name}” (in this browser)`);
}
function exportCsv() {
  if (!P.grid) return toast("CSV export needs AG Grid (CDN unreachable)");
  const raw = { amount: 1, due: 1, late: 1 };
  P.grid.exportDataAsCsv({
    fileName: `scope2cash-pipeline-${new Date().toISOString().slice(0, 10)}.csv`, skipPinnedBottom: true,
    columnKeys: ["client_name", "project", "number", "milestone", "currency", "amount", "due", "due_date", "late", "status", "agent", "last_event"],
    processCellCallback: (p) => (raw[p.column.getColId()] ? (p.value ?? "") : p.column.getColId() === "last_event" ? short(p.value) : p.value ?? ""),
  });
  toast(`Exported ${visible().length} rows (current filter and sort) to CSV`);
}

// Narrow screens: drop secondary columns and unpin, so the grid stays usable on a phone or a split screen.
let narrowNow = null;
function responsive(e) {
  const w = e?.clientWidth ?? $("#grid").clientWidth, narrow = w < 820;
  if (!P.grid || narrow === narrowNow) return;
  narrowNow = narrow;
  P.grid.setColumnsVisible(["last_event"], !narrow);
  P.grid.applyColumnState({ state: [{ colId: "client_name", pinned: narrow ? null : "left" }, { colId: "actions", pinned: narrow ? null : "right" }] });
}

async function loadPipeline() {
  await fxReady;  // rates for "≈" totals (app.js)
  const [rows, pend] = await Promise.all([api("/api/invoices"), api("/api/agent/actions?status=pending").catch(() => [])]);
  P.rows = rows;
  const open = new Set(rows.filter((i) => UNPAID.includes(i.status)).map((i) => i.id));
  P.pending = {};
  const paid = new Set(rows.filter((i) => i.status === "PAID").map((i) => i.id));
  pend.slice().reverse().forEach((a) => {  // newest wins; payouts belong to paid rows, nudges to open ones
    if (a.tool === "create_payout" ? paid.has(a.invoice_id) : open.has(a.invoice_id)) P.pending[a.invoice_id] = a;
  });
  kpis(); renderGrid(); loadEvents(); loadBoard();
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
    ${r.proposals.length ? `<h5 class="muted" style="margin:12px 0 6px">WAITING FOR YOUR APPROVAL <span class="small">(also shown in the grid's Collections agent column)</span></h5>` : ""}
    <div id="props">${r.proposals.map(propHtml).join("")}</div>`;
}
const propHtml = (p) => `<div class="prop ${p.status}" data-pid="${esc(p.id)}"><div><b>${esc(p.summary)}</b><br>
  <span class="muted small"><code>${esc(p.tool)}</code>${p.reason ? " · " + esc(p.reason) : ""}</span></div>
  <div class="acts">${p.status === "pending" ? `<button class="ghost tiny" data-pa="reject" data-pid="${esc(p.id)}">Reject</button><button class="pp tiny" data-pa="approve" data-pid="${esc(p.id)}">Approve</button>` : `<span class="pill ${p.status === "executed" ? "PAID" : "CANCELLED"}">${esc(p.status)}</span>`}</div></div>`;
// One handler for Approve / Reject wherever the proposal is shown (agent panel or grid cell): each click is the
// human approval for exactly one queued PayPal Agent Toolkit call.
async function decide(btn) {
  const pid = btn.dataset.pid, verb = btn.dataset.pa;
  await busy(btn, async () => {
    const r = await api(`/api/agent/actions/${pid}/${verb}`, {});
    const card = document.querySelector(`#props .prop[data-pid="${CSS.escape(pid)}"]`);
    if (card) card.outerHTML = propHtml(r.action);
    if (r.action.status === "executed") {
      toast((r.invoice?.provider === "mock" ? "Mock: " : "PayPal: ") + r.action.summary.split(" · ")[0] + " — done");
      const payout = r.action.tool === "create_payout";
      addTrace([{ step: payout ? "Approved collaborator payout" : "Approved agent action", model: payout ? "paypal-payouts" : "paypal-agent-toolkit", tier: "tool",
        mode: r.invoice?.provider === "mock" ? "mock" : "live", ms: 0, note: payout ? r.action.result : r.action.tool }]);
    } else if (r.action.status === "failed") toast("Blocked: " + r.action.result);
    else toast("Rejected: nothing was sent");
  });
  await loadPipeline();
}
async function ask(text, btn = $("#ask-go")) {
  await busy(btn, async () => {
    const r = await api("/api/agent/collections", { instruction: text });
    addTrace(r.trace); renderAgent(r); await loadPipeline();
    if (r.proposals.length) toast(`${r.proposals.length} proposal${r.proposals.length === 1 ? "" : "s"} waiting for your approval (grid + panel)`);
  });
}

// ---------- wiring
$("#pipe-filter").innerHTML = FILTERS.map(([k, l]) => `<button data-f="${k}" class="${k === "all" ? "on" : ""}">${l}</button>`).join("");
$("#pipe-filter").onclick = (e) => { const f = e.target.dataset.f; if (!f) return; setFilter(f); refilter(); };
$("#qf").oninput = (e) => { P.q = e.target.value.toLowerCase(); if (P.grid) P.grid.setGridOption("quickFilterText", e.target.value); else renderGrid(); };
$("#active-filters").onclick = (e) => {
  const k = e.target.closest("[data-clear]")?.dataset.clear; if (!k) return;
  if (k === "client") { P.client = ""; refilter(); } else setWeek(null);
};
$("#grid").addEventListener("click", (e) => {
  const pa = e.target.closest("button[data-pa]"); if (pa) return decide(pa);
  const n = e.target.closest("button[data-nudge]");
  if (n) { $("#ask").value = `Draft a friendly reminder for invoice ${n.dataset.nudge}`; return ask($("#ask").value, n); }
  const b = e.target.closest("button[data-a]"); if (!b) return;
  busy(b, () => act(b.dataset.a, b.dataset.id));
});
$("#board").addEventListener("click", (e) => {
  const b = e.target.closest("button[data-chase]"); if (!b) return;
  $("#ask").value = `Chase ${b.dataset.chase}`; ask($("#ask").value, b);
});
$("#agent-out").addEventListener("click", (e) => { const pa = e.target.closest("button[data-pa]"); if (pa) decide(pa); });
$("#views").onchange = (e) => {
  const v = e.target.value; if (!v) return;
  applyView(v[0] === "p" ? PRESETS[+v.slice(1)] : myViews()[+v.slice(1)]);
  $("#view-del").classList.toggle("hidden", v[0] !== "m");
};
$("#view-save").onclick = () => { $("#view-form").classList.toggle("hidden"); $("#view-name").focus(); };
$("#view-form").onsubmit = (e) => { e.preventDefault(); const n = $("#view-name").value.trim().slice(0, 40); if (!n) return; saveView(n); $("#view-name").value = ""; $("#view-form").classList.add("hidden"); };
$("#view-del").onclick = () => {
  const v = $("#views").value; if (!v.startsWith("m")) return;
  const mine = myViews(); const [gone] = mine.splice(+v.slice(1), 1);
  localStorage.setItem(VKEY, JSON.stringify(mine)); renderViews(); toast(`Deleted view “${gone?.name || ""}”`);
};
$("#csv").onclick = exportCsv;
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
renderViews();
loadPipeline();
