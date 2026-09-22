// ---------------------------------------------------------------------------
// Config
// ---------------------------------------------------------------------------
const API_BASE = window.API_BASE || "http://localhost:8000";
const WS_BASE = API_BASE.replace(/^http/, "ws");

let riskChart = null;
let trendChart = null;
let liveSocket = null;
let liveRunning = false;

// ---------------------------------------------------------------------------
// Navigation
// ---------------------------------------------------------------------------
const VIEW_META = {
  dashboard: { title: "Fraud Detection Dashboard", subtitle: "Real-time transaction risk monitoring" },
  network: { title: "Transaction Network", subtitle: "Relationships between users, cards, devices, IPs and merchants" },
  live: { title: "Live Simulation", subtitle: "Streaming synthetic transactions scored in real time" },
};

document.querySelectorAll(".nav-item").forEach((btn) => {
  btn.addEventListener("click", () => switchView(btn.dataset.view));
});

function switchView(view) {
  document.querySelectorAll(".nav-item").forEach((b) => b.classList.toggle("active", b.dataset.view === view));
  document.querySelectorAll(".view").forEach((v) => v.classList.toggle("active", v.id === `view-${view}`));
  const meta = VIEW_META[view];
  document.getElementById("viewTitle").textContent = meta.title;
  document.getElementById("viewSubtitle").textContent = meta.subtitle;

  if (view === "dashboard") loadDashboard();
  if (view === "network") loadNetwork();
}

// ---------------------------------------------------------------------------
// API helpers
// ---------------------------------------------------------------------------
async function apiGet(path) {
  const res = await fetch(`${API_BASE}${path}`);
  if (!res.ok) throw new Error(`GET ${path} failed: ${res.status}`);
  return res.json();
}

async function apiPost(path, body) {
  const res = await fetch(`${API_BASE}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    const detail = await res.json().catch(() => ({}));
    throw new Error(detail.detail || `POST ${path} failed: ${res.status}`);
  }
  return res.json();
}

async function checkHealth() {
  const pill = document.getElementById("apiStatus");
  try {
    await apiGet("/api/health");
    pill.classList.add("online");
    pill.classList.remove("offline");
    pill.innerHTML = `<span class="dot"></span> API Online`;
  } catch (e) {
    pill.classList.add("offline");
    pill.classList.remove("online");
    pill.innerHTML = `<span class="dot"></span> API Offline`;
  }
}

// ---------------------------------------------------------------------------
// Dashboard
// ---------------------------------------------------------------------------
async function loadDashboard() {
  try {
    const [stats, txData] = await Promise.all([
      apiGet("/api/statistics"),
      apiGet("/api/transactions?limit=25"),
    ]);
    renderStats(stats);
    renderRiskChart(stats.by_level || {});
    renderTrendChart(txData.transactions || []);
    renderTable(txData.transactions || []);
  } catch (e) {
    console.error(e);
    document.getElementById("txTableBody").innerHTML =
      `<tr><td colspan="8" class="empty-row">Could not reach the API at ${API_BASE}. Is the backend running?</td></tr>`;
  }
}

function renderStats(stats) {
  document.getElementById("statTotal").textContent = stats.total_transactions ?? "—";
  document.getElementById("statSuspicious").textContent = stats.suspicious_transactions ?? "—";
  document.getElementById("statHighRisk").textContent = stats.high_risk_transactions ?? "—";
  document.getElementById("statDetectionRate").textContent =
    stats.detection_rate !== undefined ? `${stats.detection_rate}%` : "—";
}

function renderRiskChart(byLevel) {
  const ctx = document.getElementById("riskChart");
  const labels = ["LOW", "MEDIUM", "HIGH", "CRITICAL"];
  const colors = ["#29d391", "#f2b84b", "#ff9142", "#ff5c72"];
  const data = labels.map((l) => byLevel[l] || 0);

  if (riskChart) riskChart.destroy();
  riskChart = new Chart(ctx, {
    type: "doughnut",
    data: { labels, datasets: [{ data, backgroundColor: colors, borderWidth: 0 }] },
    options: {
      plugins: { legend: { position: "bottom", labels: { color: "#8b96ab", boxWidth: 10, padding: 14 } } },
      cutout: "68%",
    },
  });
}

function renderTrendChart(transactions) {
  const ctx = document.getElementById("trendChart");
  const ordered = [...transactions].reverse();
  const labels = ordered.map((t) => t.tx_id);
  const scores = ordered.map((t) => t.risk_score);

  if (trendChart) trendChart.destroy();
  trendChart = new Chart(ctx, {
    type: "line",
    data: {
      labels,
      datasets: [
        {
          label: "Risk Score",
          data: scores,
          borderColor: "#4f8dff",
          backgroundColor: "rgba(79,141,255,0.15)",
          tension: 0.35,
          fill: true,
          pointRadius: 2,
        },
      ],
    },
    options: {
      plugins: { legend: { display: false } },
      scales: {
        x: { ticks: { display: false }, grid: { color: "#1f2937" } },
        y: { min: 0, max: 100, ticks: { color: "#8b96ab" }, grid: { color: "#1f2937" } },
      },
    },
  });
}

function riskBadge(level) {
  return `<span class="badge ${level}">${level}</span>`;
}

function formatAmount(v) {
  return `${Math.round(v).toLocaleString("en-US")} ₸`;
}

function formatTime(iso) {
  try {
    const d = new Date(iso);
    if (isNaN(d.getTime())) return iso;
    return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  } catch {
    return iso;
  }
}

function renderTable(transactions) {
  const body = document.getElementById("txTableBody");
  if (!transactions.length) {
    body.innerHTML = `<tr><td colspan="8" class="empty-row">No transactions yet. Click "+ Analyze Transaction" to add one.</td></tr>`;
    return;
  }
  body.innerHTML = transactions
    .map(
      (t) => `
    <tr>
      <td>${t.tx_id}</td>
      <td>${t.user_id}</td>
      <td>${formatAmount(t.amount)}</td>
      <td>${formatTime(t.tx_time)}</td>
      <td>${t.location || "—"}</td>
      <td>${t.device_id || "—"}</td>
      <td>${t.risk_score}%</td>
      <td>${riskBadge(t.risk_level)}</td>
    </tr>`
    )
    .join("");
}

document.getElementById("refreshTxBtn").addEventListener("click", loadDashboard);

// ---------------------------------------------------------------------------
// Network view (simple layered SVG graph, no external graph library needed)
// ---------------------------------------------------------------------------
async function loadNetwork() {
  const svg = document.getElementById("networkSvg");
  svg.innerHTML = "";
  try {
    const graph = await apiGet("/api/network");
    renderNetwork(graph);
  } catch (e) {
    console.error(e);
    svg.innerHTML = `<text x="20" y="30" fill="#8b96ab" font-size="14">Could not load network data.</text>`;
  }
}

const NETWORK_COLUMNS = ["user", "card", "device", "ip", "merchant"];
const NETWORK_COLORS = { user: "#4f8dff", card: "#29d391", device: "#f2b84b", ip: "#b98bff", merchant: "#ff9142" };

function renderNetwork(graph) {
  const svg = document.getElementById("networkSvg");
  const width = 1000;
  const height = 560;
  const colWidth = width / NETWORK_COLUMNS.length;

  const byType = {};
  NETWORK_COLUMNS.forEach((c) => (byType[c] = []));
  graph.nodes.forEach((n) => {
    if (byType[n.type]) byType[n.type].push(n);
  });

  const positions = {};
  NETWORK_COLUMNS.forEach((col, ci) => {
    const nodes = byType[col];
    const x = colWidth * ci + colWidth / 2;
    const gap = height / (nodes.length + 1);
    nodes.forEach((n, i) => {
      positions[n.id] = { x, y: gap * (i + 1), node: n };
    });
  });

  let svgContent = "";

  // Edges first (so nodes render on top)
  graph.edges.forEach((e) => {
    const a = positions[e.source];
    const b = positions[e.target];
    if (!a || !b) return;
    const risky = a.node.risk === "HIGH" || a.node.risk === "CRITICAL" || b.node.risk === "HIGH" || b.node.risk === "CRITICAL";
    svgContent += `<path d="M ${a.x} ${a.y} C ${(a.x + b.x) / 2} ${a.y}, ${(a.x + b.x) / 2} ${b.y}, ${b.x} ${b.y}"
      stroke="${risky ? "#ff5c72" : "#243044"}" stroke-width="${risky ? 1.6 : 1}" fill="none" opacity="${risky ? 0.75 : 0.5}" />`;
  });

  // Nodes
  Object.values(positions).forEach(({ x, y, node }) => {
    const color = NETWORK_COLORS[node.type] || "#8b96ab";
    const risky = node.risk === "HIGH" || node.risk === "CRITICAL";
    svgContent += `
      <g class="net-node">
        <circle cx="${x}" cy="${y}" r="${risky ? 8 : 6}" fill="${color}" stroke="${risky ? "#ff5c72" : "none"}" stroke-width="2">
          <title>${node.id}</title>
        </circle>
        <text x="${x}" y="${y - 12}" text-anchor="middle" font-size="10" fill="#8b96ab">${truncateLabel(node.id)}</text>
      </g>`;
  });

  // Column headers
  NETWORK_COLUMNS.forEach((col, ci) => {
    const x = colWidth * ci + colWidth / 2;
    svgContent = `<text x="${x}" y="20" text-anchor="middle" font-size="12" fill="#4f8dff" font-weight="700">${col.toUpperCase()}</text>` + svgContent;
  });

  svg.innerHTML = svgContent;
}

function truncateLabel(id) {
  return id.length > 14 ? id.slice(0, 12) + "…" : id;
}

document.getElementById("refreshNetworkBtn").addEventListener("click", loadNetwork);

// ---------------------------------------------------------------------------
// Live simulation (WebSocket)
// ---------------------------------------------------------------------------
const liveToggleBtn = document.getElementById("liveToggleBtn");
const liveFeed = document.getElementById("liveFeed");

liveToggleBtn.addEventListener("click", () => {
  if (liveRunning) stopLive();
  else startLive();
});

function startLive() {
  try {
    liveSocket = new WebSocket(`${WS_BASE}/ws/live`);
  } catch (e) {
    liveFeed.innerHTML = `<div class="empty-row">Could not open WebSocket connection to ${WS_BASE}.</div>`;
    return;
  }

  liveFeed.innerHTML = "";
  liveRunning = true;
  liveToggleBtn.textContent = "■ Stop Live Simulation";

  liveSocket.onmessage = (event) => {
    const tx = JSON.parse(event.data);
    prependLiveItem(tx);
    // Keep dashboard numbers fresh if the user flips back to it
    loadDashboard().catch(() => {});
  };

  liveSocket.onerror = () => {
    liveFeed.insertAdjacentHTML(
      "afterbegin",
      `<div class="empty-row">WebSocket error — check that the backend is running at ${API_BASE}.</div>`
    );
  };

  liveSocket.onclose = () => {
    if (liveRunning) stopLive();
  };
}

function stopLive() {
  liveRunning = false;
  liveToggleBtn.textContent = "▶ Start Live Simulation";
  if (liveSocket) {
    liveSocket.close();
    liveSocket = null;
  }
}

function prependLiveItem(tx) {
  const el = document.createElement("div");
  el.className = "live-item";
  el.innerHTML = `
    <span class="lt-time">${tx.time}</span>
    <span>${tx.tx_id}</span>
    <span class="lt-amount">${formatAmount(tx.amount)}</span>
    <span>${tx.user_id} · ${tx.location} · ${tx.device_id}</span>
    ${riskBadge(tx.risk_level)}
  `;
  liveFeed.prepend(el);
  while (liveFeed.children.length > 40) {
    liveFeed.removeChild(liveFeed.lastChild);
  }
}

// ---------------------------------------------------------------------------
// Analyze Transaction modal
// ---------------------------------------------------------------------------
const modalOverlay = document.getElementById("modalOverlay");
const txForm = document.getElementById("txForm");
const resultPanel = document.getElementById("resultPanel");

function openModal() {
  modalOverlay.classList.add("open");
  txForm.hidden = false;
  resultPanel.hidden = true;
  txForm.reset();
}
function closeModal() {
  modalOverlay.classList.remove("open");
}

document.getElementById("openModalBtn").addEventListener("click", openModal);
document.getElementById("closeModalBtn").addEventListener("click", closeModal);
document.getElementById("cancelModalBtn").addEventListener("click", closeModal);
document.getElementById("doneModalBtn").addEventListener("click", () => {
  closeModal();
  loadDashboard();
});
modalOverlay.addEventListener("click", (e) => {
  if (e.target === modalOverlay) closeModal();
});

txForm.addEventListener("submit", async (e) => {
  e.preventDefault();
  const fd = new FormData(txForm);
  const payload = {
    user_id: fd.get("user_id").trim(),
    amount: parseFloat(fd.get("amount")),
    tx_time: fd.get("tx_time") ? new Date(fd.get("tx_time")).toISOString() : null,
    location: fd.get("location") || "Astana",
    device_id: fd.get("device_id").trim(),
    previous_transactions_count: parseInt(fd.get("previous_transactions_count") || "0", 10),
    tx_last_10min: parseInt(fd.get("tx_last_10min") || "0", 10),
    is_new_device: fd.get("is_new_device") === "on",
    distance_from_prev_location_km: parseFloat(fd.get("distance_from_prev_location_km") || "0"),
  };

  const submitBtn = txForm.querySelector("button[type=submit]");
  submitBtn.disabled = true;
  submitBtn.textContent = "Analyzing…";

  try {
    const result = await apiPost("/api/analyze", payload);
    showResult(result);
  } catch (err) {
    alert(`Analysis failed: ${err.message}`);
  } finally {
    submitBtn.disabled = false;
    submitBtn.textContent = "Analyze Transaction";
  }
});

function showResult(result) {
  txForm.hidden = true;
  resultPanel.hidden = false;

  const score = Math.round(result.risk_score);
  document.getElementById("scoreValue").textContent = score;
  document.getElementById("resultLevel").textContent = `${result.risk_level} RISK`;
  document.getElementById("resultProbability").textContent = `${Math.round(result.fraud_probability * 100)}%`;

  const levelColors = { LOW: "#29d391", MEDIUM: "#f2b84b", HIGH: "#ff9142", CRITICAL: "#ff5c72" };
  const color = levelColors[result.risk_level] || "#4f8dff";
  document.getElementById("scoreCircle").style.borderColor = color;
  document.getElementById("resultLevel").style.color = color;

  const list = document.getElementById("reasonsList");
  list.innerHTML = result.reasons.map((r) => `<li>${r}</li>`).join("");
}

// ---------------------------------------------------------------------------
// Init
// ---------------------------------------------------------------------------
checkHealth();
loadDashboard();
setInterval(checkHealth, 15000);
