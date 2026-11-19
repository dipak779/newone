const $ = (s) => document.querySelector(s);
const $$ = (s) => [...document.querySelectorAll(s)];

const state = { results: {}, order: [], platforms: [], filter: "any", running: null };
const SAVED_KEY = "handlehunt.saved";

function loadSaved() {
  try { return JSON.parse(localStorage.getItem(SAVED_KEY)) || []; } catch { return []; }
}
function storeSaved(list) {
  try { localStorage.setItem(SAVED_KEY, JSON.stringify(list)); } catch {}
  renderSaved();
}

// ---------- tabs
let activeTab = "manual";
$$(".tab").forEach((t) => t.addEventListener("click", () => {
  activeTab = t.dataset.tab;
  $$(".tab").forEach((x) => x.classList.toggle("active", x === t));
  $$(".tabpane").forEach((p) => p.classList.toggle("hidden", p.dataset.pane !== activeTab));
}));

// ---------- quota
async function refreshQuota() {
  try {
    const q = await (await fetch("/api/limits")).json();
    $("#quota").textContent = `${q.remaining}/${q.per_hour} checks left this hour · max ${q.max_names} names`;
  } catch {}
}

// ---------- random generator
$("#genBtn").addEventListener("click", async () => {
  const p = new URLSearchParams({
    count: $("#rCount").value, min_len: $("#rMin").value, max_len: $("#rMax").value,
  });
  const res = await fetch("/api/words?" + p);
  if (!res.ok) return showError("Couldn't generate names — check the numbers.");
  const { names } = await res.json();
  $("#names").value = names.join(", ");
  $$(".tab")[0].click();
});

// ---------- scanning
function parseNames(text) {
  const names = text.split(/[\s,]+/).map((n) => n.trim().replace(/^@+/, "").toLowerCase()).filter(Boolean);
  return [...new Set(names)];
}
function showError(msg) {
  $("#error").textContent = msg;
  $("#error").classList.toggle("hidden", !msg);
}

async function scan(names) {
  showError("");
  const platforms = $$(".platforms input:checked").map((i) => i.value);
  if (!names.length) return showError("Add at least one username (or generate some).");
  if (!platforms.length) return showError("Pick at least one platform.");
  if (state.running) state.running.abort();

  state.platforms = platforms;
  state.order = names;
  state.results = Object.fromEntries(names.map((n) => [n, {}]));
  $$(".col-telegram").forEach((e) => e.classList.toggle("hidden", !platforms.includes("telegram")));
  $$(".col-instagram").forEach((e) => e.classList.toggle("hidden", !platforms.includes("instagram")));
  $("#resultsCard").classList.remove("hidden");
  renderRows();

  const total = names.length * platforms.length;
  let done = 0;
  const btn = $("#scanBtn");
  btn.disabled = true; btn.textContent = "Scanning…";
  $("#progressWrap").classList.remove("hidden");
  $("#progress").style.width = "0";

  const url = "/api/scan?" + new URLSearchParams({ names: names.join(","), platforms: platforms.join(",") });
  const ctrl = new AbortController();
  state.running = ctrl;
  try {
    const res = await fetch(url, { signal: ctrl.signal });
    if (!res.ok) {
      let msg = `Scan failed (HTTP ${res.status}).`;
      try { msg = (await res.json()).detail || msg; } catch {}
      throw new Error(msg);
    }
    // Read the Server-Sent Events stream by hand so we can see HTTP errors above.
    const reader = res.body.getReader();
    const dec = new TextDecoder();
    let buf = "";
    for (;;) {
      const { value, done: end } = await reader.read();
      if (end) break;
      buf += dec.decode(value, { stream: true });
      let i;
      while ((i = buf.indexOf("\n\n")) >= 0) {
        const block = buf.slice(0, i); buf = buf.slice(i + 2);
        const ev = /^event: (.*)$/m.exec(block)?.[1];
        const data = /^data: (.*)$/m.exec(block)?.[1];
        if (ev !== "result" || !data) continue;
        const r = JSON.parse(data);
        (state.results[r.username] ||= {})[r.platform] = r;
        done++;
        $("#progress").style.width = `${(done / total) * 100}%`;
        renderRows();
      }
    }
    if (done < total) showError("Connection dropped before the scan finished. Try again.");
  } catch (err) {
    if (err.name !== "AbortError") showError(err.message || "Connection lost. Try again.");
  } finally {
    if (state.running === ctrl) {
      btn.disabled = false; btn.textContent = "Scan usernames";
      state.running = null; refreshQuota();
    }
  }
}

$("#scanBtn").addEventListener("click", () => scan(parseNames($("#names").value)));
$("#names").addEventListener("keydown", (e) => {
  if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) $("#scanBtn").click();
});

// ---------- results table
const isFree = (r) => r && r.status === "AVAILABLE";
function passes(name) {
  const res = state.results[name];
  const free = state.platforms.map((p) => isFree(res[p]));
  if (state.filter === "any") return free.some(Boolean);
  if (state.filter === "both") return free.every(Boolean);
  return true;
}
function cell(r) {
  if (!r) return `<span class="st PENDING">checking</span>`;
  const YN = { AVAILABLE: "✅ Yes, available", TAKEN: "❌ No, taken", FRAGMENT: "❌ No, for sale on Fragment", INVALID: "❌ No, not allowed", UNKNOWN: "⏳ Couldn't check", UNCONFIRMED: "⚠️ Not confirmed yet" };
  return `<span class="st ${r.status}">${YN[r.status] || r.status}</span><span class="detail">${escapeHtml(r.detail)}${r.cached ? " · cached" : ""}</span>`;
}
function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}
function renderRows() {
  const saved = new Set(loadSaved());
  const rows = state.order.filter(passes).map((n) => {
    const res = state.results[n];
    const win = state.platforms.some((p) => isFree(res[p]));
    const cols = ["telegram", "instagram"].map((p) =>
      `<td class="col-${p}${state.platforms.includes(p) ? "" : " hidden"}">${state.platforms.includes(p) ? cell(res[p]) : ""}</td>`
    ).join("");
    return `<tr class="${win ? "win" : ""}"><td class="name">@${escapeHtml(n)}</td>${cols}
      <td><button class="star ${saved.has(n) ? "on" : ""}" data-n="${escapeHtml(n)}" title="Save">★</button></td></tr>`;
  });
  const scanning = Object.values(state.results).some((r) => state.platforms.some((p) => !r[p]));
  $("#rows").innerHTML = rows.join("") || `<tr><td colspan="4" class="muted">${
    scanning ? "Checking… free names will appear here." : "None of these are free. Tap “All” to see the taken ones."}</td></tr>`;
  const all = Object.values(state.results).flatMap((r) => Object.values(r));
  $("#summary").textContent = `· ${all.filter(isFree).length} free of ${all.length} checks`;
}
$("#rows").addEventListener("click", (e) => {
  const b = e.target.closest(".star");
  if (!b) return;
  const list = loadSaved(), n = b.dataset.n;
  storeSaved(list.includes(n) ? list.filter((x) => x !== n) : [...list, n]);
  renderRows();
});
$$("#filter button").forEach((b) => b.addEventListener("click", () => {
  state.filter = b.dataset.f;
  $$("#filter button").forEach((x) => x.classList.toggle("active", x === b));
  renderRows();
}));

function freeLines() {
  return state.order.flatMap((n) => state.platforms.filter((p) => isFree(state.results[n][p])).map((p) => `@${n} (${p})`));
}
$("#copyBtn").addEventListener("click", async () => {
  const lines = freeLines();
  try { await navigator.clipboard.writeText(lines.join("\n")); } catch {}
  $("#copyBtn").textContent = lines.length ? `Copied ${lines.length}` : "None free";
  setTimeout(() => ($("#copyBtn").textContent = "Copy free"), 1500);
});
$("#csvBtn").addEventListener("click", () => {
  const rows = [["username", "platform", "status", "detail"]];
  state.order.forEach((n) => Object.values(state.results[n]).forEach((r) => rows.push([n, r.platform, r.status, r.detail])));
  const csv = rows.map((r) => r.map((v) => `"${String(v).replace(/"/g, '""')}"`).join(",")).join("\n");
  const a = Object.assign(document.createElement("a"), {
    href: URL.createObjectURL(new Blob([csv], { type: "text/csv" })), download: "handlehunt-results.csv",
  });
  a.click();
});

// ---------- saved
function renderSaved() {
  const list = loadSaved();
  $("#savedCount").textContent = list.length;
  $("#savedList").innerHTML = list.length
    ? list.map((n) => `<span class="chip">@${escapeHtml(n)}<button data-n="${escapeHtml(n)}" title="Remove">✕</button></span>`).join("")
    : `<span class="muted small">No saved names yet.</span>`;
}
$("#savedList").addEventListener("click", (e) => {
  const b = e.target.closest("button");
  if (b) { storeSaved(loadSaved().filter((x) => x !== b.dataset.n)); renderRows(); }
});
$("#rescanSaved").addEventListener("click", () => {
  const list = loadSaved();
  if (list.length) { $("#names").value = list.join(", "); scan(list); $("#resultsCard").scrollIntoView({ behavior: "smooth" }); }
});

renderSaved();
refreshQuota();
