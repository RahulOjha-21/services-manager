/* Shared front-end module: API, formatting, alerts (#5), roles (#13), critical
   confirmations (#12), ANSI colours (#9), sparklines (#7), links (#3),
   keyboard shortcuts (#11), theme + favicon (#14). */
const SM = (() => {
  const body = document.body;
  const LS = {
    get(k, d) { try { const v = localStorage.getItem(k); return v === null ? d : JSON.parse(v); } catch (_) { return d; } },
    set(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch (_) {} },
  };

  /* ------------------------------------------------------------ basics -- */
  const STATES = {
    running:  { label: "Running",  cls: "s-success", pulse: true },
    starting: { label: "Starting", cls: "s-info",    pulse: true },
    stopping: { label: "Stopping", cls: "s-warning", pulse: true },
    stopped:  { label: "Stopped",  cls: "" },
    exited:   { label: "Exited",   cls: "" },
    failed:   { label: "Failed",   cls: "s-danger" },
    external: { label: "Running (outside manager)", cls: "s-purple", pulse: true },
  };
  const HEALTH = {
    healthy:   { label: "Healthy",   cls: "h-ok" },
    starting:  { label: "Starting…", cls: "h-starting" },
    unhealthy: { label: "Unhealthy", cls: "h-bad" },
    unknown:   { label: "Health unknown", cls: "h-unknown" },
  };

  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

  function setConn(ok) {
    const el = document.getElementById("conn");
    if (!el) return;
    el.dataset.state = ok ? "ok" : "down";
    el.querySelector(".conn-label").textContent = ok ? "Connected" : "Server unreachable";
  }

  async function api(path, { method = "GET", body: payload } = {}) {
    let res;
    try {
      res = await fetch("/api" + path, {
        method,
        headers: { "X-Requested-With": "ServicesManager", ...(payload ? { "Content-Type": "application/json" } : {}) },
        body: payload ? JSON.stringify(payload) : undefined,
      });
    } catch (e) {
      setConn(false);
      throw new Error("Cannot reach the Services Manager server");
    }
    setConn(true);
    let data = {};
    try { data = await res.json(); } catch (_) { /* non-JSON */ }
    if (res.status === 401 && data.login) {
      location.href = "/login?next=" + encodeURIComponent(location.pathname + location.search);
      throw new Error("Session expired, please sign in again");
    }
    if (!res.ok || data.ok === false) throw new Error(data.message || `Request failed (HTTP ${res.status})`);
    return data;
  }

  function toast(message, kind = "info", ms = 4000) {
    const box = document.getElementById("toasts");
    const el = document.createElement("div");
    el.className = `toast ${kind}`;
    el.textContent = message;
    el.addEventListener("click", () => el.remove());
    box.appendChild(el);
    setTimeout(() => { el.classList.add("out"); setTimeout(() => el.remove(), 300); }, ms);
  }

  function confirmDialog(title, message, okLabel = "Confirm", style = "primary", warning = "") {
    const dlg = document.getElementById("confirm-dialog");
    document.getElementById("confirm-title").textContent = title;
    document.getElementById("confirm-message").textContent = message;
    const warn = document.getElementById("confirm-warning");
    warn.hidden = !warning;
    warn.querySelector("span").textContent = warning;
    const ok = document.getElementById("confirm-ok");
    ok.textContent = okLabel;
    ok.className = `btn btn-${style}`;
    return new Promise((resolve) => {
      dlg.addEventListener("close", () => resolve(dlg.returnValue === "ok"), { once: true });
      dlg.returnValue = "cancel";
      dlg.showModal();
      (warning ? dlg.querySelector('button[value="cancel"]') : ok).focus();
    });
  }

  /* -------------------------------------------------------- formatting -- */
  function duration(sec) {
    if (sec == null) return "–";
    sec = Math.floor(sec);
    const d = Math.floor(sec / 86400), h = Math.floor(sec % 86400 / 3600),
          m = Math.floor(sec % 3600 / 60), s = sec % 60;
    const pad = (n) => String(n).padStart(2, "0");
    return (d ? `${d}d ` : "") + `${pad(h)}:${pad(m)}:${pad(s)}`;
  }
  function time(ts, withDate = false) {
    if (!ts) return "–";
    const d = new Date(ts * 1000);
    const t = d.toLocaleTimeString([], { hour12: false });
    return withDate ? `${d.toLocaleDateString()} ${t}` : t;
  }
  function ago(sec) {
    if (sec == null) return "";
    if (sec < 5) return "just now";
    if (sec < 60) return `${Math.floor(sec)}s ago`;
    if (sec < 3600) return `${Math.floor(sec / 60)}m ago`;
    if (sec < 86400) return `${Math.floor(sec / 3600)}h ago`;
    return `${Math.floor(sec / 86400)}d ago`;
  }
  const agoTs = (ts) => ago(Date.now() / 1000 - ts);
  const fmtCpu = (m) => (m ? `${m.cpu.toFixed(m.cpu < 10 ? 1 : 0)}%` : "–");
  const fmtMem = (m) => (!m ? "–" : m.mem_mb >= 1024 ? `${(m.mem_mb / 1024).toFixed(1)} GB` : `${Math.round(m.mem_mb)} MB`);

  function stateLabel(svc) {
    const st = STATES[svc.state] || { label: svc.state };
    if (svc.state === "exited" || svc.state === "failed") return `${st.label} (code ${svc.exit_desc})`;
    return st.label;
  }
  function applyBadge(el, svc) {
    const st = STATES[svc.state] || { cls: "" };
    el.className = `badge ${st.cls} ${st.pulse ? "pulse" : ""}`;
    el.textContent = stateLabel(svc);
  }
  /* (#2) */
  function healthHTML(svc) {
    const h = svc.health;
    if (!h || !h.state) return "";
    const meta = HEALTH[h.state] || HEALTH.unknown;
    const bad = (h.checks || []).filter((c) => c.ok === false).map((c) => `${c.name}: ${c.detail}`).join("\n");
    const tip = bad || (h.checks || []).map((c) => `${c.name}: ${c.detail}`).join("\n");
    return `<span class="hbadge ${meta.cls}" title="${esc(tip)}"><svg><use href="#i-heart"/></svg>${meta.label}</span>`;
  }

  function monitorHTML(m, compact = false) {
    const stateTxt = m.state === "live" ? `<span class="live">● live</span>`
      : m.state === "stale" ? `stale · ${ago(m.age)}` : "waiting for data…";
    const title = m.message ? ` title="${esc(m.message)}"` : "";
    return `<div class="monitor s-${esc(m.style)} ${compact ? "compact" : ""}"${title}>
      <div><div class="monitor-label">${esc(m.label)}</div>
      <div class="monitor-value">${esc(m.value ?? "—")}</div></div>
      <div class="monitor-state">${stateTxt}</div></div>`;
  }

  /* (#3) links: {host} and localhost are replaced by the host used to open this page */
  function linkURL(url) {
    return String(url).replace(/\{host\}/g, location.hostname)
      .replace(/\/\/(localhost|127\.0\.0\.1|0\.0\.0\.0)(?=[:/]|$)/, `//${location.hostname}`);
  }
  function linksHTML(links, small = false) {
    return (links || []).map((l) =>
      `<a class="btn ${small ? "btn-sm" : ""} btn-link" href="${esc(linkURL(l.url))}" target="_blank" rel="noopener" title="${esc(linkURL(l.url))}">${esc(l.label)}<svg><use href="#i-external"/></svg></a>`).join("");
  }

  /* (#7) tiny SVG line chart */
  function sparkline(values, { w = 120, h = 28, max = null, cls = "" } = {}) {
    if (!values || values.length < 2) return `<svg class="spark ${cls}" viewBox="0 0 ${w} ${h}" preserveAspectRatio="none"></svg>`;
    const top = Math.max(max ?? 0, ...values, 1);
    const step = w / (values.length - 1);
    const pts = values.map((v, i) => `${(i * step).toFixed(1)},${(h - 2 - (v / top) * (h - 4)).toFixed(1)}`);
    return `<svg class="spark ${cls}" viewBox="0 0 ${w} ${h}" preserveAspectRatio="none" aria-hidden="true">
      <polygon class="spark-area" points="0,${h} ${pts.join(" ")} ${w},${h}"/>
      <polyline class="spark-line" points="${pts.join(" ")}"/></svg>`;
  }

  /* ---------------------------------------------------- (#13) roles ----- */
  const ROLE_RANK = { viewer: 1, operator: 2, admin: 3 };
  const role = body.dataset.role || "viewer";
  const can = (min) => (ROLE_RANK[role] || 0) >= (ROLE_RANK[min] || 0);
  /* Enable a control only if the condition holds AND the user's role allows it. */
  function setEnabled(el, cond = true) {
    if (!el) return;
    const min = el.dataset.minRole;
    const allowed = !min || can(min);
    el.disabled = !(cond && allowed);
    if (!allowed) el.title = `Requires the ${min} role (you are ${role})`;
  }
  function applyRoles(root = document) {
    root.querySelectorAll("[data-min-role]").forEach((el) => {
      if (!can(el.dataset.minRole)) {
        if (el.dataset.minRole === "admin" && el.id === "reload-config") el.hidden = true;
        setEnabled(el, false);
      }
    });
  }

  /* ---------------------------------- (#12) operations with confirmation -- */
  const CRITICAL_WARNING = "This service controls physical equipment (robots / door PLC). Make sure the area is safe.";

  async function serviceOp(svc, op, btn) {
    const crit = svc.critical ? CRITICAL_WARNING : "";
    if (op === "start" && svc.critical) {
      if (!(await confirmDialog(`Start ${svc.name}?`, "The service will be started.", "Start", "success", crit))) return null;
    }
    if (op === "stop") {
      const ext = svc.state === "external";
      const ok = await confirmDialog(
        ext ? `Stop external ${svc.name}?` : `Stop ${svc.name}?`,
        ext ? `This process was started outside the manager (PID ${svc.external_pids.join(", ")}). It will receive Ctrl+C (SIGINT).`
            : "The process will receive Ctrl+C (SIGINT) and be force-killed if it does not exit.",
        "Stop", "danger", crit);
      if (!ok) return null;
    }
    if (op === "restart" && !(await confirmDialog(`Restart ${svc.name}?`, "The process will be stopped and started again.", "Restart", "primary", crit))) return null;
    return withBusy(btn, async () => {
      const r = await api(`/services/${svc.id}/${op}`, { method: "POST" });
      toast(r.message, op === "stop" ? "warning" : "success");
      services.refresh();
      return r.service;
    });
  }

  async function runAction(svc, action, btn) {
    const crit = svc.critical ? CRITICAL_WARNING : "";
    if ((action.confirm || svc.critical) && !(await confirmDialog(`${action.label}?`, action.description || action.command, action.label, action.style, crit))) return null;
    return withBusy(btn, async () => {
      const r = await api(`/services/${svc.id}/actions/${action.id}`, { method: "POST" });
      toast(r.message, "success");
      services.refresh();
      return r.service;
    });
  }

  async function withBusy(btn, fn) {
    btn?.classList.add("busy");
    try { return await fn(); }
    catch (e) { toast(e.message, "error", 6000); return null; }
    finally { btn?.classList.remove("busy"); }
  }

  /* -------------------------------------------------- clipboard (#9) ---- */
  async function copyText(text) {
    try {
      if (navigator.clipboard && window.isSecureContext) { await navigator.clipboard.writeText(text); return true; }
    } catch (_) { /* fall back */ }
    const ta = document.createElement("textarea");
    ta.value = text;
    ta.style.cssText = "position:fixed;top:-1000px;opacity:0";
    document.body.appendChild(ta);
    ta.select();
    let ok = false;
    try { ok = document.execCommand("copy"); } catch (_) {}
    ta.remove();
    return ok;
  }

  /* ---------------------------------------------- ANSI colours (#9) ----- */
  const RGB256 = (n) => {
    if (n < 16) return null;
    if (n >= 232) { const v = 8 + (n - 232) * 10; return `rgb(${v},${v},${v})`; }
    n -= 16; const c = [Math.floor(n / 36), Math.floor(n / 6) % 6, n % 6].map((x) => (x ? 55 + x * 40 : 0));
    return `rgb(${c.join(",")})`;
  };
  function ansiToHtml(s) {
    const st = { fg: null, bg: null, fgS: null, bgS: null, b: false, d: false, i: false, u: false };
    let out = "", last = 0, m;
    const re = /\x1b\[([0-9;]*)m/g;
    const wrap = (t) => {
      if (!t) return "";
      const cls = [], style = [];
      if (st.fg !== null) cls.push(`a-fg${st.fg}`);
      if (st.bg !== null) cls.push(`a-bg${st.bg}`);
      if (st.fgS) style.push(`color:${st.fgS}`);
      if (st.bgS) style.push(`background:${st.bgS}`);
      if (st.b) cls.push("a-b"); if (st.d) cls.push("a-d"); if (st.i) cls.push("a-i"); if (st.u) cls.push("a-u");
      if (!cls.length && !style.length) return esc(t);
      return `<span class="${cls.join(" ")}"${style.length ? ` style="${style.join(";")}"` : ""}>${esc(t)}</span>`;
    };
    const apply = (codes) => {
      const c = codes === "" ? [0] : codes.split(";").map(Number);
      for (let k = 0; k < c.length; k++) {
        const n = c[k];
        if (n === 0) Object.assign(st, { fg: null, bg: null, fgS: null, bgS: null, b: false, d: false, i: false, u: false });
        else if (n === 1) st.b = true; else if (n === 2) st.d = true; else if (n === 3) st.i = true; else if (n === 4) st.u = true;
        else if (n === 22) st.b = st.d = false; else if (n === 23) st.i = false; else if (n === 24) st.u = false;
        else if (n >= 30 && n <= 37) { st.fg = n - 30; st.fgS = null; }
        else if (n >= 90 && n <= 97) { st.fg = n - 82; st.fgS = null; }
        else if (n >= 40 && n <= 47) { st.bg = n - 40; st.bgS = null; }
        else if (n >= 100 && n <= 107) { st.bg = n - 92; st.bgS = null; }
        else if (n === 39) { st.fg = null; st.fgS = null; }
        else if (n === 49) { st.bg = null; st.bgS = null; }
        else if (n === 38 || n === 48) {
          let col = null;
          if (c[k + 1] === 5) { const v = c[k + 2]; col = v < 16 ? null : RGB256(v); if (v < 16) { if (n === 38) st.fg = v; else st.bg = v; } k += 2; }
          else if (c[k + 1] === 2) { col = `rgb(${c[k + 2]},${c[k + 3]},${c[k + 4]})`; k += 4; }
          if (col) { if (n === 38) { st.fgS = col; st.fg = null; } else { st.bgS = col; st.bg = null; } }
        }
      }
    };
    while ((m = re.exec(s))) { out += wrap(s.slice(last, m.index)); apply(m[1]); last = re.lastIndex; }
    return out + wrap(s.slice(last));
  }

  /* -------------------------------------------------- theme (#14) ------- */
  const themeBtn = document.getElementById("theme-toggle");
  function applyTheme(t) {
    if (t === "light" || t === "dark") document.documentElement.dataset.theme = t;
    else delete document.documentElement.dataset.theme;
    if (themeBtn) {
      themeBtn.querySelector("use").setAttribute("href", t === "light" ? "#i-sun" : t === "dark" ? "#i-moon" : "#i-auto");
      themeBtn.title = `Theme: ${t || "auto"} (T)`;
    }
  }
  function cycleTheme() {
    const order = ["auto", "light", "dark"];
    let cur = "auto";
    try { cur = localStorage.getItem("sm.theme") || "auto"; } catch (_) {}
    const next = order[(order.indexOf(cur) + 1) % order.length];
    try { localStorage.setItem("sm.theme", next); } catch (_) {}   // raw string: read by the <head> script
    applyTheme(next);
    toast(`Theme: ${next}`);
  }
  (() => { let t; try { t = localStorage.getItem("sm.theme"); } catch (_) {} applyTheme(t || "auto"); })();
  themeBtn?.addEventListener("click", cycleTheme);

  /* --------------------------------------- favicon + title (#5, #14) ---- */
  const baseTitle = document.title;
  function setProblemIndicators(count) {
    const color = count ? "%23dc2626" : "%232563eb";
    const badge = count
      ? `%3Ctext x='16' y='22' font-size='17' font-family='Arial' font-weight='700' text-anchor='middle' fill='white'%3E${count > 9 ? "9+" : count}%3C/text%3E`
      : "%3Cpath d='M9 11h14M9 16h14M9 21h9' stroke='white' stroke-width='3' stroke-linecap='round'/%3E";
    const fav = document.getElementById("favicon");
    if (fav) fav.href = `data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'%3E%3Crect width='32' height='32' rx='7' fill='${color}'/%3E${badge}%3C/svg%3E`;
    document.title = (count ? `(${count} problem${count > 1 ? "s" : ""}) ` : "") + (SM.pageTitle || baseTitle);
    const dot = document.querySelector(".bell-dot");
    if (dot) dot.hidden = !count;
  }

  /* ------------------------------------------------- alerts (#5) -------- */
  const alertCfg = { sound: LS.get("sm.alert.sound", true), desktop: LS.get("sm.alert.desktop", false) };
  const desktopSupported = "Notification" in window && window.isSecureContext;
  let audioCtx = null;
  document.addEventListener("pointerdown", () => {
    if (!audioCtx && (window.AudioContext || window.webkitAudioContext)) {
      try { audioCtx = new (window.AudioContext || window.webkitAudioContext)(); } catch (_) {}
    }
  }, { once: true });

  function beep() {
    if (!alertCfg.sound || !audioCtx) return;
    const now = audioCtx.currentTime;
    [[880, 0], [660, 0.18]].forEach(([f, t]) => {
      const o = audioCtx.createOscillator(), g = audioCtx.createGain();
      o.frequency.value = f; o.type = "sine";
      g.gain.setValueAtTime(0.0001, now + t);
      g.gain.exponentialRampToValueAtTime(0.25, now + t + 0.02);
      g.gain.exponentialRampToValueAtTime(0.0001, now + t + 0.16);
      o.connect(g).connect(audioCtx.destination);
      o.start(now + t); o.stop(now + t + 0.18);
    });
  }
  function alertUser(title, message, svcId) {
    toast(`${title} — ${message}`, "error", 10000);
    beep();
    if (alertCfg.desktop && desktopSupported && Notification.permission === "granted") {
      const n = new Notification(title, { body: message, tag: `sm-${svcId}` });
      n.onclick = () => { window.focus(); location.href = `/services/${svcId}`; };
    }
  }
  (() => {
    const snd = document.getElementById("alert-sound"), dsk = document.getElementById("alert-desktop");
    if (!snd) return;
    snd.checked = alertCfg.sound;
    dsk.checked = alertCfg.desktop && desktopSupported;
    dsk.disabled = !desktopSupported;
    document.getElementById("alert-desktop-note").hidden = desktopSupported;
    snd.addEventListener("change", () => { alertCfg.sound = snd.checked; LS.set("sm.alert.sound", snd.checked); });
    dsk.addEventListener("change", async () => {
      if (dsk.checked && Notification.permission !== "granted") {
        dsk.checked = (await Notification.requestPermission()) === "granted";
      }
      alertCfg.desktop = dsk.checked; LS.set("sm.alert.desktop", dsk.checked);
    });
    document.getElementById("alert-test").addEventListener("click", () => alertUser("Test alert", "This is how a failure alert looks.", ""));
  })();

  const prev = new Map();
  function watchServices(list) {
    let problems = 0;
    for (const s of list) {
      const health = s.health?.state || null;
      const bad = s.state === "failed" || health === "unhealthy";
      if (bad) problems++;
      const p = prev.get(s.id);
      if (p) {
        if (s.state === "failed" && p.state !== "failed") alertUser(`${s.name} crashed`, `Exit code ${s.exit_desc}`, s.id);
        else if (health === "unhealthy" && p.health !== "unhealthy") {
          const why = (s.health.checks || []).filter((c) => c.ok === false).map((c) => c.detail).join("; ");
          alertUser(`${s.name} is unhealthy`, why || "Health check failed", s.id);
        } else if (p.health === "unhealthy" && health === "healthy") toast(`${s.name} recovered`, "success");
      }
      prev.set(s.id, { state: s.state, health });
    }
    setProblemIndicators(problems);
  }

  /* ------------------------------- shared poller for /api/services ------ */
  const services = (() => {
    const subs = [];
    let timer = null, inflight = false, last = null;
    async function refresh() {
      if (inflight) return;
      inflight = true;
      try {
        const { services: list } = await api("/services");
        last = list;
        watchServices(list);
        subs.forEach((cb) => { try { cb(list); } catch (e) { console.error(e); } });
      } catch (e) { subs.forEach((cb) => cb.onError?.(e)); }
      finally { inflight = false; }
    }
    function start() { if (!timer) { refresh(); timer = setInterval(refresh, 2000); } }
    return {
      subscribe(cb, onError) { cb.onError = onError; subs.push(cb); if (last) cb(last); start(); },
      refresh, start,
      get last() { return last; },
    };
  })();

  /* ------------------------------------------- shortcuts (#11) ---------- */
  const keys = [];
  let pending = null, pendingTimer = null;
  function registerKey(combo, desc, fn, group = "General") { keys.push({ combo, desc, fn, group }); }
  document.addEventListener("keydown", (e) => {
    if (e.ctrlKey || e.metaKey || e.altKey) return;
    const t = e.target;
    if (t.closest && t.closest("input, textarea, select, [contenteditable]")) {
      if (e.key === "Escape") t.blur();
      return;
    }
    if (document.querySelector("dialog[open]")) return;
    const k = e.key;
    const seq = pending ? `${pending} ${k}` : k;
    const hit = keys.find((x) => x.combo === seq);
    if (hit) { e.preventDefault(); pending = null; hit.fn(e); return; }
    if (!pending && keys.some((x) => x.combo.startsWith(k + " "))) {
      pending = k; clearTimeout(pendingTimer);
      pendingTimer = setTimeout(() => (pending = null), 1200);
      e.preventDefault(); return;
    }
    pending = null;
  });
  function showHelp() {
    const groups = {};
    keys.forEach((k) => (groups[k.group] ||= []).push(k));
    const kbd = (combo) => combo.split(" ").map((c) => `<kbd>${esc(c)}</kbd>`).join(" then ");
    document.getElementById("help-list").innerHTML = Object.entries(groups).map(([g, list]) =>
      `<section><h4>${esc(g)}</h4>${list.map((k) => `<div class="sc-row"><span>${esc(k.desc)}</span><span>${kbd(k.combo)}</span></div>`).join("")}</section>`).join("");
    document.getElementById("help-dialog").showModal();
  }
  document.getElementById("help-open")?.addEventListener("click", showHelp);
  if (body.dataset.user) {
    registerKey("?", "Show keyboard shortcuts", showHelp);
    registerKey("g s", "Go to Services", () => (location.href = "/"), "Navigation");
    registerKey("g e", "Go to Events", () => (location.href = "/events"), "Navigation");
    registerKey("T", "Switch theme (auto / light / dark)", cycleTheme);
  }

  // close open menus when clicking elsewhere
  document.addEventListener("click", (e) => {
    document.querySelectorAll("details.menu[open]").forEach((d) => { if (!d.contains(e.target)) d.open = false; });
  });

  // header clock
  const clock = document.getElementById("clock");
  if (clock) {
    const tick = () => (clock.textContent = new Date().toLocaleTimeString([], { hour12: false }));
    tick(); setInterval(tick, 1000);
  }

  // alerts work on every page (signed-in pages only)
  if (body.dataset.user && body.dataset.page !== "dashboard" && body.dataset.page !== "service") services.start();
  applyRoles();

  return {
    LS, api, toast, confirmDialog, esc, duration, time, ago, agoTs, fmtCpu, fmtMem,
    stateLabel, applyBadge, healthHTML, monitorHTML, linkURL, linksHTML, sparkline,
    role, can, setEnabled, applyRoles, serviceOp, runAction, copyText, ansiToHtml,
    services, registerKey, pageTitle: null, HEALTH,
  };
})();
