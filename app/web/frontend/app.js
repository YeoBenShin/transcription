const $ = (id) => document.getElementById(id);
const openLogs = new Set();

$("form").addEventListener("submit", async (e) => {
  e.preventDefault();
  $("error").textContent = "";
  const res = await fetch("/api/jobs", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ url: $("url").value, model: $("model").value }),
  });
  if (!res.ok) { $("error").textContent = (await res.json()).error; return; }
  $("url").value = "";
  refresh();
});

function duration(sec) {
  const t = Math.floor(sec), h = Math.floor(t / 3600), m = Math.floor(t / 60) % 60, s = String(t % 60).padStart(2, "0");
  return h ? `${h}:${String(m).padStart(2, "0")}:${s}` : `${m}:${s}`;
}

function esc(s) { const d = document.createElement("div"); d.textContent = s; return d.innerHTML; }

function render(jobs) {
  const scroll = {};
  document.querySelectorAll("details[data-id] pre").forEach((pre) => {
    const atBottom = pre.scrollHeight - pre.scrollTop - pre.clientHeight < 4;
    scroll[pre.parentElement.dataset.id] = atBottom ? null : pre.scrollTop;
  });
  $("jobs").innerHTML = jobs.map((j) => {
    const files = j.files.map((f) => {
      const href = "/files/" + f.split("/").map(encodeURIComponent).join("/");
      return `<li><span class="name">${esc(f)}</span><a href="${href}" target="_blank">view</a></li>`;
    }).join("");
    const logOpen = openLogs.has(j.id) || j.status === "running" || j.status === "failed";
    return `<div class="job">
      <header><span class="url">${esc(j.url)}</span>${j.elapsed == null ? "" : `<span class="elapsed">${duration(j.elapsed)}</span>`}<span class="status ${j.status}">${j.status}</span></header>
      ${files ? `<ul class="files">${files}</ul>` : ""}
      ${j.log ? `<details data-id="${j.id}" ${logOpen ? "open" : ""}><summary>log</summary><pre>${esc(j.log)}</pre></details>` : ""}
    </div>`;
  }).join("");
  document.querySelectorAll("details[data-id]").forEach((d) => {
    const pre = d.querySelector("pre");
    pre.scrollTop = scroll[d.dataset.id] ?? pre.scrollHeight;
    d.addEventListener("toggle", () => d.open ? openLogs.add(d.dataset.id) : openLogs.delete(d.dataset.id));
  });
}

async function refresh() {
  const jobs = await (await fetch("/api/jobs")).json();
  render(jobs);
  return jobs.some((j) => j.status === "queued" || j.status === "running");
}

(async function loop() {
  let busy = false;
  try { busy = await refresh(); } catch {}
  setTimeout(loop, busy ? 1000 : 4000);
})();
