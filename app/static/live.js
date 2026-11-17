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
        fetch("/api/available?limit=300" + (platform ? `&platform=${platform}` : "")).then((r) => r.json()),
      ]);
      el("#liveDot").classList.toggle("on", h.running);
      el("#hunterInfo").textContent = h.running
        ? `Hunting every ${Math.round(h.interval_seconds)}s · ${h.pool_checked}/${h.pool_size} names checked` +
          (h.last_round_at ? ` · last round ${ago(h.last_round_at)}: ${h.last_batch.map((n) => "@" + n).join(", ")}` : " · first round starting…") +
          (h.last_error ? ` · error: ${h.last_error}` : "")
        : "Background hunter is off (set HUNT_ENABLED=1). Showing names found by scans.";
      // one card per username, listing every platform where it's free
      const byName = new Map();
      for (const i of a.items) {
        const g = byName.get(i.username) || { username: i.username, found: 0, platforms: [] };
        g.platforms.push(i);
        g.platforms.sort((x, y) => (x.platform > y.platform ? -1 : 1)); // telegram first
        g.found = Math.max(g.found, i.first_free || i.checked_at);
        byName.set(i.username, g);
      }
      const groups = [...byName.values()].sort((x, y) => y.found - x.found);
      el("#liveCount").textContent = `· ${groups.length} names`;
      const badge = (i) => `<a class="pf" href="${link(i)}" target="_blank" rel="noopener">${i.platform === "telegram" ? "✈️ Telegram" : "📸 Instagram"}</a>`;
      el("#liveList").innerHTML = groups.length
        ? groups.map((g) => `<div class="find${seen && !seen.has(g.username) ? " new" : ""}">
            <span class="h">@${esc(g.username)}</span>
            <span class="m"><span class="pfs">${g.platforms.map(badge).join("")}</span><span>found ${ago(g.found)}</span></span>
          </div>`).join("")
        : `<span class="muted small">Nothing found yet — the hunter adds names here as it finds them.</span>`;
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
