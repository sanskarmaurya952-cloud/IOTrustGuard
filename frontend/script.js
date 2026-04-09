const API_BASE = window.location.origin.replace(/\/$/, "") + "/api";
const AUTO_REFRESH_INTERVAL = 15000;
let token = localStorage.getItem("iot_token") || "";
let trustChart;
let selectedDeviceId = null;
let refreshIntervalId = null;

const el = (id) => document.getElementById(id);

const loginView = el("login-view");
const dashboardView = el("dashboard-view");

function headers() {
  return {
    "Content-Type": "application/json",
    Authorization: `Bearer ${token}`,
  };
}

async function api(path, options = {}) {
  const res = await fetch(`${API_BASE}${path}`, options);
  if (!res.ok) {
    let msg = "Request failed";
    try {
      const payload = await res.json();
      msg = payload.detail || msg;
    } catch (_err) {}
    throw new Error(msg);
  }
  return res.json();
}

function showDashboard() {
  loginView.classList.add("hidden");
  dashboardView.classList.remove("hidden");
}

function showLogin() {
  dashboardView.classList.add("hidden");
  loginView.classList.remove("hidden");
}

function renderAlerts(alerts) {
  const list = el("alerts-list");
  list.innerHTML = "";
  if (!alerts.length) {
    list.innerHTML = "<li>No alerts.</li>";
    return;
  }
  for (const a of alerts) {
    const li = document.createElement("li");
    li.innerHTML = `<strong>[${a.severity.toUpperCase()}]</strong> ${a.device_name}<br/>${a.message}<br/><small>${new Date(a.timestamp).toLocaleString()}</small>`;
    list.appendChild(li);
  }
}

function renderDevices(devices) {
  const list = el("devices-list");
  list.innerHTML = "";
  if (!devices.length) {
    list.innerHTML = "<li>No devices added.</li>";
    return;
  }
  for (const d of devices) {
    const li = document.createElement("li");
    const trust = d.last_analysis?.trust_score ?? "N/A";
    li.innerHTML = `
      <strong>${d.name}</strong><br/>
      Source: ${d.source} (${d.source_type})<br/>
      Trust: ${trust}<br/>
      <button data-id="${d.id}" class="analyze-btn">Analyze Now</button>
      <button data-id="${d.id}" class="history-btn secondary">View Graph</button>
    `;
    list.appendChild(li);
  }
}

function initChart() {
  const ctx = el("trust-chart");
  trustChart = new Chart(ctx, {
    type: "line",
    data: {
      labels: [],
      datasets: [{ label: "Trust Score", data: [], borderWidth: 2, tension: 0.2 }],
    },
    options: {
      responsive: true,
      scales: { y: { min: 0, max: 100 } },
    },
  });
}

async function refreshDashboard() {
  const [summary, devices, alerts] = await Promise.all([
    api("/dashboard", { headers: headers() }),
    api("/devices", { headers: headers() }),
    api("/alerts?limit=20", { headers: headers() }),
  ]);

  el("total-devices").textContent = summary.total_devices;
  el("analyzed-devices").textContent = summary.analyzed_devices;
  el("avg-trust").textContent = summary.average_trust_score;
  el("active-alerts").textContent = summary.active_alerts;

  renderDevices(devices);
  renderAlerts(alerts);
}

async function loadHistory(deviceId) {
  const history = await api(`/devices/${deviceId}/history?limit=50`, { headers: headers() });
  trustChart.data.labels = history.map((h) => new Date(h.timestamp).toLocaleTimeString());
  trustChart.data.datasets[0].data = history.map((h) => h.trust_score);
  trustChart.update();
}

el("login-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  el("login-error").textContent = "";
  try {
    const payload = await api("/auth/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        username: el("username").value.trim(),
        password: el("password").value,
      }),
    });
    token = payload.token;
    localStorage.setItem("iot_token", token);
    showDashboard();
    await refreshDashboard();
  } catch (err) {
    el("login-error").textContent = err.message;
  }
});

el("logout-btn").addEventListener("click", () => {
  token = "";
  localStorage.removeItem("iot_token");
  showLogin();
});

el("device-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  await api("/devices", {
    method: "POST",
    headers: headers(),
    body: JSON.stringify({
      name: el("device-name").value.trim(),
      source_type: el("device-type").value,
      source: el("device-source").value.trim(),
    }),
  });
  e.target.reset();
  await refreshDashboard();
});

el("ensure-webcam-btn").addEventListener("click", async () => {
  await api("/devices/bootstrap-webcam", { method: "POST", headers: headers() });
  await refreshDashboard();
});

el("devices-list").addEventListener("click", async (e) => {
  const target = e.target;
  if (target.classList.contains("analyze-btn")) {
    const id = target.getAttribute("data-id");
    await api(`/devices/${id}/analyze`, { method: "POST", headers: headers() });
    await refreshDashboard();
    if (selectedDeviceId === id) {
      await loadHistory(id);
    }
  }
  if (target.classList.contains("history-btn")) {
    selectedDeviceId = target.getAttribute("data-id");
    await loadHistory(selectedDeviceId);
  }
});

function startAutoRefresh() {
  if (refreshIntervalId) {
    clearInterval(refreshIntervalId);
  }
  refreshIntervalId = setInterval(async () => {
    if (!token || dashboardView.classList.contains("hidden")) return;
    await refreshDashboard();
    if (selectedDeviceId) {
      await loadHistory(selectedDeviceId);
    }
  }, AUTO_REFRESH_INTERVAL);
}

async function init() {
  initChart();
  if (!token) {
    showLogin();
    return;
  }
  try {
    showDashboard();
    await refreshDashboard();
    const devices = await api("/devices", { headers: headers() });
    if (devices.length) {
      selectedDeviceId = devices[0].id;
      await loadHistory(selectedDeviceId);
    }
  } catch (_err) {
    token = "";
    localStorage.removeItem("iot_token");
    showLogin();
  }
}

startAutoRefresh();
init();

window.addEventListener("beforeunload", () => {
  if (refreshIntervalId) {
    clearInterval(refreshIntervalId);
    refreshIntervalId = null;
  }
});
