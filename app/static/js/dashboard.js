/* Dashboard: card view and table view (#10), live via the shared poller. */
(() => {
  const root = document.getElementById("services");
  const search = document.getElementById("search");
  const entries = new Map();          // id -> { card, row, svc }
  let ids = "";
  let view = SM.LS.get("sm.view", "cards");
  let statFilter = "all";

  /* ------------------------------------------------------------ helpers -- */
  const isRunning = (s) => ["running", "starting", "stopping", "external"].includes(s.state);
  const isProblem = (s) => s.state === "failed" || s.health?.state === "unhealthy";
  const matchesStat = (s) => ({
    all: true, running: isRunning(s), healthy: s.health?.state === "healthy",
    stopped: ["stopped", "exited"].includes(s.state), problems: isProblem(s),
  }[statFilter]);

  function controlButtons(sm) {
    const sz = sm ? "btn-sm" : "";
    return `
      <button class="btn ${sz} btn-success" data-op="start" data-min-role="operator" title="Start"><svg><use href="#i-play"/></svg>${sm ? "" : "Start"}</button>
      <button class="btn ${sz} btn-danger" data-op="stop" data-min-role="operator" title="Stop"><svg><use href="#i-stop"/></svg>${sm ? "" : "Stop"}</button>
      <button class="btn ${sz} btn-secondary" data-op="restart" data-min-role="operator" title="Restart"><svg><use href="#i-restart"/></svg></button>`;
  }

  async function onClick(ev, sid) {
    const btn = ev.target.closest("button[data-op], button[data-action]");
    if (!btn) return;
    const cur = entries.get(sid).svc;
    if (btn.dataset.op) await SM.serviceOp(cur, btn.dataset.op, btn);
    else await SM.runAction(cur, cur.actions.find((a) => a.id === btn.dataset.action), btn);
  }

  /* -------------------------------------------------------------- cards -- */
  function makeCard(svc) {
    const el = document.createElement("article");
    el.className = "card";
    const actions = svc.actions.map((a) =>
      `<button class="btn btn-sm btn-outline btn-${SM.esc(a.style)}" data-action="${SM.esc(a.id)}" data-min-role="operator" title="${SM.esc(a.description || a.command)}">
         <svg><use href="#i-bolt"/></svg>${SM.esc(a.label)}</button>`).join("");
    el.innerHTML = `
      <div class="card-body">
        <div class="card-top">
          <div class="card-heading">
            <a class="card-title" href="/services/${SM.esc(svc.id)}">${SM.esc(svc.name)}</a>
            ${svc.critical ? `<span class="chip chip-danger" title="Controls physical equipment"><svg><use href="#i-warn"/></svg>Equipment</span>` : ""}
            <p class="card-desc">${SM.esc(svc.description)}</p>
          </div>
          <div class="card-badges"><span class="badge" data-f="badge"></span><span data-f="health"></span></div>
        </div>
        <div class="monitors" data-f="monitors"></div>
        <div class="card-error" data-f="error" hidden></div>
        <div class="card-res" data-f="res"></div>
        <div class="card-meta" data-f="meta"></div>
      </div>
      <div class="card-foot">
        ${controlButtons(true)}
        ${actions ? `<span class="sep"></span>${actions}` : ""}
        <span class="spacer"></span>
        <span data-f="links" class="links"></span>
        <a class="btn btn-sm btn-ghost" href="/services/${SM.esc(svc.id)}" title="Console and details"><svg><use href="#i-terminal"/></svg>Logs</a>
      </div>`;
    el.addEventListener("click", (ev) => onClick(ev, svc.id));
    return el;
  }

  function updateCard(el, s) {
    const f = (n) => el.querySelector(`[data-f="${n}"]`);
    SM.applyBadge(f("badge"), s);
    setHTML(f("health"), SM.healthHTML(s));
    setHTML(f("monitors"), s.monitors.map((m) => SM.monitorHTML(m, true)).join(""));
    const err = f("error");
    const showErr = s.state === "failed" && s.last_error;
    err.hidden = !showErr;
    if (showErr) { err.textContent = s.last_error; err.title = s.last_error; }

    const m = s.metrics;
    setHTML(f("res"), m ? `
      <div class="res-mini"><span>CPU <b>${SM.fmtCpu(m)}</b></span>${SM.sparkline(s.history?.cpu, { w: 110, h: 24, max: 100, cls: "spark-cpu" })}</div>
      <div class="res-mini"><span>Mem <b>${SM.fmtMem(m)}</b></span>${SM.sparkline(s.history?.mem, { w: 110, h: 24, cls: "spark-mem" })}</div>` : "");

    const meta = [];
    if (s.pid) meta.push(`<span>PID <b>${s.pid}</b></span>`);
    if (s.external_pids.length) meta.push(`<span>External PID <b>${s.external_pids.join(", ")}</b></span>`);
    if (s.uptime != null) meta.push(`<span>Uptime <b>${SM.duration(s.uptime)}</b></span>`);
    else if (s.stopped_at) meta.push(`<span>Stopped <b>${SM.time(s.stopped_at)}</b></span>`);
    meta.push(`<span class="${s.logs.errors ? "err" : ""}">Errors <b>${s.logs.errors}</b></span>`);
    meta.push(`<span class="${s.logs.warnings ? "warn" : ""}">Warnings <b>${s.logs.warnings}</b></span>`);
    if (s.crashes_today) meta.push(`<span class="err">Crashes today <b>${s.crashes_today}</b></span>`);
    if (s.restart_count) meta.push(`<span>Auto-restarts <b>${s.restart_count}</b></span>`);
    setHTML(f("meta"), meta.join(""));
    setHTML(f("links"), SM.linksHTML(s.links, true));
    updateButtons(el, s);
  }

  /* -------------------------------------------------------------- table -- */
  function makeRow(svc) {
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td class="t-name">
        <a href="/services/${SM.esc(svc.id)}">${SM.esc(svc.name)}</a>
        ${svc.critical ? `<svg class="crit-icon" title="Controls physical equipment"><use href="#i-warn"/></svg>` : ""}
        <div class="muted small">${SM.esc(svc.group)}</div></td>
      <td><span class="badge" data-f="badge"></span></td>
      <td data-f="health"></td>
      <td class="num" data-f="uptime"></td>
      <td class="num t-res" data-f="cpu"></td>
      <td class="num" data-f="mem"></td>
      <td class="num" data-f="errors"></td>
      <td data-f="links" class="links"></td>
      <td class="t-actions">${controlButtons(true)}
        <a class="btn btn-sm btn-ghost" href="/services/${SM.esc(svc.id)}" title="Console and details"><svg><use href="#i-terminal"/></svg></a></td>`;
    tr.addEventListener("click", (ev) => onClick(ev, svc.id));
    return tr;
  }

  function updateRow(tr, s) {
    const f = (n) => tr.querySelector(`[data-f="${n}"]`);
    SM.applyBadge(f("badge"), s);
    setHTML(f("health"), SM.healthHTML(s) || `<span class="muted">–</span>`);
    f("uptime").textContent = s.uptime != null ? SM.duration(s.uptime) : "–";
    setHTML(f("cpu"), s.metrics ? `<span>${SM.fmtCpu(s.metrics)}</span>${SM.sparkline(s.history?.cpu, { w: 60, h: 18, max: 100, cls: "spark-cpu" })}` : "–");
    f("mem").textContent = SM.fmtMem(s.metrics);
    setHTML(f("errors"), `<span class="${s.logs.errors ? "t-err" : "muted"}">${s.logs.errors}</span> / <span class="${s.logs.warnings ? "t-warn" : "muted"}">${s.logs.warnings}</span>`);
    setHTML(f("links"), SM.linksHTML(s.links, true));
    updateButtons(tr, s);
  }

  /* ------------------------------------------------------------ shared -- */
  function setHTML(el, html) { if (el._html !== html) { el.innerHTML = html; el._html = html; } }

  function updateButtons(el, s) {
    SM.setEnabled(el.querySelector('[data-op="start"]'), s.can_start);
    SM.setEnabled(el.querySelector('[data-op="stop"]'), s.can_stop);
    SM.setEnabled(el.querySelector('[data-op="restart"]'), s.can_restart);
    for (const a of s.actions) {
      const b = el.querySelector(`[data-action="${a.id}"]`);
      if (b) { b.classList.toggle("busy", a.running); SM.setEnabled(b, true); }
    }
  }

  function build(list) {
    entries.clear();
    root.innerHTML = "";
    if (!list.length) {
      root.innerHTML = `<div class="empty">No services configured. Add one to <code>config/services.yaml</code> and click Reload config.</div>`;
      return;
    }
    // cards, grouped
    const cardsWrap = document.createElement("div");
    cardsWrap.className = "view-cards";
    const groups = {};
    list.forEach((s) => (groups[s.group] ||= []).push(s));
    for (const [name, items] of Object.entries(groups)) {
      const g = document.createElement("section");
      g.className = "group";
      g.innerHTML = `<h2 class="group-title">${SM.esc(name)} <span class="count">${items.length}</span></h2><div class="cards"></div>`;
      items.forEach((s) => {
        const card = makeCard(s);
        g.querySelector(".cards").appendChild(card);
        entries.set(s.id, { card, svc: s });
      });
      cardsWrap.appendChild(g);
    }
    // table
    const tableWrap = document.createElement("div");
    tableWrap.className = "view-table panel table-panel";
    tableWrap.innerHTML = `<table class="table svc-table"><thead><tr>
      <th>Service</th><th>Status</th><th>Health</th><th class="num">Uptime</th><th class="num">CPU</th>
      <th class="num">Memory</th><th class="num" title="Errors / warnings">Err / Warn</th><th>Open</th><th>Controls</th>
      </tr></thead><tbody></tbody></table>`;
    const tbody = tableWrap.querySelector("tbody");
    list.forEach((s) => { const row = makeRow(s); tbody.appendChild(row); entries.get(s.id).row = row; });

    root.append(cardsWrap, tableWrap);
    SM.applyRoles(root);
    applyView();
  }

  function applyView() {
    root.dataset.view = view;
    document.querySelectorAll(".seg-btn").forEach((b) => b.classList.toggle("active", b.dataset.view === view));
  }

  function applyFilter() {
    const q = search.value.trim().toLowerCase();
    entries.forEach(({ card, row, svc }) => {
      const text = `${svc.name} ${svc.id} ${svc.group} ${svc.description}`.toLowerCase();
      const show = (!q || text.includes(q)) && matchesStat(svc);
      card.hidden = !show;
      if (row) row.hidden = !show;
    });
    root.querySelectorAll(".group").forEach((g) => (g.hidden = ![...g.querySelectorAll(".card")].some((c) => !c.hidden)));
    document.querySelectorAll(".stat").forEach((b) => b.classList.toggle("active", b.dataset.filter === statFilter && statFilter !== "all"));
  }

  function stats(list) {
    const set = (k, v) => (document.querySelector(`[data-stat="${k}"]`).textContent = v);
    set("total", list.length);
    set("running", list.filter(isRunning).length);
    set("healthy", list.filter((s) => s.health?.state === "healthy").length);
    set("stopped", list.filter((s) => ["stopped", "exited"].includes(s.state)).length);
    set("problems", list.filter(isProblem).length);
  }

  SM.services.subscribe((list) => {
    const newIds = list.map((s) => s.id).join(",");
    if (newIds !== ids) { ids = newIds; build(list); }
    for (const s of list) {
      const e = entries.get(s.id);
      if (!e) continue;
      e.svc = s;
      updateCard(e.card, s);
      updateRow(e.row, s);
    }
    stats(list);
    applyFilter();
  }, (e) => { if (!ids) root.innerHTML = `<div class="empty">${SM.esc(e.message)}</div>`; });

  /* ------------------------------------------------------------- events -- */
  search.addEventListener("input", applyFilter);
  document.querySelectorAll(".seg-btn").forEach((b) => b.addEventListener("click", () => {
    view = b.dataset.view; SM.LS.set("sm.view", view); applyView();
  }));
  document.querySelectorAll(".stat").forEach((b) => b.addEventListener("click", () => {
    statFilter = statFilter === b.dataset.filter ? "all" : b.dataset.filter;
    applyFilter();
  }));

  // (#4) reload configuration
  const reloadBtn = document.getElementById("reload-config");
  reloadBtn?.addEventListener("click", async () => {
    const ok = await SM.confirmDialog("Reload configuration?",
      "services.yaml will be read again. New services are added, removed ones are stopped, and running services whose command or environment changed are restarted. Unchanged services are not touched.",
      "Reload");
    if (!ok) return;
    reloadBtn.classList.add("busy");
    try {
      const r = await SM.api("/config/reload", { method: "POST" });
      SM.toast(r.message, "success", 7000);
      SM.services.refresh();
    } catch (e) { SM.toast(e.message, "error", 10000); }
    finally { reloadBtn.classList.remove("busy"); }
  });

  // (#11) shortcuts
  SM.registerKey("/", "Focus the filter box", () => search.focus(), "Services");
  SM.registerKey("v", "Switch card / table view", () => {
    view = view === "cards" ? "table" : "cards"; SM.LS.set("sm.view", view); applyView();
  }, "Services");
  SM.registerKey("Escape", "Clear filters", () => { search.value = ""; statFilter = "all"; applyFilter(); }, "Services");
})();
