// Общие UI-хелперы Workspace: экранирование, иконки, состояния (loading / empty / error), бейджи, SVG-график.
// Графики строятся один раз на данные (строка SVG), без постоянной перерисовки и анимаций.
import { t, formatNumber, formatDate } from '../../src/i18n/index.js';

export const esc = (v) => String(v ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
export const icon = (name, cls = '') => `<svg class="ico ${cls}" aria-hidden="true"><use href="#i-${name}"/></svg>`;
export const num = (n, digits = 0) => (n == null ? '—' : formatNumber(n, { minimumFractionDigits: digits, maximumFractionDigits: digits }));

// уровень оценки — только оформление; сам балл приходит от Backend и на frontend не пересчитывается
export const band = (score) => (score == null ? 'none' : score >= 90 ? 'top' : score >= 75 ? 'good' : score >= 60 ? 'mid' : 'low');
export const scoreBadge = (score) => `<span class="score score--${band(score)}">${num(score)}</span>`;

export const shortDate = (iso) => formatDate(iso, { day: 'numeric', month: 'short' });
export const dayLabel = (iso) => formatDate(`${String(iso).slice(0, 10)}T12:00:00`, { day: 'numeric', month: 'short' });

export const chip = (kind, label) => `<span class="chip chip--${esc(kind)}">${esc(label)}</span>`;
export const langTag = (l) => `<span class="ltag" lang="${esc(l)}">${esc(String(l).toUpperCase())}</span>`;

// ---------- состояния ----------
export function loadingState(blocks = 4) {
  return `<div class="state-loading" aria-busy="true"><p class="sr-only" role="status">${esc(t('ws.state.loading'))}</p>
    <div class="skel-row">${'<i class="skel skel--kpi"></i>'.repeat(blocks)}</div>
    <div class="skel-row skel-row--2"><i class="skel skel--chart"></i><i class="skel skel--chart"></i></div></div>`;
}
export function emptyState(title, text) {
  return `<div class="state state--empty">${icon('inbox', 'ico--lg')}<h3>${esc(title)}</h3><p>${esc(text)}</p></div>`;
}
export function errorState(err, { mode, base }) {
  const text = err?.kind === 'http' ? t('ws.state.errorHttp', { code: err.status }) : t('ws.state.errorText', { url: base });
  return `<div class="state state--error" role="alert">${icon('alert', 'ico--lg')}<h3>${esc(t('ws.state.errorTitle'))}</h3><p>${esc(text)}</p>
    <div class="state__actions"><button class="btn" type="button" data-act="retry">${esc(t('ws.state.retry'))}</button>
    ${mode === 'api' ? `<button class="btn btn--ghost" type="button" data-act="to-demo">${esc(t('ws.mode.toDemo'))}</button>` : ''}</div></div>`;
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
