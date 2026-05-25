const API = 'http://localhost:8000';

// ── Session & persisted state ─────────────────────────────────────────────────
let sessionId = localStorage.getItem('session_id');
if (!sessionId) {
  sessionId = crypto.randomUUID();
  localStorage.setItem('session_id', sessionId);
}

let profile      = JSON.parse(localStorage.getItem('coach_profile') || 'null');
let streakDays   = parseInt(localStorage.getItem('streak_days')    || '0', 10);
let totalCheckins= parseInt(localStorage.getItem('total_checkins') || '0', 10);

// ── Screen routing ────────────────────────────────────────────────────────────
const screens = {
  onboarding: document.getElementById('screen-onboarding'),
  chat:       document.getElementById('screen-chat'),
  stats:      document.getElementById('screen-stats'),
};

function showScreen(name) {
  Object.values(screens).forEach(s => s.classList.add('hidden'));
  screens[name].classList.remove('hidden');
}

// ── Boot ──────────────────────────────────────────────────────────────────────
if (profile?.onboarding_complete) {
  showScreen('chat');
  initChat();
} else {
  showScreen('onboarding');
  initOnboarding();
}

// =============================================================================
// ONBOARDING
// =============================================================================
function initOnboarding() {
  let pendingGoal = '';

  function showStep(n) {
    document.querySelectorAll('.step').forEach(s => s.classList.remove('active'));
    document.getElementById(`step-${n}`).classList.add('active');
  }

  // Step 1 — goal
  const goalInput = document.getElementById('goal-input');
  const goalBtn   = document.getElementById('goal-btn');

  goalBtn.addEventListener('click', () => {
    const val = goalInput.value.trim();
    if (!val) return;
    pendingGoal = val;
    showStep(2);
  });

  goalInput.addEventListener('keydown', e => {
    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); goalBtn.click(); }
  });

  // Step 2 — coaching style
  document.querySelectorAll('.style-card').forEach(card => {
    card.addEventListener('click', () => {
      document.querySelectorAll('.style-card').forEach(c => c.classList.remove('selected'));
      card.classList.add('selected');
      setTimeout(() => showStep(3), 280);
    });
  });

  // Step 3 — start: silently wire up backend, then enter chat
  document.getElementById('start-btn').addEventListener('click', async () => {
    const selectedCard = document.querySelector('.style-card.selected');
    const style = selectedCard ? selectedCard.dataset.style : 'balanced';

    const startBtn = document.getElementById('start-btn');
    startBtn.textContent = 'Setting up…';
    startBtn.disabled = true;

    // Save to localStorage
    profile = { goal: pendingGoal, coaching_style: style, onboarding_complete: true };
    localStorage.setItem('coach_profile', JSON.stringify(profile));

    // Drive through backend's 3-step onboarding silently
    try {
      await apiSend('Hello');
      await apiSend(pendingGoal);
      await apiSend(style);
    } catch (_) {
      // Non-fatal — backend might still have the profile from a partial run
    }

    // Flag so chat sends the opening message once
    sessionStorage.setItem('first_chat', '1');

    showScreen('chat');
    initChat();
  });
}

// =============================================================================
// CHAT
// =============================================================================
function initChat() {
  const messagesEl = document.getElementById('messages');
  const inputEl    = document.getElementById('msg-input');
  const sendBtn    = document.getElementById('send-btn');

  // Auto-resize textarea
  inputEl.addEventListener('input', () => {
    inputEl.style.height = 'auto';
    inputEl.style.height = Math.min(inputEl.scrollHeight, 110) + 'px';
  });

  inputEl.addEventListener('keydown', e => {
    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); handleSend(); }
  });

  sendBtn.addEventListener('click', handleSend);

  document.getElementById('stats-btn').addEventListener('click', () => {
    renderStats();
    showScreen('stats');
  });

  // Auto-open message — only fires once, right after onboarding
  if (sessionStorage.getItem('first_chat') === '1') {
    sessionStorage.removeItem('first_chat');
    sendToChat("Hi, I'm ready to start");
  } else {
    inputEl.focus();
  }

  // ── Send handler ────────────────────────────────────────────────────────────
  async function handleSend() {
    const text = inputEl.value.trim();
    if (!text || sendBtn.disabled) return;
    inputEl.value = '';
    inputEl.style.height = 'auto';
    await sendToChat(text);
    inputEl.focus();
  }

  async function sendToChat(text) {
    appendUserMsg(text);
    scrollBottom();

    sendBtn.disabled = true;
    const loader = appendLoader();

    try {
      const data = await apiSend(text);
      loader.remove();
      appendCoachMsg(data.reply, data.provider_used, data.intent);

      // Persist updated stats
      if (typeof data.streak_days === 'number') {
        streakDays = data.streak_days;
        localStorage.setItem('streak_days', streakDays);
      }
      totalCheckins++;
      localStorage.setItem('total_checkins', totalCheckins);

    } catch (err) {
      loader.remove();
      appendCoachMsg('Something went wrong. Please try again.');
    } finally {
      sendBtn.disabled = false;
      scrollBottom();
    }
  }

  // ── Message renderers ────────────────────────────────────────────────────────
  function appendUserMsg(text) {
    const row = document.createElement('div');
    row.className = 'msg user';

    const bubble = document.createElement('div');
    bubble.className = 'bubble';
    bubble.textContent = text;

    row.appendChild(bubble);
    messagesEl.appendChild(row);
  }

  function appendCoachMsg(text, provider, intent) {
    const row = document.createElement('div');
    row.className = 'msg coach';

    const bubble = document.createElement('div');
    bubble.className = 'bubble';
    bubble.textContent = text;
    row.appendChild(bubble);

    if (provider || (intent && intent !== 'onboarding')) {
      const meta = document.createElement('div');
      meta.className = 'msg-meta';

      if (provider) {
        const b = document.createElement('span');
        b.className = 'badge badge-provider';
        b.textContent = provider;
        meta.appendChild(b);
      }

      if (intent && intent !== 'onboarding') {
        const b = document.createElement('span');
        b.className = `badge badge-intent ${intent}`;
        b.textContent = intent;
        meta.appendChild(b);
      }

      row.appendChild(meta);
    }

    messagesEl.appendChild(row);
  }

  function appendLoader() {
    const loader = document.createElement('div');
    loader.className = 'loader';
    loader.innerHTML = '<span></span><span></span><span></span>';
    messagesEl.appendChild(loader);
    scrollBottom();
    return loader;
  }

  function scrollBottom() {
    messagesEl.scrollTop = messagesEl.scrollHeight;
  }
}

// =============================================================================
// STATS
// =============================================================================
document.getElementById('back-btn').addEventListener('click', () => showScreen('chat'));

function renderStats() {
  const motivations = [
    { max: 2,        text: 'Every expert was once a beginner.' },
    { max: 6,        text: "You're building something real." },
    { max: 13,       text: 'One week strong. Keep going.' },
    { max: Infinity, text: "You're in the top 1%. Don't stop now." },
  ];

  document.getElementById('streak-num').textContent   = streakDays;
  document.getElementById('s-goal').textContent       = profile?.goal          || '—';
  document.getElementById('s-style').textContent      = profile?.coaching_style || '—';
  document.getElementById('s-checkins').textContent   = totalCheckins;

  const line = motivations.find(m => streakDays <= m.max);
  document.getElementById('motivation-line').textContent = `"${line.text}"`;
}

// =============================================================================
// API
// =============================================================================
async function apiSend(message) {
  const res = await fetch(`${API}/api/coach`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ session_id: sessionId, message }),
  });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}
