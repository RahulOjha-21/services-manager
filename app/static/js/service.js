/* Service detail page: controls, health (#2), links (#3), resources (#7),
   history (#8), enhanced console (#9) and shortcuts (#11). */
(() => {
  const root = document.getElementById("svc");
  const SID = root.dataset.sid;
  const f = (n) => root.querySelector(`[data-f="${n}"]`);
  let svc = null;

  function setHTML(el, html) { if (el._html !== html) { el.innerHTML = html; el._html = html; } }

  /* ------------------------------------------------------------ status --- */
  function render(s) {
    svc = s;
    SM.applyBadge(f("badge"), s);
    SM.pageTitle = `${s.name} · ${SM.stateLabel(s)} · Services Manager`;

    // (#2) health badge in the header
    const hb = f("hbadge");
    const hhtml = SM.healthHTML(s);
    hb.hidden = !hhtml;
    setHTML(hb, hhtml);
    hb.className = "";

    SM.setEnabled(root.querySelector('[data-op="start"]'), s.can_start);
    SM.setEnabled(root.querySelector('[data-op="stop"]'), s.can_stop);
    SM.setEnabled(root.querySelector('[data-op="restart"]'), s.can_restart);
    root.querySelector('[data-op="stop"] span').textContent = s.state === "external" ? "Stop external" : "Stop";
    setHTML(f("links"), SM.linksHTML(s.links));

    const notice = f("notice");
    if (s.state === "external") {
      notice.className = "notice";
      notice.textContent = `This program is already running outside the Services Manager (PID ${s.external_pids.join(", ")}), e.g. from a terminal. Its output cannot be captured here. Stop it, then start it from this page to see live logs.`;
      notice.hidden = false;
    } else if (s.state === "failed" && s.last_error) {
      notice.className = "notice danger";
      notice.textContent = s.last_error;
      notice.hidden = false;
    } else notice.hidden = true;

    // (#2) health checks
    f("health-panel").hidden = !s.has_health;
    if (s.has_health) {
      const h = s.health;
      const icon = (ok) => ok === true ? `<span class="ck ck-ok">✓</span>` : ok === false ? `<span class="ck ck-bad">✕</span>` : `<span class="ck ck-wait">…</span>`;
      setHTML(f("checks"), !h || !h.state
        ? `<p class="muted small">Checks run while the service is running.</p>`
        : `<div class="checks-state">${SM.healthHTML(s)}<span class="muted small">${h.since ? "since " + SM.time(h.since) : ""}</span></div>` +
          (h.checks || []).map((c) => `<div class="check-row">${icon(c.ok)}<div><b>${SM.esc(c.name)}</b><div class="muted small">${SM.esc(c.detail || "")}</div></div></div>`).join(""));
    }

    // (#7) resources
    const m = s.metrics;
    f("cpu").textContent = SM.fmtCpu(m);
    f("mem").textContent = SM.fmtMem(m);
    setHTML(f("cpu-chart"), SM.sparkline(s.history?.cpu, { w: 160, h: 44, max: 100, cls: "spark-cpu" }));
    setHTML(f("mem-chart"), SM.sparkline(s.history?.mem, { w: 160, h: 44, cls: "spark-mem" }));
    f("procs").textContent = m ? `${m.procs} process${m.procs === 1 ? "" : "es"} · last ${Math.round((s.history?.cpu.length || 0) * 2 / 60) || "<1"} min` : "Not running";

    f("pid").textContent = s.pid ?? (s.external_pids.length ? `${s.external_pids.join(", ")} (external)` : "–");
    f("uptime").textContent = SM.duration(s.uptime);
    f("started").textContent = SM.time(s.started_at, true);
    f("exit").textContent = s.exit_code == null ? "–" : s.exit_desc;
    f("counts").innerHTML = `<span class="t-err">${s.logs.errors}</span> / <span class="t-warn">${s.logs.warnings}</span>`;
    f("crashes").innerHTML = s.crashes_today ? `<span class="t-err">${s.crashes_today}</span>` : "0";
    f("autorestart").textContent = s.restart_on_failure ? `On (${s.restart_count} used)` : "Off";
    f("cwd").textContent = s.cwd || "–";
    f("command").textContent = s.command;

    f("monitors-panel").hidden = !s.monitors.length;
    setHTML(f("monitors"), s.monitors.map((mo) => SM.monitorHTML(mo)).join(""));
    f("actions-panel").hidden = !s.actions.length;
    renderActions(s.actions);
  }

  function renderActions(actions) {
    const box = f("actions");
    if (!box.children.length && actions.length) {
      box.innerHTML = actions.map((a) => `
        <div class="action" data-aid="${SM.esc(a.id)}">
          <div class="action-row">
            <button class="btn btn-${SM.esc(a.style)}" data-action="${SM.esc(a.id)}" data-min-role="operator"><svg><use href="#i-bolt"/></svg>${SM.esc(a.label)}</button>
          </div>
          ${a.description ? `<div class="action-desc">${SM.esc(a.description)}</div>` : ""}
          <div class="action-result" data-r="result"></div>
          <details data-r="details" hidden><summary>Output</summary><pre data-r="output"></pre></details>
        </div>`).join("");
      SM.applyRoles(box);
    }
    const statusCls = { success: "s-success", failed: "s-danger", timeout: "s-danger", running: "s-info pulse" };
    for (const a of actions) {
      const el = box.querySelector(`[data-aid="${a.id}"]`);
      if (!el) continue;
      el.querySelector("button").classList.toggle("busy", a.running);
      const res = el.querySelector('[data-r="result"]');
      const det = el.querySelector('[data-r="details"]');
      if (!a.last) { res.textContent = "Not run yet"; det.hidden = true; continue; }
      const L = a.last;
      res.innerHTML = `<span class="badge ${statusCls[L.status] || ""}">${SM.esc(L.status)}</span>
        <span>${SM.time(L.started_at)}</span>
        ${L.duration != null ? `<span>· ${L.duration.toFixed(1)}s</span>` : ""}
        ${L.exit_code != null && L.exit_code !== 0 ? `<span>· code ${L.exit_code}</span>` : ""}`;
      det.hidden = !L.output;
      const pre = el.querySelector('[data-r="output"]');
      if (pre.textContent !== L.output) pre.textContent = L.output;
    }
  }

  SM.services.subscribe((list) => {
    const s = list.find((x) => x.id === SID);
    if (s) render(s);
    else { SM.toast("This service was removed from the configuration", "warning"); }
  });

  root.addEventListener("click", async (ev) => {
    const btn = ev.target.closest("button[data-op], button[data-action]");
    if (!btn || !svc) return;
    if (btn.dataset.op) await SM.serviceOp(svc, btn.dataset.op, btn);
    else await SM.runAction(svc, svc.actions.find((a) => a.id === btn.dataset.action), btn);
    setTimeout(loadHistory, 800);
  });

  /* ------------------------------------------------------ (#8) history --- */
  const EVENT_LABEL = {
    start: "Started", stop: "Stopped", restart: "Restart", crash: "Crashed", exit: "Exited",
    auto_restart: "Auto-restart", start_failed: "Start failed", stop_external: "Stopped external",
    action: "Action", action_failed: "Action failed", healthy: "Healthy", unhealthy: "Unhealthy",
  };
  async function loadHistory() {
    try {
      const { events } = await SM.api(`/events?service=${encodeURIComponent(SID)}&limit=12`);
      setHTML(f("history"), events.length ? events.map((e) => `
        <li class="tl-item tl-${SM.esc(e.level)}">
          <div class="tl-head"><b>${SM.esc(EVENT_LABEL[e.kind] || e.kind)}</b>
            <span class="muted small" title="${SM.esc(SM.time(e.ts, true))}">${SM.agoTs(e.ts)}</span></div>
          <div class="small">${SM.esc(e.message)}${e.user && e.user !== "system" ? ` · <span class="muted">by ${SM.esc(e.user)}</span>` : ""}</div>
        </li>`).join("") : `<li class="muted small">No events yet.</li>`);
    } catch (_) { /* ignore */ }
  }
  loadHistory();
  setInterval(loadHistory, 10000);

  /* ----------------------------------------------------- (#9) console --- */
  const con = document.getElementById("console");
  const jump = document.getElementById("jump");
  const streamState = document.getElementById("stream-state");
  const levelSel = document.getElementById("level-filter");
  const searchBox = document.getElementById("log-search");
  const errCount = document.getElementById("err-count");
  const timeBtn = document.getElementById("time-mode");
  const MAX_LINES = 5000;
  let follow = true, query = "", lastSeq = 0, empty = true, errIdx = -1;
  let timeMode = SM.LS.get("sm.timeMode", "clock");

  const tsText = (ts) => (timeMode === "ago" ? SM.agoTs(ts) : SM.time(ts));

  function showEmpty() {
    con.innerHTML = `<div class="console-empty">No output yet. Start the service to see its terminal output here.</div>`;
    empty = true;
    updateErrCount();
  }

  function renderMsg(div) {
    const msg = div.querySelector(".msg");
    const text = div._raw;
    if (!query) {
      div.classList.remove("nomatch");
      if (div._ansi) msg.innerHTML = SM.ansiToHtml(div._ansi); else msg.textContent = text;
      return;
    }
    const i = text.toLowerCase().indexOf(query);
    if (i < 0) { msg.textContent = text; div.classList.add("nomatch"); return; }
    div.classList.remove("nomatch");
    msg.innerHTML = SM.esc(text.slice(0, i)) + `<mark>${SM.esc(text.slice(i, i + query.length))}</mark>` + SM.esc(text.slice(i + query.length));
  }

  function lineEl(l) {
    const div = document.createElement("div");
    div.className = `line st-${l.stream} lvl-${l.level}`;
    div._raw = l.text; div._ansi = l.ansi || null; div._ts = l.ts; div._stream = l.stream;
    const tag = l.stream === "stdout" ? "out" : l.stream === "stderr" ? "err" : l.stream;
    div.innerHTML = `<span class="ts" title="${SM.esc(SM.time(l.ts, true))}">${tsText(l.ts)}</span><span class="tag">${tag}</span><span class="msg"></span><button class="line-copy" type="button" title="Copy line" tabindex="-1"><svg><use href="#i-copy"/></svg></button>`;
    renderMsg(div);
    return div;
  }

  function append(lines) {
    if (!lines.length) return;
    if (empty) { con.innerHTML = ""; empty = false; }
    const frag = document.createDocumentFragment();
    lines.forEach((l) => frag.appendChild(lineEl(l)));
    con.appendChild(frag);
    lastSeq = lines[lines.length - 1].seq;
    while (con.childElementCount > MAX_LINES) con.firstElementChild.remove();
    if (follow) con.scrollTop = con.scrollHeight;
    else jump.hidden = false;
    if (lines.some((l) => l.level === "error")) updateErrCount();
  }

  const visibleLines = () => [...con.querySelectorAll(".line")].filter((d) => d.offsetParent !== null);
  const errorLines = () => [...con.querySelectorAll(".line.lvl-error")].filter((d) => d.offsetParent !== null);

  function updateErrCount() {
    const n = con.querySelectorAll(".line.lvl-error").length;
    errCount.textContent = `${n} error${n === 1 ? "" : "s"}`;
    errCount.classList.toggle("has", n > 0);
  }

  function gotoError(dir) {
    const errs = errorLines();
    if (!errs.length) { SM.toast("No errors in the console"); return; }
    if (errIdx < 0 || errIdx >= errs.length) {
      // start from the error nearest to the current viewport
      const mid = con.scrollTop + con.clientHeight / 2;
      errIdx = dir > 0 ? errs.findIndex((e) => e.offsetTop > mid) : errs.findLastIndex((e) => e.offsetTop < mid);
      if (errIdx < 0) errIdx = dir > 0 ? 0 : errs.length - 1;
    } else errIdx = (errIdx + dir + errs.length) % errs.length;
    const el = errs[errIdx];
    follow = false;
    el.scrollIntoView({ block: "center" });
    el.classList.remove("flash"); void el.offsetWidth; el.classList.add("flash");
    errCount.textContent = `${errIdx + 1} / ${errs.length} errors`;
  }

  con.addEventListener("scroll", () => {
    const atBottom = con.scrollHeight - con.scrollTop - con.clientHeight < 30;
    follow = atBottom;
    if (atBottom) jump.hidden = true;
  });
  con.addEventListener("click", async (ev) => {
    const b = ev.target.closest(".line-copy");
    if (!b) return;
    const line = b.closest(".line");
    const ok = await SM.copyText(line._raw);
    SM.toast(ok ? "Line copied" : "Copy failed", ok ? "success" : "error", 1500);
  });
  jump.addEventListener("click", () => { follow = true; jump.hidden = true; con.scrollTop = con.scrollHeight; });

  levelSel.addEventListener("change", () => {
    con.classList.remove("f-warn", "f-error");
    if (levelSel.value !== "all") con.classList.add(`f-${levelSel.value}`);
    errIdx = -1;
    con.scrollTop = con.scrollHeight;
  });

  let searchTimer;
  searchBox.addEventListener("input", () => {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(() => {
      query = searchBox.value.trim().toLowerCase();
      con.querySelectorAll(".line").forEach(renderMsg);
      errIdx = -1;
    }, 150);
  });

  function setTimeMode(mode) {
    timeMode = mode; SM.LS.set("sm.timeMode", mode);
    timeBtn.classList.toggle("active", mode === "ago");
    timeBtn.title = `Timestamps: ${mode === "ago" ? "time ago" : "clock"} (t)`;
    con.querySelectorAll(".line").forEach((d) => (d.querySelector(".ts").textContent = tsText(d._ts)));
  }
  timeBtn.addEventListener("click", () => setTimeMode(timeMode === "ago" ? "clock" : "ago"));
  setInterval(() => { if (timeMode === "ago") setTimeMode("ago"); }, 10000);
  setTimeMode(timeMode);

  document.getElementById("err-prev").addEventListener("click", () => gotoError(-1));
  document.getElementById("err-next").addEventListener("click", () => gotoError(1));
  document.getElementById("wrap").addEventListener("change", (e) => con.classList.toggle("wrap", e.target.checked));
  document.getElementById("download").href = `/api/services/${SID}/logs/download`;
  document.getElementById("copy-last").addEventListener("click", async () => {
    const lines = visibleLines().slice(-100).map((d) => `${SM.time(d._ts)} [${d._stream}] ${d._raw}`);
    if (!lines.length) { SM.toast("Nothing to copy"); return; }
    const ok = await SM.copyText(lines.join("\n"));
    SM.toast(ok ? `Copied ${lines.length} lines` : "Copy failed", ok ? "success" : "error");
  });
  document.getElementById("clear").addEventListener("click", async () => {
    try { await SM.api(`/services/${SID}/logs/clear`, { method: "POST" }); showEmpty(); SM.toast("Console cleared"); }
    catch (e) { SM.toast(e.message, "error"); }
  });

  function connect() {
    const es = new EventSource(`/api/services/${SID}/logs/stream?since=${lastSeq}`);
    es.onopen = () => { streamState.dataset.state = "live"; streamState.textContent = "live"; };
    es.onmessage = (ev) => append(JSON.parse(ev.data));
    es.onerror = () => { streamState.dataset.state = "down"; streamState.textContent = "reconnecting…"; };
  }

  /* --------------------------------------------------- (#11) shortcuts --- */
  const op = (name) => () => { const b = root.querySelector(`[data-op="${name}"]`); if (b && !b.disabled) b.click(); };
  SM.registerKey("s", "Start the service", op("start"), "This service");
  SM.registerKey("x", "Stop the service", op("stop"), "This service");
  SM.registerKey("r", "Restart the service", op("restart"), "This service");
  SM.registerKey("e", "Next error in the console", () => gotoError(1), "Console");
  SM.registerKey("E", "Previous error in the console", () => gotoError(-1), "Console");
  SM.registerKey("/", "Search the console", () => searchBox.focus(), "Console");
  SM.registerKey("t", "Toggle clock / time-ago timestamps", () => setTimeMode(timeMode === "ago" ? "clock" : "ago"), "Console");
  SM.registerKey("f", "Follow output (jump to latest)", () => jump.click(), "Console");
  SM.registerKey("w", "Toggle line wrap", () => document.getElementById("wrap").click(), "Console");
  SM.registerKey("Backspace", "Back to all services", () => (location.href = "/"), "Navigation");

  showEmpty();
  connect();
})();
