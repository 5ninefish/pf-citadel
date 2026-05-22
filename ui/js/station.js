// PeakForge Station — Core Navigation + UI Logic

// Dynamic base URLs — works from any machine on the network
const API_BASE    = `http://${window.location.hostname}:8520`;
const OC_BASE     = `http://${window.location.hostname}:18789`;
const STATIC_BASE = `http://${window.location.hostname}:8521`;

// ---- Tab Switching ----
document.querySelectorAll('.sidebar li').forEach(tab => {
    tab.addEventListener('click', () => {
        const target = tab.dataset.tab;

        document.querySelectorAll('.sidebar li').forEach(t => t.classList.remove('active'));
        tab.classList.add('active');

        document.querySelectorAll('.panel').forEach(p => p.classList.remove('active'));
        const panel = document.getElementById(target + '-panel');
        if (panel) panel.classList.add('active');

        if (target === 'dna')             { if (typeof loadDNA === 'function') loadDNA(); }
        if (target === 'memory')          { if (typeof loadMemoryExplorer === 'function') loadMemoryExplorer(); }
        if (target === 'cron')            { if (typeof loadCronFloor === 'function') loadCronFloor(); }
        if (target === 'boardroom')       { document.getElementById('debate-topic').focus(); }
        if (target === 'revenue-tracker') { if (typeof loadRevenueTracker === 'function') loadRevenueTracker(); }
        if (target === 'intelligence')    { loadIntelligence(); clearIntelUnreadDot(); }
    });
});

// ---- Collapsible Panel Headers ----
document.querySelectorAll('.panel-header.collapsible').forEach(header => {
    header.addEventListener('click', () => {
        const targetId = header.getAttribute('data-target');
        const body = document.getElementById(targetId);
        if (!body) return;
        const collapsed = body.style.display === 'none';
        body.style.display = collapsed ? '' : 'none';
        header.classList.toggle('collapsed', !collapsed);
    });
});

// ---- Section Collapsibles (data-section) ----
document.querySelectorAll('[data-section]').forEach(header => {
    const sectionId = header.getAttribute('data-section');
    const content = document.getElementById(`section-${sectionId}`);
    if (!content) return;
    header.addEventListener('click', () => {
        const isOpen = content.classList.contains('open');
        content.classList.toggle('open', !isOpen);
        header.classList.toggle('open', !isOpen);
    });
});

// Open Daily Rundown on load
const firstSection = document.querySelector('[data-section="rundown"]');
const firstContent = document.getElementById('section-rundown');
if (firstSection && firstContent) {
    firstSection.classList.add('open');
    firstContent.classList.add('open');
}

// ==================== BOARDROOM v2.0 — MULTI-ROUND + FILE ATTACH ====================

// ---- State ----
let debateHistory  = [];   // array of rounds; each round = [{participant, response}]
let currentRound   = 0;
let attachedFiles  = [];   // [{path, content}]
let brainFileTree  = null; // cached memory tree

// ---- File Attach ----
async function loadBrainFileTree() {
    if (brainFileTree) return brainFileTree;
    try {
        const res = await fetch(`${API_BASE}/api/v1/memory/tree`);
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const data = await res.json();
        brainFileTree = data.files || [];
    } catch (e) {
        brainFileTree = [];
    }
    return brainFileTree;
}

async function searchBrainFiles(query) {
    const resultsEl = document.getElementById('file-search-results');
    if (!query.trim()) { resultsEl.style.display = 'none'; return; }
    const tree = await loadBrainFileTree();  // array of relative path strings
    const q = query.toLowerCase();
    const matches = tree.filter(p => typeof p === 'string' && p.toLowerCase().includes(q)).slice(0, 12);
    if (!matches.length) { resultsEl.style.display = 'none'; return; }
    resultsEl.style.display = 'block';
    resultsEl.innerHTML = matches.map(p => {
        const name = p.split('/').pop();
        const escaped = p.replace(/'/g, "\\'");
        return `<div onclick="attachBrainFile('${escaped}')" style="padding:6px 10px;cursor:pointer;font-size:12px;border-bottom:1px solid var(--border);color:var(--text-secondary);" onmouseover="this.style.background='var(--bg-card)'" onmouseout="this.style.background=''">${name} <span style="opacity:0.4;font-size:10px;">${p}</span></div>`;
    }).join('');
}

async function attachBrainFile(relPath) {
    if (attachedFiles.find(f => f.path === relPath)) return;
    try {
        const res = await fetch(`${API_BASE}/api/v1/memory/read?path=${encodeURIComponent(relPath)}`);
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const data = await res.json();
        const content = data.content || '';
        attachedFiles.push({ path: relPath, content });
        renderAttachedFiles();
        document.getElementById('file-search-results').style.display = 'none';
        document.getElementById('file-search-input').value = '';
    } catch (e) {
        alert(`Could not load file: ${e.message}`);
    }
}

function removeAttachedFile(path) {
    attachedFiles = attachedFiles.filter(f => f.path !== path);
    renderAttachedFiles();
}

function renderAttachedFiles() {
    const el = document.getElementById('attached-files');
    el.innerHTML = attachedFiles.map(f => {
        const name = f.path.split('/').pop();
        const escaped = f.path.replace(/'/g, "\\'");
        return `<span style="background:var(--bg-card);border:1px solid var(--accent);border-radius:4px;padding:3px 8px;font-size:11px;display:flex;align-items:center;gap:6px;">📄 ${name} <span onclick="removeAttachedFile('${escaped}')" style="cursor:pointer;color:var(--danger);">✕</span></span>`;
    }).join('');
}

function buildFileContext() {
    if (!attachedFiles.length) return '';
    return '\n\n---\nATTACHED CONTEXT FILES:\n' +
        attachedFiles.map(f => `\n### ${f.path.split('/').pop()}\n${f.content}`).join('\n') +
        '\n---\n';
}

// ---- Debate Engine ----
async function startNewDebate() {
    const topic = document.getElementById('debate-topic').value.trim();
    if (!topic) { alert('Enter a strategic topic.'); return; }
    debateHistory = [];
    currentRound  = 0;
    document.getElementById('debate-results').innerHTML = '';
    document.getElementById('synthesis-output').innerHTML = '';
    document.getElementById('round-controls').style.display = 'none';
    await runDebateRound(topic);
}

async function nextRound() {
    const topic = document.getElementById('debate-topic').value.trim();
    await runDebateRound(topic);
}

async function runDebateRound(topic) {
    currentRound++;
    const selected = Array.from(document.querySelectorAll('.checkbox-group input:checked'))
        .map(cb => cb.value);

    // Build history context from all previous rounds
    let historyContext = '';
    if (debateHistory.length) {
        historyContext = '\n\nPREVIOUS DEBATE ROUNDS (read carefully — critique, correct, or build on these):\n';
        debateHistory.forEach((round, i) => {
            historyContext += `\n--- Round ${i + 1} ---\n`;
            round.forEach(entry => {
                historyContext += `@${entry.participant}: ${entry.response}\n\n`;
            });
        });
    }

    const fileContext = buildFileContext();
    const roundLabel = document.getElementById('round-label');
    const roundControls = document.getElementById('round-controls');
    roundControls.style.display = 'flex';
    roundLabel.textContent = `Round ${currentRound} — running…`;
    document.getElementById('next-round-btn').disabled = true;

    // Add loading cards for this round
    const resultsDiv = document.getElementById('debate-results');
    const roundSection = document.createElement('div');
    roundSection.id = `round-section-${currentRound}`;
    roundSection.innerHTML = `<div class="log-label" style="margin:16px 0 8px;">Round ${currentRound}</div><div class="debate-grid" id="round-grid-${currentRound}"><div class="loading">Round ${currentRound} — firing parallel calls…</div></div>`;
    resultsDiv.appendChild(roundSection);

    const prompt = currentRound === 1
        ? `@{persona} Boardroom Debate — Round 1. Topic: ${topic}${fileContext}\n\nGive your honest, direct position. No hedging.`
        : `@{persona} Boardroom Debate — Round ${currentRound}. Topic: ${topic}${fileContext}${historyContext}\nThis is Round ${currentRound}. Respond to the previous round(s) above. Critique flaws, correct errors, build on strengths. Be direct.`;

    const promises = selected.map(async (persona) => {
        try {
            const res = await fetch(`${API_BASE}/chat`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    message: prompt.replace(/{persona}/g, persona),
                    committee: 'council'
                })
            });
            if (!res.ok) throw new Error(`HTTP ${res.status}`);
            const data = await res.json();
            return {
                participant: persona,
                response: data.system_message || (data.responses && data.responses[persona]) || JSON.stringify(data),
                round: currentRound
            };
        } catch (e) {
            return { participant: persona, response: `Error: ${e.message}`, round: currentRound };
        }
    });

    const roundData = await Promise.all(promises);
    debateHistory.push(roundData);
    renderRoundCards(roundData, currentRound);
    roundLabel.textContent = `Round ${currentRound} complete`;
    document.getElementById('next-round-btn').disabled = false;
}

function renderRoundCards(roundData, roundNum) {
    const grid = document.getElementById(`round-grid-${roundNum}`);
    grid.innerHTML = '';
    roundData.forEach(entry => {
        const card = document.createElement('div');
        card.className = 'debate-card';
        card.innerHTML = `
            <div class="debate-card-header">
                <span class="persona-badge">@${entry.participant}</span>
                <span class="stance">Round ${roundNum}</span>
            </div>
            <div class="debate-card-body">${entry.response}</div>`;
        grid.appendChild(card);
    });
}

async function synthesizeDebate() {
    const synthesisDiv = document.getElementById('synthesis-output');
    if (!debateHistory.length) {
        synthesisDiv.innerHTML = '<p style="color:var(--warn);">No debate output to synthesize — launch a debate first.</p>';
        return;
    }
    synthesisDiv.innerHTML = '<div class="loading">Synthesizing full debate history…</div>';

    let fullTranscript = '';
    debateHistory.forEach((round, i) => {
        fullTranscript += `\n=== Round ${i + 1} ===\n`;
        round.forEach(entry => {
            fullTranscript += `@${entry.participant}: ${entry.response}\n\n`;
        });
    });

    try {
        const res = await fetch(`${API_BASE}/chat`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                message: `Council synthesis task: ${debateHistory.length} round(s) of debate below. Produce a concise, honest consensus. Note any unresolved disagreements. No ratification theater.\n\n${fullTranscript}`,
                committee: 'council'
            })
        });
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const data = await res.json();
        const text = data.system_message || (data.responses && Object.values(data.responses).join('\n\n')) || 'No synthesis returned.';
        synthesisDiv.innerHTML = `<div class="synthesis-card"><strong>Council Synthesis (${debateHistory.length} round${debateHistory.length > 1 ? 's' : ''}):</strong><br><br>${text}</div>`;
    } catch (e) {
        synthesisDiv.innerHTML = `<div class="loading" style="color:var(--danger);">Synthesis failed: ${e.message}</div>`;
    }
}

async function ratifyDecision() {
    const synthesisText = document.getElementById('synthesis-output').textContent || '';
    if (!synthesisText.trim() || synthesisText.includes('No debate output')) {
        alert('Synthesize the debate first before ratifying.');
        return;
    }
    showApprovalModal(synthesisText);
}

// ---- App Builder ----
async function generateNewPanel() {
    const id    = document.getElementById('new-panel-name').value.trim();
    const title = document.getElementById('new-panel-title').value.trim() || id;
    const desc  = document.getElementById('new-panel-desc').value.trim();

    if (!id) {
        alert('Panel ID is required (e.g. revenue-tracker)');
        return;
    }

    const output      = document.getElementById('code-output');
    const generatedDiv = document.getElementById('generated-code');
    output.textContent = 'Sending to @clc...';
    generatedDiv.classList.remove('hidden');

    const prompt = `@clc You are the PeakForge Station UI engineer. Generate a complete new panel snippet for station.html and station.js.

Panel ID: ${id}
Display title: ${title}
Description / purpose: ${desc || 'General purpose panel'}

STRICT rules — never break them:
- HTML block only: one <div id="${id}-panel" class="panel"> with panel-header containing <h1>${title}</h1>, then panel-body.
- Use only these CSS classes: panel, panel-header, panel-body, card, card-grid, card-label, card-value, log-box, log-label, log-content, station-input, station-btn, station-btn accent, status-led, status-ok, status-error, status-warn.
- JS block only: one async load function. For any fetch, use the literal \${API_BASE} — never hard-code an IP.
- Always guard fetches with if (!res.ok) throw new Error(...).
- No emoji anywhere. No inline styles except margin/padding adjustments.
- Output exactly two labeled blocks: /* HTML */ and /* JS */. No explanations, no prose.`;

    try {
        const res = await fetch(`${API_BASE}/chat`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ message: prompt, committee: 'tech' })
        });
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const data = await res.json();
        const raw  = data.responses?.clc || data.response || data.message || JSON.stringify(data, null, 2);
        // Strip <thinking>...</thinking> blocks — surface only the generated code
        output.textContent = raw.replace(/<thinking>[\s\S]*?<\/thinking>/g, '').trim();
    } catch (e) {
        output.textContent = `Error: ${e.message}`;
        console.error('App Builder:', e);
    }
}

function copyGeneratedCode(btn) {
    const text = document.getElementById('code-output').textContent;
    navigator.clipboard.writeText(text).then(() => {
        const orig = btn.textContent;
        btn.textContent = 'Copied';
        setTimeout(() => { btn.textContent = orig; }, 2000);
    });
}

// ---- Health Check ----
function setLed(ledId, state) {
    const el = document.getElementById(ledId);
    if (!el) return;
    el.className = `status-led status-${state}`;
}

async function fetchHealth() {
    // Citadel API
    try {
        const r    = await fetch(`${API_BASE}/health`);
        const data = await r.json();
        const ok   = data.status === 'ok';

        const apiEl = document.getElementById('api-health');
        if (apiEl) { apiEl.textContent = ok ? 'OK' : 'DEGRADED'; apiEl.className = `card-value ${ok ? 'ok' : 'warn'}`; }
        setLed('led-api', ok ? 'ok' : 'warn');
        const apiStatus = document.getElementById('api-status');
        if (apiStatus) apiStatus.textContent = ok ? 'LIVE' : 'DEGRADED';

        setLed('led-cl',  ok ? 'ok' : 'error');
        setLed('led-gec', ok ? 'ok' : 'warn');
        setLed('led-grc', ok ? 'ok' : 'warn');
        ['status-cl', 'status-gec', 'status-grc'].forEach(id => {
            const el = document.getElementById(id);
            if (el) el.textContent = ok ? 'LIVE' : '—';
        });
    } catch {
        const apiEl = document.getElementById('api-health');
        if (apiEl) { apiEl.textContent = 'ERR'; apiEl.className = 'card-value err'; }
        setLed('led-api', 'error');
        ['led-cl', 'led-gec', 'led-grc'].forEach(id => setLed(id, 'error'));
    }

    // OpenClaw — proxied through Citadel to avoid cross-origin block
    try {
        const oc = await fetch(`${API_BASE}/api/v1/openclaw/health`);
        const od = oc.ok ? await oc.json() : { ok: false };
        setLed('led-openclaw', od.ok ? 'ok' : 'warn');
        const el = document.getElementById('status-openclaw');
        if (el) el.textContent = od.ok ? 'LIVE' : 'DEGRADED';
    } catch {
        setLed('led-openclaw', 'error');
        const el = document.getElementById('status-openclaw');
        if (el) el.textContent = 'OFFLINE';
    }

    // gbrain dynamic status
    try {
        const gr = await fetch(`${API_BASE}/api/v1/health`);
        if (gr.ok) {
            const gd = await gr.json();
            const gEl = document.getElementById('gbrain-status');
            if (gEl) {
                const pages = gd.gbrain?.pages ?? gd.pages ?? '—';
                const score = gd.gbrain?.brain_score ?? gd.brain_score ?? null;
                gEl.textContent = `${pages} pages • ${score !== null ? `Score ${score}` : 'Healthy'}`;
            }
            setLed('led-gbrain', 'ok');
        }
    } catch { /* gbrain status stays as last known */ }
}

// ---- Token Velocity LED ----
async function updateTokenVelocity() {
    try {
        const r = await fetch(`${API_BASE}/api/v1/token/velocity`);
        if (!r.ok) return;
        const d = await r.json();
        const led  = document.getElementById('led-token-velocity');
        const text = document.getElementById('token-velocity-text');
        const card = document.getElementById('token-velocity-card');
        const state = d.state || 'green';
        const spendStr = `$${(d.total_cost || 0).toFixed(3)} / $${(d.budget || 0.50).toFixed(2)}`;
        const tooltip  = `Daily Spend: $${(d.total_cost || 0).toFixed(4)} / $${(d.budget || 0.50).toFixed(2)} | Last Runner: ${d.last_runner || '—'} | Runs today: ${d.run_count || 0}`;
        if (led)  setLed('led-token-velocity', state);
        if (text) text.textContent = spendStr;
        if (card) card.title = tooltip;
        // sidebar LED
        setLed('led-sidebar-token', state);
        const sidebarText = document.getElementById('sidebar-token-text');
        if (sidebarText) sidebarText.textContent = spendStr;
        const sidebarBar = document.getElementById('sidebar-token-velocity');
        if (sidebarBar) sidebarBar.title = tooltip;
    } catch { /* keep last state */ }
}

// ---- Mission Control — Dynamic Data ----
async function loadMissionControl() {
    try {
        // Trading summary
        const tRes  = await fetch(`${API_BASE}/api/v1/trading/summary`);
        if (!tRes.ok) throw new Error(`trading ${tRes.status}`);
        const tData = await tRes.json();

        const fleetLed = (ledId, textId, value) => {
            const led  = document.getElementById(ledId);
            const span = document.getElementById(textId);
            if (!led || !span) return;
            const v = String(value ?? '').toLowerCase().trim();
            const isOff  = ['hold','off','none','suspended','offline','—',''].some(x => v === x);
            const isLive = !isOff && (v.includes('live') || v.includes('position'));
            led.className = `status-led ${isOff ? 'status-error' : isLive ? 'status-ok' : 'status-warn'}`;
            span.textContent = value ?? '—';
        };
        fleetLed('mc-bot15-led',  'mc-bot15-text',  tData.bot15_v5?.status);
        fleetLed('mc-s1-led',     'mc-s1-text',     tData.s1_bopb?.status);
        fleetLed('mc-pmcc-led',   'mc-pmcc-text',   tData.pmcc?.status);
        fleetLed('mc-sniper-led', 'mc-sniper-text', tData.mic_sniper?.status);
        fleetLed('mc-q2-led',     'mc-q2-text',     tData.q2_watchlist?.status);
        fleetLed('mc-q3-led',     'mc-q3-text',     tData.q3_exec?.status);
        fleetLed('mc-mic-s1-led', 'mc-mic-s1-text', tData.mic_s1?.status);
        fleetLed('mc-mic-l1-led', 'mc-mic-l1-text', tData.mic_l1?.status);
        fleetLed('mc-mic-s2-led', 'mc-mic-s2-text', tData.mic_s2?.status);

        // Wire market-status stat card — trim to first segment for card display
        const marketStatusEl = document.getElementById('market-status');
        if (marketStatusEl) marketStatusEl.textContent = (tData.bot15_v5?.status || '—').split(' — ')[0];

        // Latest EOD
        const eRes  = await fetch(`${API_BASE}/api/v1/eod/latest`);
        if (!eRes.ok) throw new Error(`eod ${eRes.status}`);
        const eData = await eRes.json();
        const eodEl = document.getElementById('eod-content');
        if (eodEl) {
            eodEl.innerHTML =
                `<p><strong>${eData.date} Summary:</strong></p>` +
                `<p>${eData.summary}</p>`;
        }
    } catch (err) {
        console.error('Station: Mission Control load failed —', err.message);
    }

    await loadDailyRundown();
    await loadSovereignAuditStatus();
}

async function loadLiveData() {
    await fetchHealth();
    await updateTokenVelocity();
    await loadMissionControl();
}

loadLiveData();
setInterval(fetchHealth, 30000);
setInterval(updateTokenVelocity, 60000);

// ---- Memory Explorer ----
async function loadMemoryExplorer() {
    const treeContainer = document.getElementById('memory-tree');
    if (!treeContainer) return;
    treeContainer.style.lineHeight = '1.4';
    treeContainer.style.whiteSpace = 'normal';

    try {
        const res  = await fetch(`${API_BASE}/api/v1/memory/tree`);
        if (!res.ok) throw new Error(`memory/tree ${res.status}`);
        const data = await res.json();

        // Populate stat cards
        const gbrainPagesEl = document.getElementById('gbrain-pages');
        if (gbrainPagesEl) gbrainPagesEl.textContent = data.gbrain_pages ?? '—';
        const wikiFilesEl = document.getElementById('wiki-files');
        if (wikiFilesEl) wikiFilesEl.textContent = data.raw_file_count ?? '—';
        const recentDecEl = document.getElementById('recent-decisions');
        if (recentDecEl) recentDecEl.textContent = data.files.filter(f => f.includes('decision') || f.includes('DECISION')).length || '—';

        const fileListHTML = data.files.map(f => `
            <li onclick="viewFile('${f}')" style="padding:2px 0;margin:0;cursor:pointer;list-style:none;font-size:12px;line-height:1.4;">
                <span style="margin-right:5px;opacity:0.6;">📄</span>${f.split('/').pop()}
            </li>
        `).join('');

        treeContainer.innerHTML = `
            <div style="margin:0 0 6px 0;font-size:12px;color:#aaa;line-height:1.2;">
                <span class="status-led status-ok" style="display:inline-block;margin-right:6px;"></span>
                <strong style="color:#e0e0e0;">${data.tree}</strong> &nbsp;·&nbsp; ${data.gbrain_pages} pages &nbsp;·&nbsp; ${data.raw_file_count} raw files &nbsp;·&nbsp; ${data.status}
            </div>
            <ul style="margin:0;padding:0;display:flex;flex-direction:column;gap:0;">
                ${fileListHTML}
            </ul>
        `;
    } catch (e) {
        treeContainer.innerHTML = `<p style="color:#ff4757;">Failed to load memory substrate: ${e.message}</p>`;
    }
}

async function viewFile(filePath, displayName) {
    const modal   = document.getElementById('memory-modal');
    const titleEl = document.getElementById('modal-title');
    const bodyEl  = document.getElementById('modal-body');

    titleEl.textContent = displayName || filePath.split('/').pop();
    bodyEl.innerHTML    = '<div class="loading">Loading file from EmpireWiki/raw/...</div>';
    modal.classList.remove('hidden');

    try {
        const res = await fetch(`${API_BASE}/api/v1/memory/read?path=${encodeURIComponent(filePath)}`);
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const data = await res.json();
        bodyEl.innerHTML = `<pre style="white-space:pre-wrap;font-family:inherit;font-size:12px;line-height:1.6;">${data.content || 'File empty.'}</pre>`;
    } catch (e) {
        bodyEl.innerHTML = `<p style="color:var(--danger);">Failed to load file: ${e.message}</p>`;
    }
}

function closeMemoryModal() {
    document.getElementById('memory-modal').classList.add('hidden');
}

// ---- Nova Cron Floor ----
async function loadCronFloor() {
    const grid = document.getElementById('cron-grid');
    if (!grid) return;
    try {
        const res = await fetch(`${API_BASE}/api/v1/cron/status`);
        if (!res.ok) throw new Error(`cron/status ${res.status}`);
        const data = await res.json();

        if (!data.agents || data.agents.length === 0) {
            grid.innerHTML = '<p style="color:#aaa;font-size:12px;">No PeakForge LaunchAgents found.</p>';
            return;
        }

        grid.innerHTML = data.agents.map(agent => {
            const isCritical = agent.label.includes('openclaw') || agent.label.includes('strategic.brain');
            const isRunning  = agent.status === 'running';
            const ledClass   = isRunning ? 'status-ok' : 'status-error';
            const border     = isCritical ? '1px solid var(--accent)' : '1px solid #1e1e1e';
            const shortLabel = agent.label.replace('com.peakforge.', '');
            return `
                <div class="card" style="border:${border};padding:14px;">
                    <span class="status-led ${ledClass}"></span>
                    <strong style="font-size:13px;">${shortLabel}</strong><br>
                    <small style="color:#aaa;">PID: ${agent.pid}</small><br>
                    <span style="color:${isRunning ? '#00ff9d' : '#ff4757'};font-size:12px;font-weight:bold;">
                        ${agent.status.toUpperCase()}
                    </span>
                </div>`;
        }).join('');
    } catch (e) {
        grid.innerHTML = `<p style="color:#ff4757;font-size:12px;">Cron Floor offline — ${e.message}</p>`;
    }
}

// ---- Revenue Tracker ----
function rvToggle(id) {
    const body = document.getElementById(id);
    const chev = document.getElementById(id + '-chev');
    if (!body) return;
    const open = body.classList.toggle('visible');
    if (chev) chev.classList.toggle('open', open);
}

function rvFmt(v) {
    const abs = Math.abs(v).toFixed(2);
    return (v >= 0 ? '+' : '-') + '$' + abs;
}

function rvPlClass(v) { return v > 0.005 ? 'pos' : v < -0.005 ? 'neg' : 'neu'; }

function rvParseJsonl(text) {
    return text.trim().split('\n').filter(l => l.trim()).map(l => {
        try { return JSON.parse(l); } catch (e) { return null; }
    }).filter(Boolean);
}

async function loadValidationDashboard() {
    try {
        const res = await fetch(`${API_BASE}/api/v1/validation/kpis`);
        if (!res.ok) throw new Error(`validation/kpis ${res.status}`);
        const d = await res.json();

        // Clock
        const clk = d.clock ?? {};
        const el = id => document.getElementById(id);
        if (el('vd-clock-label'))  el('vd-clock-label').textContent  = `Day ${clk.day} of ${clk.total}`;
        if (el('vd-clock-start'))  el('vd-clock-start').textContent  = clk.start ?? '—';
        if (el('vd-clock-end'))    el('vd-clock-end').textContent    = clk.end   ?? '—';
        if (el('vd-clock-pct'))    el('vd-clock-pct').textContent    = `${clk.pct ?? 0}% complete`;
        const bar = el('vd-progress-bar');
        if (bar) bar.style.width = `${Math.min(clk.pct ?? 0, 100)}%`;

        // Gate chip
        const chip = el('vd-gate-chip');
        if (chip) {
            const gd = d.gate_decision ?? 'ACCUMULATING';
            chip.textContent  = gd === 'PASS' ? 'TRACK B: PASS' : gd === 'FAIL' ? 'TRACK B: FAIL' : 'ACCUMULATING';
            chip.style.background = gd === 'PASS' ? '#1a3a1a' : gd === 'FAIL' ? '#3a1a1a' : '#1a1a2a';
            chip.style.color      = gd === 'PASS' ? '#4c4'    : gd === 'FAIL' ? '#c44'    : '#77a';
        }

        // Corpus bar
        const corp = d.corpus ?? {};
        const corpEl = el('vd-corpus-bar');
        if (corpEl) corpEl.textContent =
            `Corpus: ${corp.total} total signals · ${corp.with_outcomes} with outcomes · ` +
            `conviction ≥8: ${corp.conviction_gte8} · <8: ${corp.conviction_lt8}`;

        // KPI grid
        const grid = el('vd-kpi-grid');
        if (grid && d.kpis) {
            const statusColor = s => s === 'GREEN' ? '#4c4' : s === 'RED' ? '#c44' : s === 'YELLOW' ? '#ca4' : '#77a';
            const statusBg    = s => s === 'GREEN' ? '#0d1f0d' : s === 'RED' ? '#1f0d0d' : s === 'YELLOW' ? '#1f1a0d' : '#0d0d1f';
            const fmtVal = (kpi) => {
                const v = kpi.value;
                if (v === null || v === undefined) return '—';
                if (v === '∞') return '∞';
                if (typeof v === 'object') {
                    // max_dd or gate_integrity
                    if (v.directional !== undefined) return `${v.directional}% / ${v.pmcc}% / ${v.combined}%`;
                    if (v.wr_ge8 !== undefined) return `${(v.wr_ge8*100).toFixed(0)}% vs ${(v.wr_lt8*100).toFixed(0)}%`;
                    if (v.directional !== undefined) return `$${v.directional}`;
                    return JSON.stringify(v);
                }
                return `${v}${kpi.unit ?? ''}`;
            };
            grid.innerHTML = Object.values(d.kpis).map(kpi => `
                <div title="${kpi.note ?? ''}" style="background:${statusBg(kpi.status)};border:1px solid ${statusColor(kpi.status)}33;border-radius:5px;padding:7px 9px;cursor:default;">
                    <div style="font-size:0.52rem;font-weight:700;letter-spacing:0.1em;text-transform:uppercase;color:#999;margin-bottom:3px;">${kpi.label}</div>
                    <div style="display:flex;align-items:baseline;gap:6px;">
                        <span style="font-size:1rem;font-weight:700;color:${statusColor(kpi.status)};font-variant-numeric:tabular-nums;">${fmtVal(kpi)}</span>
                        ${kpi.threshold && typeof kpi.threshold !== 'object' ? `<span style="font-size:0.58rem;color:#aaa;">threshold: ${kpi.threshold}${kpi.unit ?? ''}</span>` : ''}
                    </div>
                    <div style="font-size:0.55rem;color:#aaa;margin-top:2px;">${kpi.status === 'ACCUMULATING' ? kpi.note ?? '' : `n=${kpi.corpus}`}</div>
                </div>
            `).join('');
        }

        // Scatter plot
        const canvas = el('vd-scatter');
        if (canvas && d.scatter && d.scatter.length > 0) {
            const dpr   = window.devicePixelRatio || 1;
            const cssW  = canvas.offsetWidth  || 480;
            const cssH  = 200;
            canvas.width  = cssW * dpr;
            canvas.height = cssH * dpr;
            canvas.style.width  = cssW + 'px';
            canvas.style.height = cssH + 'px';
            const ctx   = canvas.getContext('2d');
            ctx.scale(dpr, dpr);
            const W     = cssW;
            const H     = cssH;
            const pad   = {l: 36, r: 12, t: 12, b: 28};
            ctx.clearRect(0, 0, W, H);

            // Axis ranges
            const pnls  = d.scatter.map(p => p.pnl);
            const yMin  = Math.min(Math.floor(Math.min(...pnls) - 5), -5);
            const yMax  = Math.max(Math.ceil(Math.max(...pnls) + 5), 10);
            const xMin  = 1, xMax = 10;

            const toX = v => pad.l + (v - xMin) / (xMax - xMin) * (W - pad.l - pad.r);
            const toY = v => H - pad.b - (v - yMin) / (yMax - yMin) * (H - pad.t - pad.b);

            // Grid lines
            ctx.strokeStyle = '#1a1a1a'; ctx.lineWidth = 1;
            for (let x = xMin; x <= xMax; x++) {
                ctx.beginPath(); ctx.moveTo(toX(x), pad.t); ctx.lineTo(toX(x), H - pad.b); ctx.stroke();
            }
            // Zero line
            const y0 = toY(0);
            ctx.strokeStyle = '#333'; ctx.lineWidth = 1;
            ctx.beginPath(); ctx.moveTo(pad.l, y0); ctx.lineTo(W - pad.r, y0); ctx.stroke();

            // Axes labels
            ctx.fillStyle = '#777'; ctx.font = '9px monospace'; ctx.textAlign = 'center';
            for (let x = xMin; x <= xMax; x++) {
                ctx.fillText(x, toX(x), H - 6);
            }
            ctx.textAlign = 'right'; ctx.font = '8px monospace';
            for (let y = Math.ceil(yMin / 10) * 10; y <= yMax; y += 10) {
                ctx.fillText(y, pad.l - 3, toY(y) + 3);
            }

            // Dots
            d.scatter.forEach(pt => {
                const win = pt.pnl >= 0 ? '#4a8' : '#c44';
                ctx.beginPath();
                ctx.arc(toX(pt.conviction), toY(pt.pnl), 4, 0, Math.PI * 2);
                ctx.fillStyle = win;
                ctx.fill();
                // Ticker label
                ctx.fillStyle = '#aaa'; ctx.font = '7px monospace'; ctx.textAlign = 'center';
                ctx.fillText(pt.ticker, toX(pt.conviction), toY(pt.pnl) - 6);
            });
        } else if (canvas) {
            const dpr  = window.devicePixelRatio || 1;
            const cssW = canvas.offsetWidth || 480;
            const cssH = 200;
            canvas.width  = cssW * dpr;
            canvas.height = cssH * dpr;
            canvas.style.width  = cssW + 'px';
            canvas.style.height = cssH + 'px';
            const ctx = canvas.getContext('2d');
            ctx.scale(dpr, dpr);
            ctx.clearRect(0, 0, cssW, cssH);
            ctx.fillStyle = '#666'; ctx.font = '11px monospace'; ctx.textAlign = 'center';
            ctx.fillText('No outcome signals yet — accumulating corpus', cssW / 2, cssH / 2);
        }
    } catch (err) {
        const g = document.getElementById('vd-kpi-grid');
        if (g) g.innerHTML = `<div style="color:#999;font-size:0.7rem;grid-column:span 2;padding:8px;">Validation API offline — ${err.message}</div>`;
    }
}

async function loadRevenueTracker() {
    // Validation dashboard first
    await loadValidationDashboard();

    // Legacy API cards (MRR / forecast / Bot15 LEDs)
    try {
        const res = await fetch(`${API_BASE}/api/v1/revenue/summary`);
        if (!res.ok) throw new Error(`revenue/summary ${res.status}`);
        const d = await res.json();
        const el = id => document.getElementById(id);
        if (el('revenue-mrr-value'))      el('revenue-mrr-value').textContent      = d.mrr            ?? '—';
        if (el('revenue-mrr-label'))      el('revenue-mrr-label').textContent      = d.mrr_label      ?? 'MRR (current)';
        if (el('revenue-eng2-value'))     el('revenue-eng2-value').textContent     = d.engine2_mrr    ?? '—';
        if (el('revenue-pipeline-value')) el('revenue-pipeline-value').textContent = d.pipeline_value ?? '—';
        if (el('revenue-fc30'))           el('revenue-fc30').textContent           = d.forecast_30d   ?? '—';
        if (el('revenue-fc60'))           el('revenue-fc60').textContent           = d.forecast_60d   ?? '—';
        if (el('revenue-fc90'))           el('revenue-fc90').textContent           = d.forecast_90d   ?? '—';
    } catch (_) { /* API not live yet — cards stay at — */ }

    try {
        const res = await fetch(`${API_BASE}/api/v1/trading/summary`);
        if (!res.ok) throw new Error(`trading/summary ${res.status}`);
        const t = await res.json();
        const INACTIVE = new Set(['hold','off','none','suspended','—','']);
        const applyLed = (ledId, textId, value) => {
            const led = document.getElementById(ledId);
            const span = document.getElementById(textId);
            if (!led || !span) return;
            const v = String(value ?? '').toLowerCase().trim();
            led.className = `status-led ${INACTIVE.has(v) ? 'status-error' : 'status-ok'}`;
            span.textContent = value ?? '—';
        };
        applyLed('bot15-led', 'bot15-status-text', t.bot15_v5?.status);
        applyLed('pmcc-led',  'pmcc-signal-text',  t.pmcc_signal);
        applyLed('dux-led',   'dux-signal-text',   t.dux_signal ?? '—');
        const hn = document.getElementById('bot15-hold-note');
        if (hn) hn.textContent = t.hold_note ?? 'No active hold.';
    } catch (_) { /* API not live yet */ }

    // Live engine cards (local data files)
    const banner = document.getElementById('rv-error-banner');
    if (banner) banner.style.display = 'none';
    try {
        const [stateRes, signalsRes, q2Res, q3Res, micS1Res, micL1Res, micS2Res, pmccRes] = await Promise.all([
            fetch('data/s1_bopb_state.json?t='         + Date.now()),
            fetch('data/paper_signals.jsonl?t='        + Date.now()),
            fetch('data/q2_equity_state.json?t='       + Date.now()),
            fetch('data/q3_equity_state.json?t='       + Date.now()),
            fetch('data/s1_lhf_state.json?t='          + Date.now()),
            fetch('data/l1_bounce_state.json?t='       + Date.now()),
            fetch('data/s2_death_candle_state.json?t=' + Date.now()),
            fetch('data/pmcc_paper_state.json?t='      + Date.now()),
        ]);
        if (!stateRes.ok)   throw new Error('s1_bopb_state.json: HTTP '   + stateRes.status);
        if (!signalsRes.ok) throw new Error('paper_signals.jsonl: HTTP '  + signalsRes.status);
        const state     = await stateRes.json();
        const signals   = rvParseJsonl(await signalsRes.text());
        const q2State   = q2Res.ok    ? await q2Res.json()    : { open_positions: {} };
        const q3State   = q3Res.ok    ? await q3Res.json()    : { open_positions: {} };
        const micS1State = micS1Res.ok ? await micS1Res.json() : { open_positions: {} };
        const micL1State = micL1Res.ok ? await micL1Res.json() : { open_positions: {} };
        const micS2State = micS2Res.ok ? await micS2Res.json() : { open_positions: {} };
        const pmccState  = pmccRes.ok  ? await pmccRes.json()  : { positions: {} };
        rvRender(state, signals, q2State, q3State, micS1State, micL1State, micS2State, pmccState);
    } catch (err) {
        if (banner) { banner.textContent = 'Data fetch failed — ' + err.message + '. Serving fallback snapshot.'; banner.style.display = 'block'; }
        rvRenderFallback();
    }
    const el = document.getElementById('rv-last-refresh');
    if (el) el.textContent = 'Refreshed ' + new Date().toLocaleTimeString('en-US', {hour:'2-digit',minute:'2-digit',second:'2-digit'});
}

function rvRender(state, signals, q2State = {}, q3State = {}, micS1State = {}, micL1State = {}, micS2State = {}, pmccState = {}) {
    // Bot 15
    const b15 = signals.filter(s => s.bot_id === 'BOT15_V5').slice(-1)[0];
    const b15Chip   = document.getElementById('rv-b15-chip');
    const b15Reason = document.getElementById('rv-b15-reason');
    if (b15 && b15.signal_type === 'NO_TRADE') {
        const meta = b15.metadata || {};
        const vix  = meta.vix ? 'VIX ' + meta.vix : '';
        const gate = meta.gate || b15.status || 'no signal';
        if (b15Chip)   { b15Chip.className = 'rv-bot-chip standby'; b15Chip.textContent = 'Standby'; }
        if (b15Reason) b15Reason.textContent = [vix, gate].filter(Boolean).join(' · ');
    } else if (b15 && b15.signal_type === 'ENTRY') {
        if (b15Chip)   { b15Chip.className = 'rv-bot-chip live'; b15Chip.textContent = 'Live'; }
        if (b15Reason) b15Reason.textContent = b15.instrument || '';
    } else {
        if (b15Reason) b15Reason.textContent = 'No signal today';
    }

    // S1 positions
    const tickers = Object.keys(state);
    const eodMap  = {};
    signals.filter(s => s.signal_type === 'EOD_HOLD').forEach(s => { eodMap[s.instrument] = s; });

    let totalPL = 0, maxDays = 0, rowsHtml = '';

    tickers.forEach(ticker => {
        const pos      = state[ticker];
        const eod      = eodMap[ticker];
        const livePrice = pos.session_high && pos.session_high > pos.entry_price ? pos.session_high : null;
        const eodPrice = livePrice ?? (eod ? eod.eod_price : pos.entry_price);
        const pl       = (eodPrice - pos.entry_price) * pos.shares;
        const risk     = pos.risk || (pos.entry_price - pos.stop_price);
        const rMult    = risk > 0 ? (eodPrice - pos.entry_price) / risk : 0;
        const progress = (pos.target_price - pos.entry_price) > 0
            ? Math.min(100, Math.max(0, ((eodPrice - pos.entry_price) / (pos.target_price - pos.entry_price)) * 100)) : 0;
        totalPL += pl;
        if (pos.days_held > maxDays) maxDays = pos.days_held;

        const cls  = rvPlClass(pl);
        const rCls = rvPlClass(rMult);
        rowsHtml += `<div class="rv-pos-row">
            <div><div class="rv-ticker">${ticker}</div><div class="rv-meta">Day ${pos.days_held}</div></div>
            <div>
                <div class="rv-num">$${pos.entry_price.toFixed(2)} → $${eodPrice.toFixed(2)}</div>
                <div class="rv-bar-wrap"><div class="rv-bar-fill ${cls}" style="width:${Math.abs(progress)}%"></div></div>
            </div>
            <div class="rv-num">${pos.shares}</div>
            <div class="rv-num">$${pos.stop_price.toFixed(2)}</div>
            <div class="rv-num">$${pos.target_price.toFixed(2)}</div>
            <div class="rv-pl ${cls}">${rvFmt(pl)}</div>
            <div class="rv-r ${rCls}">${rMult >= 0 ? '+' : ''}${rMult.toFixed(2)}R</div>
        </div>`;
    });

    if (!tickers.length) rowsHtml = '<div style="padding:12px 0;color:#999;font-size:0.72rem;">No open positions</div>';
    const posList = document.getElementById('rv-positions-list');
    if (posList) posList.innerHTML = rowsHtml;

    // S1 summary row
    const s1Pl   = document.getElementById('rv-s1-pl');
    const s1Dot  = document.getElementById('rv-s1-dot');
    const s1Chip = document.getElementById('rv-s1-chip');
    const s1Rsn  = document.getElementById('rv-s1-reason');
    if (s1Pl)   { s1Pl.textContent = rvFmt(totalPL); s1Pl.className = 'rv-bot-pl ' + rvPlClass(totalPL); }
    if (s1Rsn)  s1Rsn.textContent = tickers.length ? tickers.join(', ') + ' · Day ' + maxDays : 'No positions';
    if (s1Dot)  s1Dot.className = tickers.length ? 'rv-pulse' : 'rv-pulse dormant';
    if (s1Chip) { s1Chip.className = tickers.length ? 'rv-bot-chip live' : 'rv-bot-chip standby'; s1Chip.textContent = tickers.length ? 'Live' : 'Standby'; }

    // Bot Q2 — Breakout Long
    const q2Pos  = (q2State && q2State.open_positions) ? q2State.open_positions : {};
    const q2Tickers = Object.keys(q2Pos);
    let q2PL = 0;
    q2Tickers.forEach(t => {
        const p = q2Pos[t];
        q2PL += (p.entry_price > 0) ? ((p.entry_price) - p.entry_price) * p.shares_remaining : 0;
        // Live P/L unavailable without real-time feed — show entry info
    });
    const q2Dot  = document.getElementById('rv-q2-dot');
    const q2Chip = document.getElementById('rv-q2-chip');
    const q2Rsn  = document.getElementById('rv-q2-reason');
    const q2PlEl = document.getElementById('rv-q2-pl');
    if (q2Tickers.length > 0) {
        if (q2Dot)  q2Dot.className  = 'rv-pulse';
        if (q2Chip) { q2Chip.className = 'rv-bot-chip live'; q2Chip.textContent = 'Live'; }
        if (q2Rsn)  q2Rsn.textContent = q2Tickers.map(t => `${t} (day ${q2Pos[t].partial_exits ?? 0} exits)`).join(', ');
        if (q2PlEl) { q2PlEl.textContent = '—'; q2PlEl.className = 'rv-bot-pl neu'; }
    } else {
        if (q2Dot)  q2Dot.className  = 'rv-pulse dormant';
        if (q2Chip) { q2Chip.className = 'rv-bot-chip standby'; q2Chip.textContent = 'Standby'; }
        if (q2Rsn)  q2Rsn.textContent = 'No positions — awaiting conviction ≥ 8 signal';
        if (q2PlEl) { q2PlEl.textContent = '—'; q2PlEl.className = 'rv-bot-pl neu'; }
    }

    // Bot Q3 — Parabolic Short
    const q3Pos  = (q3State && q3State.open_positions) ? q3State.open_positions : {};
    const q3Tickers = Object.keys(q3Pos);
    const q3Dot  = document.getElementById('rv-q3-dot');
    const q3Chip = document.getElementById('rv-q3-chip');
    const q3Rsn  = document.getElementById('rv-q3-reason');
    const q3PlEl = document.getElementById('rv-q3-pl');
    if (q3Tickers.length > 0) {
        if (q3Dot)  q3Dot.className  = 'rv-pulse';
        if (q3Chip) { q3Chip.className = 'rv-bot-chip live'; q3Chip.textContent = 'Live'; }
        if (q3Rsn)  q3Rsn.textContent = q3Tickers.map(t => `${t} short (${q3Pos[t].shares_remaining ?? 0} shares)`).join(', ');
        if (q3PlEl) { q3PlEl.textContent = '—'; q3PlEl.className = 'rv-bot-pl neu'; }
    } else {
        if (q3Dot)  q3Dot.className  = 'rv-pulse dormant';
        if (q3Chip) { q3Chip.className = 'rv-bot-chip standby'; q3Chip.textContent = 'Standby'; }
        if (q3Rsn)  q3Rsn.textContent = 'No shorts — awaiting parabolic exhaustion signal';
        if (q3PlEl) { q3PlEl.textContent = '—'; q3PlEl.className = 'rv-bot-pl neu'; }
    }

    // MIC S1 — LHF Short
    // MIC S1 — LHF Short [ASYMMETRIC gate: EV ≥ 1% + Sharpe ≥ 2.0]
    const micS1Pos     = (micS1State && micS1State.open_positions) ? micS1State.open_positions : {};
    const micS1Tickers = Object.keys(micS1Pos);
    const micS1NetPnl  = (micS1State && micS1State.ledger)
        ? micS1State.ledger.reduce((a, e) => a + (e.net_pnl ?? 0), 0) : 0;
    const micS1Dot  = document.getElementById('rv-mic-s1-dot');
    const micS1Chip = document.getElementById('rv-mic-s1-chip');
    const micS1Rsn  = document.getElementById('rv-mic-s1-reason');
    const micS1Pl   = document.getElementById('rv-mic-s1-pl');
    if (micS1Tickers.length > 0) {
        if (micS1Dot)  micS1Dot.className  = 'rv-pulse';
        if (micS1Chip) { micS1Chip.className = 'rv-bot-chip live'; micS1Chip.textContent = 'Live'; }
        if (micS1Rsn)  micS1Rsn.textContent = micS1Tickers.map(t =>
            t + ' short (' + (micS1Pos[t].shares_remaining ?? micS1Pos[t].shares_total ?? '?') + ' sh)').join(', ');
        if (micS1Pl)   { micS1Pl.textContent = rvFmt(micS1NetPnl); micS1Pl.className = 'rv-bot-pl ' + rvPlClass(micS1NetPnl); }
    } else {
        if (micS1Dot)  micS1Dot.className  = 'rv-pulse dormant';
        if (micS1Chip) { micS1Chip.className = 'rv-bot-chip standby'; micS1Chip.textContent = 'Shadow'; }
        if (micS1Rsn)  micS1Rsn.textContent = 'Phase 3 Shadow · EV 5.56% Sharpe 6.97 · Day 2 outer-line (conv ≥ 8)';
        if (micS1Pl)   { micS1Pl.textContent = micS1NetPnl !== 0 ? rvFmt(micS1NetPnl) : '—'; micS1Pl.className = 'rv-bot-pl ' + (micS1NetPnl !== 0 ? rvPlClass(micS1NetPnl) : 'neu'); }
    }

    // MIC L1 — First Bounce Long [ASYMMETRIC gate: EV ≥ 1% + Sharpe ≥ 2.0]
    const micL1Pos     = (micL1State && micL1State.open_positions) ? micL1State.open_positions : {};
    const micL1Tickers = Object.keys(micL1Pos);
    const micL1NetPnl  = (micL1State && micL1State.ledger)
        ? micL1State.ledger.reduce((a, e) => a + (e.net_pnl ?? 0), 0) : 0;
    const micL1Dot  = document.getElementById('rv-mic-l1-dot');
    const micL1Chip = document.getElementById('rv-mic-l1-chip');
    const micL1Rsn  = document.getElementById('rv-mic-l1-reason');
    const micL1Pl   = document.getElementById('rv-mic-l1-pl');
    if (micL1Tickers.length > 0) {
        if (micL1Dot)  micL1Dot.className  = 'rv-pulse';
        if (micL1Chip) { micL1Chip.className = 'rv-bot-chip live'; micL1Chip.textContent = 'Live'; }
        if (micL1Rsn)  micL1Rsn.textContent = micL1Tickers.map(t =>
            t + ' long (' + (micL1Pos[t].shares_remaining ?? micL1Pos[t].shares_total ?? '?') + ' sh)').join(', ');
        if (micL1Pl)   { micL1Pl.textContent = rvFmt(micL1NetPnl); micL1Pl.className = 'rv-bot-pl ' + rvPlClass(micL1NetPnl); }
    } else {
        if (micL1Dot)  micL1Dot.className  = 'rv-pulse dormant';
        if (micL1Chip) { micL1Chip.className = 'rv-bot-chip standby'; micL1Chip.textContent = 'Shadow'; }
        if (micL1Rsn)  micL1Rsn.textContent = 'Phase 3 Shadow · EV 2.52% Sharpe 2.92 · Day 1 VWAP-bounce (conv ≥ 8)';
        if (micL1Pl)   { micL1Pl.textContent = micL1NetPnl !== 0 ? rvFmt(micL1NetPnl) : '—'; micL1Pl.className = 'rv-bot-pl ' + (micL1NetPnl !== 0 ? rvPlClass(micL1NetPnl) : 'neu'); }
    }

    // MIC S2 — Death Candle Short [BALANCED gate: EV ≥ 0.5% + WR ≥ 60%]
    const micS2Pos     = (micS2State && micS2State.open_positions) ? micS2State.open_positions : {};
    const micS2Tickers = Object.keys(micS2Pos);
    const micS2NetPnl  = (micS2State && micS2State.ledger)
        ? micS2State.ledger.reduce((a, e) => a + (e.net_pnl ?? 0), 0) : 0;
    const micS2Dot  = document.getElementById('rv-mic-s2-dot');
    const micS2Chip = document.getElementById('rv-mic-s2-chip');
    const micS2Rsn  = document.getElementById('rv-mic-s2-reason');
    const micS2Pl   = document.getElementById('rv-mic-s2-pl');
    if (micS2Tickers.length > 0) {
        if (micS2Dot)  micS2Dot.className  = 'rv-pulse';
        if (micS2Chip) { micS2Chip.className = 'rv-bot-chip live'; micS2Chip.textContent = 'Live'; }
        if (micS2Rsn)  micS2Rsn.textContent = micS2Tickers.map(t => t + ' · dc short').join(', ');
        if (micS2Pl)   { micS2Pl.textContent = rvFmt(micS2NetPnl); micS2Pl.className = 'rv-bot-pl ' + rvPlClass(micS2NetPnl); }
    } else {
        if (micS2Dot)  micS2Dot.className  = 'rv-pulse dormant';
        if (micS2Chip) { micS2Chip.className = 'rv-bot-chip standby'; micS2Chip.textContent = 'Shadow'; }
        if (micS2Rsn)  micS2Rsn.textContent = 'Phase 3 Shadow · WR 69.7% EV 1.20% · death candle reactive (conv ≥ 8)';
        if (micS2Pl)   { micS2Pl.textContent = micS2NetPnl !== 0 ? rvFmt(micS2NetPnl) : '—'; micS2Pl.className = 'rv-bot-pl ' + (micS2NetPnl !== 0 ? rvPlClass(micS2NetPnl) : 'neu'); }
    }

    // PMCC
    const pmccPositions = (pmccState && pmccState.positions) ? pmccState.positions : {};
    const pmccTickers   = Object.keys(pmccPositions);
    const pmccNetPnl    = Object.values(pmccPositions).reduce((a, p) => a + (p.net_pnl_to_date ?? 0), 0);
    totalPL += pmccNetPnl;
    const pmccDot  = document.getElementById('rv-pmcc-dot');
    const pmccChip = document.getElementById('rv-pmcc-chip');
    const pmccRsn  = document.getElementById('rv-pmcc-reason');
    const pmccPlEl = document.getElementById('rv-pmcc-pl');
    if (pmccTickers.length > 0) {
        if (pmccDot)  pmccDot.className  = 'rv-pulse';
        if (pmccChip) { pmccChip.className = 'rv-bot-chip live'; pmccChip.textContent = 'Paper'; }
        if (pmccRsn)  pmccRsn.textContent = pmccTickers.join(', ') + ' · LEAPS+short';
        if (pmccPlEl) { pmccPlEl.textContent = rvFmt(pmccNetPnl); pmccPlEl.className = 'rv-bot-pl ' + rvPlClass(pmccNetPnl); }
    } else {
        if (pmccDot)  pmccDot.className  = 'rv-pulse dormant';
        if (pmccChip) { pmccChip.className = 'rv-bot-chip standby'; pmccChip.textContent = 'Standby'; }
        if (pmccRsn)  pmccRsn.textContent = 'Scanning TSLA · NVDA · MSTR';
        if (pmccPlEl) { pmccPlEl.textContent = '—'; pmccPlEl.className = 'rv-bot-pl neu'; }
    }

    // Earnings Watch card
    const ewPanel = document.getElementById('rv-earnings-watch');
    const ewRows  = document.getElementById('rv-earnings-watch-rows');
    if (ewPanel && ewRows) {
        const watchEntries = [];
        for (const [tkr, p] of Object.entries(pmccPositions)) {
            const shortStatus = p.current_short?.status ?? '';
            const earningsDate = p.earnings_date ?? null;
            if (shortStatus === 'closed_pre_earnings' && p.earnings_pre_close) {
                const n = (p.vol_settle_checks ?? []).length;
                watchEntries.push(`${tkr}: VOL SETTLEMENT PENDING (${n}/3 readings)`);
            } else if (p.earnings_flag && earningsDate) {
                watchEntries.push(`${tkr}: CLOSE REQUIRED — earnings ${earningsDate}`);
            }
        }
        if (watchEntries.length > 0) {
            ewPanel.style.display = '';
            ewRows.innerHTML = watchEntries.map(e => `<div>${e}</div>`).join('');
        } else {
            ewPanel.style.display = 'none';
        }
    }

    // Engine 3 header P/L
    const e3Pl = document.getElementById('rv-e3-pl');
    if (e3Pl) { e3Pl.textContent = rvFmt(totalPL); e3Pl.className = 'rv-engine-pl ' + rvPlClass(totalPL); }

    // Stat grid
    const sp = document.getElementById('rv-stat-pos');   if (sp) sp.textContent = tickers.length;
    const sd = document.getElementById('rv-stat-days');  if (sd) sd.textContent = maxDays > 0 ? maxDays + 'd' : '—';
    const sf = document.getElementById('rv-stat-fleet'); if (sf) { sf.textContent = rvFmt(totalPL); sf.className = 'rv-stat-val ' + rvPlClass(totalPL); }

    // Grand total bar
    const tot = document.getElementById('rv-total-pl');
    if (tot) { tot.textContent = rvFmt(totalPL); tot.className = 'rv-total-value ' + rvPlClass(totalPL); }
    const dot = document.getElementById('rv-live-dot');
    if (dot) dot.className = tickers.length ? 'rv-pulse' : 'rv-pulse standby';
    const meta = document.getElementById('rv-total-meta');
    if (meta) meta.innerHTML = `Engine 3 · ${tickers.length} position${tickers.length !== 1 ? 's' : ''}<br>Bot 15: standby · S1: ${tickers.length} open`;
}

function rvRenderFallback() {
    const state = {
        "AAPL":{"entry_price":279.98,"shares":10,"stop_price":274.62,"target_price":288.42,"days_held":1,"risk":5.36},
        "NVO": {"entry_price":44.31, "shares":10,"stop_price":35.93, "target_price":55.81, "days_held":1,"risk":8.38}
    };
    const signals = [
        {"bot_id":"BOT15_V5","signal_type":"NO_TRADE","instrument":"XSP","status":"no_hc_regime","metadata":{"vix":17.38,"gate":"basic_fail"}},
        {"bot_id":"S1_BOPB","signal_type":"EOD_HOLD","instrument":"AAPL","eod_price":276.6,"stop_price":274.62,"target_price":288.42,"shares":10,"days_held":1},
        {"bot_id":"S1_BOPB","signal_type":"EOD_HOLD","instrument":"NVO", "eod_price":44.45,"stop_price":35.93, "target_price":55.81, "shares":10,"days_held":1}
    ];
    rvRender(state, signals);
}

// ---- Daily Rundown Stat Cards ----
async function loadDailyRundown() {
    // Crons Active — response shape is {agents: [...]}
    try {
        const res  = await fetch(`${API_BASE}/api/v1/cron/status`);
        if (!res.ok) throw new Error(`cron/status ${res.status}`);
        const data = await res.json();
        const active = (data.agents || []).filter(a => a.status === 'running').length;
        const el = document.getElementById('crons-active');
        if (el) el.textContent = active;
    } catch (e) {
        console.error('Daily Rundown: cron fetch failed —', e.message);
    }

    // Overnight accomplishments + today's to-do — parsed from latest EOD
    try {
        const res  = await fetch(`${API_BASE}/api/v1/eod/rundown`);
        if (!res.ok) throw new Error(`eod/rundown ${res.status}`);
        const data = await res.json();
        const oLog = document.getElementById('overnight-log');
        const tLog = document.getElementById('todo-log');
        if (oLog) {
            oLog.innerHTML = data.accomplished.length
                ? data.accomplished.map((a, i) => `<div style="margin-bottom:3px"><strong>${i+1}.</strong> ${a}</div>`).join('')
                : '<em style="opacity:.5">No shipped items found in last EOD.</em>';
        }
        if (tLog) {
            tLog.innerHTML = data.todo.length
                ? data.todo.map(t => `<div style="margin-bottom:3px">· ${t}</div>`).join('')
                : '<em style="opacity:.5">No open items found in last EOD.</em>';
        }
    } catch (e) {
        console.error('Daily Rundown: EOD rundown fetch failed —', e.message);
    }

    // email-count has no source yet — leave as —
    await loadApiUsage();
}

async function loadApiUsage() {
    try {
        const res = await fetch(`${API_BASE}/api/v1/system/usage`);
        if (!res.ok) throw new Error(`system/usage ${res.status}`);
        const d = await res.json();
        const fmt = n => n >= 1000 ? `${(n/1000).toFixed(1)}k` : String(n);
        const el = id => document.getElementById(id);
        if (el('usage-anthropic')) el('usage-anthropic').innerHTML =
            `Anthropic: <strong>${fmt(d.anthropic_tokens || 0)}</strong> tok`;
        if (el('usage-gemini'))    el('usage-gemini').innerHTML =
            `Gemini: <strong>${fmt(d.gemini_tokens || 0)}</strong> tok`;
        if (el('usage-grok'))      el('usage-grok').innerHTML =
            `Grok: <strong>${fmt(d.grok_tokens || 0)}</strong> tok`;
        if (el('usage-cache'))     el('usage-cache').innerHTML =
            `Cache Hit: <strong>${d.cache_hit_rate || 0}%</strong>`;
        if (el('usage-cost'))      el('usage-cost').innerHTML =
            `Est. <strong>$${(d.estimated_cost || 0).toFixed(4)}</strong>`;
    } catch (e) {
        console.error('API Usage fetch failed —', e.message);
    }
}

// ==================== SOVEREIGN AUDIT ====================

async function loadSovereignAuditStatus() {
    try {
        const res = await fetch(`${API_BASE}/api/v1/sovereign-audit/status`);
        if (!res.ok) throw new Error(`sovereign-audit/status ${res.status}`);
        const d = await res.json();

        const el = id => document.getElementById(id);

        // Verdict badge in section header
        const badge = el('audit-verdict-badge');
        if (badge) {
            badge.textContent = d.verdict || '—';
            const isClean = (d.verdict || '').toLowerCase().includes('clean');
            badge.style.background = isClean ? 'rgba(0,255,157,0.15)' : 'rgba(255,68,68,0.15)';
            badge.style.color = isClean ? 'var(--accent)' : 'var(--danger)';
        }

        if (el('audit-verdict'))   el('audit-verdict').textContent  = d.verdict   || '—';
        if (el('audit-last-run'))  el('audit-last-run').textContent  = d.last_run  || 'Never';

        // Cycle 1 status from response
        if (d.cycle_1 && el('audit-cycle-status')) {
            el('audit-cycle-status').textContent = d.cycle_1.status || 'ACTIVE';
        }

        if (el('audit-report'))       el('audit-report').textContent      = d.report            || 'No report yet.';
        if (el('audit-drift-entry'))  el('audit-drift-entry').textContent  = d.last_drift_entry  || '—';

    } catch (e) {
        console.error('Sovereign Audit status fetch failed —', e.message);
        const rep = document.getElementById('audit-report');
        if (rep) rep.textContent = `Status fetch failed: ${e.message}`;
    }
}

async function runSovereignAudit() {
    const btn = document.getElementById('audit-run-btn');
    const rep = document.getElementById('audit-report');
    if (btn) { btn.disabled = true; btn.textContent = 'Running…'; }
    if (rep) rep.textContent = 'Audit running — this takes ~15 seconds…';

    try {
        const res = await fetch(`${API_BASE}/api/v1/sovereign-audit/run`, { method: 'POST' });
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const d = await res.json();
        if (rep) rep.textContent = d.output || 'Audit complete.';
        // Refresh status cards
        await loadSovereignAuditStatus();
    } catch (e) {
        if (rep) rep.textContent = `Audit failed: ${e.message}`;
        console.error('runSovereignAudit error —', e.message);
    } finally {
        if (btn) { btn.disabled = false; btn.textContent = 'Run Sovereign Audit'; }
    }
}

console.log('PeakForge Station initialized.');

// ==================== APPROVAL FLOWS & LEDGER INTEGRATION (Week 3) ====================
let currentSynthesisText = '';

function showApprovalModal(synthesisText) {
    currentSynthesisText = synthesisText || 'No synthesis text provided';
    const modal = document.getElementById('approval-modal');
    const body  = document.getElementById('approval-body');
    body.innerHTML = `<div class="card"><strong>Decision to ratify:</strong><br><br>${currentSynthesisText}</div>`;
    modal.classList.remove('hidden');
}

function closeApprovalModal() {
    document.getElementById('approval-modal').classList.add('hidden');
    currentSynthesisText = '';
}

async function approveDecision() {
    if (!currentSynthesisText) return;
    const text = currentSynthesisText;
    closeApprovalModal();
    try {
        const res = await fetch(`${API_BASE}/chat`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                message: `/approve ${text}`,
                committee: 'council'
            })
        });
        if (res.ok) {
            alert('✅ Decision APPROVED and logged to council_history.db + gbrain timeline');
        } else {
            alert(`Approval failed — HTTP ${res.status}`);
        }
    } catch (e) {
        console.error(e);
        alert(`Approval error: ${e.message}`);
    }
}

// ── Intelligence Board ────────────────────────────────────────────────────────

function clearIntelUnreadDot() {
    const dot = document.getElementById('intel-unread-dot');
    if (dot) dot.style.display = 'none';
}

function switchIntelView(view, btn) {
    ['brief','business','cascades','conviction'].forEach(v => {
        const panel = document.getElementById(`intel-view-${v}`);
        const tab   = document.getElementById(`intel-tab-${v}`);
        if (panel) panel.style.display = v === view ? '' : 'none';
        if (tab)   tab.classList.toggle('active', v === view);
    });
}

function convictionColor(score) {
    if (score >= 9)  return '#7acca0';
    if (score >= 7)  return '#ccb870';
    if (score >= 4)  return '#9a8a4a';
    return '#cc7a7a';
}

function convictionBar(score) {
    const color = convictionColor(score);
    const pct   = Math.min(100, (score / 10) * 100).toFixed(1);
    return `<div class="conviction-track">
        <div class="conviction-fill" style="width:${pct}%;background:${color};"></div>
    </div>`;
}

function renderSignalCard(sig) {
    const score  = sig.conviction ?? 0;
    const color  = convictionColor(score);
    const status = sig.frontrun_status ?? 'pending';
    const isHigh = score >= 7;
    const srcBadge = sig.source_type
        ? `<span style="font-size:0.5rem;font-weight:700;letter-spacing:0.1em;text-transform:uppercase;
                        padding:2px 5px;border-radius:2px;background:#222;color:#999;">${sig.source_type}</span>`
        : '';
    const langBadge = sig.lang && sig.lang !== 'en'
        ? `<span style="font-size:0.5rem;font-weight:700;letter-spacing:0.1em;text-transform:uppercase;
                        padding:2px 5px;border-radius:2px;background:rgba(74,106,154,0.18);color:#7aa0cc;">${sig.lang.toUpperCase()}</span>`
        : '';
    const frontrunBadge = sig.type === 'cascade'
        ? `<span class="frontrun-badge ${status}">${status.toUpperCase()}</span>`
        : '';
    const e3Badge = sig.engine3_triggered
        ? `<span style="font-size:0.5rem;font-weight:700;letter-spacing:0.1em;text-transform:uppercase;
                        padding:2px 5px;border-radius:2px;background:rgba(74,154,106,0.2);color:#7acca0;">E3 ⚡</span>`
        : '';
    return `<div class="intel-card${isHigh ? ' high' : ''}${status === 'triggered' ? ' triggered' : ''}">
        <div style="display:flex;align-items:flex-start;justify-content:space-between;gap:12px;">
            <div style="flex:1;min-width:0;">
                <div style="display:flex;align-items:center;gap:5px;flex-wrap:wrap;margin-bottom:6px;">
                    ${srcBadge}${langBadge}${frontrunBadge}${e3Badge}
                </div>
                <div style="font-size:0.78rem;font-weight:600;color:var(--text,#d8d8d8);line-height:1.4;margin-bottom:4px;">${sig.title ?? '—'}</div>
                <div style="font-size:0.7rem;color:var(--text-dim,#777);line-height:1.5;">${(sig.body ?? '').slice(0, 280)}${(sig.body ?? '').length > 280 ? '…' : ''}</div>
            </div>
            <div style="text-align:right;flex-shrink:0;">
                <div style="font-size:1.5rem;font-weight:700;font-variant-numeric:tabular-nums;color:${color};line-height:1;">${score.toFixed(1)}</div>
                <div style="font-size:0.48rem;text-transform:uppercase;letter-spacing:0.1em;color:var(--text-dim,#777);margin-top:2px;">conviction</div>
            </div>
        </div>
        ${convictionBar(score)}
        ${sig.url ? `<div style="margin-top:8px;font-size:0.6rem;"><a href="${sig.url}" target="_blank" style="color:#7aa0cc;text-decoration:none;">${sig.url.slice(0,72)}${sig.url.length > 72 ? '…' : ''}</a></div>` : ''}
    </div>`;
}

function renderHorizonRow(sig, defaultScore) {
    const color = convictionColor(defaultScore);
    return `<div style="display:flex;align-items:flex-start;gap:10px;padding:8px 0;border-bottom:1px solid #1e1e1e;">
        <span style="font-size:0.5rem;font-weight:700;letter-spacing:0.1em;text-transform:uppercase;
                     padding:2px 5px;border-radius:2px;white-space:nowrap;margin-top:2px;
                     ${sig.source === 'H1' ? 'background:rgba(74,106,154,0.18);color:#7aa0cc;' : 'background:rgba(154,138,74,0.18);color:#ccb870;'}">${sig.source}</span>
        <div style="flex:1;min-width:0;">
            <div style="font-size:0.72rem;color:var(--text,#d8d8d8);line-height:1.5;">${sig.text}</div>
            ${convictionBar(defaultScore)}
        </div>
        <span style="font-size:0.75rem;font-weight:700;color:${color};font-variant-numeric:tabular-nums;flex-shrink:0;">${defaultScore.toFixed(1)}</span>
    </div>`;
}

function renderEmptyState(msg) {
    return `<div style="padding:28px 0;text-align:center;color:var(--text-dim,#777);font-size:0.72rem;font-style:italic;">${msg}</div>`;
}

async function loadIntelligence() {
    const statusDot    = document.getElementById('intel-status-dot');
    const statusLabel  = document.getElementById('intel-status-label');
    const dateBadge    = document.getElementById('intel-date-badge');
    const noData       = document.getElementById('intel-no-data');
    const brief        = document.getElementById('intel-brief');
    const sourceFooter = document.getElementById('intel-source-footer');
    const unreadDot    = document.getElementById('intel-unread-dot');
    const sigCount     = document.getElementById('intel-signal-count');

    if (statusLabel) statusLabel.textContent = 'Loading…';
    if (statusDot)   { statusDot.style.background = '#777'; statusDot.style.animation = 'none'; }

    // Parallel fetch — horizon brief + asymmetry signals + runner status
    const [horizonResult, asymmetryResult, statusResult] = await Promise.allSettled([
        fetch(`${API_BASE}/api/v1/horizon/brief`).then(r => r.ok ? r.json() : null),
        fetch(`${API_BASE}/api/v1/intelligence/asymmetry`).then(r => r.ok ? r.json() : null),
        fetch(`${API_BASE}/api/v1/intelligence/status`).then(r => r.ok ? r.json() : null),
    ]);

    // ── Runner status row ──
    const runnerStatus = statusResult.status === 'fulfilled' ? statusResult.value : null;
    const runnerMap = {
        'runner-librarian':    runnerStatus?.librarian,
        'runner-breakthrough': runnerStatus?.hunter_breakthrough,
        'runner-cascade':      runnerStatus?.hunter_cascade,
        'runner-qullamaggie':  runnerStatus?.hunter_qullamaggie,
    };
    let anyFailed = false;
    Object.entries(runnerMap).forEach(([elId, r]) => {
        const el = document.getElementById(elId);
        if (!el) return;
        if (!r) { el.style.color = '#777'; return; }
        const label = elId.replace('runner-', '');
        const ts = r.last_run ? new Date(r.last_run).toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit', hour12: false }) : '—';
        if (r.success) {
            el.innerHTML = `<span style="color:#4a9a6a;">●</span> ${label.charAt(0).toUpperCase()+label.slice(1)}: ${ts}`;
            el.style.color = 'var(--text-dim,#777)';
        } else {
            el.innerHTML = `<span style="color:#cc7a7a;">●</span> ${label.charAt(0).toUpperCase()+label.slice(1)}: FAILED`;
            el.style.color = '#cc7a7a';
            anyFailed = true;
        }
    });
    if (anyFailed && statusDot) {
        statusDot.style.background = '#cc7a7a';
        statusDot.style.animation  = 'rv-pulse 2s ease-in-out infinite';
        if (statusLabel) statusLabel.textContent = 'Runner Failure';
    }

    const hz  = horizonResult.status   === 'fulfilled' ? horizonResult.value   : null;
    const asy = asymmetryResult.status === 'fulfilled' ? asymmetryResult.value : null;

    const hasHorizon   = hz  && hz.status  === 'ok';
    const hasAsymmetry = asy && asy.status === 'ok' && asy.signal_count > 0;

    if (!hasHorizon && !hasAsymmetry) {
        if (noData)  { noData.style.display = 'block'; noData.textContent = 'No horizon or asymmetry data yet.'; }
        if (brief)   brief.style.display = 'none';
        if (statusDot)   { statusDot.style.background = '#666'; statusDot.style.animation = 'none'; }
        if (statusLabel) statusLabel.textContent = 'No Data';
        return;
    }

    if (noData) noData.style.display = 'none';
    if (brief)  brief.style.display  = 'block';

    // Determine top conviction across all signals
    const topConv = Math.max(
        hasAsymmetry ? asy.top_conviction : 0,
        hasHorizon   ? 5.1 : 0
    );
    const isUnread = hasHorizon && hz.unread;

    if (statusDot) {
        const dotColor = topConv >= 8 ? '#7acca0' : topConv >= 5 ? '#9a8a4a' : '#4a7a9a';
        statusDot.style.background = dotColor;
        statusDot.style.animation  = isUnread ? 'rv-pulse 2s ease-in-out infinite' : 'none';
    }
    if (statusLabel) statusLabel.textContent = topConv >= 8 ? `High Conviction — ${topConv.toFixed(1)}` : isUnread ? 'Unread Brief' : 'Board Current';
    if (dateBadge)   dateBadge.textContent   = hz?.date ? `${hz.date}` : '';
    if (unreadDot)   unreadDot.style.display = isUnread ? 'inline-block' : 'none';

    // ── Daily Brief (Stage 1) ──
    const signalsEl = document.getElementById('intel-signals');
    if (signalsEl) {
        const rows = hasHorizon && hz.signals?.length
            ? hz.signals.map(s => `
                <div style="display:flex;align-items:flex-start;gap:10px;padding:6px 0;border-bottom:1px solid #222;">
                    <span style="font-size:0.52rem;font-weight:700;letter-spacing:0.1em;text-transform:uppercase;
                                 padding:2px 6px;border-radius:2px;white-space:nowrap;margin-top:1px;
                                 ${s.source === 'H1' ? 'background:rgba(74,106,154,0.18);color:#7aa0cc;' : 'background:rgba(154,138,74,0.18);color:#ccb870;'}">${s.source}</span>
                    <span style="font-size:0.73rem;color:var(--text,#d8d8d8);line-height:1.5;">${s.text}</span>
                </div>`).join('')
            : '<div style="color:var(--text-dim,#777);font-size:0.72rem;">No signals today.</div>';
        signalsEl.innerHTML = rows;
    }

    // ── Alpha ──
    const alphaEl = document.getElementById('intel-alpha');
    if (alphaEl) {
        const txt = hasHorizon && hz.alpha ? hz.alpha : '';
        alphaEl.style.fontStyle = txt ? 'normal' : 'italic';
        alphaEl.style.color     = txt ? 'var(--text,#d8d8d8)' : 'var(--text-dim,#777)';
        alphaEl.textContent     = txt || 'GRC/GEC synthesis runs automatically via intel-librarian at 06:10 HST.';
    }

    // ── Action ──
    const actionEl = document.getElementById('intel-action');
    if (actionEl) {
        const txt = hasHorizon && hz.action ? hz.action : '';
        actionEl.style.fontStyle = txt ? 'normal' : 'italic';
        actionEl.style.color     = txt ? 'var(--text,#d8d8d8)' : 'var(--text-dim,#777)';
        actionEl.textContent     = txt || 'Action items populated automatically after each librarian run.';
    }

    // ── Business Edge ──
    const breakthroughEl = document.getElementById('intel-breakthrough-list');
    if (breakthroughEl) {
        if (hasAsymmetry && asy.breakthrough.length) {
            breakthroughEl.innerHTML = asy.breakthrough.map(renderSignalCard).join('');
        } else {
            const br = runnerStatus?.hunter_breakthrough;
            const ts = br?.last_run ? new Date(br.last_run).toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit', hour12: false }) : null;
            const msg = ts
                ? `Quiet Skies — No new asymmetry detected &nbsp;·&nbsp; last scan ${ts}`
                : 'Breakthrough Hunter armed — next run at 06:05 HST.';
            breakthroughEl.innerHTML = renderEmptyState(msg);
        }
    }

    // H1 horizon rows in Business Edge
    const h1El = document.getElementById('intel-h1-list');
    if (h1El) {
        const rows = hasHorizon && hz.signals?.length
            ? hz.signals.filter(s => s.source === 'H1').map(s => renderHorizonRow(s, 5.1)).join('')
            : renderEmptyState('No H1 data today.');
        h1El.innerHTML = rows;
    }

    // ── Market Cascades ──
    const cascadeEl = document.getElementById('intel-cascade-list');
    if (cascadeEl) {
        if (hasAsymmetry && asy.cascade.length) {
            cascadeEl.innerHTML = asy.cascade.map(renderSignalCard).join('');
        } else {
            const cr = runnerStatus?.hunter_cascade;
            const ts = cr?.last_run ? new Date(cr.last_run).toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit', hour12: false }) : null;
            const msg = ts
                ? `Quiet Skies — No new cascade detected &nbsp;·&nbsp; last scan ${ts}`
                : 'Cascade Hunter armed — integration scavenge in progress.';
            cascadeEl.innerHTML = renderEmptyState(msg);
        }
    }

    // H2 horizon rows in Market Cascades
    const h2El = document.getElementById('intel-h2-list');
    if (h2El) {
        const rows = hasHorizon && hz.signals?.length
            ? hz.signals.filter(s => s.source === 'H2').map(s => renderHorizonRow(s, 4.9)).join('')
            : renderEmptyState('No H2 data today.');
        h2El.innerHTML = rows;
    }

    // ── Conviction Meter ──
    const convEl = document.getElementById('intel-conviction-list');
    if (convEl) {
        // Merge all signals into one ranked list
        const all = [];
        if (hasAsymmetry) asy.all_sorted.forEach(s => all.push({ sig: s, score: s.conviction }));
        if (hasHorizon && hz.signals?.length) {
            hz.signals.forEach(s => all.push({
                sig: { title: s.text.slice(0, 100), body: s.text, source_type: `horizon-${s.source.toLowerCase()}`, conviction: s.source === 'H1' ? 5.1 : 4.9, type: 'horizon' },
                score: s.source === 'H1' ? 5.1 : 4.9,
            }));
        }
        all.sort((a, b) => b.score - a.score);

        if (sigCount) sigCount.textContent = `${all.length} signal${all.length !== 1 ? 's' : ''}`;

        convEl.innerHTML = all.length
            ? all.map(({ sig }) => renderSignalCard(sig)).join('')
            : renderEmptyState('No signals to rank yet.');
    }

    // Source footer
    if (sourceFooter) {
        const parts = [];
        if (hasHorizon)   parts.push(`H1/H2: ${hz.date} (${(hz.h1_query_count ?? 0) + (hz.h2_query_count ?? 0)} queries)`);
        if (hasAsymmetry) parts.push(`Asymmetry: ${asy.signal_count} signal${asy.signal_count !== 1 ? 's' : ''}`);
        sourceFooter.textContent = parts.join(' · ');
    }

    if (!hasHorizon && !hasAsymmetry) {
        if (statusDot)   { statusDot.style.background = '#cc7a7a'; statusDot.style.animation = 'none'; }
        if (statusLabel) statusLabel.textContent = 'Error — Citadel offline?';
    }
}

async function rejectDecision() {
    if (!currentSynthesisText) return;
    const text = currentSynthesisText;
    closeApprovalModal();
    try {
        const res = await fetch(`${API_BASE}/chat`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                message: `/decision REJECTED: ${text}`,
                committee: 'council'
            })
        });
        if (res.ok) {
            alert('❌ Decision REJECTED and logged');
        }
    } catch (e) {
        console.error(e);
    }
}
