/* ═══════════════════════════════════════════════════════════
   ICT Tender Tracker — Frontend Logic
   ═══════════════════════════════════════════════════════════ */

'use strict';

// ── State ────────────────────────────────────────────────────
let state = {
  page: 1,
  perPage: 12,
  search: '',
  total: 0,
  pages: 1,
  tenders: [],
  searchTimer: null,
  statusTimer: null,
};

// Bootstrap modal instance
let tenderModal = null;

// ── Initialise ───────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', () => {
  tenderModal = new bootstrap.Modal(document.getElementById('tenderModal'));
  loadTenders();
  startStatusPolling();
});

// ── API helpers ──────────────────────────────────────────────
async function apiFetch(url) {
  const res = await fetch(url);
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}

// ── Load tenders ─────────────────────────────────────────────
async function loadTenders() {
  showLoading(true);

  const params = new URLSearchParams({
    page:     state.page,
    per_page: state.perPage,
    search:   state.search,
  });

  try {
    const data = await apiFetch(`/api/tenders?${params}`);
    state.total   = data.total;
    state.pages   = data.pages;
    state.tenders = data.tenders;
    renderTenders();
    renderPagination();
    updateResultsLabel();
  } catch (err) {
    console.error('Failed to load tenders:', err);
    showAlert('danger', 'Could not load tenders. Is the Flask server running?');
  } finally {
    showLoading(false);
  }
}

// ── Render tender cards ───────────────────────────────────────
function renderTenders() {
  const grid  = document.getElementById('tenders-grid');
  const empty = document.getElementById('empty-state');

  if (!state.tenders.length) {
    grid.innerHTML = '';
    empty.classList.remove('d-none');
    return;
  }

  empty.classList.add('d-none');
  grid.innerHTML = state.tenders.map(buildCard).join('');
}

function buildCard(t) {
  const urgency    = closingUrgency(t.closing_date);
  const stripeClass = urgency === 'urgent' ? 'closing-urgent'
                    : urgency === 'soon'   ? 'closing-soon'
                    : '';
  const dateClass   = urgency === 'urgent' ? 'closing-urgent'
                    : urgency === 'soon'   ? 'closing-warn'
                    : '';

  const tn       = esc(t.tender_number || 'N/A');
  const cat      = esc(t.category      || 'ICT');
  const title    = esc(t.title         || '—');
  const org      = esc(t.issuing_org   || 'Not specified');
  const date     = esc(t.closing_date  || 'Not specified');
  const time     = esc(t.closing_time  || '—');
  const brief    = esc(t.briefing_details || '');
  const docUrls  = parseDocUrls(t);
  const srcUrl   = t.source_url   || '#';
  const idx      = state.tenders.indexOf(t);

  const docBtn = docUrls.length
    ? `<a class="btn-doc" href="${esc(docUrls[0])}" target="_blank" rel="noopener noreferrer"
          title="${esc(docFileName(docUrls[0], 0))}" onclick="event.stopPropagation()">
         <i class="bi bi-file-earmark-arrow-down"></i>${docUrls.length > 1 ? `Documents (${docUrls.length})` : 'Tender Document'}
       </a>`
    : `<span class="btn-doc btn-doc-disabled">
         <i class="bi bi-file-earmark-x"></i>No Document
       </span>`;

  const briefHtml = brief
    ? `<div class="info-row">
         <i class="bi bi-geo-alt info-icon"></i>
         <div>
           <div class="info-label">Briefing</div>
           <div class="briefing-text">${brief}</div>
         </div>
       </div>`
    : '';

  const closingHtml = t.closing_date
    ? `<div class="info-row">
         <i class="bi bi-calendar-x info-icon"></i>
         <div>
           <div class="info-label">Closing</div>
           <div class="info-value ${dateClass}">${date}${t.closing_time ? ' &bull; ' + time : ''}</div>
         </div>
       </div>`
    : '';

  return `
  <div class="tender-card" onclick="openDetail(${idx})">
    <div class="card-stripe ${stripeClass}"></div>
    <div class="card-body-inner">
      <div class="card-meta">
        <span class="badge-tn" title="Tender Number">${tn}</span>
        <span class="badge-cat">${cat}</span>
      </div>
      <div class="card-title">${title}</div>
      <div class="info-row">
        <i class="bi bi-building info-icon"></i>
        <div>
          <div class="info-label">Issuing Organisation</div>
          <div class="info-value">${org}</div>
        </div>
      </div>
      ${closingHtml}
      ${briefHtml}
    </div>
    <div class="card-footer-inner">
      ${docBtn}
      <a class="btn-portal" href="${esc(srcUrl)}" target="_blank" rel="noopener noreferrer"
         onclick="event.stopPropagation()">
        <i class="bi bi-box-arrow-up-right"></i>Portal
      </a>
      <button class="btn-detail" onclick="event.stopPropagation(); openDetail(${idx})">
        <i class="bi bi-chevron-right"></i>Details
      </button>
      <button class="btn-remove" title="Remove this tender" onclick="event.stopPropagation(); removeTender(${t.id}, this)">
        <i class="bi bi-trash3"></i>
      </button>
    </div>
  </div>`;
}

// ── Remove tender ───────────────────────────────────────────
async function removeTender(id, btn) {
  btn.disabled = true;
  btn.innerHTML = '<span class="spinner-border spinner-border-sm"></span>';
  try {
    const res = await fetch(`/api/tenders/${id}`, { method: 'DELETE' });
    if (res.ok) {
      const card = btn.closest('.tender-card');
      card.style.transition = 'opacity .25s, transform .25s';
      card.style.opacity = '0';
      card.style.transform = 'scale(.95)';
      setTimeout(() => {
        state.tenders = state.tenders.filter(t => t.id !== id);
        state.total = Math.max(0, state.total - 1);
        renderTenders();
        renderPagination();
        updateResultsLabel();
      }, 260);
    } else {
      btn.disabled = false;
      btn.innerHTML = '<i class="bi bi-trash3"></i>';
      showAlert('danger', 'Could not remove tender.');
    }
  } catch (err) {
    btn.disabled = false;
    btn.innerHTML = '<i class="bi bi-trash3"></i>';
    showAlert('danger', 'Error: ' + err.message);
  }
}

// ── Detail modal ─────────────────────────────────────────────
function openDetail(idx) {
  const t = state.tenders[idx];
  if (!t) return;

  document.getElementById('modal-title').textContent         = t.title || '—';
  document.getElementById('modal-tender-number').textContent = t.tender_number || 'No tender number';
  document.getElementById('modal-org').textContent           = t.issuing_org || '—';
  document.getElementById('modal-category').textContent      = t.category || '—';
  document.getElementById('modal-closing-date').textContent  = t.closing_date || '—';
  document.getElementById('modal-closing-time').textContent  = t.closing_time || '—';
  document.getElementById('modal-briefing').textContent      = t.briefing_details || 'No briefing details available.';
  document.getElementById('modal-advert').textContent        = t.advertised_date || '—';

  // Document links — populate all docs in the modal footer
  const docsWrap = document.getElementById('modal-docs-wrap');
  const docUrls  = parseDocUrls(t);
  if (docUrls.length) {
    docsWrap.innerHTML = docUrls.map((url, i) => {
      const name  = docFileName(url, i);
      const label = docUrls.length === 1 ? 'Download Tender Document' : esc(name);
      return `<a href="${esc(url)}" target="_blank" rel="noopener noreferrer"
                 class="btn btn-success" title="${esc(name)}">
                <i class="bi bi-file-earmark-arrow-down me-1"></i>${label}
              </a>`;
    }).join('');
  } else {
    docsWrap.innerHTML = '';
  }

  // Source portal link
  const srcLink = document.getElementById('modal-src-link');
  srcLink.href = t.source_url || 'https://www.etenders.gov.za';

  tenderModal.show();
}

// ── Pagination ────────────────────────────────────────────────
function renderPagination() {
  const wrap = document.getElementById('pagination-wrap');
  const ul   = document.getElementById('pagination');

  if (state.pages <= 1) { wrap.classList.add('d-none'); return; }
  wrap.classList.remove('d-none');

  const { page, pages } = state;
  let html = '';

  // Prev
  html += `<li class="page-item ${page === 1 ? 'disabled' : ''}">
    <button class="page-link" onclick="goPage(${page - 1})">
      <i class="bi bi-chevron-left"></i>
    </button>
  </li>`;

  // Page numbers (smart window)
  const window_ = buildPageWindow(page, pages);
  for (const p of window_) {
    if (p === '…') {
      html += `<li class="page-item disabled"><span class="page-link">…</span></li>`;
    } else {
      html += `<li class="page-item ${p === page ? 'active' : ''}">
        <button class="page-link" onclick="goPage(${p})">${p}</button>
      </li>`;
    }
  }

  // Next
  html += `<li class="page-item ${page === pages ? 'disabled' : ''}">
    <button class="page-link" onclick="goPage(${page + 1})">
      <i class="bi bi-chevron-right"></i>
    </button>
  </li>`;

  ul.innerHTML = html;
}

function buildPageWindow(current, total) {
  if (total <= 7) return Array.from({ length: total }, (_, i) => i + 1);
  if (current <= 4) return [1, 2, 3, 4, 5, '…', total];
  if (current >= total - 3) return [1, '…', total - 4, total - 3, total - 2, total - 1, total];
  return [1, '…', current - 1, current, current + 1, '…', total];
}

function goPage(p) {
  if (p < 1 || p > state.pages) return;
  state.page = p;
  loadTenders();
  window.scrollTo({ top: 0, behavior: 'smooth' });
}

// ── Search ────────────────────────────────────────────────────
function onSearch() {
  clearTimeout(state.searchTimer);
  state.searchTimer = setTimeout(() => {
    state.search = document.getElementById('search-input').value.trim();
    state.page   = 1;
    loadTenders();
  }, 400);
}

function onPerPageChange() {
  state.perPage = parseInt(document.getElementById('per-page-sel').value, 10);
  state.page    = 1;
  loadTenders();
}

// ── Crawl ─────────────────────────────────────────────────────
async function triggerCrawl() {
  const btn = document.getElementById('btn-crawl');
  btn.disabled = true;
  btn.innerHTML = '<span class="spinner-border spinner-border-sm me-2"></span>Starting…';

  try {
    const res  = await fetch('/api/crawl', { method: 'POST' });
    const json = await res.json();
    showAlert('info', json.message || 'Crawl started.');
  } catch (err) {
    showAlert('danger', 'Could not start crawl: ' + err.message);
    btn.disabled = false;
    btn.innerHTML = '<i class="bi bi-arrow-repeat me-2"></i>Refresh Tenders';
  }
}

// ── Status polling ────────────────────────────────────────────
function startStatusPolling() {
  checkStatus();
  state.statusTimer = setInterval(checkStatus, 5000);
}

async function checkStatus() {
  try {
    const s = await apiFetch('/api/status');

    // Header stats
    document.getElementById('stat-total').textContent      = s.total_tenders ?? '—';
    document.getElementById('stat-last-crawl').textContent = s.last_crawl    ?? 'Never';

    const dot      = document.querySelector('.status-dot');
    const statSpan = document.getElementById('stat-status');
    const btn      = document.getElementById('btn-crawl');

    if (s.running) {
      dot.className      = 'bi bi-circle-fill status-dot running';
      statSpan.textContent = 'Crawling…';
      btn.disabled       = true;
      btn.innerHTML      = '<span class="spinner-border spinner-border-sm me-2"></span>Crawling…';
    } else {
      dot.className      = 'bi bi-circle-fill status-dot ' + (s.last_crawl ? 'done' : 'idle');
      statSpan.textContent = s.last_crawl ? 'Ready' : 'Idle';
      btn.disabled       = false;
      btn.innerHTML      = '<i class="bi bi-arrow-repeat me-2"></i>Refresh Tenders';

      // Reload grid after a crawl finishes
      if (btn._wasCrawling) loadTenders();
    }

    btn._wasCrawling = s.running;

  } catch { /* server not yet up */ }
}

// ── Utility ──────────────────────────────────────────────────
function esc(str) {
  return String(str)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

/** Parse the document_urls JSON array, falling back to document_url. */
function parseDocUrls(t) {
  try {
    const arr = JSON.parse(t.document_urls || '[]');
    if (Array.isArray(arr) && arr.length) return arr;
  } catch (e) {}
  return t.document_url ? [t.document_url] : [];
}

/** Extract a human-readable filename from a Download URL query param. */
function docFileName(url, idx) {
  try {
    const name = new URL(url).searchParams.get('downloadedFileName');
    if (name) return name;
  } catch (e) {}
  return `Document ${idx + 1}`;
}

function showLoading(on) {
  document.getElementById('loading-state').classList.toggle('d-none', !on);
  document.getElementById('tenders-grid').classList.toggle('d-none', on);
}

function showAlert(type, msg) {
  const bar = document.getElementById('alert-bar');
  const txt = document.getElementById('alert-msg');

  bar.className = `alert alert-${type} d-flex align-items-center mb-4`;
  txt.textContent = msg;

  clearTimeout(bar._timer);
  bar._timer = setTimeout(() => bar.classList.add('d-none'), 8000);
}

function updateResultsLabel() {
  const { total, search } = state;
  const el = document.getElementById('results-label');
  if (!total) {
    el.textContent = 'No results';
  } else if (search) {
    el.textContent = `${total} result${total !== 1 ? 's' : ''} for "${search}"`;
  } else {
    el.textContent = `${total} ICT tender${total !== 1 ? 's' : ''}`;
  }
}

/**
 * Returns 'urgent' (<= 3 days), 'soon' (<= 7 days), or '' (open)
 * Handles date strings like "21/05/2026", "2026-05-21", "in X days"
 */
function closingUrgency(dateStr) {
  if (!dateStr) return '';

  // "in X days"
  const inDays = dateStr.match(/in\s+(\d+)\s+day/i);
  if (inDays) {
    const d = parseInt(inDays[1], 10);
    return d <= 3 ? 'urgent' : d <= 7 ? 'soon' : '';
  }

  // Parse DD/MM/YYYY or YYYY-MM-DD
  let dt = null;
  const dmy = dateStr.match(/(\d{1,2})\/(\d{1,2})\/(\d{4})/);
  if (dmy) dt = new Date(`${dmy[3]}-${dmy[2].padStart(2,'0')}-${dmy[1].padStart(2,'0')}`);
  else {
    const iso = dateStr.match(/(\d{4})-(\d{2})-(\d{2})/);
    if (iso) dt = new Date(dateStr.slice(0, 10));
  }

  if (!dt || isNaN(dt)) return '';
  const diffDays = Math.ceil((dt - Date.now()) / 86400000);
  return diffDays <= 3 ? 'urgent' : diffDays <= 7 ? 'soon' : '';
}
