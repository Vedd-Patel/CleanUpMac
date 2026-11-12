// Tidy: the page. Talks to tidy/server.py, nothing else.

const TOKEN = document.querySelector('meta[name="tidy-token"]').content;
const GROUPS = ["Docker", "Xcode and iOS", "Build tools", "Editors and browsers",
                "Apps", "System", "Downloads", "Large folders"];
const LEVEL_TEXT = { safe: "Safe", careful: "Check first", info: "Look only" };
const SMALL = 10 * 1024 * 1024;

const state = {
  disk: null,
  items: [],
  running: false,
  picked: new Set(),
  open: new Set(),
  filter: "all",
  hideSmall: true,
};

const $ = (sel) => document.querySelector(sel);

// ------------------------------------------------------------ formatting

function bytes(n) {
  if (n == null) return "–";
  if (n < 1000) return n + " B";
  const units = ["KB", "MB", "GB", "TB"];
  let i = -1;
  do { n /= 1000; i++; } while (n >= 1000 && i < units.length - 1);
  return (n >= 100 || i === 0 ? n.toFixed(0) : n.toFixed(1)) + " " + units[i];
}

function ago(ts) {
  if (!ts) return "–";
  const s = Date.now() / 1000 - ts;
  if (s < 90) return "just now";
  if (s < 3600) return Math.round(s / 60) + " minutes ago";
  if (s < 86400) return Math.round(s / 3600) + " hours ago";
  const d = Math.round(s / 86400);
  if (d < 45) return d + (d === 1 ? " day ago" : " days ago");
  const date = new Date(ts * 1000);
  return date.toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" });
}

function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

// backticks in catalog text become <code>
function prose(s) {
  return esc(s).replace(/`([^`]+)`/g, "<code>$1</code>");
}

// what deleting this item actually gets back
function gain(item) {
  if (item.reclaimable != null) return item.reclaimable;
  return item.size || 0;
}

function deletable(item) {
  return item.action !== "none" && item.measured && item.exists && gain(item) > 0;
}

// ------------------------------------------------------------ server

async function api(path, body) {
  const opts = body === undefined ? {} : {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-Tidy-Token": TOKEN },
    body: JSON.stringify(body),
  };
  const res = await fetch(path, opts);
  if (!res.ok) throw new Error((await res.json().catch(() => ({}))).error || res.statusText);
  return res.json();
}

let pollTimer = null;

async function poll() {
  clearTimeout(pollTimer);
  try {
    const data = await api("/api/state");
    state.disk = data.disk;
    state.items = data.scan.items;
    state.running = data.scan.running;
    for (const id of [...state.picked]) {
      const it = state.items.find((i) => i.id === id);
      if (!it || !deletable(it)) state.picked.delete(id);
    }
    render();
    if (state.running) pollTimer = setTimeout(poll, 700);
  } catch (e) {
    $("#scan-status").textContent = "Lost contact with the server. Is tidy.sh still running?";
    pollTimer = setTimeout(poll, 3000);
  }
}

async function rescan() {
  state.picked.clear();
  await api("/api/scan", {});
  poll();
}

// ------------------------------------------------------------ rendering

function render() {
  renderDisk();
  renderList();
  renderTray();
}

function renderDisk() {
  const d = state.disk;
  if (!d) return;
  $("#free").textContent = bytes(d.free);
  $("#total").textContent = bytes(d.total);
  const pick = pickedBytes();
  $("#bar-used").style.width = ((d.used - Math.min(pick, d.used)) / d.total * 100) + "%";
  $("#bar-pick").style.width = (Math.min(pick, d.used) / d.total * 100) + "%";

  const done = state.items.filter((i) => i.measured).length;
  if (state.running) {
    $("#scan-status").textContent = `Scanning… ${done} of ${state.items.length || "?"} checked`;
  } else {
    const total = state.items.filter((i) => i.level === "safe" && deletable(i))
                             .reduce((s, i) => s + gain(i), 0);
    $("#scan-status").textContent = `${bytes(total)} can be freed without losing anything`;
  }
  $("#rescan").disabled = state.running;
}

function visible(item) {
  if (state.filter !== "all" && item.level !== state.filter) return false;
  if (state.hideSmall && item.measured && (item.size ?? 1) < SMALL
      && !(item.size == null && item.note)) return false;
  return true;
}

function renderList() {
  const list = $("#list");
  if (!state.items.length) {
    list.innerHTML = `<div class="skeleton">Looking around your disk…</div>`;
    return;
  }

  const html = [];
  for (const g of GROUPS) {
    const rows = state.items.filter((i) => i.group === g && visible(i));
    if (!rows.length) continue;
    rows.sort((a, b) => (b.size ?? -1) - (a.size ?? -1));
    const free = rows.filter(deletable).reduce((s, i) => s + gain(i), 0);
    const head = free ? `${bytes(free)} can be freed` : "";
    html.push(`<section class="group"><div class="group-head"><h3>${esc(g)}</h3><span>${head}</span></div>`);
    for (const it of rows) html.push(rowHTML(it));
    html.push(`</section>`);
  }
  list.innerHTML = html.join("") ||
    `<div class="skeleton">${state.running ? "Still scanning…" : "Nothing matches this filter."}</div>`;
}

function rowHTML(it) {
  const can = deletable(it);
  const picked = state.picked.has(it.id);
  const open = state.open.has(it.id);
  const empty = it.measured && it.exists === false;

  let size = `<span class="size pending">…</span>`;
  if (it.measured) {
    if (it.size == null) size = `<span class="size pending">–</span>`;
    else if (it.reclaimable != null && it.reclaimable !== it.size)
      size = `<span class="size" title="${bytes(it.size)} in total">${bytes(it.reclaimable)}</span>`;
    else size = `<span class="size">${bytes(it.size)}</span>`;
  }

  const meta = [];
  if (empty) meta.push("Not on this Mac");
  else if (it.note) meta.push(esc(it.note));
  if (it.running && it.running.length) meta.push(`<span class="running">${esc(it.running.join(", "))} is open</span>`);
  if (it.error) meta.push(`<span class="running">${esc(it.error)}</span>`);

  const paths = it.paths.length ? it.paths.map((p) => `<code>${esc(p)}</code>`).join("<br>") : "–";
  let how = "";
  if (it.action === "run") how = `Runs <code>${esc(it.cmd.join(" "))}</code>`;
  else if (it.action === "empty") how = "Deletes everything inside the folder, keeps the folder";
  else if (it.action === "remove") how = "Deletes the whole folder";
  else how = prose(it.how) || "Nothing to do from here";

  const rows = [
    ["What it is", prose(it.what)],
    ["Used by", esc(it.used_by)],
    ["Where", paths],
    ["Last changed", ago(it.last_changed)],
  ];
  if (it.reclaimable != null) rows.push(["Total / freeable", `${bytes(it.size)} / ${bytes(it.reclaimable)}`]);
  if (it.after) rows.push(["After deleting", prose(it.after)]);
  rows.push([it.action === "none" ? "How to clean" : "What Delete does", how]);
  if (it.quit && it.quit.length) rows.push(["Before deleting", `Quits ${esc(it.quit.join(", "))} if it is open`]);

  const canReveal = it.real_paths && it.real_paths.length && it.exists;

  return `
  <div class="row ${open ? "open" : ""} ${empty ? "empty" : ""}" data-id="${esc(it.id)}">
    <div class="row-main">
      <input type="checkbox" ${picked ? "checked" : ""} ${can ? "" : "disabled"}
             title="${can ? "Select to delete" : "Nothing to delete here"}">
      <div><div class="name">${esc(it.name)}</div><div class="meta">${meta.join(" · ") || esc(it.used_by)}</div></div>
      <span class="tag ${it.level}">${LEVEL_TEXT[it.level]}</span>
      ${size}
    </div>
    <div class="details">
      <dl>${rows.map(([k, v]) => `<dt>${k}</dt><dd>${v}</dd>`).join("")}</dl>
      <div class="actions">
        ${canReveal ? `<button class="btn small" data-act="reveal">Show in Finder</button>` : ""}
        ${can ? `<button class="btn small danger" data-act="delete-one">Delete this…</button>` : ""}
      </div>
    </div>
  </div>`;
}

function pickedItems() {
  return state.items.filter((i) => state.picked.has(i.id));
}

function pickedBytes() {
  return pickedItems().reduce((s, i) => s + gain(i), 0);
}

function renderTray() {
  const n = state.picked.size;
  $("#tray").hidden = n === 0;
  $("#pick-count").textContent = n;
  $("#pick-size").textContent = bytes(pickedBytes());
}

// ------------------------------------------------------------ confirm + delete

let pending = [];

function askToDelete(items) {
  pending = items;
  const total = items.reduce((s, i) => s + gain(i), 0);
  $("#confirm-title").textContent = items.length === 1 ? `Delete ${items[0].name}?` : `Delete ${items.length} items?`;
  $("#confirm-lead").textContent = `This frees about ${bytes(total)}.`;
  $("#confirm-list").innerHTML = items.map((i) =>
    `<li><span>${esc(i.name)} <span class="tag ${i.level}">${LEVEL_TEXT[i.level]}</span></span><span class="r">${bytes(gain(i))}</span></li>`).join("");

  const careful = items.some((i) => i.level === "careful");
  $("#confirm-warn").hidden = !careful;
  $("#confirm-ok").checked = false;
  $("#confirm-yes").disabled = careful;

  const quits = [...new Set(items.flatMap((i) => (i.running || []).filter((a) => (i.quit || []).includes(a))))];
  const busy = [...new Set(items.flatMap((i) => (i.running || []).filter((a) => !(i.quit || []).includes(a))))];
  const notes = [];
  if (quits.length) notes.push(`${quits.join(", ")} will be asked to quit first.`);
  if (busy.length) notes.push(`${busy.join(", ")} is open. It will rebuild what it needs, but quitting it first is cleaner.`);
  $("#confirm-quit").hidden = !notes.length;
  $("#confirm-quit").textContent = notes.join(" ");

  $("#confirm").hidden = false;
  $("#confirm-no").focus();
}

function closeConfirm() {
  $("#confirm").hidden = true;
  pending = [];
}

async function doDelete() {
  const items = pending;
  closeConfirm();
  if (!items.length) return;

  $("#working-title").textContent = "Deleting…";
  $("#working-done").hidden = true;
  $("#working-list").innerHTML = items.map((i) =>
    `<li data-id="${esc(i.id)}"><span>${esc(i.name)}</span><span class="r">waiting</span></li>`).join("");
  $("#working").hidden = false;

  let freed = 0, failed = 0;
  // one at a time so the list shows progress and a failure does not stop the rest
  for (const it of items) {
    const li = $(`#working-list li[data-id="${CSS.escape(it.id)}"] .r`);
    li.textContent = "deleting…";
    try {
      const { results, disk } = await api("/api/clean", { ids: [it.id] });
      const r = results[0];
      state.disk = disk;
      if (r.ok) {
        freed += r.freed;
        li.innerHTML = `<span class="ok">freed ${bytes(r.freed)}</span>`;
        const idx = state.items.findIndex((x) => x.id === it.id);
        if (idx >= 0) state.items[idx] = r.item;
      } else {
        failed++;
        li.innerHTML = `<span class="bad">${esc(r.error)}</span>`;
      }
    } catch (e) {
      failed++;
      li.innerHTML = `<span class="bad">${esc(e.message)}</span>`;
    }
    state.picked.delete(it.id);
    render();
  }

  $("#working-title").textContent = failed
    ? `Freed ${bytes(freed)}, ${failed} could not be deleted`
    : `Done. Freed ${bytes(freed)}`;
  $("#working-done").hidden = false;
  $("#working-done").focus();
}

// ------------------------------------------------------------ events

function toast(msg) {
  const t = $("#toast");
  t.textContent = msg;
  t.hidden = false;
  clearTimeout(toast.t);
  toast.t = setTimeout(() => (t.hidden = true), 2400);
}

$("#list").addEventListener("click", async (e) => {
  const row = e.target.closest(".row");
  if (!row) return;
  const id = row.dataset.id;
  const item = state.items.find((i) => i.id === id);

  if (e.target.matches("input[type=checkbox]")) {
    e.target.checked ? state.picked.add(id) : state.picked.delete(id);
    renderDisk(); renderTray();
    return;
  }
  const act = e.target.dataset.act;
  if (act === "reveal") {
    const { ok } = await api("/api/reveal", { id });
    if (!ok) toast("It is not there any more");
    return;
  }
  if (act === "delete-one") { askToDelete([item]); return; }

  if (e.target.closest(".row-main")) {
    state.open.has(id) ? state.open.delete(id) : state.open.add(id);
    row.classList.toggle("open");
  }
});

$("#filters").addEventListener("click", (e) => {
  const b = e.target.closest("button");
  if (!b) return;
  state.filter = b.dataset.f;
  document.querySelectorAll("#filters button").forEach((x) => x.classList.toggle("on", x === b));
  renderList();
});

$("#hide-small").addEventListener("change", (e) => { state.hideSmall = e.target.checked; renderList(); });
$("#rescan").addEventListener("click", rescan);
$("#clear-pick").addEventListener("click", () => { state.picked.clear(); render(); });
$("#delete").addEventListener("click", () => askToDelete(pickedItems()));
$("#confirm-no").addEventListener("click", closeConfirm);
$("#confirm-yes").addEventListener("click", doDelete);
$("#confirm-ok").addEventListener("change", (e) => ($("#confirm-yes").disabled = !e.target.checked));
$("#working-done").addEventListener("click", () => { $("#working").hidden = true; poll(); });
$("#confirm").addEventListener("click", (e) => { if (e.target.id === "confirm") closeConfirm(); });

document.addEventListener("keydown", (e) => {
  if (e.key === "Escape" && !$("#confirm").hidden) closeConfirm();
});

poll();
