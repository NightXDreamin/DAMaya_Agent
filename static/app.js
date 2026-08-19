/* ============================================================
   DAMaya Agent – app.js  (v2.0)
   ============================================================ */

// ── Config ─────────────────────────────────────────────────────
const CFG = {
    animEnabled: true,
    ragEnabled: true,
    animSpeed: 1.0,
    currentModel: '',
    availableModels: [],
};

// ── DOM References ──────────────────────────────────────────────
const DOM = {
    timeline: document.getElementById('timelineContainer'),
    input: document.getElementById('msgInput'),
    sendBtn: document.getElementById('sendBtn'),
    sessionList: document.getElementById('sessionList'),
    statusToast: document.getElementById('statusToast'),
    statusText: document.getElementById('statusText'),
    ragToggle: document.getElementById('ragToggle'),
    modelSelect: document.getElementById('modelSelect'),
    fileBtn: document.getElementById('fileBtn'),
    fileInput: document.getElementById('fileInput'),
    imageBtn: document.getElementById('imageBtn'),
    imageInput: document.getElementById('imageInput'),
    chips: document.getElementById('attachmentChips'),
    settingsBtn: document.getElementById('settingsBtn'),
    settingsOverlay: document.getElementById('settingsOverlay'),
    settingsDrawer: document.getElementById('settingsDrawer'),
    settingsClose: document.getElementById('settingsClose'),
    // Settings controls inside drawer
    setAnimToggle: document.getElementById('setAnimToggle'),
    setRagToggle: document.getElementById('setRagToggle'),
    setModelSelect: document.getElementById('setModelSelect'),
};

// ── State ───────────────────────────────────────────────────────
let currentSessionId = null;
let ws = null;
let isBusy = false;
let pendingFiles = []; // {url, filename, type}

// ── Marked Options (custom renderer for code blocks) ───────────
const renderer = new marked.Renderer();
renderer.code = (code, lang) => {
    const displayLang = lang || 'text';
    let highlighted;
    try {
        highlighted = lang && hljs.getLanguage(lang)
            ? hljs.highlight(code, { language: lang }).value
            : hljs.highlightAuto(code).value;
    } catch {
        highlighted = escapeHtml(code);
    }

    const id = `code-${Math.random().toString(36).slice(2)}`;
    return `
<div class="code-wrapper">
  <div class="code-mac-bar">
    <span class="mac-dot red"></span>
    <span class="mac-dot yellow"></span>
    <span class="mac-dot green"></span>
    <span class="code-lang">${escapeHtml(displayLang)}</span>
    <div class="code-actions">
      <button class="code-btn" onclick="toggleCode('${id}')" title="折叠/展开">
        <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="15 3 21 3 21 9"/><polyline points="9 21 3 21 3 15"/><line x1="21" y1="3" x2="14" y2="10"/><line x1="3" y1="21" x2="10" y2="14"/></svg>
      </button>
      <button class="code-btn" onclick="copyCode('${id}', this)" title="复制代码">
        <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg>
      </button>
    </div>
  </div>
  <pre id="${id}"><code class="hljs language-${escapeHtml(displayLang)}">${highlighted}</code></pre>
</div>`;
};

marked.use({ renderer, breaks: true, gfm: true });

// ── Utility ─────────────────────────────────────────────────────
function escapeHtml(t) {
    if (!t) return '';
    return String(t)
        .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;').replace(/'/g, '&#039;');
}

window.toggleCode = (id) => {
    const pre = document.getElementById(id);
    if (pre) pre.classList.toggle('collapsed');
};

window.copyCode = (id, btn) => {
    const pre = document.getElementById(id);
    if (!pre) return;
    navigator.clipboard.writeText(pre.textContent).then(() => {
        btn.innerHTML = `<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="#10b981" stroke-width="2"><polyline points="20 6 9 17 4 12"/></svg>`;
        setTimeout(() => {
            btn.innerHTML = `<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg>`;
        }, 1800);
    });
};

// ── Think-tag regex (permissive, matches variants) ──────────────
const RE_THINK_BLOCK = /<(?:think|thought_process|reasoning)[^>]*>([\s\S]*?)<\/(?:think|thought_process|reasoning)>/gi;
const RE_THINK_OPEN = /<(?:think|thought_process|reasoning)[^>]*>/i;
const RE_THINK_CLOSE = /<\/(?:think|thought_process|reasoning)>/i;

// ── TimelineManager ─────────────────────────────────────────────
class TimelineManager {
    constructor(container) {
        this.container = container;
        this.currentGroup = null;
        this.thinkNode = null;
        this.resultNode = null;
        this.thinkContent = '';
        this.resultContent = '';
        this._toolCallTs = {}; // name -> timestamp for duration calc
        this._pendingTools = {}; // details element by tool name for pending glow
    }

    startNewGroup(userQuery) {
        const group = document.createElement('div');
        group.className = 'timeline-group';

        const uq = document.createElement('div');
        uq.className = 'user-query';
        uq.innerHTML = `<div class="user-card">${escapeHtml(userQuery)}</div>`;
        group.appendChild(uq);

        this.container.appendChild(group);
        this.currentGroup = group;
        this.thinkNode = null;
        this.resultNode = null;
        this.thinkContent = '';
        this.resultContent = '';
        this._toolCallTs = {};
        this._pendingTools = {};
        this._scroll();
    }

    // Think ────────────────────────────────────────────────────────
    updateThink(chunk, append = true) {
        if (!this.thinkNode) this._createThinkNode();
        this.thinkContent = append ? this.thinkContent + chunk : chunk;
        const body = this.thinkNode.querySelector('.think-body');
        body.textContent = this.thinkContent;
        this._scroll();
    }

    _createThinkNode() {
        const node = document.createElement('div');
        node.className = 'step-think active';
        node.innerHTML = `
      <div class="think-card open">
        <div class="think-header" onclick="this.parentElement.classList.toggle('open')">
          <span class="think-icon">▶</span>
          <span>思考过程</span>
        </div>
        <div class="think-body"></div>
      </div>`;
        this._insertBefore(node);
        this.thinkNode = node;
    }

    finishThink() {
        if (this.thinkNode) {
            this.thinkNode.classList.remove('active');
            // Auto-collapse after stream ends
            this.thinkNode.querySelector('.think-card')?.classList.remove('open');
        }
    }

    // Tool Call ───────────────────────────────────────────────────
    addToolCall(name, args, ts) {
        this._toolCallTs[name] = ts || Date.now() / 1000;
        const details = document.createElement('details');
        details.className = 'tool-box pending';
        details.setAttribute('open', '');
        const argsStr = typeof args === 'string' ? args : JSON.stringify(args, null, 2);
        details.innerHTML = `
      <summary>
        <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="3"/><path d="M19.07 4.93A10 10 0 1 0 4.93 19.07"/></svg>
        ⚙ 执行工具: ${escapeHtml(name)}
        <span class="duration-badge" id="dur-${escapeHtml(name)}">运行中…</span>
      </summary>
      <div class="tool-content"><pre>${escapeHtml(argsStr)}</pre></div>`;
        this._pendingTools[name] = details;
        this._insertBefore(details);
        this._scroll();
    }

    // Observation ─────────────────────────────────────────────────
    addObservation(name, result, ts) {
        // Finish pending glow on matching tool
        if (this._pendingTools[name]) {
            this._pendingTools[name].classList.remove('pending');
        }
        // Update duration badge
        if (this._toolCallTs[name] && ts) {
            const dur = (ts - this._toolCallTs[name]).toFixed(2);
            const badge = document.getElementById(`dur-${name}`);
            if (badge) {
                badge.textContent = `${dur}s`;
                badge.style.color = 'var(--accent-green)';
            }
        } else {
            const badge = document.getElementById(`dur-${name}`);
            if (badge) badge.textContent = '完成';
        }

        const details = document.createElement('details');
        const resultStr = typeof result === 'string' ? result : JSON.stringify(result, null, 2);
        const isError = /Traceback|Error:/i.test(resultStr);
        details.className = `obs-box${isError ? ' error' : ''}`;
        details.innerHTML = `
      <summary>
        ${isError ? '⚠️' : '📋'} 观察结果: ${escapeHtml(name)}
      </summary>
      <div class="obs-content"><pre>${escapeHtml(resultStr)}</pre></div>`;
        this._insertBefore(details);
        this._scroll();
    }

    // Result ───────────────────────────────────────────────────────
    updateResult(chunk, append = true) {
        if (!this.resultNode) this._createResultNode();
        this.resultContent = append ? this.resultContent + chunk : chunk;
        const body = this.resultNode.querySelector('.markdown-body');
        body.innerHTML = marked.parse(this.resultContent);
        this._scroll();
    }

    _createResultNode() {
        const node = document.createElement('div');
        node.className = 'step-result';
        node.innerHTML = `<div class="markdown-body"></div>`;
        this.currentGroup.appendChild(node);
        this.resultNode = node;
    }

    // Approval ────────────────────────────────────────────────────
    addApprovalCard(name, preview) {
        const div = document.createElement('div');
        div.className = 'approval-card';
        div.innerHTML = `
      <div class="approval-title">
        <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/></svg>
        审批请求: ${escapeHtml(name)}
      </div>
      <div class="approval-code">${escapeHtml(preview)}</div>
      <div class="approval-actions">
        <button class="btn-approve">✅ 批准执行</button>
        <button class="btn-reject">❌ 拒绝</button>
      </div>`;
        div.querySelector('.btn-approve').onclick = () => {
            ws?.send(JSON.stringify({ type: 'approval_response', approved: true }));
            div.innerHTML = '<div style="color:var(--accent-green);padding:8px">✅ 已批准</div>';
        };
        div.querySelector('.btn-reject').onclick = () => {
            ws?.send(JSON.stringify({ type: 'approval_response', approved: false }));
            div.innerHTML = '<div style="color:var(--accent-red);padding:8px">❌ 已拒绝</div>';
        };
        this._insertBefore(div);
        this._scroll();
    }

    _insertBefore(el) {
        if (this.currentGroup) {
            if (this.resultNode) {
                this.currentGroup.insertBefore(el, this.resultNode);
            } else {
                this.currentGroup.appendChild(el);
            }
        }
    }

    _scroll() {
        this.container.scrollTop = this.container.scrollHeight;
    }
}

const timeline = new TimelineManager(DOM.timeline);

// ── WebSocket ───────────────────────────────────────────────────
async function ensureWsConnected() {
    if (!currentSessionId) return;
    if (ws) { ws.close(); ws = null; }
    const proto = location.protocol === 'https:' ? 'wss' : 'ws';
    ws = new WebSocket(`${proto}://${location.host}/ws/chat/${currentSessionId}`);
    ws.onmessage = (e) => handleWsMessage(JSON.parse(e.data));
    ws.onclose = () => { setBusy(false); ws = null; };
}

function handleWsMessage(data) {
    switch (data.type) {
        case 'status_update': showStatus(data.content); break;
        case 'think_stream':
            showStatus('🧠 思考中…');
            timeline.updateThink(data.content);
            break;
        case 'tool_call':
            timeline.finishThink();
            timeline.addToolCall(data.name, data.arguments, data.ts);
            showStatus(`⚙ 执行: ${data.name}`);
            break;
        case 'tool_result':
            timeline.addObservation(data.name, data.result, data.ts);
            break;
        case 'approval_required':
            timeline.addApprovalCard(data.name, data.preview);
            break;
        case 'stream':
            timeline.finishThink();
            showStatus('✍ 生成回答…');
            timeline.updateResult(data.content);
            break;
        case 'error':
            hideStatus();
            timeline.updateResult(`\n\n**⚠ 错误:** ${escapeHtml(data.message)}`);
            setBusy(false);
            break;
        case 'done':
            hideStatus();
            setBusy(false);
            timeline.finishThink();
            break;
    }
}

function showStatus(text) {
    DOM.statusText.textContent = text;
    DOM.statusToast.classList.add('visible');
}
function hideStatus() { DOM.statusToast.classList.remove('visible'); }
function setBusy(b) {
    isBusy = b;
    DOM.sendBtn.disabled = b;
    DOM.input.disabled = b;
    if (!b) setTimeout(() => DOM.input.focus(), 80);
}

// ── Send ────────────────────────────────────────────────────────
async function sendMessage() {
    const text = DOM.input.value.trim();
    if (!text || isBusy) return;

    setBusy(true);
    showStatus('📡 发送中…');
    DOM.input.value = '';
    DOM.input.style.height = 'auto';

    // Upload pending files first
    const uploadedUrls = pendingFiles.map(f => f.url);
    clearChips();
    timeline.startNewGroup(text);

    const payload = {
        text,
        use_rag: CFG.ragEnabled,
        model: CFG.currentModel || undefined,
        attached_files: uploadedUrls,
    };
    ws?.send(JSON.stringify(payload));
}

DOM.sendBtn.onclick = sendMessage;
DOM.input.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); sendMessage(); }
});
DOM.input.addEventListener('input', function () {
    this.style.height = 'auto';
    this.style.height = this.scrollHeight + 'px';
});

// ── File Upload ─────────────────────────────────────────────────
DOM.fileBtn.onclick = () => DOM.fileInput.click();
DOM.imageBtn.onclick = () => DOM.imageInput.click();

async function handleFileSelect(input) {
    const files = [...input.files];
    input.value = '';
    for (const file of files) {
        const fd = new FormData();
        fd.append('file', file);
        try {
            const res = await fetch('/api/upload', { method: 'POST', body: fd });
            const data = await res.json();
            if (data.url) {
                pendingFiles.push(data);
                addChip(data);
            }
        } catch (e) {
            console.error('Upload failed:', e);
        }
    }
}

DOM.fileInput.addEventListener('change', () => handleFileSelect(DOM.fileInput));
DOM.imageInput.addEventListener('change', () => handleFileSelect(DOM.imageInput));

function addChip({ filename, type }) {
    const chip = document.createElement('div');
    chip.className = 'chip';
    chip.dataset.filename = filename;
    const icon = type === 'image' ? '🖼' : '📎';
    chip.innerHTML = `<span>${icon} ${escapeHtml(filename)}</span><span class="chip-remove" title="移除">×</span>`;
    chip.querySelector('.chip-remove').onclick = () => {
        pendingFiles = pendingFiles.filter(f => f.filename !== filename);
        chip.remove();
    };
    DOM.chips.appendChild(chip);
}

function clearChips() {
    pendingFiles = [];
    DOM.chips.innerHTML = '';
}

// ── RAG Toggle ──────────────────────────────────────────────────
DOM.ragToggle.addEventListener('click', () => {
    CFG.ragEnabled = !CFG.ragEnabled;
    DOM.ragToggle.classList.toggle('active', CFG.ragEnabled);
    if (DOM.setRagToggle) DOM.setRagToggle.checked = CFG.ragEnabled;
});

// ── Model Select ────────────────────────────────────────────────
DOM.modelSelect.addEventListener('change', () => {
    CFG.currentModel = DOM.modelSelect.value;
    if (DOM.setModelSelect) DOM.setModelSelect.value = CFG.currentModel;
});

// ── Settings Drawer ─────────────────────────────────────────────
function openSettings() {
    DOM.settingsOverlay.classList.add('open');
    DOM.settingsDrawer.classList.add('open');
}
function closeSettings() {
    DOM.settingsOverlay.classList.remove('open');
    DOM.settingsDrawer.classList.remove('open');
}
DOM.settingsBtn?.addEventListener('click', openSettings);
DOM.settingsClose?.addEventListener('click', closeSettings);
DOM.settingsOverlay?.addEventListener('click', closeSettings);

// Anim toggle inside settings
DOM.setAnimToggle?.addEventListener('change', () => {
    CFG.animEnabled = DOM.setAnimToggle.checked;
    document.body.classList.toggle('no-anim', !CFG.animEnabled);
});

// RAG toggle inside settings
DOM.setRagToggle?.addEventListener('change', () => {
    CFG.ragEnabled = DOM.setRagToggle.checked;
    DOM.ragToggle.classList.toggle('active', CFG.ragEnabled);
});

// Model select inside settings
DOM.setModelSelect?.addEventListener('change', () => {
    CFG.currentModel = DOM.setModelSelect.value;
    DOM.modelSelect.value = CFG.currentModel;
});

// ── Session Management ──────────────────────────────────────────
async function loadSessions() {
    const res = await fetch('/api/sessions');
    const sessions = await res.json();
    DOM.sessionList.innerHTML = '';

    for (const s of sessions) {
        const li = document.createElement('li');
        li.className = `session-item ${s.id === currentSessionId ? 'active' : ''}`;
        li.innerHTML = `
      <div class="s-title">${escapeHtml(s.title || 'New Session')}</div>
      <div class="s-time">${new Date(s.created_at).toLocaleString()}</div>
      <button class="session-del" title="删除">✕</button>`;
        li.onclick = (e) => { if (!e.target.classList.contains('session-del')) switchSession(s.id); };
        li.querySelector('.session-del').onclick = async (e) => {
            e.stopPropagation();
            if (!confirm('删除此会话？')) return;
            await fetch(`/api/sessions/${s.id}`, { method: 'DELETE' });
            if (currentSessionId === s.id) currentSessionId = null;
            loadSessions();
        };
        DOM.sessionList.appendChild(li);
    }

    if (!currentSessionId && sessions.length > 0) switchSession(sessions[0].id);
    else if (!currentSessionId) createNewSession();
}

async function createNewSession() {
    const res = await fetch('/api/sessions', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ title: `会话 ${new Date().toLocaleTimeString()}` }),
    });
    const data = await res.json();
    await loadSessions();
    switchSession(data.id);
}

document.getElementById('newSessionBtn').onclick = createNewSession;

async function switchSession(id) {
    currentSessionId = id;
    loadSessions();
    DOM.timeline.innerHTML = '';
    clearChips();

    const msgs = await (await fetch(`/api/sessions/${id}/messages`)).json();
    for (const msg of msgs) {
        if (msg.role === 'user') {
            timeline.startNewGroup(msg.content);
        } else if (msg.role === 'assistant') {
            if (!timeline.currentGroup) timeline.startNewGroup('(历史)');

            let content = msg.content || '';

            // Strip all known think variants
            const thinkMatches = [...content.matchAll(RE_THINK_BLOCK)];
            if (thinkMatches.length > 0) {
                for (const m of thinkMatches) {
                    timeline.updateThink(m[1], thinkMatches.indexOf(m) !== 0);
                }
                content = content.replace(RE_THINK_BLOCK, '');
                timeline.finishThink();
            }

            // Tool calls from history
            if (msg.tool_calls) {
                let tools = msg.tool_calls;
                if (typeof tools === 'string') { try { tools = JSON.parse(tools); } catch { } }
                if (Array.isArray(tools)) {
                    tools.forEach(t => timeline.addToolCall(t.function?.name || 'tool', t.function?.arguments));
                }
            }

            if (content.trim()) {
                // Also strip any lingering action/final_answer tags from history
                content = content
                    .replace(/<\s*action[^>]*>[\s\S]*?<\s*\/\s*action\s*>/gi, '')
                    .replace(/<\s*final_answer[^>]*>([\s\S]*?)<\s*\/\s*final_answer\s*>/gi, '$1')
                    .trim();
                if (content) timeline.updateResult(content, false);
            }
        } else if (msg.role === 'tool') {
            if (!timeline.currentGroup) timeline.startNewGroup('(系统)');
            // Try to match tool message to a tool name via tool_call_id
            const toolName = msg.tool_call_id || '工具结果';
            timeline.addObservation(toolName, msg.content);
        }
    }

    await ensureWsConnected();
}

// ── Init: load config then sessions ────────────────────────────
async function init() {
    try {
        const cfg = await (await fetch('/api/config')).json();

        // Apply animation settings
        if (cfg.ui_animations_enabled === false) {
            CFG.animEnabled = false;
            document.body.classList.add('no-anim');
        }
        if (cfg.ui_animation_speed) {
            CFG.animSpeed = cfg.ui_animation_speed;
            document.documentElement.style.setProperty('--anim-speed', cfg.ui_animation_speed);
        }

        // RAG default
        CFG.ragEnabled = cfg.ui_rag_enabled_default !== false;
        DOM.ragToggle.classList.toggle('active', CFG.ragEnabled);
        if (DOM.setRagToggle) DOM.setRagToggle.checked = CFG.ragEnabled;

        // Models
        if (cfg.available_models?.length) {
            CFG.availableModels = cfg.available_models;
            CFG.currentModel = cfg.chat_model || cfg.available_models[0];

            // Populate both model selects
            [DOM.modelSelect, DOM.setModelSelect].forEach(sel => {
                if (!sel) return;
                sel.innerHTML = cfg.available_models.map(m =>
                    `<option value="${escapeHtml(m)}" ${m === CFG.currentModel ? 'selected' : ''}>${escapeHtml(m)}</option>`
                ).join('');
            });
        }

        // Settings drawer defaults
        if (DOM.setAnimToggle) DOM.setAnimToggle.checked = CFG.animEnabled;
    } catch (e) {
        console.warn('Failed to load /api/config, using defaults.', e);
    }

    await loadSessions();
}

init();
