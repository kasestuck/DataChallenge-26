// Форма создания апелляции (Modal): выбор критерия, просмотр исходной оценки и evidence, комментарий, отправка.
// Исходная оценка не меняется: апелляция создаётся отдельной сущностью, итог пересматривает проверяющий.
import { t } from '../../src/i18n/index.js';
import { createAppeal, isAbort, getApiBase, getMode } from '../api.js';
import { esc, num, loc, evidenceHref, errorText } from './ui.js';
import { openModal, toast } from './overlay.js';

export const MIN_COMMENT = 10;

// evaluation: готовая оценка с criteria[]; criterionId — предвыбранный критерий; onDone(appeal) — после успешной отправки
export function openAppealForm({ evaluation, criterionId, onDone }) {
  const minimumComment = getMode() === 'api' ? 1 : MIN_COMMENT;
  const list = (evaluation.criteria || []).filter((c) => getMode() === 'api' ? !c.invalid && c.answer_key != null : !c.na);
  if (!list.length) { toast(t('ws.apl.noCriteria'), { kind: 'error' }); return null; }
  const cur = list.find((c) => c.criterion_id === criterionId) || list[0];
  let busy = false;
  const m = openModal({
    title: t('ws.apl.formTitle'), wide: true,
    body: `<p class="modal__text">${esc(t('ws.apl.formText', { id: evaluation.conversation_id }))}</p>
      <label class="field"><span>${esc(t('ws.apl.criterion'))}</span><select data-crit>${list.map((c) => `<option value="${esc(c.criterion_id)}"${c.criterion_id === cur.criterion_id ? ' selected' : ''}>${esc(loc(c.name))}</option>`).join('')}</select></label>
      <div data-orig></div>
      <label class="field"><span>${esc(t('ws.apl.comment'))}</span><textarea data-comment rows="4" maxlength="${getMode() === 'api' ? 20000 : 1000}" placeholder="${esc(t('ws.apl.commentPh'))}" aria-describedby="apl-hint"></textarea></label>
      <p class="form-hint" id="apl-hint" data-hint>${esc(t('ws.apl.commentHint', { n: minimumComment }))}</p>
      <p class="form-error" data-err hidden role="alert"></p>`,
    actions: `<button class="btn btn--ghost" type="button" data-modal-close>${esc(t('ws.common.cancel'))}</button><button class="btn" type="button" data-send>${esc(t('ws.apl.send'))}</button>`,
  });
  const q = (s) => m.el.querySelector(s);
  const crit = () => list.find((c) => c.criterion_id === q('[data-crit]').value);
  function renderOrig() {
    const c = crit();
    q('[data-orig]').innerHTML = `<div class="orig"><div class="orig__row"><span>${esc(t('ws.apl.original'))}</span><b>${c.na ? esc(t('ws.crit.na')) : c.answer_type === 'flag' ? esc(c.answer ? t('ws.crit.violation') : t('ws.crit.noViolation')) : `${esc(num(c.score, getMode() === 'api' ? 2 : 1))} / ${esc(num(c.weight, getMode() === 'api' ? 2 : 0))}`}</b></div>
      ${c.explanation ? `<p class="orig__exp">${esc(c.explanation)}</p>` : ''}
      ${(c.evidence || []).length ? `<ul class="crit__evid">${c.evidence.map((e) => `<li><a class="evid" href="${evidenceHref(evaluation.conversation_id, e.message_id)}" target="_blank" rel="noopener"><span class="evid__id">${esc(e.message_id)}</span><span class="evid__q">${esc(e.quote || '')}</span></a></li>`).join('')}</ul>` : `<p class="crit__noev">${esc(t('ws.crit.noEvidence'))}</p>`}</div>`;
  }
  renderOrig();
  q('[data-crit]').addEventListener('change', renderOrig);
  q('[data-send]').addEventListener('click', async () => {
    if (busy) return;
    const comment = q('[data-comment]').value.trim();
    const er = q('[data-err]');
    er.hidden = true;
    if (comment.length < minimumComment) { er.hidden = false; er.textContent = t('ws.apl.commentHint', { n: minimumComment }); q('[data-comment]').focus(); return; }
    busy = true; q('[data-send]').disabled = true; q('[data-send]').textContent = t('ws.apl.sending');
    try {
      const a = await createAppeal({ evaluation_id: evaluation.id, criterion_id: crit().criterion_id, comment });
      m.close('ok');
      toast(t('ws.apl.sent'));
      onDone?.(a);
    } catch (e) {
      if (isAbort(e)) return;
      busy = false; q('[data-send]').disabled = false; q('[data-send]').textContent = t('ws.apl.send');
      er.hidden = false;
      er.textContent = errorText(e, getApiBase());
    }
  });
  return m;
}
