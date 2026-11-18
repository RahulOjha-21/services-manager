/* (#8) Events page: filterable history, refreshed every 5 s. */
(() => {
  const body = document.getElementById("events-body");
  const fService = document.getElementById("f-service");
  const fLevel = document.getElementById("f-level");
  const fSearch = document.getElementById("f-search");
  const LABEL = {
    start: "Started", stop: "Stopped", restart: "Restart", crash: "Crashed", exit: "Exited by itself",
    auto_restart: "Auto-restart", start_failed: "Start failed", stop_external: "Stopped external",
    action: "Action", action_failed: "Action failed", healthy: "Healthy", unhealthy: "Unhealthy",
    config_reload: "Config reloaded", login: "Signed in", logout: "Signed out", login_failed: "Sign-in failed",
  };
  const names = Object.fromEntries([...fService.options].filter((o) => o.value).map((o) => [o.value, o.text]));
  let events = [];

  const params = new URLSearchParams(location.search);
  if (params.get("service")) fService.value = params.get("service");

  function render() {
    const q = fSearch.value.trim().toLowerCase();
    const rows = events.filter((e) => !q || `${e.message} ${e.user} ${e.service} ${e.kind}`.toLowerCase().includes(q));
    body.innerHTML = rows.length ? rows.map((e) => `
      <tr class="ev ev-${SM.esc(e.level)}">
        <td class="nowrap" title="${SM.esc(SM.time(e.ts, true))}">${SM.esc(SM.time(e.ts, true))}<div class="muted small">${SM.agoTs(e.ts)}</div></td>
        <td>${e.service ? `<a href="/services/${SM.esc(e.service)}">${SM.esc(names[e.service] || e.service)}</a>` : `<span class="muted">—</span>`}</td>
        <td><span class="ev-kind">${SM.esc(LABEL[e.kind] || e.kind)}</span></td>
        <td>${SM.esc(e.message)}</td>
        <td>${e.user ? SM.esc(e.user) : `<span class="muted">—</span>`}</td>
      </tr>`).join("") : `<tr><td colspan="5" class="muted">No events match.</td></tr>`;
  }

  async function load() {
    const qs = new URLSearchParams({ limit: "500" });
    if (fService.value) qs.set("service", fService.value);
    if (fLevel.value) qs.set("level", fLevel.value);
    try { events = (await SM.api(`/events?${qs}`)).events; render(); }
    catch (e) { body.innerHTML = `<tr><td colspan="5">${SM.esc(e.message)}</td></tr>`; }
  }

  fService.addEventListener("change", () => {
    const u = new URL(location.href);
    fService.value ? u.searchParams.set("service", fService.value) : u.searchParams.delete("service");
    history.replaceState(null, "", u);
    load();
  });
  fLevel.addEventListener("change", load);
  fSearch.addEventListener("input", render);
  SM.registerKey("/", "Search events", () => fSearch.focus(), "Events");
  load();
  setInterval(load, 5000);
})();
