// Overview: KPI, динамика качества, распределение оценок, языки, последние диалоги и апелляции.
// Итоговые баллы и счётчики приходят от Backend (или Mock Adapter) и на frontend не пересчитываются.
import { t, applyDom } from '../../../src/i18n/index.js';
import { getOverview, getMode, getApiBase, setMode, isAbort } from '../../api.js';
import { esc, icon, num, band, scoreBadge, chip, langTag, shortDate, dayLabel, loadingState, emptyState, errorState, lineChart } from '../ui.js';

const tr = (key, fallback) => { const v = t(key); return v === key ? fallback : v; };
const link = (section) => `#/dashboard/${section}`;
const clock = (d) => `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`;

const SHELL = `
<div class="ov">
  <div class="ov__bar">
    <p class="ov__sub" data-i18n="ws.ov.subtitle"></p>
    <div class="ov__tools">
      <div class="seg" role="group" data-i18n-attr="aria-label:ws.ov.period.label">
        <button type="button" data-days="7" data-i18n="ws.ov.period.d7"></button>
        <button type="button" data-days="30" data-i18n="ws.ov.period.d30"></button>
        <button type="button" data-days="90" data-i18n="ws.ov.period.d90"></button>
      </div>
      <button class="iconbtn" type="button" data-act="refresh" data-i18n-attr="aria-label:ws.state.refresh;title:ws.state.refresh"><svg class="ico" aria-hidden="true"><use href="#i-refresh"/></svg></button>
      <span class="ov__updated" role="status"></span>
    </div>
  </div>
  <p class="banner" hidden><svg class="ico" aria-hidden="true"><use href="#i-alert"/></svg><span data-i18n="ws.mock.banner"></span></p>
  <div class="ov__content"></div>
</div>`;

export function mountOverview(root, ctx) {
  let days = 30, data = null, err = null, updatedAt = null, ac = null, q = ctx.query();
  root.innerHTML = SHELL;
  applyDom(root);
  const $ = (s) => root.querySelector(s);
  const content = $('.ov__content'), banner = $('.banner'), stamp = $('.ov__updated'), refreshBtn = $('[data-act="refresh"]');
  const segs = [...root.querySelectorAll('[data-days]')];
  const syncSegs = () => segs.forEach((b) => b.setAttribute('aria-pressed', String(Number(b.dataset.days) === days)));

  async function load() {
    ac?.abort();
    const mine = (ac = new AbortController());            // устаревший запрос отменяется
    refreshBtn.disabled = true;
    banner.hidden = true;
    stamp.textContent = '';
    content.innerHTML = loadingState();
    try {
      const res = await getOverview({ days, signal: mine.signal });
      if (mine.signal.aborted) return;
      data = res; err = null; updatedAt = new Date();
    } catch (e) {
      if (isAbort(e)) return;
      data = null; err = e;
    }
    refreshBtn.disabled = false;
    render();
  }

  function kpiCard({ key, ico, value, note, href, delta, tone = '' }) {
    const d = delta == null ? '' : `<span class="wk-delta wk-delta--${delta >= 0 ? 'up' : 'down'}" title="${esc(t('ws.ov.kpi.vsPrev'))}">${delta >= 0 ? '▲ +' : '▼ −'}${esc(num(Math.abs(delta), 1))}</span>`;
    return `<a class="wk-kpi ${tone}" href="${href}" aria-label="${esc(t(`ws.ov.kpi.${key}`))}: ${esc(value)}. ${esc(t('ws.ov.kpi.open'))}">
      <span class="wk-kpi__ico">${icon(ico)}</span>
      <span class="wk-kpi__label">${esc(t(`ws.ov.kpi.${key}`))}</span>
      <span class="wk-kpi__value">${esc(value)}</span>
      <span class="wk-kpi__foot">${note ? `<span>${esc(note)}</span>` : ''}${d}</span>
    </a>`;
  }

  const trendPanel = () => {
    const pts = (data.trend || []).filter((p) => p.avg_score != null).map((p) => ({ label: dayLabel(p.date), value: p.avg_score }));
    const body = pts.length
      ? lineChart(pts, { label: `${t('ws.ov.chart.trend')}: ${pts[0].label} — ${pts[pts.length - 1].label}` })
      : `<p class="muted">${esc(t('ws.ov.chart.noData'))}</p>`;
    return `<section class="panel panel--trend"><header><h2>${esc(t('ws.ov.chart.trend'))}</h2><p>${esc(t('ws.ov.chart.trendNote'))}</p></header>${body}</section>`;
  };

  const distPanel = () => {
    const b = data.score_distribution || [];
    const max = Math.max(1, ...b.map((x) => x.count));
    const body = b.length
      ? `<ul class="bars">${b.map((x) => `<li class="bar bar--${band(x.max)}"><span class="bar__n">${esc(num(x.count))}</span><span class="bar__track"><i style="height:${Math.max(2, Math.round((x.count / max) * 100))}%"></i></span><span class="bar__l">${x.min}–${x.max}</span></li>`).join('')}</ul>`
      : `<p class="muted">${esc(t('ws.ov.chart.noData'))}</p>`;
    return `<section class="panel panel--dist"><header><h2>${esc(t('ws.ov.chart.distribution'))}</h2><p>${esc(t('ws.ov.chart.distNote'))}</p></header>${body}</section>`;
  };

  const langPanel = () => {
    const l = data.by_language || [];
    const total = Math.max(1, l.reduce((s, x) => s + x.count, 0));
    const body = l.length
      ? `<ul class="langs">${l.map((x) => `<li><span class="langs__name">${langTag(x.language)}<span>${esc(tr(`ws.lang.${x.language}`, x.language))}</span></span>
          <span class="langs__bar"><i style="width:${Math.round((x.count / total) * 100)}%"></i></span>
          <span class="langs__n">${esc(t('ws.ov.chart.count', { n: num(x.count) }))}</span><span class="langs__avg">${esc(t('ws.ov.chart.avgShort', { n: num(x.avg_score, 1) }))}</span></li>`).join('')}</ul>`
      : `<p class="muted">${esc(t('ws.ov.chart.noData'))}</p>`;
    return `<section class="panel panel--lang"><header><h2>${esc(t('ws.ov.chart.languages'))}</h2><p>${esc(t('ws.ov.chart.langNote'))}</p></header>${body}</section>`;
  };

  const listPanel = (cls, title, section, kind) => `<section class="panel panel--${cls}"><header class="panel__head"><h2>${esc(title)}</h2>
    <a class="more" href="${link(section)}">${esc(t('ws.ov.recent.all'))} ${icon('arrow')}</a></header><div class="rows" data-list="${kind}"></div></section>`;

  function render() {
    if (err) {
      content.innerHTML = errorState(err, { mode: getMode(), base: getApiBase() });
      return;
    }
    banner.hidden = data.meta?.source !== 'mock';
    stamp.textContent = t('ws.state.updated', { time: clock(updatedAt) });
    const k = data.kpis || {};
    if (!k.evaluated_count) {
      content.innerHTML = emptyState(t('ws.state.emptyTitle'), t('ws.state.emptyText'));
      return;
    }
    content.innerHTML = `
      <div class="wk-kpis">
        ${kpiCard({ key: 'avg', ico: 'trend', value: num(k.avg_score, 1), note: t('ws.ov.kpi.avgNote'), href: link('evaluations'), delta: k.avg_score_delta })}
        ${kpiCard({ key: 'evaluated', ico: 'chat', value: num(k.evaluated_count), href: link('conversations') })}
        ${kpiCard({ key: 'violations', ico: 'alert', value: num(k.gross_violations_count), href: `${link('evaluations')}?gross=1`, tone: k.gross_violations_count > 0 ? 'wk-kpi--danger' : '' })}
        ${kpiCard({ key: 'appeals', ico: 'quote', value: num(k.pending_appeals_count), href: `${link('appeals')}?status=pending`, tone: k.pending_appeals_count > 0 ? 'wk-kpi--warn' : '' })}
      </div>
      <div class="ov__grid">
        ${trendPanel()}${distPanel()}
        ${listPanel('conv', t('ws.ov.recent.conversations'), 'conversations', 'conv')}
        ${langPanel()}
        ${listPanel('appeals', t('ws.ov.recent.appeals'), 'appeals', 'appeals')}
      </div>`;
    fillLists();
  }

  const match = (...fields) => !q || fields.some((f) => String(f ?? '').toLowerCase().includes(q.toLowerCase()));

  function fillLists() {
    if (!data || err) return;
    const convEl = $('[data-list="conv"]'), apEl = $('[data-list="appeals"]');
    if (!convEl || !apEl) return;
    const op = (id) => (id ? esc(id) : `<em class="muted">${esc(t('ws.unassigned'))}</em>`);
    const conv = (data.recent_conversations || []).filter((c) => match(c.id, c.operator_id));
    const aps = (data.recent_appeals || []).filter((a) => match(a.id, a.conversation_id, a.operator_id));
    const none = (msg) => `<p class="rows__empty">${esc(q ? t('ws.ov.recent.noMatch', { q }) : msg)}</p>`;
    convEl.innerHTML = conv.length ? conv.map((c) => `<a class="row row--conv" href="${link(`conversations/${encodeURIComponent(c.id)}`)}">
        <span class="row__id">${esc(c.id)}</span>${langTag(c.language)}
        <span class="row__meta">${esc(tr(`ws.channel.${c.channel}`, c.channel))}</span>
        <span class="row__op">${op(c.operator_id)}</span>${scoreBadge(c.score)}
        ${chip(c.status, tr(`ws.status.conv.${c.status}`, c.status))}<time class="row__date" datetime="${esc(c.evaluated_at)}">${esc(shortDate(c.evaluated_at))}</time></a>`).join('')
      : none(t('ws.ov.recent.emptyConv'));
    apEl.innerHTML = aps.length ? aps.map((a) => `<a class="row row--appeal" href="${link(`appeals/${encodeURIComponent(a.id)}`)}">
        <span class="row__id">${esc(a.conversation_id)}</span>
        <span class="row__op">${op(a.operator_id)}</span>
        ${chip(`ap-${a.status}`, tr(`ws.status.appeal.${a.status}`, a.status))}<time class="row__date" datetime="${esc(a.created_at)}">${esc(shortDate(a.created_at))}</time></a>`).join('')
      : none(t('ws.ov.recent.emptyAppeals'));
  }

  function onClick(e) {
    const day = e.target.closest('[data-days]');
    if (day) { days = Number(day.dataset.days); syncSegs(); load(); return; }
    const act = e.target.closest('[data-act]')?.dataset.act;
    if (act === 'refresh' || act === 'retry') load();
    else if (act === 'to-demo') setMode('demo');
  }
  root.addEventListener('click', onClick);
  syncSegs();
  load();

  return {
    search(next) { q = next; fillLists(); },
    relang() { applyDom(root); if (data || err) render(); },
    destroy() { ac?.abort(); root.removeEventListener('click', onClick); root.replaceChildren(); },
  };
}
