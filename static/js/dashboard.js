let riskPieChart;
let eventPieChart;
let timelineChart;

const liveStatus = document.getElementById('liveStatus');
const recentEventsTable = document.getElementById('recentEventsTable');
const highRiskEventsTable = document.getElementById('highRiskEventsTable');
const addAgentForm = document.getElementById('addAgentForm');
const agentsDashboardTable = document.getElementById('agentsDashboardTable');
const agentLogsPreviewTable = document.getElementById('agentLogsPreviewTable');
const alertsTable = document.getElementById('alertsTable');
const alertSeverityFilter = document.getElementById('alertSeverityFilter');
const clearAlertsBtn = document.getElementById('clearAlertsBtn');
const highAlertToastElement = document.getElementById('highAlertToast');
const highAlertToastBody = document.getElementById('highAlertToastBody');
let highAlertToast;

function riskBadge(risk) {
  const value = (risk || 'LOW').toUpperCase();
  if (value === 'HIGH') return '<span class="badge badge-risk-high">HIGH</span>';
  if (value === 'MEDIUM') return '<span class="badge badge-risk-medium">MEDIUM</span>';
  return '<span class="badge badge-risk-low">LOW</span>';
}

function replayBadge(row) {
  return row && row.replayed_offline
    ? '<span class="badge text-bg-info ms-1">Offline Replay</span>'
    : '';
}

function setCard(id, value) {
  document.getElementById(id).textContent = value ?? 0;
}

function updateCards(cards) {
  setCard('cardTotalAgents', cards.total_agents);
  setCard('cardActiveAgents', cards.active_agents);
  setCard('cardInactiveAgents', cards.inactive_agents);
  setCard('cardEventsToday', cards.total_events_today);
  setCard('cardHighRisk', cards.high_risk_events);
  setCard('cardMediumRisk', cards.medium_risk_events);
  setCard('cardLowRisk', cards.low_risk_events);
}

function updateAlertCards(counts) {
  setCard('cardTotalAlerts', counts.total_alerts);
  setCard('cardUnreadAlerts', counts.unread_alerts);
  setCard('cardHighSeverityAlerts', counts.high_severity_alerts);
}

function alertSeverityBadge(severity) {
  const normalized = (severity || 'LOW').toUpperCase();
  if (normalized === 'HIGH') return '<span class="badge alert-severity-high">HIGH</span>';
  if (normalized === 'MEDIUM') return '<span class="badge alert-severity-medium">MEDIUM</span>';
  return '<span class="badge alert-severity-low">LOW</span>';
}

function alertStatusBadge(isRead) {
  return isRead
    ? '<span class="badge text-bg-secondary">Read</span>'
    : '<span class="badge text-bg-primary">Unread</span>';
}

function updateRiskChart(riskDistribution) {
  const labels = ['HIGH', 'MEDIUM', 'LOW'];
  const values = labels.map(label => riskDistribution[label] || 0);

  if (riskPieChart) riskPieChart.destroy();
  riskPieChart = new Chart(document.getElementById('riskPieChart'), {
    type: 'pie',
    data: {
      labels,
      datasets: [{
        data: values,
        backgroundColor: ['#dc3545', '#fd7e14', '#ffc107']
      }]
    }
  });
}

function updateEventChart(eventDistribution) {
  const labels = ['created', 'modified', 'deleted'];
  const values = labels.map(label => eventDistribution[label] || 0);

  if (eventPieChart) eventPieChart.destroy();
  eventPieChart = new Chart(document.getElementById('eventPieChart'), {
    type: 'pie',
    data: {
      labels,
      datasets: [{
        data: values,
        backgroundColor: ['#198754', '#0d6efd', '#6c757d']
      }]
    }
  });
}

function updateTimelineChart(timeline) {
  const labels = (timeline.labels || []).map(formatUtcToNepali);
  if (timelineChart) timelineChart.destroy();
  timelineChart = new Chart(document.getElementById('timelineChart'), {
    type: 'line',
    data: {
      labels,
      datasets: [{
        label: 'Events',
        data: timeline.counts || [],
        borderColor: '#0d6efd',
        backgroundColor: 'rgba(13,110,253,0.15)',
        tension: 0.2,
        fill: true
      }]
    },
    options: {
      scales: {
        x: { ticks: { maxRotation: 45, minRotation: 45 } },
        y: { beginAtZero: true }
      }
    }
  });
}

function renderRecentEvents(events) {
  recentEventsTable.innerHTML = events.map(row => `
    <tr>
      <td>${formatUtcToNepali(row.timestamp_utc)}</td>
      <td>${row.agent_id}</td>
      <td class="text-break">${row.file_path}</td>
      <td>${row.event_type}${replayBadge(row)}</td>
      <td>${riskBadge(row.risk_level)}</td>
    </tr>
  `).join('');
}

function renderHighRisk(events) {
  highRiskEventsTable.innerHTML = events.map(row => `
    <tr>
      <td>${formatUtcToNepali(row.timestamp_utc)}</td>
      <td>${row.agent_id}</td>
      <td class="text-break">${row.file_path}</td>
    </tr>
  `).join('');
}

function renderSummary(summary) {
  updateCards(summary.cards || {});
  updateRiskChart(summary.risk_distribution || {});
  updateEventChart(summary.event_distribution || {});
  updateTimelineChart(summary.timeline || { labels: [], counts: [] });
  renderRecentEvents(summary.recent_events || []);
  renderHighRisk(summary.recent_high_risk || []);
}

function statusBadge(status) {
  return status === 'Active'
    ? '<span class="badge text-bg-success">Active</span>'
    : '<span class="badge text-bg-danger">Inactive</span>';
}

function monitorHealthNote(agent) {
  const monitorStatus = String(agent.monitor_status || '').toLowerCase();
  const monitorMessage = String(agent.monitor_message || '').trim();
  if (monitorStatus !== 'degraded' || !monitorMessage) {
    return '';
  }
  return `<div class="small text-warning mt-1">${monitorMessage}</div>`;
}

function renderAgents(agents) {
  if (!agentsDashboardTable) {
    return;
  }

  agentsDashboardTable.innerHTML = agents.map((agent) => `
    <tr>
      <td>${agent.agent_name || agent.hostname || '-'}</td>
      <td>${agent.agent_id}</td>
      <td>${agent.ip_address || '-'}</td>
      <td>${agent.port || '-'}</td>
      <td>${formatUtcToNepali(agent.last_seen_utc)}</td>
      <td>${statusBadge(agent.status)}${monitorHealthNote(agent)}</td>
      <td class="d-flex gap-2">
        <button class="btn btn-sm btn-outline-primary" data-action="fetch-logs" data-agent-id="${agent.agent_id}">Fetch Logs</button>
        <a class="btn btn-sm btn-outline-secondary" href="/agents/${encodeURIComponent(agent.agent_id)}/logs">View Logs</a>
      </td>
    </tr>
  `).join('');
}

function renderAgentLogsPreview(logs) {
  if (!agentLogsPreviewTable) {
    return;
  }

  agentLogsPreviewTable.innerHTML = (logs || []).map((row) => `
    <tr>
      <td>${formatUtcToNepali(row.timestamp_utc)}</td>
      <td class="text-break">${row.file_path}</td>
      <td>${row.event_type}${replayBadge(row)}</td>
      <td>${riskBadge(row.risk_level)}</td>
    </tr>
  `).join('');
}

function renderAlerts(alerts) {
  if (!alertsTable) {
    return;
  }

  alertsTable.innerHTML = (alerts || []).map((alert) => `
    <tr>
      <td>${formatUtcToNepali(alert.timestamp_utc)}</td>
      <td>${alert.agent_name || alert.agent_id}</td>
      <td class="text-break">${alert.file_path}</td>
      <td>${alert.event_type}</td>
      <td>${alertSeverityBadge(alert.severity)}</td>
      <td>${alertStatusBadge(alert.is_read)}</td>
      <td>
        ${alert.is_read
          ? '<span class="text-muted small">-</span>'
          : `<button class="btn btn-sm btn-outline-primary" data-action="mark-alert-read" data-alert-id="${alert.alert_id}">Mark Read</button>`}
      </td>
    </tr>
  `).join('');
}

async function loadAlertCounts() {
  const response = await fetch('/api/alerts/summary');
  const counts = await response.json();
  updateAlertCards(counts);
}

async function loadAlerts() {
  if (!alertsTable) {
    return;
  }

  const severity = alertSeverityFilter ? alertSeverityFilter.value : '';
  const query = new URLSearchParams({ limit: '50' });
  if (severity) {
    query.set('severity', severity);
  }

  const response = await fetch(`/api/alerts?${query.toString()}`);
  const alerts = await response.json();
  renderAlerts(alerts);
}

async function markAlertRead(alertId) {
  const response = await fetch(`/api/alerts/${encodeURIComponent(alertId)}/read`, {
    method: 'PATCH',
  });
  if (!response.ok) {
    return;
  }

  const payload = await response.json();
  if (payload && payload.counts) {
    updateAlertCards(payload.counts);
  }
  await loadAlerts();
}

async function clearAlerts() {
  const severity = alertSeverityFilter ? alertSeverityFilter.value : '';
  const query = new URLSearchParams();
  if (severity) {
    query.set('severity', severity);
  }

  const suffix = query.toString() ? `?${query.toString()}` : '';
  const response = await fetch(`/api/alerts${suffix}`, { method: 'DELETE' });
  if (!response.ok) {
    return;
  }

  const payload = await response.json();
  if (payload && payload.counts) {
    updateAlertCards(payload.counts);
  }
  await loadAlerts();
}

function showHighAlertToast(alert) {
  if (!highAlertToastElement || !highAlertToastBody) {
    return;
  }

  if (!highAlertToast) {
    highAlertToast = new bootstrap.Toast(highAlertToastElement, { delay: 5000 });
  }

  highAlertToastBody.textContent = alert.alert_message || `${alert.agent_id} reported a HIGH severity alert.`;
  highAlertToast.show();
}

async function loadAgents() {
  if (!agentsDashboardTable) {
    return;
  }
  const response = await fetch('/api/agents');
  const agents = await response.json();
  renderAgents(agents);
}

async function fetchAgentLogs(agentId) {
  const response = await fetch(`/api/agents/${encodeURIComponent(agentId)}/logs?limit=50`);
  const logs = await response.json();
  renderAgentLogsPreview(logs);
}

if (addAgentForm) {
  addAgentForm.addEventListener('submit', async (event) => {
    event.preventDefault();
    const formData = new FormData(addAgentForm);

    const payload = {
      agent_name: String(formData.get('agent_name') || '').trim(),
      agent_ip_address: String(formData.get('agent_ip_address') || '').trim(),
      port: String(formData.get('port') || '').trim(),
    };

    const response = await fetch('/api/agents/register', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });

    if (!response.ok) {
      return;
    }

    addAgentForm.reset();
    await loadAgents();
    await loadSummary();
  });
}

if (agentsDashboardTable) {
  agentsDashboardTable.addEventListener('click', (event) => {
    const target = event.target;
    if (!(target instanceof HTMLElement)) {
      return;
    }
    if (target.dataset.action === 'fetch-logs' && target.dataset.agentId) {
      fetchAgentLogs(target.dataset.agentId);
    }
  });
}

if (alertsTable) {
  alertsTable.addEventListener('click', (event) => {
    const target = event.target;
    if (!(target instanceof HTMLElement)) {
      return;
    }
    if (target.dataset.action === 'mark-alert-read' && target.dataset.alertId) {
      markAlertRead(target.dataset.alertId);
    }
  });
}

if (alertSeverityFilter) {
  alertSeverityFilter.addEventListener('change', loadAlerts);
}

if (clearAlertsBtn) {
  clearAlertsBtn.addEventListener('click', clearAlerts);
}

async function loadSummary() {
  const response = await fetch('/api/dashboard/summary');
  const summary = await response.json();
  renderSummary(summary);
}

const socket = io();
socket.on('connect', () => {
  liveStatus.className = 'badge text-bg-success';
  liveStatus.textContent = 'Live';
});

socket.on('disconnect', () => {
  liveStatus.className = 'badge text-bg-danger';
  liveStatus.textContent = 'Disconnected';
});

socket.on('dashboard:update', renderSummary);
socket.on('event:new', loadSummary);
socket.on('agent:update', loadSummary);
socket.on('agent:update', loadAgents);
socket.on('alerts:update', updateAlertCards);
socket.on('alert:read', loadAlerts);
socket.on('alerts:cleared', loadAlerts);
socket.on('alert:new', (alert) => {
  loadAlerts();
  loadAlertCounts();
  if ((alert.severity || '').toUpperCase() === 'HIGH') {
    showHighAlertToast(alert);
  }
});

loadSummary();
loadAgents();
loadAlertCounts();
loadAlerts();
setInterval(loadSummary, 20000);
setInterval(loadAgents, 20000);
setInterval(loadAlertCounts, 20000);
setInterval(loadAlerts, 20000);
