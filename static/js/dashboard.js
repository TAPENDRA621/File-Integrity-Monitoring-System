let riskPieChart;
let eventPieChart;
let timelineChart;

const liveStatus = document.getElementById('liveStatus');
const recentEventsTable = document.getElementById('recentEventsTable');
const highRiskEventsTable = document.getElementById('highRiskEventsTable');
const addAgentForm = document.getElementById('addAgentForm');
const agentsDashboardTable = document.getElementById('agentsDashboardTable');
const agentLogsPreviewTable = document.getElementById('agentLogsPreviewTable');

function riskBadge(risk) {
  const value = (risk || 'LOW').toUpperCase();
  if (value === 'HIGH') return '<span class="badge badge-risk-high">HIGH</span>';
  if (value === 'MEDIUM') return '<span class="badge badge-risk-medium">MEDIUM</span>';
  return '<span class="badge badge-risk-low">LOW</span>';
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
      <td>${row.event_type}</td>
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
      <td>${statusBadge(agent.status)}</td>
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
      <td>${row.event_type}</td>
      <td>${riskBadge(row.risk_level)}</td>
    </tr>
  `).join('');
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

loadSummary();
loadAgents();
setInterval(loadSummary, 20000);
setInterval(loadAgents, 20000);
