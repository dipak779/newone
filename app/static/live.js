// Live feed of names the background hunter found AVAILABLE. Refreshes every 20s.
(() => {
  const el = (s) => document.querySelector(s);
  let platform = "";
  let seen = null;
  const esc = (s) => String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const ago = (t) => {
    const s = Math.max(0, Date.now() / 1000 - t);
    if (s < 60) return "just now";
    if (s < 3600) return `${Math.floor(s / 60)} min ago`;
    if (s < 86400) return `${Math.floor(s / 3600)} h ago`;
    return `${Math.floor(s / 86400)} d ago`;
  };
  const link = (i) => i.platform === "telegram" ? `https://t.me/${i.username}` : `https://instagram.com/${i.username}`;

  async function refresh() {
    try {
      const [h, a] = await Promise.all([
        fetch("/api/hunter").then((r) => r.json()),
        fetch("/api/finds?limit=300" + (platform ? `&platform=${platform}` : "")).then((r) => r.json()),
      ]);
      el("#liveDot").classList.toggle("on", h.running);
      el("#hunterInfo").textContent = h.running
        ? `Hunting every ${Math.round(h.interval_seconds)}s · ${h.pool_checked}/${h.pool_size} names checked` +
          (h.last_round_at ? ` · last round ${ago(h.last_round_at)}: ${h.last_batch.map((n) => "@" + n).join(", ")}` : " · first round starting…") +
          (h.last_error ? ` · error: ${h.last_error}` : "") +
          Object.entries(h.paused || {}).map(([p, sec]) =>
            ` · ⏸ ${p[0].toUpperCase() + p.slice(1)} is blocking requests, retrying in ${Math.ceil(sec / 60)} min`).join("")
        : "Background hunter is off (set HUNT_ENABLED=1). Showing names found by scans.";
      if (h.helper && h.helper.enabled) {
        const on = h.helper.last_seen_at && Date.now() / 1000 - h.helper.last_seen_at < 300;
        el("#hunterInfo").textContent += on ? " · 🏠 Instagram helper online" : " · 🏠 Instagram helper offline";
      }
      // one card per name that's free somewhere, with a yes/no line for every platform
      const groups = a.items;
      const shown = a.platforms.length ? a.platforms : ["telegram", "instagram"];
      el("#liveCount").textContent = `· ${groups.length} free names`;
      const LABEL = { telegram: "✈️ Telegram", instagram: "📸 Instagram" };
      const verdict = (st) => {
        if (!st) return `<span class="yn wait">⏳ Not checked yet</span>`;
        if (st.status === "AVAILABLE") return `<span class="yn yes">✅ Available</span>`;
        if (st.status === "TAKEN") return `<span class="yn no">❌ Taken</span>`;
        if (st.status === "FRAGMENT") return `<span class="yn no">❌ For sale on Fragment</span>`;
        if (st.status === "INVALID") return `<span class="yn no">❌ ${/reserved/.test(st.detail || "") ? "Reserved by Telegram" : "Not allowed"}</span>`;
        if (st.status === "UNCONFIRMED") return `<span class="yn wait">⚠️ Not confirmed yet</span>`;
        return `<span class="yn wait">⏳ Couldn't check</span>`;
      };
      const line = (g, p) => {
        const st = g.platforms[p];
        const name = st && st.status === "AVAILABLE"
          ? `<a class="pf" href="${link({ platform: p, username: g.username })}" target="_blank" rel="noopener">${LABEL[p]}</a>`
          : `<span class="pf dim">${LABEL[p]}</span>`;
        return `<span class="pl">${name}${verdict(st)}</span>`;
      };
      el("#liveList").innerHTML = groups.length
        ? groups.map((g) => `<div class="find${seen && !seen.has(g.username) ? " new" : ""}">
            <span class="h">@${esc(g.username)}</span>
            ${shown.map((p) => line(g, p)).join("")}
            <span class="m">found ${ago(g.found)}</span>
          </div>`).join("")
        : (h.telegram_confirm && !h.telegram_confirm.logged_in
            ? `<span class="muted small">Telegram names are only listed after Telegram itself confirms them. Connect Telegram at <a href="/admin" style="color:var(--accent2)">/admin</a> to start.</span>`
            : `<span class="muted small">Nothing confirmed free yet — names appear here as soon as they're verified.</span>`);
      seen = new Set(groups.map((g) => g.username));
    } catch {
      el("#hunterInfo").textContent = "Can't reach the server.";
    }
  }

  document.querySelectorAll("#liveFilter button").forEach((b) => b.addEventListener("click", () => {
    platform = b.dataset.p; seen = null;
    document.querySelectorAll("#liveFilter button").forEach((x) => x.classList.toggle("active", x === b));
    refresh();
  }));
  refresh();
  setInterval(refresh, 20000);
})();
