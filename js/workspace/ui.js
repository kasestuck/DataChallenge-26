// Общие UI-компоненты Workspace (возвращают HTML-строки; обработчики вешаются делегированием на корень страницы).
// SearchInput, FilterSelect, DataTable, Pagination, StatusBadge, ScoreIndicator, CriteriaCard, EvidenceLink,
// EmptyState, ErrorState, LoadingSkeleton + SVG-график. Модальные окна и Toast — в overlay.js.
// Графики строятся один раз на данные, без постоянной перерисовки и анимаций.
import { t, getLang, formatNumber, formatDate } from '../../src/i18n/index.js';

export const esc = (v) => String(v ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
export const icon = (name, cls = '') => `<svg class="ico ${cls}" aria-hidden="true"><use href="#i-${name}"/></svg>`;
export const num = (n, digits = 0) => (n == null ? '—' : formatNumber(n, { minimumFractionDigits: digits, maximumFractionDigits: digits }));
// t() с запасным значением для неизвестных enum Backend
export const tr = (key, fallback) => { const v = t(key); return v === key ? fallback : v; };
// имя/описание критерия: строка или { ru, kk, en } (предложение контракта для многоязычных чек-листов)
export const loc = (v) => (v && typeof v === 'object' ? v[getLang()] || v.ru || Object.values(v)[0] || '' : v ?? '');

// уровень оценки — только оформление; сам балл приходит от Backend и на frontend не пересчитывается
export const band = (score) => (score == null ? 'none' : score >= 90 ? 'top' : score >= 75 ? 'good' : score >= 60 ? 'mid' : 'low');
export const scoreBadge = (score) => `<span class="score score--${band(score)}">${num(score)}</span>`;
export const shortDate = (iso) => formatDate(iso, { day: 'numeric', month: 'short' });
export const dayLabel = (iso) => formatDate(`${String(iso).slice(0, 10)}T12:00:00`, { day: 'numeric', month: 'short' });
export const clock = (d) => `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`;
export const dateTime = (iso) => { if (iso == null || iso === '') return '—'; const d = new Date(iso); return Number.isNaN(+d) ? '—' : `${formatDate(d, { day: 'numeric', month: 'short' })} ${clock(d)}`; };

export const chip = (kind, label) => `<span class="chip chip--${esc(kind)}">${esc(label)}</span>`;
export const langTag = (l) => l == null || l === '' ? '<span class="muted">—</span>' : `<span class="ltag" lang="${esc(l)}">${esc(String(l).toUpperCase())}</span>`;
export const opCell = (id) => (id ? esc(id) : `<em class="muted">${esc(t('ws.unassigned'))}</em>`);

// ---------- StatusBadge / ScoreIndicator ----------
// kind: 'conv' | 'appeal' | 'eval'
export function statusBadge(kind, status) {
  return chip(kind === 'appeal' ? `ap-${status}` : kind === 'eval' ? `ev-${status}` : status, tr(`ws.status.${kind}.${status}`, status));
}
export function scoreIndicator(score, { label = '' } = {}) {
  if (score == null) return `<span class="muted">—</span>`;
  return `<span class="sind" title="${esc(label)}"><b class="score score--${band(score)}">${num(score)}</b><i class="sind__bar"><i style="width:${clamp(score)}%" class="sind__fill sind__fill--${band(score)}"></i></i></span>`;
}
const clamp = (v) => Math.min(100, Math.max(0, v));
export const grossBadge = () => `<span class="chip chip--gross" title="${esc(t('ws.gross.title'))}">${icon('alert')}${esc(t('ws.gross.short'))}</span>`;

// ---------- SearchInput / FilterSelect ----------
export const searchInput = ({ value = '', placeholder, label }) =>
  `<label class="search"><span class="sr-only">${esc(label)}</span>${icon('search')}<input type="search" data-q value="${esc(value)}" placeholder="${esc(placeholder)}" autocomplete="off"></label>`;
export const filterSelect = ({ name, value = '', label, options }) =>
  `<label class="fsel"><span>${esc(label)}</span><select data-filter="${esc(name)}">${options.map(([v, l]) => `<option value="${esc(v)}"${String(v) === String(value) ? ' selected' : ''}>${esc(l)}</option>`).join('')}</select></label>`;

// ---------- Pagination ----------
export function pagination({ page, page_size, total }) {
  const pages = Math.max(1, Math.ceil(total / page_size));
  const from = total ? (page - 1) * page_size + 1 : 0, to = Math.min(total, page * page_size);
  return `<nav class="pager" aria-label="${esc(t('ws.pager.label'))}"><span class="pager__info">${esc(t('ws.pager.range', { from: num(from), to: num(to), total: num(total) }))}</span>
    <span class="pager__ctl"><button class="iconbtn" type="button" data-page="${page - 1}" ${page <= 1 ? 'disabled' : ''} aria-label="${esc(t('ws.pager.prev'))}">${icon('chevron-left')}</button>
    <span class="pager__cur">${esc(t('ws.pager.page', { page: num(page), pages: num(pages) }))}</span>
    <button class="iconbtn" type="button" data-page="${page + 1}" ${page >= pages ? 'disabled' : ''} aria-label="${esc(t('ws.pager.next'))}">${icon('chevron-right')}</button></span></nav>`;
}

// ---------- DataTable ----------
// columns: [{ label, sortKey?, cls?, render(row) }]; rowHref(row) → ссылка для перехода (клик по строке и ссылка в ячейке)
export function dataTable({ columns, rows, sort, rowHref, label }) {
  const head = columns.map((c) => {
    const act = c.sortKey && sort?.key === c.sortKey;
    return `<th scope="col" class="${c.cls || ''}"${act ? ` aria-sort="${sort.order === 'asc' ? 'ascending' : 'descending'}"` : ''}>${c.sortKey
      ? `<button type="button" class="dt__sort${act ? ' is-on' : ''}" data-sort="${esc(c.sortKey)}">${esc(c.label)}<i aria-hidden="true">${act ? (sort.order === 'asc' ? '▲' : '▼') : '↕'}</i></button>` : esc(c.label)}</th>`;
  }).join('');
  const body = rows.map((r) => `<tr${rowHref ? ` data-href="${esc(rowHref(r))}"` : ''}>${columns.map((c) => `<td data-label="${esc(c.label)}" class="${c.cls || ''}">${c.render(r)}</td>`).join('')}</tr>`).join('');
  return `<div class="dt-wrap"><table class="dt" aria-label="${esc(label || '')}"><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table></div>`;
}

// ---------- состояния ----------
export const loadingState = (blocks = 4) => `<div class="state-loading" aria-busy="true"><p class="sr-only" role="status">${esc(t('ws.state.loading'))}</p>
  <div class="skel-row">${'<i class="skel skel--kpi"></i>'.repeat(blocks)}</div><div class="skel-row skel-row--2"><i class="skel skel--chart"></i><i class="skel skel--chart"></i></div></div>`;
export const loadingList = () => `<div class="state-loading" aria-busy="true"><p class="sr-only" role="status">${esc(t('ws.state.loading'))}</p><i class="skel skel--table"></i></div>`;
export const emptyState = (title, text, action = '') => `<div class="state state--empty">${icon('inbox', 'ico--lg')}<h3>${esc(title)}</h3><p>${esc(text)}</p>${action}</div>`;

export function errorText(err, base = '') {
  if (err?.kind === 'unsupported') return t('ws.state.unsupported', { what: err.detail || '' });
  if (err?.kind === 'http') {
    const key = { 404: 'ws.state.notFound', 409: 'ws.state.conflict', 422: 'ws.state.invalid', 503: 'ws.state.aiDown', 502: 'ws.state.modelDown', 429: 'ws.state.modelDown', 504: 'ws.state.modelDown' }[err.status];
    const summary = key ? t(key) : t('ws.state.errorHttp', { code: err.status });
    return `${summary}${err.code ? ` [${err.code}]` : ''}${err.detail && err.detail !== summary ? ` ${err.detail}` : ''}`;
  }
  if (err?.kind === 'timeout') return t('ws.state.errorTimeout', { url: base });
  return t('ws.state.errorText', { url: base });
}
export function errorState(err, { mode, base }, { retry = true } = {}) {
  const message = errorText(err, base);
  return `<div class="state state--error" role="alert">${icon('alert', 'ico--lg')}<h3>${esc(t('ws.state.errorTitle'))}</h3><p>${esc(message)}</p>
    ${err?.detail && !message.includes(String(err.detail)) ? `<p class="state__detail">${esc(err.detail)}</p>` : ''}
    <div class="state__actions">${retry ? `<button class="btn" type="button" data-act="retry">${esc(t('ws.state.retry'))}</button>` : ''}
    ${mode === 'api' ? `<button class="btn btn--ghost" type="button" data-act="to-demo">${esc(t('ws.mode.toDemo'))}</button>` : ''}</div></div>`;
}
export const mockBanner = () => `<p class="banner">${icon('alert')}<span>${esc(t('ws.mock.banner'))}</span></p>`;

// ---------- EvidenceLink ----------
export const evidenceHref = (conversationId, messageId) => `#/dashboard/conversations/${encodeURIComponent(conversationId)}?evidence=${encodeURIComponent(messageId)}`;
export function evidenceLink(conversationId, ev, text) {
  return `<a class="evid" href="${evidenceHref(conversationId, ev.message_id)}" data-evidence="${esc(ev.message_id)}">${icon('quote')}<span class="evid__id">${esc(ev.message_id)}</span>${text ? `<span class="evid__q">${esc(text)}</span>` : ''}</a>`;
}

// ---------- CriteriaCard ----------
export function answerText(c) {
  if (c.invalid || (c.answer == null && !c.na)) return t('ws.contract.unknown');
  if (c.na) return t('ws.crit.na');
  if (c.answer_type === 'flag') return c.answer ? t('ws.crit.violation') : t('ws.crit.noViolation');
  if (c.answer_type === 'yes_no') return c.answer ? t('ws.ans.yes') : t('ws.ans.no');
  return `${num(c.answer)} / ${num(c.scale?.max ?? 5)}`;
}
// opts: { conversationId, appeal: bool, appealed: Set<criterion_id> (уже оспорено/ожидает), showScore }
export function criteriaCard(c, opts = {}) {
  const pointDigits = c.answer_key !== undefined ? 2 : 1;
  const weightDigits = c.answer_key !== undefined ? 2 : 0;
  const rev = c.revised ? `<p class="crit__rev">${icon('check-circle')}${esc(t('ws.crit.revised', { score: c.revised.answer === 'na' ? t('ws.crit.na') : num(c.revised.score, pointDigits) }))}</p>` : '';
  const evid = c.evidence?.length
    ? `<ul class="crit__evid">${c.evidence.map((e) => `<li>${evidenceLink(opts.conversationId, e, e.quote)}</li>`).join('')}</ul>`
    : `<p class="crit__noev">${esc(t('ws.crit.noEvidence'))}</p>`;
  const appealBtn = opts.appeal && !c.invalid && (c.answer_key != null || (!c.na && c.answer != null))
    ? (opts.appealed?.has(c.criterion_id)
      ? `<button class="btn btn--ghost btn--sm" type="button" disabled title="${esc(t('ws.appeal.alreadyTip'))}">${esc(t('ws.appeal.exists'))}</button>`
      : `<button class="btn btn--ghost btn--sm" type="button" data-appeal="${esc(c.criterion_id)}">${esc(t('ws.appeal.open'))}</button>`) : '';
  return `<article class="crit${c.gross ? ' crit--gross' : ''}${c.na ? ' crit--na' : ''}" data-criterion="${esc(c.criterion_id)}">
    <header class="crit__head"><h4>${esc(loc(c.name))}</h4>
      <span class="crit__pills"><span class="pill" title="${esc(t('ws.crit.weight'))}">${esc(t('ws.crit.weightN', { n: num(c.weight, weightDigits) }))}</span><span class="pill pill--type">${esc(tr(`ws.atype.${c.answer_type}`, c.answer_type))}</span>${c.gross ? grossBadge() : ''}</span></header>
    ${c.description ? `<p class="crit__desc">${esc(loc(c.description))}</p>` : ''}
    <div class="crit__res"><span class="crit__ans">${esc(t('ws.crit.answer'))}: <b>${esc(answerText(c))}</b></span>
      ${c.na ? '' : `<span class="crit__pts">${esc(t('ws.crit.points'))}: <b>${c.answer_type === 'flag' ? '—' : `${esc(num(c.score, pointDigits))} / ${esc(num(c.weight, weightDigits))}`}</b></span>`}</div>
    ${c.na ? `<p class="crit__na">${chip('na', t('ws.crit.na'))} ${esc(t('ws.crit.naReason'))}: ${esc(c.na.reason)}</p>` : `<p class="crit__exp">${esc(c.explanation)}</p>`}
    ${evid}${rev}${appealBtn ? `<footer class="crit__foot">${appealBtn}</footer>` : ''}</article>`;
}

// ---------- линейный график (SVG) ----------
let uid = 0;
export function lineChart(points, { label }) {
  const W = 640, H = 230, L = 38, R = 14, T = 14, B = 28;
  const vals = points.map((p) => p.value);
  const lo = Math.max(0, Math.floor((Math.min(...vals) - 4) / 5) * 5);
  const hi = Math.min(100, Math.ceil((Math.max(...vals) + 3) / 5) * 5);
  const span = Math.max(1, hi - lo);
  const x = (i) => (points.length === 1 ? (L + W - R) / 2 : L + (i * (W - L - R)) / (points.length - 1));
  const y = (v) => T + (1 - (v - lo) / span) * (H - T - B);
  const line = points.map((p, i) => `${i ? 'L' : 'M'}${x(i).toFixed(1)} ${y(p.value).toFixed(1)}`).join('');
  const area = `${line}L${x(points.length - 1).toFixed(1)} ${H - B}L${x(0).toFixed(1)} ${H - B}Z`;
  const id = `lg${++uid}`;
  const grid = [0, 1, 2, 3].map((i) => {
    const v = lo + (span * i) / 3, yy = y(v).toFixed(1);
    return `<line x1="${L}" x2="${W - R}" y1="${yy}" y2="${yy}" class="ch-grid"/><text x="${L - 8}" y="${(+yy + 4).toFixed(1)}" class="ch-axis" text-anchor="end">${Math.round(v)}</text>`;
  }).join('');
  const idx = points.length > 2 ? [0, Math.floor((points.length - 1) / 2), points.length - 1] : points.map((_, i) => i);
  const xl = idx.map((i, k) => `<text x="${x(i).toFixed(1)}" y="${H - 8}" class="ch-axis" text-anchor="${k === 0 ? 'start' : k === idx.length - 1 ? 'end' : 'middle'}">${esc(points[i].label)}</text>`).join('');
  const hits = points.map((p, i) => `<circle cx="${x(i).toFixed(1)}" cy="${y(p.value).toFixed(1)}" r="7" class="ch-hit"><title>${esc(p.label)}: ${esc(num(p.value, 1))}</title></circle>`).join('');
  const last = points.length - 1;
  return `<svg class="wk-chart" viewBox="0 0 ${W} ${H}" role="img" aria-label="${esc(label)}" preserveAspectRatio="xMidYMid meet">
    <defs><linearGradient id="${id}" x1="0" x2="0" y1="0" y2="1"><stop offset="0" stop-color="#4fb84f" stop-opacity=".28"/><stop offset="1" stop-color="#4fb84f" stop-opacity="0"/></linearGradient></defs>
    ${grid}<path d="${area}" fill="url(#${id})"/><path d="${line}" class="ch-line"/>
    <circle cx="${x(last).toFixed(1)}" cy="${y(points[last].value).toFixed(1)}" r="4.5" class="ch-dot"/>${xl}${hits}</svg>`;
}
