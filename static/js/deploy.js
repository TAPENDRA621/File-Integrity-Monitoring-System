const deployForm = document.getElementById('deployAgentForm');
const deployAlert = document.getElementById('deployAlert');
const profilesTable = document.getElementById('deployProfilesTable');
const refreshProfilesBtn = document.getElementById('refreshProfilesBtn');

const editAgentIdInput = document.getElementById('editAgentId');
const agentNameInput = document.getElementById('agentNameInput');
const agentIdInput = document.getElementById('agentIdInput');
const monitorPathsInput = document.getElementById('monitorPathsInput');
const pollInput = document.getElementById('pollInput');
const heartbeatInput = document.getElementById('heartbeatInput');

const stepPanes = Array.from(document.querySelectorAll('[data-step-pane]'));
const stepNavButtons = Array.from(document.querySelectorAll('[data-step-nav]'));

const step1NextBtn = document.getElementById('step1NextBtn');
const step2BackBtn = document.getElementById('step2BackBtn');
const step2NextBtn = document.getElementById('step2NextBtn');
const step3BackBtn = document.getElementById('step3BackBtn');
const step3NextBtn = document.getElementById('step3NextBtn');
const step4BackBtn = document.getElementById('step4BackBtn');
const resetWizardBtn = document.getElementById('resetWizardBtn');

const selectedAgentId = document.getElementById('selectedAgentId');
const tokenPreview = document.getElementById('tokenPreview');
const commandPlatformSelect = document.getElementById('commandPlatformSelect');
const installCommandPreview = document.getElementById('installCommandPreview');

const downloadWindowsBtn = document.getElementById('downloadWindowsBtn');
const downloadLinuxBtn = document.getElementById('downloadLinuxBtn');
const copyCommandBtn = document.getElementById('copyCommandBtn');

let currentStep = 1;
let cachedProfiles = [];
let selectedProfile = null;

function showAlert(kind, message) {
  if (!deployAlert) {
    return;
  }
  deployAlert.className = `alert alert-${kind}`;
  deployAlert.textContent = message;
}

function hideAlert() {
  if (!deployAlert) {
    return;
  }
  deployAlert.className = 'alert d-none';
  deployAlert.textContent = '';
}

async function apiJson(url, options = {}) {
  const response = await fetch(url, options);
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(payload.error || 'Request failed');
  }
  return payload;
}

function splitPaths(raw) {
  return String(raw || '')
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter(Boolean);
}

function clearFieldError(element) {
  if (!element) {
    return;
  }
  element.classList.remove('is-invalid');
}

function setFieldError(element, message) {
  if (!element) {
    return;
  }
  element.classList.add('is-invalid');
  const feedback = element.parentElement && element.parentElement.querySelector('.invalid-feedback');
  if (feedback && message) {
    feedback.textContent = message;
  }
}

function clearValidation() {
  [agentNameInput, monitorPathsInput, pollInput, heartbeatInput].forEach(clearFieldError);
}

function setStep(stepNumber) {
  currentStep = stepNumber;

  stepPanes.forEach((pane) => {
    const paneStep = Number(pane.getAttribute('data-step-pane'));
    pane.classList.toggle('d-none', paneStep !== stepNumber);
  });

  stepNavButtons.forEach((button) => {
    const buttonStep = Number(button.getAttribute('data-step-nav'));
    button.classList.toggle('btn-primary', buttonStep === stepNumber);
    button.classList.toggle('btn-outline-secondary', buttonStep !== stepNumber);
  });
}

function validateStepOne(showErrors = false) {
  clearFieldError(agentNameInput);
  if (!agentNameInput.value.trim()) {
    if (showErrors) {
      setFieldError(agentNameInput, 'Agent name is required.');
    }
    return false;
  }
  return true;
}

function validateStepTwo(showErrors = false) {
  clearFieldError(monitorPathsInput);
  if (!splitPaths(monitorPathsInput.value).length) {
    if (showErrors) {
      setFieldError(monitorPathsInput, 'Enter at least one path to monitor.');
    }
    return false;
  }
  return true;
}

function validateStepThree(showErrors = false) {
  let valid = true;

  clearFieldError(pollInput);
  clearFieldError(heartbeatInput);

  const pollValue = Number(pollInput.value || 0);
  if (!Number.isFinite(pollValue) || pollValue < 5) {
    valid = false;
    if (showErrors) {
      setFieldError(pollInput, 'Poll interval must be 5 seconds or more.');
    }
  }

  const heartbeatValue = Number(heartbeatInput.value || 0);
  if (!Number.isFinite(heartbeatValue) || heartbeatValue < 5) {
    valid = false;
    if (showErrors) {
      setFieldError(heartbeatInput, 'Heartbeat interval must be 5 seconds or more.');
    }
  }

  return valid;
}

function validateAll(showErrors = false) {
  const one = validateStepOne(showErrors);
  const two = validateStepTwo(showErrors);
  const three = validateStepThree(showErrors);
  return one && two && three;
}

function statusBadge(status) {
  if (status === 'Active') return '<span class="badge text-bg-success">Active</span>';
  if (status === 'Offline') return '<span class="badge text-bg-warning">Offline</span>';
  if (status === 'Pending') return '<span class="badge text-bg-info">Pending</span>';
  if (status === 'Disabled') return '<span class="badge text-bg-secondary">Disabled</span>';
  return '<span class="badge text-bg-light">Not Installed</span>';
}

function rowActions(profile) {
  return [
    `<button class="btn btn-sm btn-outline-primary" data-action="select" data-agent-id="${profile.agent_id}">Load</button>`,
    `<button class="btn btn-sm btn-outline-dark" data-action="edit" data-agent-id="${profile.agent_id}">Edit</button>`,
    `<button class="btn btn-sm btn-outline-warning" data-action="token" data-agent-id="${profile.agent_id}">New Token</button>`,
    `<button class="btn btn-sm btn-outline-danger" data-action="delete" data-agent-id="${profile.agent_id}">Delete</button>`,
  ].join(' ');
}

function renderProfiles(profiles) {
  cachedProfiles = profiles;

  if (!profilesTable) {
    return;
  }

  if (!profiles.length) {
    profilesTable.innerHTML = '<tr><td colspan="5" class="text-center text-muted py-4">No agent profiles yet. Complete the wizard to create your first one.</td></tr>';
    return;
  }

  profilesTable.innerHTML = profiles.map((profile) => `
      <tr>
        <td>${profile.agent_name}</td>
        <td>${profile.agent_id}</td>
        <td>${statusBadge(profile.status)}</td>
        <td>${profile.last_seen_utc ? formatUtcToNepali(profile.last_seen_utc) : '-'}</td>
        <td class="d-flex flex-wrap gap-1">${rowActions(profile)}</td>
      </tr>
    `).join('');
}

function setPackageButtonsEnabled(enabled) {
  if (downloadWindowsBtn) {
    downloadWindowsBtn.disabled = !enabled;
  }
  if (downloadLinuxBtn) {
    downloadLinuxBtn.disabled = !enabled;
  }
  if (copyCommandBtn) {
    copyCommandBtn.disabled = !enabled;
  }
}

async function loadInstallCommand(agentId, platformName) {
  const result = await apiJson(`/api/deploy/agents/${encodeURIComponent(agentId)}/install-command?platform=${encodeURIComponent(platformName)}`);
  return result.command || '';
}

async function refreshInstallCommandPreview() {
  if (!selectedProfile || !installCommandPreview || !commandPlatformSelect) {
    return;
  }

  installCommandPreview.value = 'Loading install command...';
  try {
    const command = await loadInstallCommand(selectedProfile.agent_id, commandPlatformSelect.value);
    installCommandPreview.value = command;
  } catch (error) {
    installCommandPreview.value = '';
    showAlert('danger', error.message || 'Unable to load install command.');
  }
}

function setSelectedProfile(profile) {
  selectedProfile = profile;
  if (selectedAgentId) {
    selectedAgentId.textContent = profile ? profile.agent_id : 'Not generated yet';
  }
  if (tokenPreview) {
    tokenPreview.value = profile ? (profile.enrollment_token || '') : '';
  }
  if (!profile && installCommandPreview) {
    installCommandPreview.value = '';
  }
  setPackageButtonsEnabled(Boolean(profile));
}

function applyProfileToForm(profile) {
  editAgentIdInput.value = profile.agent_id || '';
  agentNameInput.value = profile.agent_name || '';
  agentIdInput.value = profile.agent_id || '';
  monitorPathsInput.value = (profile.monitor_paths || []).join('\n');
  pollInput.value = String(profile.poll_seconds || 15);
  heartbeatInput.value = String(profile.heartbeat_seconds || 30);
}

function resetWizard() {
  editAgentIdInput.value = '';
  deployForm.reset();
  pollInput.value = '15';
  heartbeatInput.value = '30';
  if (commandPlatformSelect) {
    commandPlatformSelect.value = 'windows';
  }
  clearValidation();
  setSelectedProfile(null);
  setStep(1);
  hideAlert();
}

async function downloadPackage(agentId, platformName) {
  const response = await fetch(`/api/deploy/agents/${encodeURIComponent(agentId)}/package?platform=${encodeURIComponent(platformName)}`);
  if (!response.ok) {
    const payload = await response.json().catch(() => ({}));
    throw new Error(payload.error || `Failed to build ${platformName} package.`);
  }

  const disposition = response.headers.get('content-disposition') || '';
  const filenameMatch = disposition.match(/filename=([^;]+)/i);
  const filename = filenameMatch ? filenameMatch[1].replace(/"/g, '') : `fim-agent-${agentId}-${platformName}.zip`;

  const blob = await response.blob();
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement('a');
  anchor.href = url;
  anchor.download = filename;
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  URL.revokeObjectURL(url);
}

async function loadProfiles() {
  const profiles = await apiJson('/api/deploy/agents');
  renderProfiles(profiles);

  if (selectedProfile) {
    const fresh = profiles.find((item) => item.agent_id === selectedProfile.agent_id);
    if (fresh) {
      setSelectedProfile(fresh);
      await refreshInstallCommandPreview();
    } else {
      setSelectedProfile(null);
    }
  }
}

async function createOrUpdateProfile(event) {
  event.preventDefault();
  hideAlert();

  if (!validateAll(true)) {
    showAlert('danger', 'Please fix highlighted fields before generating the package.');
    return;
  }

  const payload = {
    agent_name: agentNameInput.value.trim(),
    agent_id: agentIdInput.value.trim(),
    monitor_paths: splitPaths(monitorPathsInput.value),
    poll_seconds: Number(pollInput.value || 15),
    heartbeat_seconds: Number(heartbeatInput.value || 30),
    risk_label: '',
  };

  try {
    const editingId = editAgentIdInput.value.trim();
    let profile;

    if (editingId) {
      profile = await apiJson(`/api/deploy/agents/${encodeURIComponent(editingId)}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
      showAlert('success', `Package profile updated for ${profile.agent_id}. You can now download installer packages.`);
    } else {
      profile = await apiJson('/api/deploy/agents', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
      showAlert('success', `Package profile generated for ${profile.agent_id}. Download the package below.`);
      editAgentIdInput.value = profile.agent_id;
    }

    await loadProfiles();
    setSelectedProfile(profile);
    await refreshInstallCommandPreview();
    setStep(4);
  } catch (error) {
    showAlert('danger', error.message || 'Unable to generate package.');
  }
}

async function handleRowAction(event) {
  const target = event.target;
  if (!(target instanceof HTMLElement)) {
    return;
  }

  const action = target.dataset.action;
  const agentId = target.dataset.agentId;
  if (!action || !agentId) {
    return;
  }

  const profile = cachedProfiles.find((item) => item.agent_id === agentId);
  if (!profile) {
    return;
  }

  hideAlert();

  try {
    if (action === 'select') {
      applyProfileToForm(profile);
      setSelectedProfile(profile);
      await refreshInstallCommandPreview();
      setStep(4);
      showAlert('info', `Loaded profile ${agentId}. You can download packages now.`);
      return;
    }

    if (action === 'edit') {
      applyProfileToForm(profile);
      setSelectedProfile(profile);
      await refreshInstallCommandPreview();
      setStep(1);
      showAlert('info', `Editing profile ${agentId}.`);
      return;
    }

    if (action === 'token') {
      const updated = await apiJson(`/api/deploy/agents/${encodeURIComponent(agentId)}/token`, { method: 'POST' });
      setSelectedProfile(updated);
      await refreshInstallCommandPreview();
      await loadProfiles();
      showAlert('warning', `New token generated for ${updated.agent_id}. Re-download packages before installing.`);
      return;
    }

    if (action === 'delete') {
      if (!confirm(`Delete profile ${agentId}?`)) {
        return;
      }
      await apiJson(`/api/deploy/agents/${encodeURIComponent(agentId)}`, { method: 'DELETE' });
      await loadProfiles();

      if (selectedProfile && selectedProfile.agent_id === agentId) {
        setSelectedProfile(null);
      }
      if (editAgentIdInput.value.trim() === agentId) {
        resetWizard();
      }
      showAlert('success', `Profile ${agentId} deleted.`);
    }
  } catch (error) {
    showAlert('danger', error.message || 'Action failed.');
  }
}

function copyText(value, successMessage) {
  navigator.clipboard.writeText(value || '').then(() => {
    showAlert('success', successMessage);
  }).catch(() => {
    showAlert('warning', 'Copy failed. Please copy manually.');
  });
}

if (step1NextBtn) {
  step1NextBtn.addEventListener('click', () => {
    hideAlert();
    if (validateStepOne(true)) {
      setStep(2);
    }
  });
}

if (step2BackBtn) {
  step2BackBtn.addEventListener('click', () => {
    hideAlert();
    setStep(1);
  });
}

if (step2NextBtn) {
  step2NextBtn.addEventListener('click', () => {
    hideAlert();
    if (validateStepTwo(true)) {
      setStep(3);
    }
  });
}

if (step3BackBtn) {
  step3BackBtn.addEventListener('click', () => {
    hideAlert();
    setStep(2);
  });
}

if (step3NextBtn) {
  step3NextBtn.addEventListener('click', () => {
    hideAlert();
    if (validateStepThree(true)) {
      setStep(4);
    }
  });
}

if (step4BackBtn) {
  step4BackBtn.addEventListener('click', () => {
    hideAlert();
    setStep(3);
  });
}

stepNavButtons.forEach((button) => {
  button.addEventListener('click', () => {
    hideAlert();
    const targetStep = Number(button.getAttribute('data-step-nav'));

    if (targetStep <= currentStep) {
      setStep(targetStep);
      return;
    }

    if (targetStep >= 2 && !validateStepOne(true)) {
      showAlert('danger', 'Please complete Step 1 before moving forward.');
      return;
    }
    if (targetStep >= 3 && !validateStepTwo(true)) {
      showAlert('danger', 'Please complete Step 2 before moving forward.');
      return;
    }
    if (targetStep >= 4 && !validateStepThree(true)) {
      showAlert('danger', 'Please complete Step 3 before moving forward.');
      return;
    }

    setStep(targetStep);
  });
});

if (deployForm) {
  deployForm.addEventListener('submit', createOrUpdateProfile);
}

if (downloadWindowsBtn) {
  downloadWindowsBtn.addEventListener('click', async () => {
    if (!selectedProfile) {
      showAlert('warning', 'Generate or load an agent profile first.');
      return;
    }
    try {
      await downloadPackage(selectedProfile.agent_id, 'windows');
      showAlert('success', `Windows package downloaded for ${selectedProfile.agent_id}.`);
    } catch (error) {
      showAlert('danger', error.message || 'Windows package download failed.');
    }
  });
}

if (downloadLinuxBtn) {
  downloadLinuxBtn.addEventListener('click', async () => {
    if (!selectedProfile) {
      showAlert('warning', 'Generate or load an agent profile first.');
      return;
    }
    try {
      await downloadPackage(selectedProfile.agent_id, 'linux');
      showAlert('success', `Linux package downloaded for ${selectedProfile.agent_id}.`);
    } catch (error) {
      showAlert('danger', error.message || 'Linux package download failed.');
    }
  });
}

if (copyCommandBtn) {
  copyCommandBtn.addEventListener('click', () => {
    copyText(installCommandPreview.value, 'Install command copied.');
  });
}

if (commandPlatformSelect) {
  commandPlatformSelect.addEventListener('change', refreshInstallCommandPreview);
}

if (refreshProfilesBtn) {
  refreshProfilesBtn.addEventListener('click', loadProfiles);
}

if (profilesTable) {
  profilesTable.addEventListener('click', handleRowAction);
}

if (resetWizardBtn) {
  resetWizardBtn.addEventListener('click', resetWizard);
}

if (agentNameInput) {
  agentNameInput.addEventListener('input', () => clearFieldError(agentNameInput));
}
if (monitorPathsInput) {
  monitorPathsInput.addEventListener('input', () => clearFieldError(monitorPathsInput));
}
if (pollInput) {
  pollInput.addEventListener('input', () => clearFieldError(pollInput));
}
if (heartbeatInput) {
  heartbeatInput.addEventListener('input', () => clearFieldError(heartbeatInput));
}

const socket = io();
socket.on('deploy:profiles:update', loadProfiles);
socket.on('agent:update', loadProfiles);

setPackageButtonsEnabled(false);
setStep(1);
loadProfiles().catch((error) => showAlert('danger', error.message || 'Unable to load profiles.'));
