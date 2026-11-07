// Operator Intelligence: список операторов (сортировка по среднему баллу, числу оценок, нарушениям, апелляциям)
// и профиль оператора (динамика качества, сильные/слабые критерии, история диалогов и апелляций).
// Ссылки на профиль строятся только по operator_id, пришедшему из Backend. Если его нет — оператор не выдумывается:
// диалог можно привязать вручную в карточке диалога. В статистику попадают только завершённые оценки.
import { t, applyDom } from '../../../src/i18n/index.js';
import { getMode, getApiBase, setMode, isAbort, getOperators, getOperator } from '../../api.js';
import { esc, icon, num, loc, langTag, statusBadge, scoreIndicator, scoreBadge, grossBadge, dayLabel, dateTime, lineChart, errorState, emptyState, mockBanner, loadingState } from '../ui.js';
import { mountList } from '../listPage.js';

const href = (id) => `#/dashboard/operators/${encodeURIComponent(id)}`;
// Качество — только группировка уже готового среднего балла для индикатора (балл не пересчитывается)
const quality = (avg) => (avg == null ? 'none' : avg >= 85 ? 'high' : avg >= 70 ? 'mid' : 'low');
const qualityChip = (avg) => `<span class="qual qual--${quality(avg)}">${esc(t(`ws.opr.q.${quality(avg)}`))}</span>`;

// ======================= список =======================
export function mountOperators(root, ctx) {
  return mountList(root, ctx, {
    titleKey: 'ws.nav.operators', searchKey: 'ws.opr.search', sort: { key: 'avg_score', order: 'desc' }, pageSize: 25,
    fetch: getOperators,
    rowHref: (o) => href(o.id),
    emptyKeys: { title: 'ws.opr.emptyTitle', text: 'ws.opr.emptyText' },
    filters: [],
    summary: (d) => (d.unassigned_count ? `<p class="banner banner--info">${icon('alert')}<span>${esc(t('ws.opr.unassigned', { n: num(d.unassigned_count) }))} <a href="#/dashboard/conversations?operator_id=none">${esc(t('ws.opr.unassignedLink'))}</a></span></p>` : ''),
    columns: () => [
      { label: t('ws.opr.colOperator'), sortKey: 'id', render: (o) => `<a class="row__id" href="${href(o.id)}">${esc(o.name || o.id)}</a>${o.name ? `<small class="muted"> ${esc(o.id)}</small>` : ''}` },
      { label: t('ws.col.language'), render: (o) => `<span class="ltags">${(o.languages || []).map(langTag).join('')}</span>` },
      { label: t('ws.opr.colEvaluated'), sortKey: 'evaluated_count', cls: 'num', render: (o) => num(o.evaluated_count) },
      { label: t('ws.opr.colAvg'), sortKey: 'avg_score', render: (o) => scoreIndicator(o.avg_score) },
      { label: t('ws.opr.colQuality'), render: (o) => qualityChip(o.avg_score) },
      { label: t('ws.opr.colGross'), sortKey: 'gross_violations_count', cls: 'num', render: (o) => (o.gross_violations_count ? `<b class="neg">${num(o.gross_violations_count)}</b>` : '0') },
      { label: t('ws.opr.colAppeals'), sortKey: 'appeals_count', cls: 'num', render: (o) => num(o.appeals_count) },
    ],
  });
}

// ======================= профиль =======================
export function mountOperator(root, ctx) {
  const id = ctx.id;
  let d = null, err = null, ac = new AbortController();
  root.innerHTML = `<div class="cd"><a class="back" href="#/dashboard/operators">${icon('arrow-left')}<span data-i18n="ws.opr.back"></span></a><div data-body>${loadingState(4)}</div></div>`;
  applyDom(root);
  const body = root.querySelector('[data-body]');

  async function load() {
    ac.abort(); ac = new AbortController();
    body.innerHTML = loadingState(4);
    try { d = await getOperator(id, { signal: ac.signal }); err = null; } catch (e) { if (isAbort(e)) return; d = null; err = e; }
    render();
  }

  const statBar = (c, cls) => `<li class="cstat"><span class="cstat__n">${esc(loc(c.name))}</span><span class="cstat__v">${esc(num(c.avg_pct, 1))}%</span><i class="cstat__bar"><i class="cstat__fill ${cls}" style="width:${Math.min(100, Math.max(0, c.avg_pct))}%"></i></i></li>`;

  function render() {
    if (err) {
      const nf = err.kind === 'http' && err.status === 404;
      body.innerHTML = nf ? emptyState(t('ws.opr.notFoundTitle'), t('ws.opr.notFoundText', { id })) : errorState(err, { mode: getMode(), base: getApiBase() });
      return;
    }
    ctx.setTitle(`${t('ws.nav.operators')} · ${d.name || d.id}`);
    const pts = (d.trend || []).filter((p) => p.avg_score != null).map((p) => ({ label: dayLabel(p.date), value: p.avg_score }));
    const stats = d.criteria_stats || [];
    const strong = stats.slice(0, 3), weak = stats.slice(-3).reverse().filter((c) => !strong.includes(c));
    const kpi = (label, value, cls = '') => `<div class="mini ${cls}"><span>${esc(label)}</span><b>${value}</b></div>`;
    body.innerHTML = `<div class="cd__head"><div class="cd__id"><h2>${esc(d.name || d.id)}</h2>${d.name ? `<span class="muted">${esc(d.id)}</span>` : ''}${qualityChip(d.avg_score)}</div>
        <dl class="cd__meta"><div><dt>${esc(t('ws.col.language'))}</dt><dd><span class="ltags">${(d.languages || []).map(langTag).join('')}</span></dd></div></dl></div>
      ${d.source === 'mock' || d.meta?.source === 'mock' ? mockBanner() : ''}
      <div class="minis">${kpi(t('ws.opr.colAvg'), d.avg_score == null ? '—' : scoreBadge(d.avg_score))}${kpi(t('ws.opr.dialogs'), num(d.conversations_count))}${kpi(t('ws.opr.colEvaluated'), num(d.evaluated_count))}${kpi(t('ws.opr.colGross'), num(d.gross_violations_count), d.gross_violations_count ? 'mini--bad' : '')}${kpi(t('ws.opr.colAppeals'), num(d.appeals_count))}</div>
      ${d.mixed_versions ? `<p class="banner banner--warn" role="note">${icon('alert')}<span>${esc(t('ws.opr.mixed', { v: (d.checklist_versions || []).map((v) => `v${v}`).join(', ') }))}</span></p>` : ''}
      <p class="form-hint">${esc(t('ws.opr.onlyDone'))}</p>
      <div class="opr__grid">
        <section class="panel"><header><h2>${esc(t('ws.opr.trend'))}</h2><p>${esc(t('ws.opr.trendNote'))}</p></header>
          ${pts.length ? lineChart(pts, { label: `${t('ws.opr.trend')}: ${pts[0].label} — ${pts[pts.length - 1].label}` }) : `<p class="rows__empty">${esc(t('ws.ov.chart.noData'))}</p>`}</section>
        <section class="panel"><header><h2>${esc(t('ws.opr.criteria'))}</h2><p>${esc(t('ws.opr.criteriaNote'))}</p></header>
          ${stats.length ? `<h3 class="sub">${esc(t('ws.opr.strong'))}</h3><ul class="cstats">${strong.map((c) => statBar(c, 'cstat__fill--ok')).join('')}</ul>
            ${weak.length ? `<h3 class="sub">${esc(t('ws.opr.weak'))}</h3><ul class="cstats">${weak.map((c) => statBar(c, 'cstat__fill--bad')).join('')}</ul>` : ''}` : `<p class="rows__empty">${esc(t('ws.ov.chart.noData'))}</p>`}</section>
        <section class="panel opr__wide"><header><h2>${esc(t('ws.opr.history'))}</h2><p>${esc(t('ws.opr.historyNote'))}</p></header>
          ${(d.conversations || []).length ? `<div class="dt-wrap"><table class="dt" aria-label="${esc(t('ws.opr.history'))}"><thead><tr><th>${esc(t('ws.col.id'))}</th><th>${esc(t('ws.col.language'))}</th><th>${esc(t('ws.col.status'))}</th><th>${esc(t('ws.col.score'))}</th><th>${esc(t('ws.col.gross'))}</th><th>${esc(t('ws.col.date'))}</th></tr></thead><tbody>
            ${d.conversations.map((c) => `<tr data-href="#/dashboard/conversations/${encodeURIComponent(c.id)}"><td data-label="${esc(t('ws.col.id'))}"><a href="#/dashboard/conversations/${encodeURIComponent(c.id)}">${esc(c.id)}</a></td><td data-label="${esc(t('ws.col.language'))}">${langTag(c.language)}</td><td data-label="${esc(t('ws.col.status'))}">${statusBadge('conv', c.status)}</td><td data-label="${esc(t('ws.col.score'))}">${scoreIndicator(c.score)}</td><td data-label="${esc(t('ws.col.gross'))}">${c.gross_violation ? grossBadge() : '<span class="muted">—</span>'}</td><td data-label="${esc(t('ws.col.date'))}">${esc(dateTime(c.started_at))}</td></tr>`).join('')}</tbody></table></div>` : `<p class="rows__empty">${esc(t('ws.opr.noDialogs'))}</p>`}</section>
        <section class="panel opr__wide"><header><h2>${esc(t('ws.opr.appealsHistory'))}</h2></header>
          ${(d.appeals || []).length ? `<ul class="rows">${d.appeals.map((a) => `<li><a class="row row--ah" href="#/dashboard/appeals/${encodeURIComponent(a.id)}"><span class="row__id">${esc(a.conversation_id)}</span><span class="row__op">${esc(loc(a.criterion_name))}</span>${statusBadge('appeal', a.status)}<span class="row__date">${esc(dateTime(a.created_at))}</span></a></li>`).join('')}</ul>` : `<p class="rows__empty">${esc(t('ws.opr.noAppeals'))}</p>`}</section>
      </div>`;
  }

  function onClick(e) {
    const act = e.target.closest('[data-act]')?.dataset.act;
    if (act === 'retry') load();
    else if (act === 'to-demo') setMode('demo');
    else { const row = e.target.closest('tr[data-href]'); if (row && !e.target.closest('a')) ctx.nav(row.dataset.href); }
  }
  root.addEventListener('click', onClick);
  load();
  return {
    relang() { applyDom(root); if (d || err) render(); },
    destroy() { ac.abort(); root.removeEventListener('click', onClick); root.replaceChildren(); },
  };
}
