(function () {
  const toggleBtn = document.getElementById('chatbotToggleBtn');
  const panel = document.getElementById('chatbotPanel');
  const closeBtn = document.getElementById('chatbotCloseBtn');
  const messages = document.getElementById('chatbotMessages');
  const form = document.getElementById('chatbotForm');
  const input = document.getElementById('chatbotInput');

  if (!toggleBtn || !panel || !messages || !form || !input) {
    return;
  }

  function appendMessage(role, text) {
    const wrapper = document.createElement('div');
    wrapper.className = role === 'user' ? 'chatbot-msg chatbot-msg-user' : 'chatbot-msg chatbot-msg-bot';

    const bubble = document.createElement('div');
    bubble.className = 'chatbot-bubble';
    bubble.textContent = text;

    wrapper.appendChild(bubble);
    messages.appendChild(wrapper);
    messages.scrollTop = messages.scrollHeight;
  }

  function setOpen(isOpen) {
    panel.classList.toggle('is-open', isOpen);
    toggleBtn.setAttribute('aria-expanded', String(isOpen));
    if (isOpen) {
      input.focus();
    }
  }

  async function askChatbot(question) {
    const response = await fetch('/api/chatbot', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ question })
    });

    if (!response.ok) {
      return 'I could not process that request right now. Please try again.';
    }

    const payload = await response.json();
    if (payload && payload.answer) {
      return String(payload.answer);
    }

    return 'I can help only with this monitoring system. Ask me about agents, events, alerts, deployment, or heartbeat.';
  }

  toggleBtn.addEventListener('click', () => {
    setOpen(!panel.classList.contains('is-open'));
  });

  if (closeBtn) {
    closeBtn.addEventListener('click', () => setOpen(false));
  }

  form.addEventListener('submit', async (event) => {
    event.preventDefault();
    const question = String(input.value || '').trim();
    if (!question) {
      return;
    }

    appendMessage('user', question);
    input.value = '';

    appendMessage('bot', 'Thinking...');
    const pending = messages.lastElementChild;
    const answer = await askChatbot(question);

    if (pending) {
      pending.remove();
    }
    appendMessage('bot', answer);
  });

  appendMessage('bot', 'Hi, I am your FIM assistant. Ask me about agents, events, alerts, deployment, heartbeat, or sensor/syslog.');
})();
