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

function esc(s) { const d = document.createElement("div"); d.textContent = s; return d.innerHTML; }

function render(jobs) {
  $("jobs").innerHTML = jobs.map((j) => {
    const files = j.files.map((f) => {
      const href = "/files/" + f.split("/").map(encodeURIComponent).join("/");
      return `<li><span class="name">${esc(f)}</span><a href="${href}" target="_blank">view</a></li>`;
    }).join("");
    const logOpen = openLogs.has(j.id) || j.status === "running" || j.status === "failed";
    return `<div class="job">
      <header><span class="url">${esc(j.url)}</span><span class="status ${j.status}">${j.status}</span></header>
      ${files ? `<ul class="files">${files}</ul>` : ""}
      ${j.log ? `<details data-id="${j.id}" ${logOpen ? "open" : ""}><summary>log</summary><pre>${esc(j.log)}</pre></details>` : ""}
    </div>`;
  }).join("");
  document.querySelectorAll("details[data-id]").forEach((d) => {
    const pre = d.querySelector("pre"); pre.scrollTop = pre.scrollHeight;
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
