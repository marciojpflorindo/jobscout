'use strict';

// --- Palettes (kept in sync with style.css) ---------------------------------
const STATUS_COLORS = {
  'Applied': '#16a34a', 'In conversation': '#38bdf8', 'Interviewing': '#f5a623',
  'Offer': '#34c38f', 'Accepted': '#2aa86b', 'Rejected': '#ef5d6b',
  'Declined': '#8b5cf6', 'No response': '#cbd5e1',
};
const MONTH_ORDER = ['January', 'February', 'March', 'April', 'May', 'June',
  'July', 'August', 'September', 'October', 'November', 'December'];

// Derived groupings (the single Status field is the only source of truth)
const RESPONDED = ['In conversation', 'Interviewing', 'Offer', 'Accepted', 'Rejected', 'Declined'];
const INTERVIEWED = ['Interviewing', 'Offer', 'Accepted'];
const OFFERS = ['Offer', 'Accepted'];
const ACTIVE = ['Applied', 'In conversation', 'Interviewing', 'Offer'];
const POSITIVE = ['In conversation', 'Interviewing', 'Offer', 'Accepted'];

// "Potential" = a brain candidate not yet applied to. It is NOT a real application:
// excluded from the KPIs/charts, and shown in its own "Review" tab. Keeping a job =
// changing its Status to "Applied", which moves it into Applications and Tracker stats.
const POTENTIAL = 'Potential';
// Real applications only (everything that isn't a pending candidate).
function appRows() { return DATA.rows.filter((r) => r.Status !== POTENTIAL); }
function potentialRows() { return DATA.rows.filter((r) => r.Status === POTENTIAL); }
function isPotential(r) { return r && r.Status === POTENTIAL; }

// Three views: "tracker" = KPIs/charts, "companies" = real application list,
// "review" = triage the Potential queue. The default is picked once on first load
// (Review when there are candidates to triage, else Tracker when there are saved
// applications, else Applications) and then left to the user's clicks, so a
// background reload never yanks them out of the view they're using.
let VIEW = 'tracker';
let viewInitialized = false;

// --- State ------------------------------------------------------------------
let DATA = { statuses: [], months: [], rows: [] };
const charts = {};
const $ = (sel) => document.querySelector(sel);
const DESKTOP_MQ = window.matchMedia('(min-width: 1100px)');

// Sort state (driven by both the dropdown and clickable table headers).
let SORT = { key: 'activity', dir: 'desc', type: 'activity' };
const SORT_PRESETS = {
  'activity-desc': { key: 'activity', dir: 'desc', type: 'activity' },
  'date-desc': { key: 'Date', dir: 'desc', type: 'date' },
  'date-asc': { key: 'Date', dir: 'asc', type: 'date' },
  'company-asc': { key: 'Company', dir: 'asc', type: 'text' },
  'status-asc': { key: 'Status', dir: 'asc', type: 'text' },
};
// Columns after the leading selection checkbox. `field: null` = not sortable.
const TABLE_COLS = [
  { label: 'Company', field: 'Company', type: 'text' },
  { label: 'Role', field: 'Role', type: 'text' },
  { label: 'Status', field: 'Status', type: 'text' },
  { label: 'Applied', field: 'Date', type: 'date' },
  { label: 'Response', field: 'Response date', type: 'date' },
  { label: 'Via', field: 'Contact via', type: 'text' },
  { label: 'Notes', field: 'Notes', type: 'text' },
  { label: '', field: null },
];

// Selected row ids (index into DATA.rows). Stable until the next load().
const selected = new Set();

// "Suggest closing" feature: flag rows still "Applied" with no response that
// were applied more than STALE_DAYS ago. Threshold persists in localStorage.
let STALE_DAYS = (function () {
  let v;
  try { v = parseInt(localStorage.getItem('stale-days'), 10); } catch (e) { v = NaN; }
  return (Number.isInteger(v) && v > 0) ? v : 30;
})();
// Rows the user chose to keep waiting on, keyed by a value-based signature so the
// hide survives the reload after an action (positional ids would not). Session-only.
const staleDismissed = new Set();
const staleKey = (r) => (r.Company || '') + '|' + (r.Date || '');

// --- API (timeout, response.ok, try/catch) ----------------------------------
// Global activity indicator: every server call runs through api(), so a simple
// in-flight counter drives the top #busybar for ALL reads and writes.
let busyCount = 0;
let busyHideTimer;
function setBusy(on) {
  busyCount = Math.max(0, busyCount + (on ? 1 : -1));
  const bar = $('#busybar');
  if (!bar) return;
  if (busyCount > 0) {
    clearTimeout(busyHideTimer);
    bar.classList.add('on');
  } else {
    clearTimeout(busyHideTimer);
    busyHideTimer = setTimeout(() => { if (busyCount === 0) bar.classList.remove('on'); }, 200);
  }
}

async function api(path, options) {
  const { timeoutMs = 10000, ...rest } = options || {};
  const opts = Object.assign({ signal: AbortSignal.timeout(timeoutMs) }, rest);
  setBusy(true);
  try {
    const res = await fetch(path, opts);
    let body = null;
    try { body = await res.json(); } catch (e) { body = null; }
    if (!res.ok) throw new Error((body && body.error) || ('Error ' + res.status));
    return body;
  } finally {
    setBusy(false);
  }
}

// Run an async action with a busy button state (disabled + spinner + label swap),
// so a discrete click gets local feedback and can't be double-fired.
// NOTE: this swaps `textContent`, so wrap only plain-text buttons.
async function withBusy(btn, label, fn) {
  if (!btn) return fn();
  const orig = btn.textContent;
  const wasDisabled = btn.disabled;
  btn.disabled = true;
  btn.dataset.busy = '1';
  btn.textContent = label;
  try {
    return await fn();
  } finally {
    delete btn.dataset.busy;
    btn.disabled = wasDisabled;
    btn.textContent = orig;
  }
}
const getData = () => api('/api/data');
const postJSON = (path, obj) => api(path, {
  method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(obj),
});

// --- Boot -------------------------------------------------------------------
document.addEventListener('DOMContentLoaded', async () => {
  applyTheme(localStorage.getItem('theme') || 'auto');
  wireEvents();
  fillSelect($('#f-status'), null);          // populated after load
  try {
    await load();
    $('#loading').hidden = true;
    $('#app').hidden = false;
  } catch (err) {
    $('#loading').textContent = 'Could not load the data. (' + errMsg(err) + ')';
  }
});

async function load() {
  // Positional ids are only valid for the snapshot they came from — never let a
  // selection outlive a reload, or a later batch delete could hit the wrong rows.
  selected.clear();
  DATA = await getData();
  fillSelect($('#f-status'), DATA.statuses);
  fillSelect($('#f-month'), DATA.months, true);
  populateFilter();
  const nApp = appRows().length;
  $('#subtitle').textContent = nApp + ' application' + (nApp === 1 ? '' : 's')
    + ' · ' + new Date().toLocaleString();
  // First load only: land on whichever tab has something to do.
  if (!viewInitialized) {
    VIEW = potentialRows().length > 0 ? 'review' : (nApp > 0 ? 'tracker' : 'companies');
    viewInitialized = true;
  }
  renderTabs();
  renderChrome();
  renderList();
}

// KPIs + charts — the Tracker-only chrome.
function renderTracker() {
  renderKpis();
  renderCharts();
}

function wireEvents() {
  $('#btn-theme').addEventListener('click', cycleTheme);
  $('#btn-refresh').addEventListener('click', (e) =>
    withBusy(e.currentTarget, 'Refreshing…', load).then(() => toast('Refreshed.')));
  $('#btn-add').addEventListener('click', () => openModal(null));
  $('#btn-download').addEventListener('click', downloadCsv);
  $('#btn-import').addEventListener('click', () => $('#file-import').click());
  $('#file-import').addEventListener('change', function () {
    const file = this.files && this.files[0];
    this.value = '';            // allow re-importing the same filename later
    importCsv(file);
  });
  $('#btn-cancel').addEventListener('click', closeModal);
  $('#modal-bg').addEventListener('click', (e) => { if (e.target === $('#modal-bg')) closeModal(); });
  $('#form').addEventListener('submit', submitForm);
  // Clear selection when the visible set changes, so you can't batch-delete rows
  // that scrolled out of view under a filter/search.
  $('#search').addEventListener('input', () => { selected.clear(); renderList(); });
  $('#filter-status').addEventListener('change', () => { selected.clear(); renderList(); });
  $('#tab-tracker').addEventListener('click', () => setView('tracker'));
  $('#tab-companies').addEventListener('click', () => setView('companies'));
  $('#tab-review').addEventListener('click', () => setView('review'));
  $('#sort').addEventListener('change', () => {
    const p = SORT_PRESETS[$('#sort').value];
    if (p) SORT = Object.assign({}, p);
    renderList();
  });
  $('#bulk-delete').addEventListener('click', (e) => deleteIds([...selected], e.currentTarget));
  $('#bulk-clear').addEventListener('click', () => { selected.clear(); renderList(); });
  $('#stale-days').addEventListener('change', function () {
    const v = parseInt(this.value, 10);
    STALE_DAYS = (Number.isInteger(v) && v > 0) ? v : 30;
    try { localStorage.setItem('stale-days', String(STALE_DAYS)); } catch (e) { /* private mode */ }
    renderStale();
  });
  $('#stale-close-all').addEventListener('click', (e) => closeAllStale(e.currentTarget));
  $('#f-date').addEventListener('change', function () {
    if (this.value && $('#f-month').value === '') $('#f-month').value = MONTH_ORDER[parseInt(this.value.slice(5, 7), 10) - 1] || '';
  });
  // Re-render the list when crossing the desktop/mobile breakpoint.
  DESKTOP_MQ.addEventListener('change', renderList);
  // Repaint charts when the OS theme flips while in "auto".
  window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change', () => {
    if ((localStorage.getItem('theme') || 'auto') === 'auto') repaintCharts();
  });
}

// --- Theme (auto / light / dark, persisted) ---------------------------------
const THEMES = ['auto', 'light', 'dark'];
const THEME_LABEL = { auto: '🌓 Auto', light: '☀️ Light', dark: '🌙 Dark' };
function applyTheme(t) {
  if (THEMES.indexOf(t) === -1) t = 'auto';
  if (t === 'auto') document.documentElement.removeAttribute('data-theme');
  else document.documentElement.setAttribute('data-theme', t);
  try { localStorage.setItem('theme', t); } catch (e) { /* private mode */ }
  const btn = $('#btn-theme');
  if (btn) btn.textContent = THEME_LABEL[t];
}
function cycleTheme() {
  const cur = (function () { try { return localStorage.getItem('theme'); } catch (e) { return null; } })() || 'auto';
  applyTheme(THEMES[(THEMES.indexOf(cur) + 1) % THEMES.length]);
  repaintCharts(); // colors come from CSS vars at draw time
}

// --- View switch ------------------------------------------------------------
function renderTabs() {
  const review = VIEW === 'review';
  const tracker = VIEW === 'tracker';
  const companies = VIEW === 'companies';
  const n = potentialRows().length;
  const badge = $('#tab-review-count');
  badge.textContent = String(n);
  badge.hidden = n === 0;
  $('#tab-tracker').setAttribute('aria-pressed', tracker ? 'true' : 'false');
  $('#tab-companies').setAttribute('aria-pressed', companies ? 'true' : 'false');
  $('#tab-review').setAttribute('aria-pressed', review ? 'true' : 'false');
  // A status filter only makes sense over the Applications list.
  $('#filter-status').hidden = !companies;
}

function renderChrome() {
  const hasApps = appRows().length > 0;
  const showTracker = VIEW === 'tracker' && hasApps;
  const showCompanies = VIEW === 'companies' && hasApps;
  const showList = VIEW === 'companies' || VIEW === 'review';
  $('#tracker-only').hidden = !showTracker;
  $('#stale').hidden = !showCompanies;
  $('.list-controls').hidden = !showList;
  $('#list').hidden = !showList;
  if (showTracker) renderTracker();
  else Object.keys(charts).forEach((k) => { charts[k].destroy(); delete charts[k]; });
  if (showCompanies) renderStale();
}

function repaintCharts() {
  if (window.Chart && VIEW === 'tracker' && appRows().length > 0) renderCharts();
}

// Switch tabs. Clears the (positional) selection so a later batch action can't hit
// rows from the other view; keeps the search box so a query carries across.
function setView(view) {
  if (['tracker', 'companies', 'review'].indexOf(view) === -1 || view === VIEW) return;
  VIEW = view;
  selected.clear();
  renderTabs();
  renderChrome();
  renderList();
}

function renderKpis() {
  const rows = appRows();  // candidates (Potential) are not applications — exclude them
  const total = rows.length;
  const count = (arr) => rows.filter((r) => arr.indexOf(r.Status) !== -1).length;
  const responded = count(RESPONDED), interv = count(INTERVIEWED), offers = count(OFFERS), active = count(ACTIVE);
  const pct = (n) => (total ? Math.round(n / total * 100) + '%' : '—');

  const cards = [
    { label: 'Applications', value: total, sub: '' },
    { label: 'Response rate', value: pct(responded), sub: responded + ' response(s)' },
    { label: 'Interviews', value: interv, sub: pct(interv) + ' of total' },
    { label: 'Offers', value: offers, sub: pct(offers) },
    { label: 'In progress', value: active, sub: 'still active' },
  ];
  const host = $('#kpis');
  host.textContent = '';
  cards.forEach((c) => {
    const el = make('div', 'kpi');
    el.appendChild(make('div', 'label', c.label));
    el.appendChild(make('div', 'value', String(c.value)));
    el.appendChild(make('div', 'sub', c.sub));
    host.appendChild(el);
  });
}

// --- Charts -----------------------------------------------------------------
function renderCharts() {
  if (!window.Chart) return;
  Chart.defaults.color = cssVar('--text-secondary');
  Chart.defaults.font.family = getComputedStyle(document.body).fontFamily;
  if (window.ChartDataLabels) {
    Chart.register(window.ChartDataLabels);
    Chart.defaults.set('plugins.datalabels', { display: false });
  }
  Object.keys(charts).forEach((k) => charts[k].destroy());
  const rows = appRows();  // exclude Potential candidates from all application charts

  // Over time (by month)
  const byMonth = tally(rows.map((r) => r['Month'] || monthFromDate(r.Date) || 'No month'));
  const months = Object.keys(byMonth).sort((a, b) => monthIdx(a) - monthIdx(b));
  charts.time = barChart('chart-time', months, months.map((m) => byMonth[m]), cssVar('--accent'));

  // Funnel
  const f = (arr) => rows.filter((r) => arr.indexOf(r.Status) !== -1).length;
  charts.funnel = hbarChart('chart-funnel',
    ['Applications', 'Responses', 'Interviews', 'Offers'],
    [rows.length, f(RESPONDED), f(INTERVIEWED), f(OFFERS)],
    ['#4f6df5', '#38bdf8', '#f5a623', '#2aa86b']);

  // Status distribution
  const st = byKeyOrdered(rows, 'Status', DATA.statuses.filter((e) => e !== POTENTIAL));
  charts.status = doughnut('chart-status', st.labels, st.data, st.labels.map((l) => STATUS_COLORS[l] || '#888'));

  // Company response (derived buckets)
  const buckets = { 'Positive': 0, 'Negative': 0, 'No response': 0, 'Declined': 0 };
  rows.forEach((r) => {
    if (POSITIVE.indexOf(r.Status) !== -1) buckets['Positive']++;
    else if (r.Status === 'Rejected') buckets['Negative']++;
    else if (r.Status === 'Declined') buckets['Declined']++;
    else buckets['No response']++;
  });
  const bl = Object.keys(buckets).filter((k) => buckets[k]);
  charts.response = doughnut('chart-response', bl, bl.map((k) => buckets[k]),
    bl.map((k) => ({ 'Positive': '#34c38f', 'Negative': '#ef5d6b', 'No response': '#cbd5e1', 'Declined': '#8b5cf6' }[k])));

  // Source
  const src = tally(rows.map((r) => normSource(r['Contact via'])));
  const top = Object.keys(src).sort((a, b) => src[b] - src[a]).slice(0, 8);
  charts.source = hbarChart('chart-source', top, top.map((k) => src[k]), top.map(() => cssVar('--accent')));
}

function barChart(id, labels, data, color) {
  return new Chart(el(id), {
    type: 'bar',
    data: { labels, datasets: [{ data, backgroundColor: color, borderRadius: 6, maxBarThickness: 46 }] },
    options: baseOpts({ legend: false }, { x: { grid: { display: false } }, y: gridY() }),
  });
}
function hbarChart(id, labels, data, colors) {
  return new Chart(el(id), {
    type: 'bar',
    data: { labels, datasets: [{ data, backgroundColor: colors, borderRadius: 6 }] },
    options: Object.assign(baseOpts({ legend: false }, { x: gridY(), y: { grid: { display: false } } }), { indexAxis: 'y' }),
  });
}
function doughnut(id, labels, data, colors) {
  const empty = !data.length;
  if (empty) { labels = ['No data']; data = [1]; colors = [cssVar('--border')]; }
  const opts = baseOpts({ legend: 'bottom' }, null, '60%');
  if (!empty) {
    const total = data.reduce((sum, v) => sum + v, 0);
    const pct = (v) => (total ? Math.round(v / total * 100) : 0);
    opts.plugins.tooltip = {
      callbacks: { label: (ctx) => ' ' + ctx.label + ': ' + ctx.parsed + ' (' + pct(ctx.parsed) + '%)' },
    };
    opts.plugins.datalabels = {
      color: '#fff',
      font: { weight: '600', size: 12 },
      display: (ctx) => pct(ctx.dataset.data[ctx.dataIndex]) >= 8,
      formatter: (v) => pct(v) + '%',
    };
  }
  return new Chart(el(id), {
    type: 'doughnut',
    data: { labels, datasets: [{ data, backgroundColor: colors, borderWidth: 0 }] },
    options: opts,
  });
}
function baseOpts(legend, scales, cutout) {
  const o = { responsive: true, maintainAspectRatio: false, plugins: {} };
  o.plugins.legend = legend.legend === false ? { display: false }
    : { position: legend.legend, labels: { boxWidth: 12, padding: 12 } };
  if (scales) o.scales = scales;
  if (cutout) o.cutout = cutout;
  return o;
}
function gridY() { return { beginAtZero: true, ticks: { precision: 0 }, grid: { color: cssVar('--border') } }; }

// --- Sorting helpers --------------------------------------------------------
// "Last activity" rank = the row's last-touched instant, as epoch ms. Prefer the
// server-written _updated timestamp; fall back to the best-known calendar date for
// rows written before that field existed, at UTC midnight so both scales compare.
function activityKey(r) {
  const t = r._updated ? Date.parse(r._updated) : NaN;
  if (!Number.isNaN(t)) return t;
  const dk = Math.max(dateKey(r.Date), dateKey(r['Response date']));
  if (!dk) return 0;
  const y = Math.floor(dk / 10000), mo = Math.floor(dk / 100) % 100, da = dk % 100;
  return Date.UTC(y, mo - 1, da);
}

function sortVal(r, key, type) {
  if (type === 'activity') return activityKey(r);
  return type === 'date' ? dateKey(r[key]) : String(r[key] || '').toLowerCase();
}

function visibleRows() {
  const q = $('#search').value.trim().toLowerCase();
  // The view picks the universe: Review = Potential queue, Applications = real
  // applications. Tracker has no list. Search applies to Review and Applications.
  if (VIEW === 'tracker') return [];
  const base = VIEW === 'review' ? potentialRows() : appRows();
  const fe = VIEW === 'companies' ? $('#filter-status').value : '';
  let rows = base.filter((r) => {
    if (fe && r.Status !== fe) return false;
    if (q && (DATA.columns || []).map((c) => r[c] || '').join(' ').toLowerCase().indexOf(q) === -1) return false;
    return true;
  });
  const { key, dir, type } = SORT;
  const d = dir === 'asc' ? 1 : -1;
  rows = rows.slice().sort((a, b) => {
    const av = sortVal(a, key, type), bv = sortVal(b, key, type);
    return av < bv ? -d : av > bv ? d : 0;
  });
  return rows;
}

// Header click: toggle direction on the same column, else switch column.
function setSort(field, type) {
  if (SORT.key === field) SORT.dir = SORT.dir === 'asc' ? 'desc' : 'asc';
  else SORT = { key: field, dir: type === 'date' ? 'desc' : 'asc', type };
  const match = Object.keys(SORT_PRESETS).find((k) => {
    const p = SORT_PRESETS[k];
    return p.key === SORT.key && p.dir === SORT.dir;
  });
  if (match) $('#sort').value = match;
  renderList();
}

function renderList() {
  const listMode = VIEW === 'companies' || VIEW === 'review';
  const rows = visibleRows();
  const host = $('#list');
  host.textContent = '';
  if (!listMode) {
    $('#count').textContent = '';
    $('#empty').hidden = true;
    renderSelBar();
    return;
  }
  if (DESKTOP_MQ.matches) {
    host.className = '';
    host.appendChild(buildTable(rows));
  } else {
    host.className = 'cards';
    rows.forEach((r) => host.appendChild(buildCard(r)));
  }
  $('#count').textContent = VIEW === 'review'
    ? rows.length + ' to review'
    : rows.length + ' of ' + appRows().length;
  $('#empty').textContent = listEmptyMessage();
  $('#empty').hidden = rows.length !== 0;
  renderSelBar();
}

// A real, situation-specific empty state so nothing ever just silently vanishes.
function listEmptyMessage() {
  const searching = $('#search').value.trim() !== '';
  if (VIEW === 'review') {
    if (potentialRows().length === 0) {
      return 'All caught up — no jobs to review. Kept jobs are in Applications; run a search to find more.';
    }
    return 'No potential jobs match your search.';
  }
  if (appRows().length === 0) {
    return 'No applications yet. Keep jobs from the Review tab, or add one with “+ New application”.';
  }
  return searching || $('#filter-status').value ? 'No applications match.' : 'No applications yet.';
}

function renderSelBar() {
  const bar = $('#bulk-bar');
  if (!bar) return;
  const n = selected.size;
  bar.hidden = n === 0;
  if (n) $('#bulk-count').textContent = n + ' selected';
}

// --- Stale-application suggestions ------------------------------------------
function daysSince(d) {
  const m = /^(\d{1,2})-(\d{1,2})-(\d{4})$/.exec(String(d || ''));
  if (!m) return null;
  const then = Date.UTC(Number(m[3]), Number(m[2]) - 1, Number(m[1]));
  if (Number.isNaN(then)) return null;
  const now = new Date();
  const today = Date.UTC(now.getFullYear(), now.getMonth(), now.getDate());
  return Math.floor((today - then) / 86400000);
}

// Still "Applied", no response logged, applied at least STALE_DAYS ago, and not
// hidden this session. Newest-stale first so the longest silences lead.
function staleRows() {
  return DATA.rows.filter((r) => {
    if (r.Status !== 'Applied') return false;
    if (String(r['Response date'] || '').trim()) return false;
    const days = daysSince(r.Date);
    if (days == null || days < STALE_DAYS) return false;
    return !staleDismissed.has(staleKey(r));
  }).sort((a, b) => (daysSince(b.Date) || 0) - (daysSince(a.Date) || 0));
}

function renderStale() {
  const host = $('#stale');
  if (!host) return;
  $('#stale-days').value = String(STALE_DAYS);
  const rows = staleRows();
  $('#stale-count').textContent = String(rows.length);
  const list = $('#stale-list');
  list.textContent = '';
  rows.forEach((r) => list.appendChild(staleItem(r)));
  const none = rows.length === 0;
  $('#stale-close-all').hidden = none;
  $('#stale-empty').hidden = !none;
}

function staleItem(r) {
  const li = make('li', 'stale-item');
  const info = make('button', 'stale-info');
  info.type = 'button';
  info.title = 'Go to this application in the list';
  info.appendChild(make('span', 'stale-company', r.Company || '(no name)'));
  if (r.Role) info.appendChild(make('span', 'stale-role', r.Role));
  info.appendChild(make('span', 'stale-age', daysSince(r.Date) + ' days ago'));
  info.addEventListener('click', () => goToRow(r));
  li.appendChild(info);

  const acts = make('div', 'stale-acts');
  const close = make('button', 'ghost mini', 'No response');
  close.title = 'Mark this application as "No response"';
  close.addEventListener('click', () => closeStale(r, close));
  const keep = make('button', 'ghost mini', 'Keep');
  keep.title = 'Hide the suggestion until reload';
  keep.addEventListener('click', () => { staleDismissed.add(staleKey(r)); renderStale(); });
  acts.appendChild(close);
  acts.appendChild(keep);
  li.appendChild(acts);
  return li;
}

function goToRow(r) {
  $('#search').value = '';
  $('#filter-status').value = '';
  selected.clear();
  // Stale suggestions live in Applications, so be explicit and make the row visible.
  if (VIEW !== 'companies') { VIEW = 'companies'; renderTabs(); renderChrome(); }
  renderList();
  const el = $('#list').querySelector('[data-row-id="' + r.id + '"]');
  if (!el) return;
  el.scrollIntoView({ behavior: 'smooth', block: 'center' });
  el.classList.add('row-flash');
  setTimeout(() => el.classList.remove('row-flash'), 1800);
}

async function closeStale(r, btn) {
  try {
    await withBusy(btn, 'Marking…', () =>
      postJSON('/api/update', { id: r.id, field: 'Status', value: 'No response', expect: r.Company || '' }));
    toast('Marked as no response.');
    await load();
  } catch (err) {
    toast('Error: ' + errMsg(err), true);
  }
}

async function closeAllStale(btn) {
  const rows = staleRows();
  if (!rows.length) return;
  if (!confirm('Mark ' + rows.length + ' application(s) as "No response"?')) return;
  try {
    await withBusy(btn, 'Marking…', async () => {
      for (const r of rows) {
        await postJSON('/api/update', { id: r.id, field: 'Status', value: 'No response', expect: r.Company || '' });
      }
    });
    toast(rows.length + ' marked as no response.');
  } catch (err) {
    toast('Error: ' + errMsg(err), true);
  } finally {
    await load();
  }
}

// --- Table (desktop) --------------------------------------------------------
function buildTable(rows) {
  const wrap = make('div', 'table-wrap');
  const table = make('table', 'apps');

  const thead = document.createElement('thead');
  const htr = document.createElement('tr');

  const selTh = make('th', 'sel-cell');
  const all = document.createElement('input');
  all.type = 'checkbox';
  all.setAttribute('aria-label', 'Select all visible');
  const visIds = rows.map((r) => r.id);
  all.checked = visIds.length > 0 && visIds.every((id) => selected.has(id));
  all.addEventListener('change', () => {
    visIds.forEach((id) => (all.checked ? selected.add(id) : selected.delete(id)));
    renderList();
  });
  selTh.appendChild(all);
  htr.appendChild(selTh);

  TABLE_COLS.forEach((c) => {
    const th = document.createElement('th');
    if (c.field) {
      th.className = 'sortable' + (SORT.key === c.field ? ' sorted' : '');
      th.tabIndex = 0;
      th.setAttribute('role', 'button');
      th.appendChild(document.createTextNode(c.label));
      if (SORT.key === c.field) th.appendChild(make('span', 'sort-ind', SORT.dir === 'asc' ? ' ▲' : ' ▼'));
      const act = () => setSort(c.field, c.type);
      th.addEventListener('click', act);
      th.addEventListener('keydown', (e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); act(); } });
    } else {
      th.textContent = c.label;
    }
    htr.appendChild(th);
  });
  thead.appendChild(htr);
  table.appendChild(thead);

  const tbody = document.createElement('tbody');
  rows.forEach((r) => {
    const tr = document.createElement('tr');
    tr.dataset.rowId = r.id;
    tr.appendChild(checkboxCell(r));
    tr.appendChild(companyCell(r));
    tr.appendChild(nodeCell(editSpan(r, 'Role', 'text')));
    tr.appendChild(nodeCell(statusSelect(r)));
    tr.appendChild(dateCell(r, 'Date'));
    tr.appendChild(dateCell(r, 'Response date'));
    tr.appendChild(nodeCell(editSpan(r, 'Contact via', 'text')));
    tr.appendChild(notesCell(r));
    tr.appendChild(rowActions(r));
    tbody.appendChild(tr);
  });
  table.appendChild(tbody);
  wrap.appendChild(table);
  return wrap;
}

function nodeCell(node) { const d = document.createElement('td'); d.appendChild(node); return d; }
function dateCell(r, field) { const d = make('td', 'date-cell'); d.appendChild(editSpan(r, field, 'date')); return d; }

function checkboxCell(r) {
  const d = make('td', 'sel-cell');
  const cb = document.createElement('input');
  cb.type = 'checkbox';
  cb.checked = selected.has(r.id);
  cb.setAttribute('aria-label', 'Select ' + (r.Company || ''));
  cb.addEventListener('change', () => {
    if (cb.checked) selected.add(r.id); else selected.delete(r.id);
    renderSelBar();
  });
  d.appendChild(cb);
  return d;
}

function companyCell(r) {
  const d = make('td', 'company-cell');
  d.appendChild(editSpan(r, 'Company', 'text'));
  if (/^https?:\/\//i.test(r['Job link'] || '')) {
    const a = document.createElement('a');
    a.href = r['Job link']; a.target = '_blank'; a.rel = 'noopener noreferrer'; a.textContent = 'posting ↗';
    d.appendChild(a);
  }
  return d;
}

function notesCell(r) {
  const d = make('td', 'notes-cell');
  d.appendChild(editSpan(r, 'Notes', 'text', { tag: 'div', cls: 'notes' }));
  return d;
}

function rowActions(r) {
  const d = make('td', 'row-actions');
  const edit = make('button', 'ghost mini', 'Edit');
  edit.addEventListener('click', () => openModal(r));
  d.appendChild(edit);
  if (isPotential(r)) {
    const reject = make('button', 'ghost mini danger', 'Reject');
    reject.title = 'Reject with a note so JobScout learns from it';
    reject.addEventListener('click', () => rejectPotential(r, reject));
    d.appendChild(reject);
  }
  const del = make('button', 'ghost mini danger', 'Delete');
  del.addEventListener('click', () => deleteIds([r.id], del));
  d.appendChild(del);
  return d;
}

// --- Inline editing ---------------------------------------------------------
function editSpan(r, field, type, opts) {
  opts = opts || {};
  const has = r[field] != null && r[field] !== '';
  const cls = ('editable ' + (opts.cls || '')).trim() + (has ? '' : ' empty');
  const node = make(opts.tag || 'span', cls, has ? String(r[field]) : '—');
  node.tabIndex = 0;
  node.setAttribute('role', 'button');
  node.title = 'Click to edit';
  const go = (e) => {
    if (e.type === 'keydown' && e.key !== 'Enter' && e.key !== ' ') return;
    if (e.preventDefault) e.preventDefault();
    beginEdit(node, r, field, type, opts);
  };
  node.addEventListener('click', go);
  node.addEventListener('keydown', go);
  return node;
}

function beginEdit(node, r, field, type, opts) {
  let input;
  if (field === 'Notes') {
    input = document.createElement('textarea');
    input.rows = 4;
    input.value = r[field] || '';
  } else if (type === 'date') {
    input = document.createElement('input');
    input.type = 'date';
    input.value = toInputDate(r[field]);
  } else {
    input = document.createElement('input');
    input.type = 'text';
    input.value = r[field] || '';
  }
  input.className = 'cell-edit';
  node.replaceWith(input);
  input.focus();
  if (input.select) input.select();

  let done = false, dirty = false;
  input.addEventListener('input', () => { dirty = true; });

  const restore = () => input.replaceWith(editSpan(r, field, type, opts));

  const commit = async () => {
    if (!dirty) { restore(); return; }
    const value = type === 'date' ? toStoreDate(input.value) : input.value;
    if (value === (r[field] || '')) { restore(); return; }
    try {
      await postJSON('/api/update', { id: r.id, field, value, expect: r.Company || '' });
      toast('Saved.');
      await load();                 // reflect server-side normalization + Month/charts
    } catch (err) {
      toast('Error: ' + errMsg(err), true);
      restore();
    }
  };

  const finish = (fn) => {
    if (done) return;
    done = true;
    input.removeEventListener('blur', onBlur);
    fn();
  };
  const onBlur = () => finish(commit);

  input.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') { e.preventDefault(); finish(restore); }
    else if (e.key === 'Enter' && (field !== 'Notes' || e.metaKey || e.ctrlKey)) { e.preventDefault(); finish(commit); }
  });
  input.addEventListener('blur', onBlur);
}

// --- Delete -----------------------------------------------------------------
async function deleteIds(ids, btn) {
  ids = (ids || []).filter((id) => id != null && DATA.rows[id]);
  if (!ids.length) return;
  const one = ids.length === 1 ? DATA.rows[ids[0]] : null;
  const noun = one ? (isPotential(one) ? 'candidate' : 'application') : 'item';
  const msg = ids.length === 1
    ? 'Delete this ' + noun + '?'
    : 'Delete ' + ids.length + ' items? This cannot be undone.';
  if (!confirm(msg)) return;
  const items = ids.map((id) => ({ id, company: DATA.rows[id].Company || '' }));
  try {
    const res = await withBusy(btn, 'Deleting…', () => postJSON('/api/delete', { items }));
    await load();
    const n = (res && res.removed != null) ? res.removed : ids.length;
    toast(n === 1 ? (isPotential(one) ? 'Candidate deleted.' : 'Application deleted.') : n + ' items deleted.');
  } catch (err) {
    toast('Error deleting: ' + errMsg(err), true);
  }
}

async function rejectPotential(r, control) {
  const link = String(r['Job link'] || '').trim();
  const label = [r.Company, r.Role].filter(Boolean).join(' — ') || 'this job';
  const entered = prompt('Why is ' + label + ' not a fit? This note teaches JobScout what to avoid next time.', '');
  if (entered === null) return false;
  const reason = entered.trim().slice(0, 500);
  if (!reason) {
    toast('Add a short rejection note so JobScout can learn from it.', true);
    return false;
  }

  const isButton = control && control.tagName === 'BUTTON';
  const isSelect = control && control.tagName === 'SELECT';
  const run = async () => {
    await postJSON('/api/update', { id: r.id, field: 'Notes', value: rejectionNotes(r, reason), expect: r.Company || '' });
    await postJSON('/api/update', { id: r.id, field: 'Status', value: 'Rejected', expect: r.Company || '' });
    if (/^https?:\/\//i.test(link)) {
      try {
        await postJSON('/api/reject', { rejected: [{ link, reason, source: 'user' }] });
      } catch (err) {
        // The row itself now carries the rejection note for /api/links; the ledger
        // is a best-effort backup if the row is deleted later.
      }
    }
    selected.delete(r.id);
    await load();
    toast('Rejected and kept in your tracker.');
    return true;
  };

  try {
    if (isButton) return await withBusy(control, 'Rejecting…', run);
    if (isSelect) control.disabled = true;
    return await run();
  } catch (err) {
    toast('Error rejecting: ' + errMsg(err), true);
    return false;
  } finally {
    if (isSelect) control.disabled = false;
  }
}

function rejectionNotes(r, reason) {
  const current = String((r && r.Notes) || '').trim();
  const line = 'Rejected: ' + reason;
  return current ? line + '\n\n' + current : line;
}

function buildCard(r) {
  const card = make('div', 'app-card');
  card.dataset.rowId = r.id;

  const top = make('div', 'top');
  const left = make('div', 'card-title');
  const cb = document.createElement('input');
  cb.type = 'checkbox';
  cb.className = 'card-check';
  cb.checked = selected.has(r.id);
  cb.setAttribute('aria-label', 'Select ' + (r.Company || ''));
  cb.addEventListener('change', () => {
    if (cb.checked) selected.add(r.id); else selected.delete(r.id);
    renderSelBar();
  });
  const titleWrap = make('div');
  titleWrap.appendChild(make('div', 'company', r.Company || '(no name)'));
  if (r.Role) titleWrap.appendChild(make('div', 'role', r.Role));
  left.appendChild(cb);
  left.appendChild(titleWrap);
  top.appendChild(left);
  top.appendChild(statusSelect(r));
  card.appendChild(top);

  const meta = make('div', 'meta');
  if (r.Date) meta.appendChild(metaItem('Applied', r.Date));
  if (r['Response date']) meta.appendChild(metaItem('Response', r['Response date']));
  if (r['Contact via']) meta.appendChild(metaItem('Via', r['Contact via']));
  if (/^https?:\/\//i.test(r['Job link'] || '')) {
    const a = document.createElement('a');
    a.href = r['Job link']; a.target = '_blank'; a.rel = 'noopener noreferrer'; a.textContent = 'open posting ↗';
    meta.appendChild(a);
  }
  if (meta.childNodes.length) card.appendChild(meta);

  if (r.Notes) {
    const notes = make('div', 'notes', r.Notes);
    card.appendChild(notes);
    requestAnimationFrame(() => {
      if (notes.scrollHeight > notes.clientHeight + 4) {
        const t = make('button', 'notes-toggle', 'show more');
        t.addEventListener('click', () => {
          notes.classList.toggle('expanded');
          t.textContent = notes.classList.contains('expanded') ? 'show less' : 'show more';
        });
        card.appendChild(t);
      }
    });
  }

  const actions = make('div', 'card-actions');
  const edit = make('button', 'ghost', 'Edit');
  edit.addEventListener('click', () => openModal(r));
  actions.appendChild(edit);
  if (isPotential(r)) {
    const reject = make('button', 'ghost danger', 'Reject');
    reject.title = 'Reject with a note so JobScout learns from it';
    reject.addEventListener('click', () => rejectPotential(r, reject));
    actions.appendChild(reject);
  }
  const del = make('button', 'ghost danger', 'Delete');
  del.addEventListener('click', () => deleteIds([r.id], del));
  actions.appendChild(del);
  card.appendChild(actions);
  return card;
}

function statusSelect(r) {
  const sel = document.createElement('select');
  sel.className = 'status-select';
  sel.setAttribute('aria-label', 'Status of ' + (r.Company || ''));
  DATA.statuses.forEach((e) => {
    const o = document.createElement('option'); o.value = e; o.textContent = e;
    if (r.Status === e) o.selected = true;
    sel.appendChild(o);
  });
  sel.style.backgroundColor = STATUS_COLORS[r.Status] || '#888';
  sel.addEventListener('change', async () => {
    const prev = r.Status;
    if (isPotential(r) && sel.value === 'Rejected') {
      const rejected = await rejectPotential(r, sel);
      if (!rejected) sel.value = prev;
      return;
    }
    sel.disabled = true;   // freeze the control while the change is in flight
    try {
      await postJSON('/api/update', { id: r.id, field: 'Status', value: sel.value, expect: r.Company || '' });
      toast('Saved.');
      await load(); // reload so an auto-stamped "Response" date (and KPIs/charts) show
    } catch (err) { sel.value = prev; sel.disabled = false; toast('Error: ' + errMsg(err), true); }
  });
  return sel;
}

function metaItem(label, value) {
  const span = make('span');
  span.appendChild(document.createTextNode(label + ': '));
  const b = make('b', null, value);
  span.appendChild(b);
  return span;
}

// --- Modal (add + edit share one form) --------------------------------------
function openModal(row) {
  const form = $('#form');
  form.reset();
  if (row) {
    $('#modal-title').textContent = 'Edit application';
    form.id.value = row.id;
    form.Company.value = row.Company || '';
    form.Role.value = row.Role || '';
    form.Status.value = DATA.statuses.indexOf(row.Status) !== -1 ? row.Status : 'Applied';
    form.Date.value = toInputDate(row.Date);
    form['Response date'].value = toInputDate(row['Response date']);
    form['Contact via'].value = row['Contact via'] || '';
    form['Month'].value = row['Month'] || '';
    form['Job link'].value = row['Job link'] || '';
    form.Notes.value = row.Notes || '';
  } else {
    $('#modal-title').textContent = 'New application';
    form.id.value = '';
    form.Status.value = 'Applied';
    const now = new Date();
    form.Date.value = now.toISOString().slice(0, 10);
    form['Month'].value = MONTH_ORDER[now.getMonth()];
  }
  $('#modal-bg').hidden = false;
  $('#f-company').focus();
}
function closeModal() { $('#modal-bg').hidden = true; }

async function submitForm(e) {
  e.preventDefault();
  const form = e.target;
  const company = form.Company.value.trim();
  if (!company) { toast('Enter the company.', true); return; }

  const payload = {
    Company: company,
    Role: form.Role.value.trim(),
    Status: form.Status.value,
    Date: toStoreDate(form.Date.value),
    'Response date': toStoreDate(form['Response date'].value),
    'Contact via': form['Contact via'].value.trim(),
    'Month': form['Month'].value,
    'Job link': form['Job link'].value.trim(),
    Notes: form.Notes.value,
  };
  const btn = $('#btn-save'); btn.disabled = true; btn.textContent = 'Saving…';
  try {
    const id = form.id.value;
    if (id === '') {
      await postJSON('/api/add', payload);
      toast('Application added.');
    } else {
      // Edit: one update per *changed* field (server validates each). Skipping
      // untouched fields means an empty "Response date" isn't re-sent, so it can't
      // clobber a date the server auto-stamps when the Status changes in the same save.
      const orig = DATA.rows[Number(id)] || {};
      // Stale-guard each write with the row's Company (server refuses if the row
      // moved underneath, e.g. the brain ingested mid-edit). If this save changes
      // Company itself, later fields must expect the NEW value we just wrote.
      let expectCompany = orig.Company || '';
      for (const field of Object.keys(payload)) {
        if (String(payload[field] ?? '') === String(orig[field] ?? '')) continue;
        await postJSON('/api/update', { id: Number(id), field, value: payload[field], expect: expectCompany });
        if (field === 'Company') expectCompany = String(payload[field] ?? '');
      }
      toast('Changes saved.');
    }
    closeModal();
    await load();
  } catch (err) {
    toast('Error saving: ' + errMsg(err), true);
  } finally {
    btn.disabled = false; btn.textContent = 'Save';
  }
}

// --- CSV import / export ----------------------------------------------------
async function importCsv(file) {
  if (!file) return;
  if (!confirm('Importing this CSV will REPLACE all current applications. Continue?')) return;
  try {
    const text = await file.text();
    const res = await api('/api/import', {
      method: 'POST',
      headers: { 'Content-Type': 'text/csv; charset=utf-8' },
      body: text,
      timeoutMs: 30000,
    });
    await load();
    toast('Imported ' + ((res && res.count != null) ? res.count : '?') + ' application(s).');
  } catch (err) {
    toast('Error importing: ' + errMsg(err), true);
  }
}

function downloadCsv() {
  const cols = (DATA.columns && DATA.columns.length) ? DATA.columns : Object.keys(DATA.rows[0] || {});
  const cell = (v) => {
    const s = v == null ? '' : String(v);
    return /[",\n\r]/.test(s) ? '"' + s.replace(/"/g, '""') + '"' : s;
  };
  const lines = [cols.map(cell).join(',')];
  DATA.rows.forEach((r) => lines.push(cols.map((c) => cell(r[c])).join(',')));
  // Lead with a BOM so Excel/Sheets open it as UTF-8; parse_csv strips it on re-import.
  const blob = new Blob(['﻿' + lines.join('\r\n') + '\r\n'], { type: 'text/csv;charset=utf-8' });
  const url = URL.createObjectURL(blob);
  const a = make('a');
  a.href = url;
  a.download = 'jobscout-applications-' + new Date().toISOString().slice(0, 10) + '.csv';
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

// --- Utils ------------------------------------------------------------------
function make(tag, cls, text) {
  const el = document.createElement(tag);
  if (cls) el.className = cls;
  if (text != null) el.textContent = text;
  return el;
}
function el(id) { return document.getElementById(id); }
function cssVar(name) { return getComputedStyle(document.documentElement).getPropertyValue(name).trim() || '#888'; }

function tally(list) { const o = {}; list.forEach((v) => { if (v) o[v] = (o[v] || 0) + 1; }); return o; }
function byKeyOrdered(rows, key, order) {
  const c = tally(rows.map((r) => r[key]));
  const labels = (order || []).filter((l) => c[l]);
  Object.keys(c).forEach((k) => { if (labels.indexOf(k) === -1) labels.push(k); });
  return { labels, data: labels.map((l) => c[l]) };
}

function fillSelect(sel, options, keepFirst) {
  if (!sel) return;
  if (!keepFirst) sel.innerHTML = '';
  (options || []).forEach((o) => { const opt = document.createElement('option'); opt.value = o; opt.textContent = o; sel.appendChild(opt); });
}
function populateFilter() {
  const fe = $('#filter-status');
  const prev = fe.value;  // preserve the active filter across a rebuild
  fe.innerHTML = '<option value="">All statuses</option>';
  // "Potential" is the Review tab, not a Tracker filter — leave it out of the dropdown.
  DATA.statuses.filter((e) => e !== POTENTIAL).forEach((e) => {
    const o = document.createElement('option'); o.value = e; o.textContent = e; fe.appendChild(o);
  });
  if (prev && prev !== POTENTIAL && DATA.statuses.indexOf(prev) !== -1) fe.value = prev;
}

function monthFromDate(d) {
  const m = /^\d{1,2}-(\d{1,2})-\d{4}$/.exec(String(d || ''));
  if (m) { const i = parseInt(m[1], 10); if (i >= 1 && i <= 12) return MONTH_ORDER[i - 1]; }
  return '';
}
function monthIdx(m) { const i = MONTH_ORDER.indexOf(m); return i === -1 ? 99 : i; }
function dateKey(d) {
  const m = /^(\d{1,2})-(\d{1,2})-(\d{4})$/.exec(String(d || ''));
  return m ? Number(m[3] + m[2].padStart(2, '0') + m[1].padStart(2, '0')) : 0;
}
function toInputDate(d) { // DD-MM-YYYY -> YYYY-MM-DD
  const m = /^(\d{1,2})-(\d{1,2})-(\d{4})$/.exec(String(d || ''));
  return m ? m[3] + '-' + m[2].padStart(2, '0') + '-' + m[1].padStart(2, '0') : '';
}
function toStoreDate(d) { // YYYY-MM-DD -> DD-MM-YYYY
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(String(d || ''));
  return m ? m[3] + '-' + m[2] + '-' + m[1] : (d || '');
}

function normSource(s) {
  const t = String(s || '').toLowerCase();
  if (!t) return 'Unknown';
  if (t.indexOf('job finder') !== -1 || t.indexOf('jobscout') !== -1 || t.indexOf('brain') !== -1) return 'Job finder';
  if (t.indexOf('linkedin') !== -1 || t.indexOf('linked') !== -1) return 'LinkedIn';
  if (t.indexOf('indeed') !== -1) return 'Indeed';
  if (t.indexOf('remoteok') !== -1 || t.indexOf('remote ok') !== -1) return 'RemoteOK';
  if (t.indexOf('site') !== -1 || t.indexOf('career') !== -1) return 'Company site';
  if (t.indexOf('job') !== -1) return 'Job board';
  if (t.indexOf('email') !== -1) return 'Email';
  return String(s).trim();
}

function errMsg(err) { return (err && err.message) ? err.message : String(err); }

let toastTimer;
function toast(msg, isError) {
  const t = $('#toast');
  t.textContent = msg;
  t.className = 'toast show' + (isError ? ' error' : '');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { t.className = 'toast' + (isError ? ' error' : ''); }, 3000);
}
