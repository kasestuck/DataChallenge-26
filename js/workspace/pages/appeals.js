// Appeals: список апелляций и страница рассмотрения (исходная оценка, evidence, комментарий, решение проверяющего).
// Исходная оценка не изменяется: решение записывается отдельно, итоговый балл возвращает Backend.
import { t, applyDom } from '../../../src/i18n/index.js';
import { getMode, getApiBase, setMode, isAbort, getAppeals, getAppeal, decideAppeal } from '../../api.js';
import { esc, icon, num, loc, opCell, statusBadge, grossBadge, dateTime, evidenceLink, errorState, errorText, emptyState, mockBanner, loadingState } from '../ui.js';
import { mountList } from '../listPage.js';
import { confirmDialog, toast } from '../overlay.js';

const href = (id) => `#/dashboard/appeals/${encodeURIComponent(id)}`;
const short = (s, n = 70) => (String(s).length > n ? `${String(s).slice(0, n).trimEnd()}…` : String(s));

// Реальный Backend принимает не «новый балл», а новый ОТВЕТ по критерию (yes/no или ключ шкалы); баллы считает сам Backend.
function newValueField(a) {
  const c = a.criterion;
  if (c?.answer_key == null) return `<label class="field" data-newscore><span>${esc(t('ws.apl.newScore'))} (0–${esc(num(a.max_score))})</span><input type="number" name="new_score" min="0" max="${esc(a.max_score)}" step="0.5" value="${esc(a.original_score)}" inputmode="decimal"></label>`;
  const keys = c.answer_type === 'scale' ? [...(c.scale_keys || [])].sort((x, y) => Number(y) - Number(x)) : ['yes', 'no'];
  const label = (k) => (c.answer_type === 'scale' ? k : t(k === 'yes' ? 'ws.ans.yes' : 'ws.ans.no'));
  return `<label class="field" data-newscore><span>${esc(t('ws.apl.finalAnswer'))}</span><select name="final_answer">${keys.map((k) => `<option value="${esc(k)}"${k === c.answer_key ? ' selected' : ''}>${esc(label(k))}</option>`).join('')}</select></label>`;
}

// ======================= список =======================
export function mountAppeals(root, ctx) {
  return mountList(root, ctx, {
    titleKey: 'ws.nav.appeals', searchKey: 'ws.apl.search', sort: { key: 'created_at', order: 'desc' },
    fetch: getAppeals,
    rowHref: (a) => href(a.id),
    emptyKeys: { title: 'ws.apl.emptyTitle', text: 'ws.apl.emptyText' },
    filters: [{ name: 'status', labelKey: 'ws.f.status', options: [['', 'ws.f.all'], ...['pending', 'approved', 'rejected'].map((s) => [s, `ws.status.appeal.${s}`])] }],
    columns: () => [
      { label: t('ws.apl.colId'), render: (a) => `<a class="row__id" href="${href(a.id)}">${esc(a.id)}</a>` },
      { label: t('ws.evl.colDialog'), render: (a) => `<a href="#/dashboard/conversations/${encodeURIComponent(a.conversation_id)}">${esc(a.conversation_id)}</a>` },
      { label: t('ws.apl.criterion'), render: (a) => esc(loc(a.criterion_name)) },
      { label: t('ws.apl.origShort'), sortKey: 'original_score', cls: 'num', render: (a) => `${esc(num(a.original_score, 1))} / ${esc(num(a.max_score))}` },
      { label: t('ws.col.status'), sortKey: 'status', render: (a) => statusBadge('appeal', a.status) },
      { label: t('ws.apl.comment'), render: (a) => `<span class="cut" title="${esc(a.comment)}">${esc(short(a.comment))}</span>` },
      { label: t('ws.col.date'), sortKey: 'created_at', render: (a) => `<time datetime="${esc(a.created_at)}">${esc(dateTime(a.created_at))}</time>` },
    ],
  });
}

// ======================= рассмотрение =======================
export function mountAppealDetail(root, ctx) {
  const id = ctx.id;
  let a = null, err = null, ac = new AbortController(), busy = false;
  root.innerHTML = `<div class="cd"><a class="back" href="#/dashboard/appeals">${icon('arrow-left')}<span data-i18n="ws.apl.back"></span></a><div data-body>${loadingState(0)}</div></div>`;
  applyDom(root);
  const body = root.querySelector('[data-body]');

  async function load(silent = false) {
    ac.abort(); ac = new AbortController();
    if (!silent) body.innerHTML = loadingState(0);
    try { a = await getAppeal(id, { signal: ac.signal }); err = null; } catch (e) { if (isAbort(e)) return; a = null; err = e; }
    render();
  }

  function render() {
    if (err) {
      const nf = err.kind === 'http' && err.status === 404;
      body.innerHTML = nf ? emptyState(t('ws.apl.notFoundTitle'), t('ws.apl.notFoundText', { id })) : errorState(err, { mode: getMode(), base: getApiBase() });
      return;
    }
    ctx.setTitle(`${t('ws.nav.appeals')} · ${a.id}`);
    const c = a.criterion;
    const dec = a.decision;
    const ev = c?.evidence?.length
      ? `<ul class="crit__evid">${c.evidence.map((e) => `<li>${evidenceLink(a.conversation_id, e, e.quote)}</li>`).join('')}</ul>`
      : `<p class="crit__noev">${esc(t('ws.crit.noEvidence'))}</p>`;
    const review = a.status === 'pending' ? `<section class="panel"><header><h2>${esc(t('ws.apl.review'))}</h2><p>${esc(t('ws.apl.reviewNote'))}</p></header>
        <form data-form novalidate><fieldset class="seg-field"><legend>${esc(t('ws.apl.decision'))}</legend>
          <label class="radio"><input type="radio" name="decision" value="approved" checked><span>${esc(t('ws.status.appeal.approved'))}</span></label>
          <label class="radio"><input type="radio" name="decision" value="rejected"><span>${esc(t('ws.status.appeal.rejected'))}</span></label></fieldset>
          ${newValueField(a)}
          <label class="field"><span>${esc(t('ws.apl.reason'))}</span><textarea name="reason" rows="3" maxlength="1000" placeholder="${esc(t('ws.apl.reasonPh'))}"></textarea></label>
          <p class="form-error" data-err hidden role="alert"></p>
          <div class="form-actions"><button class="btn" type="submit" data-submit>${esc(t('ws.apl.submitDecision'))}</button></div></form></section>`
      : `<section class="panel"><header><h2>${esc(t('ws.apl.decisionTitle'))}</h2></header>
          <dl class="kv"><div><dt>${esc(t('ws.col.status'))}</dt><dd>${statusBadge('appeal', a.status)}</dd></div>
          <div><dt>${esc(t('ws.apl.reviewer'))}</dt><dd>${esc(dec?.reviewer || '—')}</dd></div>
          <div><dt>${esc(t('ws.apl.decidedAt'))}</dt><dd>${esc(dec?.decided_at ? dateTime(dec.decided_at) : '—')}</dd></div>
          <div><dt>${esc(t('ws.apl.origShort'))}</dt><dd>${esc(num(a.original_score, 1))} / ${esc(num(a.max_score))}</dd></div>
          <div><dt>${esc(t('ws.apl.newScoreShort'))}</dt><dd>${dec?.new_score != null ? `<b>${esc(num(dec.new_score, 1))}</b> / ${esc(num(a.max_score))}` : esc(t('ws.apl.unchanged'))}</dd></div></dl>
          <p class="orig__exp"><b>${esc(t('ws.apl.reason'))}:</b> ${esc(dec?.reason || '—')}</p></section>`;
    body.innerHTML = `<div class="cd__head"><div class="cd__id"><h2>${esc(a.id)}</h2>${statusBadge('appeal', a.status)}</div>
        <dl class="cd__meta"><div><dt>${esc(t('ws.evl.colDialog'))}</dt><dd><a href="#/dashboard/conversations/${encodeURIComponent(a.conversation_id)}">${esc(a.conversation_id)}</a></dd></div>
        <div><dt>${esc(t('ws.evl.colId'))}</dt><dd><a href="#/dashboard/evaluations/${encodeURIComponent(a.evaluation_id)}">${esc(a.evaluation_id)}</a></dd></div>
        <div><dt>${esc(t('ws.col.operator'))}</dt><dd>${opCell(a.operator_id)}</dd></div>
        <div><dt>${esc(t('ws.col.date'))}</dt><dd>${esc(dateTime(a.created_at))}</dd></div></dl></div>
      ${a.meta?.source === 'mock' ? mockBanner() : ''}
      <div class="opr__grid">
        <section class="panel"><header><h2>${esc(t('ws.apl.originalTitle'))}</h2></header>
          <h3 class="sub">${esc(loc(a.criterion_name))}</h3>
          <dl class="kv"><div><dt>${esc(t('ws.apl.origShort'))}</dt><dd><b>${esc(num(a.original_score, 1))}</b> / ${esc(num(a.max_score))}</dd></div>
            <div><dt>${esc(t('ws.eval.total'))}</dt><dd>${esc(num(a.original_total_score))}${a.final_total_score != null && a.final_total_score !== a.original_total_score ? ` → <b>${esc(num(a.final_total_score))}</b>` : ''}</dd></div></dl>
          ${c?.explanation ? `<p class="orig__exp">${esc(c.explanation)}</p>` : ''}${ev}
          ${c?.gross ? `<p>${grossBadge()}</p>` : ''}</section>
        <section class="panel"><header><h2>${esc(t('ws.apl.commentTitle'))}</h2></header><p class="orig__exp quote">${esc(a.comment)}</p><p class="form-hint">${esc(t('ws.apl.noMutate'))}</p></section>
        <div class="opr__wide">${review}</div></div>`;
  }

  async function onSubmit(e) {
    const form = e.target.closest('[data-form]');
    if (!form) return;
    e.preventDefault();
    if (busy) return;
    const er = form.querySelector('[data-err]');
    er.hidden = true;
    const decision = form.elements.decision.value;
    const reason = form.elements.reason.value.trim();
    const useAnswer = !!form.elements.final_answer;
    const newScore = useAnswer ? NaN : Number(form.elements.new_score.value);
    const fail = (msg) => { er.hidden = false; er.textContent = msg; };
    if (!reason) { fail(t('ws.apl.reasonRequired')); form.elements.reason.focus(); return; }
    if (decision === 'approved' && !useAnswer && (form.elements.new_score.value === '' || !Number.isFinite(newScore) || newScore < 0 || newScore > a.max_score)) { fail(t('ws.apl.scoreRange', { max: num(a.max_score) })); form.elements.new_score.focus(); return; }
    const ok = await confirmDialog({ title: t('ws.apl.confirmTitle'), text: t(decision === 'approved' ? 'ws.apl.confirmApprove' : 'ws.apl.confirmReject', { id: a.id }), confirm: t('ws.common.confirm') });
    if (!ok) return;
    busy = true; form.querySelector('[data-submit]').disabled = true;
    try {
      a = await decideAppeal(id, { decision, reason, ...(useAnswer ? { original_answer: a.criterion.answer_key } : {}), ...(decision === 'approved' ? (useAnswer ? { final_answer: form.elements.final_answer.value } : { new_score: newScore }) : {}) });
      toast(t('ws.apl.decided'));
      render();
    } catch (x) {
      if (isAbort(x)) return;
      fail(x.kind === 'http' && x.status === 409 ? t('ws.apl.alreadyDecided') : errorText(x, getApiBase()));
      form.querySelector('[data-submit]').disabled = false;
    } finally { busy = false; }
  }
  function onChange(e) {
    if (e.target.name !== 'decision') return;
    const box = root.querySelector('[data-newscore]');
    if (box) box.hidden = e.target.value !== 'approved';
  }
  function onClick(e) {
    const act = e.target.closest('[data-act]')?.dataset.act;
    if (act === 'retry') load(); else if (act === 'to-demo') setMode('demo');
  }
  root.addEventListener('submit', onSubmit);
  root.addEventListener('change', onChange);
  root.addEventListener('click', onClick);
  load();
  return {
    relang() { applyDom(root); if (a || err) render(); },
    destroy() { ac.abort(); root.removeEventListener('submit', onSubmit); root.removeEventListener('change', onChange); root.removeEventListener('click', onClick); root.replaceChildren(); },
  };
}
