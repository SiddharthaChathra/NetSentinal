/* =====================================================
   NetSentinel — Dashboard Controller
   ===================================================== */

let latencyChart = null;

document.addEventListener('DOMContentLoaded', () => {
    initNavigation();
    initChart();
    
    document.getElementById('run-scan-btn').addEventListener('click', runScan);
    
    // Load last run & chart data on page open
    fetchLastRun();
});

/* ─── Navigation ─────────────────────────────────── */
function initNavigation() {
    const navItems = document.querySelectorAll('.nav-item');
    navItems.forEach(item => {
        item.addEventListener('click', (e) => {
            e.preventDefault();
            navItems.forEach(nav => nav.classList.remove('active'));
            item.classList.add('active');
            
            const target = item.getAttribute('data-target');
            document.querySelectorAll('.view').forEach(view => {
                view.classList.remove('active');
                view.classList.add('hidden');
            });
            
            const targetView = document.getElementById(`view-${target}`);
            targetView.classList.remove('hidden');
            targetView.classList.add('active');
            
            // Lazy-load history when switching to that tab
            if (target === 'history') {
                loadHistory();
            }
        });
    });
}

/* ─── Chart ──────────────────────────────────────── */
function initChart() {
    const ctx = document.getElementById('latencyChart').getContext('2d');
    
    const gradient = ctx.createLinearGradient(0, 0, 0, 280);
    gradient.addColorStop(0, 'rgba(6, 214, 214, 0.15)');
    gradient.addColorStop(1, 'rgba(6, 214, 214, 0.0)');
    
    latencyChart = new Chart(ctx, {
        type: 'line',
        data: {
            labels: [],
            datasets: [{
                label: 'Latency (ms)',
                data: [],
                borderColor: '#06d6d6',
                backgroundColor: gradient,
                borderWidth: 2,
                fill: true,
                tension: 0.45,
                pointRadius: 3,
                pointHoverRadius: 6,
                pointBackgroundColor: '#06d6d6',
                pointBorderColor: '#0a0f1a',
                pointBorderWidth: 2,
                pointHoverBackgroundColor: '#fff',
                pointHoverBorderColor: '#06d6d6',
            }]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            interaction: {
                intersect: false,
                mode: 'index',
            },
            plugins: {
                legend: { display: false },
                tooltip: {
                    backgroundColor: 'rgba(10, 15, 26, 0.9)',
                    borderColor: 'rgba(6, 214, 214, 0.2)',
                    borderWidth: 1,
                    titleFont: { family: 'Inter', weight: '600' },
                    bodyFont: { family: 'JetBrains Mono', size: 12 },
                    padding: 12,
                    cornerRadius: 8,
                    callbacks: {
                        label: (ctx) => ` ${ctx.parsed.y.toFixed(1)} ms`
                    }
                }
            },
            scales: {
                y: {
                    beginAtZero: true,
                    grid: {
                        color: 'rgba(255, 255, 255, 0.03)',
                        drawBorder: false,
                    },
                    ticks: {
                        color: '#475569',
                        font: { family: 'JetBrains Mono', size: 11 },
                        callback: (val) => val + ' ms'
                    }
                },
                x: {
                    grid: { display: false },
                    ticks: {
                        color: '#475569',
                        font: { family: 'Inter', size: 11 },
                        maxRotation: 0,
                    }
                }
            }
        }
    });
}

/* ─── Fetch Last Run ──────────────────────────────── */
async function fetchLastRun() {
    try {
        const res = await fetch('/api/diagnostic-run');
        if (res.ok) {
            const data = await res.json();
            if (data) updateDashboard(data);
        }
        updateChartData();
    } catch (e) {
        console.error("Failed to fetch initial data:", e);
    }
}

/* ─── Run Scan ────────────────────────────────────── */
const LOADING_STEPS = [
    "Checking local interfaces...",
    "Probing gateway connectivity...",
    "Testing internet reachability...",
    "Resolving DNS domains...",
    "Verifying TCP services...",
    "Running diagnostic engine...",
    "Calculating health score..."
];

async function runScan() {
    const btn = document.getElementById('run-scan-btn');
    const spinner = btn.querySelector('.spinner');
    const btnText = btn.querySelector('.btn-text');
    const overlay = document.getElementById('loading-overlay');
    const loadingSteps = document.getElementById('loading-steps');
    const demoMode = document.getElementById('demo-scenario').value;
    
    btn.disabled = true;
    spinner.classList.remove('hidden');
    btnText.textContent = 'Scanning...';
    overlay.classList.remove('hidden');
    
    // Animate loading steps
    let stepIdx = 0;
    const stepInterval = setInterval(() => {
        if (stepIdx < LOADING_STEPS.length) {
            loadingSteps.innerHTML = `<p>${LOADING_STEPS[stepIdx]}</p>`;
            stepIdx++;
        }
    }, demoMode ? 100 : 800);
    
    try {
        let url = '/api/diagnostic-run';
        if (demoMode) url += `?demo=${demoMode}`;
        
        const response = await fetch(url, { method: 'POST' });
        const data = await response.json();
        
        clearInterval(stepInterval);
        loadingSteps.innerHTML = '<p>Complete!</p>';
        
        // Small delay to show "Complete!" before closing overlay
        await sleep(400);
        
        updateDashboard(data);
        updateChartData();
    } catch (error) {
        clearInterval(stepInterval);
        alert("Failed to run diagnostics. Ensure backend is running.");
        console.error(error);
    } finally {
        btn.disabled = false;
        spinner.classList.add('hidden');
        btnText.textContent = 'Run Full Scan';
        overlay.classList.add('hidden');
    }
}

/* ─── Dashboard Update ────────────────────────────── */
function updateDashboard(data) {
    if (!data) return;

    // Timestamp
    const d = new Date(data.timestamp);
    document.getElementById('last-scan-time').innerText = `Last scan: ${d.toLocaleTimeString()}`;
    
    // Demo Banner
    const banner = document.getElementById('demo-banner');
    if (data.is_demo) {
        banner.classList.remove('hidden');
    } else {
        banner.classList.add('hidden');
    }

    // Health Score — animated count-up
    const ring = document.getElementById('health-ring');
    ring.setAttribute('data-status', data.status);
    
    const scoreEl = document.getElementById('health-score-val');
    animateCounter(scoreEl, data.health_score);
    
    document.getElementById('health-status-text').innerText = data.status;
    
    let desc = "All systems nominal.";
    if (data.diagnostics && data.diagnostics.length > 0) {
        desc = data.diagnostics[0].title;
    }
    document.getElementById('health-status-desc').innerText = desc;

    // Pipeline Flow — use IDs for direct targeting
    updatePipelineStepById('step-local', true);
    updatePipelineStepById('step-gateway', data.gateway.reachable);
    updatePipelineStepById('step-internet', data.internet.reachable);
    
    const dnsPass = data.dns.some(d => d.success);
    updatePipelineStepById('step-dns', dnsPass);
    
    const tcpPass = data.tcp.some(t => t.success);
    updatePipelineStepById('step-tcp', tcpPass);

    // Status Cards
    setCardValue('#card-gateway', data.gateway.reachable, 
        data.gateway.reachable ? 'CONNECTED' : 'FAILED',
        `${data.gateway.latency_ms} ms`);
        
    setCardValue('#card-internet', data.internet.reachable,
        data.internet.reachable ? 'REACHABLE' : 'UNREACHABLE',
        `Loss: ${data.internet.packet_loss}% | ${data.internet.latency_ms} ms`);
    
    setCardValue('#card-dns', dnsPass,
        dnsPass ? 'HEALTHY' : 'FAILED', '');
    
    setCardValue('#card-tcp', tcpPass,
        tcpPass ? 'AVAILABLE' : 'CLOSED', '');

    // Findings
    renderFindings(data.diagnostics);

    // Raw Diagnostics Tab
    document.getElementById('raw-json-output').innerText = JSON.stringify(data, null, 2);
}

function setCardValue(selector, isPass, label, subtext) {
    const card = document.querySelector(selector);
    if (!card) return;
    const valueEl = card.querySelector('.status-value');
    const subEl = card.querySelector('.status-sub');
    
    valueEl.innerText = label;
    valueEl.style.color = isPass ? 'var(--success)' : 'var(--critical)';
    if (subEl && subtext) subEl.innerText = subtext;
}

function updatePipelineStepById(id, success) {
    const step = document.getElementById(id);
    if (step) {
        step.setAttribute('data-state', success ? 'pass' : 'fail');
    }
}

/* ─── Animated Counter ────────────────────────────── */
function animateCounter(el, target) {
    const duration = 800;
    const start = parseInt(el.innerText) || 0;
    const startTime = performance.now();
    
    function tick(now) {
        const elapsed = now - startTime;
        const progress = Math.min(elapsed / duration, 1);
        
        // Ease-out cubic
        const eased = 1 - Math.pow(1 - progress, 3);
        const current = Math.round(start + (target - start) * eased);
        
        el.innerText = current;
        
        if (progress < 1) {
            requestAnimationFrame(tick);
        }
    }
    
    requestAnimationFrame(tick);
}

/* ─── Findings Renderer ───────────────────────────── */
function renderFindings(diagnostics) {
    const list = document.getElementById('findings-list');
    list.innerHTML = '';
    
    if (!diagnostics || diagnostics.length === 0) {
        list.innerHTML = '<p class="empty-state">No findings available.</p>';
        return;
    }
    
    diagnostics.forEach((diag, idx) => {
        const div = document.createElement('div');
        div.className = 'finding-item';
        div.setAttribute('data-severity', diag.severity);
        div.style.animationDelay = `${idx * 0.06}s`;
        
        let checksHTML = '';
        if (diag.recommended_checks && diag.recommended_checks.length > 0) {
            checksHTML = `<ul>${diag.recommended_checks.map(c => `<li>${c}</li>`).join('')}</ul>`;
        }
        
        let evidenceHTML = '';
        if (diag.evidence && diag.evidence.length > 0) {
            evidenceHTML = `<p style="font-family:'JetBrains Mono',monospace;font-size:0.75rem;color:var(--text-dim);margin-bottom:6px">${diag.evidence.join(' · ')}</p>`;
        }
        
        const confidenceBadge = diag.confidence 
            ? `<span style="display:inline-block;font-size:0.65rem;font-weight:700;letter-spacing:0.05em;padding:2px 8px;border-radius:4px;background:var(--bg-deepest);color:var(--text-muted);margin-left:8px;">${diag.confidence.toUpperCase()}</span>` 
            : '';
        
        div.innerHTML = `
            <h4>${diag.title}${confidenceBadge}</h4>
            <p>${diag.likely_cause}</p>
            ${evidenceHTML}
            ${checksHTML}
        `;
        list.appendChild(div);
    });
}

/* ─── Chart Data ──────────────────────────────────── */
async function updateChartData() {
    try {
        const res = await fetch('/api/history?limit=25');
        if (res.ok) {
            let history = await res.json();
            history = history.reverse(); // oldest first for left-to-right
            
            latencyChart.data.labels = history.map(h => {
                const d = new Date(h.timestamp);
                return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
            });
            latencyChart.data.datasets[0].data = history.map(h => h.latency);
            latencyChart.update('none'); // skip animation for immediate draw
        }
    } catch (e) {
        console.error("Chart data update failed:", e);
    }
}

/* ─── History Table ───────────────────────────────── */
async function loadHistory() {
    const tbody = document.getElementById('history-tbody');
    try {
        const res = await fetch('/api/history');
        if (res.ok) {
            const data = await res.json();
            tbody.innerHTML = '';
            
            if (data.length === 0) {
                tbody.innerHTML = '<tr><td colspan="9" class="text-center">No history available. Run a scan to start tracking.</td></tr>';
                return;
            }
            
            data.forEach((h, idx) => {
                const tr = document.createElement('tr');
                const d = new Date(h.timestamp);
                const scoreColor = h.score >= 80 ? 'var(--success)' : (h.score >= 60 ? 'var(--warning)' : 'var(--critical)');
                
                const statusBadge = (status) => {
                    const color = status === 'PASS' ? 'var(--success)' : 'var(--critical)';
                    return `<span style="color:${color};font-weight:700;font-size:0.82rem">${status}</span>`;
                };
                
                tr.innerHTML = `
                    <td style="font-family:'JetBrains Mono',monospace;font-size:0.82rem">${d.toLocaleDateString()} ${d.toLocaleTimeString([], {hour:'2-digit',minute:'2-digit'})}</td>
                    <td><strong style="color:${scoreColor};font-size:1.05rem">${h.score}</strong></td>
                    <td><span style="color:${scoreColor}">${h.status}</span></td>
                    <td>${statusBadge(h.gateway_status)}</td>
                    <td>${statusBadge(h.internet_status)}</td>
                    <td>${statusBadge(h.dns_status)}</td>
                    <td style="font-family:'JetBrains Mono',monospace">${h.latency} ms</td>
                    <td style="font-family:'JetBrains Mono',monospace">${h.packet_loss}%</td>
                    <td>${h.is_demo ? '<span style="color:var(--warning);font-size:0.72rem;font-weight:700;letter-spacing:0.05em">DEMO</span>' : '<span style="color:var(--text-muted);font-size:0.72rem">REAL</span>'}</td>
                `;
                tbody.appendChild(tr);
            });
        }
    } catch (e) {
        tbody.innerHTML = '<tr><td colspan="9" class="text-center">Failed to load history</td></tr>';
        console.error("History load failed:", e);
    }
}

/* ─── Utility ─────────────────────────────────────── */
function sleep(ms) {
    return new Promise(resolve => setTimeout(resolve, ms));
}
