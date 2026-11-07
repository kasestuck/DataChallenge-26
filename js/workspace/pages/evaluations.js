// AI Evaluations: список всех оценок (поиск, язык, статус, диапазон баллов, версия чек-листа) и страница результата.
// Итоговый балл приходит с Backend (total_score / final_total_score) и на Frontend НЕ пересчитывается.
import { t, applyDom } from '../../../src/i18n/index.js';
import { getMode, getApiBase, setMode, isAbort, getEvaluations, getEvaluation, getChecklists, getAppeals } from '../../api.js';
import { esc, icon, num, tr, langTag, opCell, statusBadge, scoreIndicator, grossBadge, dateTime, criteriaCard, errorState, emptyState, mockBanner, loadingState } from '../ui.js';
import { mountList } from '../listPage.js';

const LANG_OPTS = [['', 'ws.f.all'], ['ru', 'ws.lang.ru'], ['kk', 'ws.lang.kk'], ['en', 'ws.lang.en']];
// Кнопка «Подать апелляцию» в карточках критериев (форма — appealForm.js)
const APPEALS_ON = true;
const RANGES = { '90-100': [90, 100], '75-89': [75, 89], '60-74': [60, 74], '0-59': [0, 59] };

// ======================= список =======================
export function mountEvaluations(root, ctx) {
  let versions = [];
  let inner = null, dead = false;
  // версии чек-листа нужны для фильтра: подгружаем до построения списка; при ошибке фильтр остаётся без версий
  getChecklists().then((r) => { versions = (r.items || []).map((c) => c.version).sort((a, b) => b - a); }).catch(() => {}).finally(() => {
    if (dead) return;
    inner = mountList(root, ctx, {
      titleKey: 'ws.nav.evaluations', searchKey: 'ws.evl.search', sort: { key: 'created_at', order: 'desc' },
      // диапазон баллов передаётся в API как score_min/score_max
      fetch({ range, ...p }, opts) {
        const r = RANGES[range];
        return getEvaluations({ ...p, ...(r ? { score_min: r[0], score_max: r[1] } : {}) }, opts);
      },
      rowHref: (e) => `#/dashboard/evaluations/${encodeURIComponent(e.id)}`,
      emptyKeys: { title: 'ws.evl.emptyTitle', text: 'ws.evl.emptyText' },
      filters: [
        { name: 'language', labelKey: 'ws.f.language', options: LANG_OPTS },
        { name: 'status', labelKey: 'ws.f.status', options: [['', 'ws.f.all'], ...['running', 'done', 'failed'].map((s) => [s, `ws.status.eval.${s}`])] },
        { name: 'range', labelKey: 'ws.evl.range', options: [['', 'ws.f.all'], ...Object.keys(RANGES).map((k) => [k, k.replace('-', '–')])] },
        { name: 'checklist_version', labelKey: 'ws.evl.version', options: [['', 'ws.f.all'], ...versions.map((v) => [String(v), `v${v}`])] },
        { name: 'gross', labelKey: 'ws.col.gross', options: [['', 'ws.f.all'], ['1', 'ws.evl.onlyGross']] },
      ],
      columns: () => [
        { label: t('ws.evl.colId'), sortKey: 'created_at', render: (e) => `<a class="row__id" href="#/dashboard/evaluations/${encodeURIComponent(e.id)}">${esc(e.id)}</a>` },
        { label: t('ws.evl.colDialog'), sortKey: 'conversation_id', render: (e) => `<a href="#/dashboard/conversations/${encodeURIComponent(e.conversation_id)}">${esc(e.conversation_id)}</a>` },
        { label: t('ws.col.language'), render: (e) => langTag(e.language) },
        { label: t('ws.col.operator'), render: (e) => opCell(e.operator_id) },
        { label: t('ws.evl.version'), render: (e) => `v${num(e.checklist_version)}` },
        { label: t('ws.col.status'), render: (e) => statusBadge('eval', e.status) },
        { label: t('ws.eval.total'), sortKey: 'total_score', render: (e) => scoreIndicator(e.total_score) },
        { label: t('ws.col.gross'), render: (e) => (e.gross_violation ? grossBadge() : '<span class="muted">—</span>') },
        { label: t('ws.col.date'), sortKey: 'created_at', render: (e) => `<time datetime="${esc(e.created_at)}">${esc(dateTime(e.created_at))}</time>` },
      ],
    });
  });
  return {
    relang() { inner?.relang(); },
    destroy() { dead = true; inner?.destroy(); root.replaceChildren(); },
  };
}

// ======================= результат оценки =======================
export function mountEvaluation(root, ctx) {
  const id = ctx.id;
  let ev = null, err = null, appeals = [], ac = new AbortController(), pollT = 0;

  root.innerHTML = `<div class="cd"><a class="back" href="#/dashboard/evaluations">${icon('arrow-left')}<span data-i18n="ws.evl.back"></span></a><div data-body>${loadingState(0)}</div></div>`;
  applyDom(root);
  const body = root.querySelector('[data-body]');

  async function load(silent = false) {
    ac.abort(); ac = new AbortController();
    if (!silent) body.innerHTML = loadingState(0);
    try {
      ev = await getEvaluation(id, { signal: ac.signal }); err = null;
      // апелляции по этой оценке: чтобы показать, по каким критериям уже подана апелляция
      appeals = (await getAppeals({ conversation_id: ev.conversation_id, page_size: 100 }, { signal: ac.signal }).catch(() => ({ items: [] }))).items || [];
    } catch (e) {
      if (isAbort(e)) return;
      ev = null; err = e;
    }
    render();
    if (ev?.status === 'running') { clearTimeout(pollT); pollT = setTimeout(() => load(true), 1000); }
  }

  function render() {
    if (err) {
      const nf = err.kind === 'http' && err.status === 404;
      body.innerHTML = nf ? emptyState(t('ws.evl.notFoundTitle'), t('ws.evl.notFoundText', { id })) : errorState(err, { mode: getMode(), base: getApiBase() });
      return;
    }
    ctx.setTitle(`${t('ws.nav.evaluations')} · ${ev.id}`);
    const changed = ev.final_total_score != null && ev.final_total_score !== ev.total_score;
    const open = new Set(appeals.filter((a) => a.evaluation_id === ev.id && a.status === 'pending').map((a) => a.criterion_id));
    let main = '';
    if (ev.status === 'running') main = `<div class="state state--run" aria-busy="true"><span class="spin" aria-hidden="true"></span><h3>${esc(t('ws.eval.runningTitle'))}</h3><p>${esc(t('ws.eval.runningText'))}</p></div>`;
    else if (ev.status === 'failed') main = `<div class="state state--error" role="alert">${icon('alert', 'ico--lg')}<h3>${esc(t('ws.eval.failTitle'))}</h3><p>${esc(ev.error?.message || t('ws.state.aiDown'))}</p></div>`;
    else {
      const done = (ev.criteria || []).filter((c) => !c.na).length, na = (ev.criteria || []).length - done;
      main = `<div class="evp__total"><div><span class="muted">${esc(t('ws.eval.total'))}</span>${scoreIndicator(ev.total_score)}${changed ? `<span class="evp__final">${esc(t('ws.eval.afterAppeals'))}: <b>${esc(num(ev.final_total_score))}</b></span>` : ''}</div>
          <div class="evp__flag evp__flag--${ev.gross_violation ? 'bad' : 'ok'}">${ev.gross_violation ? `${icon('alert')}${esc(t('ws.gross.found'))}` : `${icon('check-circle')}${esc(t('ws.gross.none'))}`}</div></div>
        <p class="form-hint">${esc(t('ws.eval.fromBackend'))} · ${esc(t('ws.evl.counts', { n: num(done), na: num(na) }))}</p>
        <div class="crit-list">${(ev.criteria || []).map((c) => criteriaCard(c, { conversationId: ev.conversation_id, appeal: APPEALS_ON, appealed: open })).join('')}</div>`;
    }
    body.innerHTML = `<div class="cd__head"><div class="cd__id"><h2>${esc(ev.id)}</h2>${statusBadge('eval', ev.status)}${ev.gross_violation ? grossBadge() : ''}</div>
        <dl class="cd__meta"><div><dt>${esc(t('ws.evl.colDialog'))}</dt><dd><a href="#/dashboard/conversations/${encodeURIComponent(ev.conversation_id)}">${esc(ev.conversation_id)}</a></dd></div>
        <div><dt>${esc(t('ws.col.language'))}</dt><dd>${langTag(ev.language)} ${esc(tr(`ws.lang.${ev.language}`, ev.language))}</dd></div>
        <div><dt>${esc(t('ws.col.operator'))}</dt><dd>${opCell(ev.operator_id)}</dd></div>
        <div><dt>${esc(t('ws.evl.version'))}</dt><dd>${esc(t('ws.eval.versionN', { n: num(ev.checklist_version) }))}</dd></div>
        <div><dt>${esc(t('ws.col.date'))}</dt><dd>${esc(dateTime(ev.created_at))}</dd></div></dl></div>
      ${ev.source === 'mock' ? mockBanner() : ''}
      <section class="panel evp-page"><header class="pane__head"><h3>${icon('sparkle')}${esc(t('ws.evl.result'))}</h3>
        <a class="btn btn--ghost btn--sm" href="#/dashboard/conversations/${encodeURIComponent(ev.conversation_id)}">${esc(t('ws.evl.openDialog'))}</a></header>${main}</section>`;
  }

  function onClick(e) {
    const act = e.target.closest('[data-act]')?.dataset.act;
    if (act === 'retry') load();
    else if (act === 'to-demo') setMode('demo');
    else if (e.target.closest('[data-appeal]')) openAppeal(e.target.closest('[data-appeal]').dataset.appeal);
  }
  async function openAppeal(criterionId) {
    const { openAppealForm } = await import('../appealForm.js');
    openAppealForm({ evaluation: ev, criterionId, onDone: () => load(true) });
  }
  root.addEventListener('click', onClick);
  load();

  return {
    relang() { applyDom(root); if (ev || err) render(); },
    destroy() { ac.abort(); clearTimeout(pollT); root.removeEventListener('click', onClick); root.replaceChildren(); },
  };
}
