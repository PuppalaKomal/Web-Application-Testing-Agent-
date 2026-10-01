const form = document.getElementById("scan-form");
const urlInput = document.getElementById("url-input");
const maxPagesInput = document.getElementById("max-pages");
const maxDepthInput = document.getElementById("max-depth");
const scanButton = document.getElementById("scan-button");
const statusNote = document.getElementById("status-note");
const results = document.getElementById("results");

form.addEventListener("submit", async (e) => {
  e.preventDefault();
  const url = urlInput.value.trim();
  if (!url) return;

  const maxPages = maxPagesInput.value || 8;
  const maxDepth = maxDepthInput.value || 1;

  setLoading(true);
  statusNote.textContent = "Scanning\u2026 this can take a few seconds.";
  statusNote.classList.remove("error");

  try {
    const res = await fetch(
      `/api/scan?url=${encodeURIComponent(url)}&max_pages=${maxPages}&max_depth=${maxDepth}`
    );
    if (!res.ok) {
      const body = await res.json().catch(() => ({}));
      throw new Error(body.detail || `Request failed with status ${res.status}`);
    }
    const data = await res.json();
    render(data);
    statusNote.textContent = `Done \u2014 scanned ${data.site_map.pages.length} page(s).`;
  } catch (err) {
    statusNote.textContent = err.message || "Something went wrong.";
    statusNote.classList.add("error");
    results.classList.add("hidden");
  } finally {
    setLoading(false);
  }
});

function setLoading(isLoading) {
  scanButton.disabled = isLoading;
  scanButton.textContent = isLoading ? "Scanning\u2026" : "Scan";
}

function esc(value) {
  if (value === null || value === undefined) return "-";
  const div = document.createElement("div");
  div.textContent = String(value);
  return div.innerHTML;
}

function render(data) {
  const { site_map: siteMap, bugs, checks } = data;
  const pages = siteMap.pages;
  const reachable = pages.filter((p) => p.reachable).length;
  const severityCounts = bugs.reduce((acc, b) => {
    acc[b.severity] = (acc[b.severity] || 0) + 1;
    return acc;
  }, {});

  document.getElementById("stat-strip").innerHTML = `
    ${stat(pages.length, "Pages scanned")}
    ${stat(reachable, "Reachable", "accent")}
    ${stat(bugs.length, "Issues found", bugs.length ? "danger" : "accent")}
    ${stat(severityCounts.critical || 0, "Critical", "danger")}
    ${stat(severityCounts.high || 0, "High", "warn")}
    ${stat(checks.length, "Checks run")}
  `;

  const issuesBody = document.querySelector("#issues-table tbody");
  issuesBody.innerHTML = bugs
    .map(
      (b) => `<tr>
        <td>${esc(b.title)}</td>
        <td><span class="severity severity-${esc(b.severity)}">${esc(b.severity)}</span></td>
        <td><span class="tag">${esc(b.likely_layer)}</span></td>
        <td class="mono">${esc(b.page)}</td>
        <td class="evidence">${esc(b.evidence)}</td>
      </tr>`
    )
    .join("");
  document.getElementById("issues-empty").hidden = bugs.length > 0;

  const checksBody = document.querySelector("#checks-table tbody");
  checksBody.innerHTML = checks
    .map(
      (c) => `<tr>
        <td>${esc(c.title)}</td>
        <td><span class="tag">${esc(c.type)}</span></td>
        <td>${esc(c.priority)}</td>
      </tr>`
    )
    .join("");

  const pagesBody = document.querySelector("#pages-table tbody");
  pagesBody.innerHTML = pages
    .map(
      (p) => `<tr>
        <td><a href="${esc(p.url)}" target="_blank" rel="noopener">${esc(p.url)}</a></td>
        <td>${p.reachable ? "yes" : "no"}</td>
        <td>${esc(p.status)}</td>
        <td>${p.duration_ms !== undefined ? esc(p.duration_ms) + " ms" : "-"}</td>
        <td>${esc(p.images_without_alt)}</td>
        <td>${esc(p.h1_count)}</td>
      </tr>`
    )
    .join("");

  results.classList.remove("hidden");
}

function stat(value, label, tone) {
  return `<div class="stat">
    <div class="stat-num ${tone || ""}">${value}</div>
    <div class="stat-label">${label}</div>
  </div>`;
}
