// Checklist Builder: просмотр версий чек-листа, создание новой версии (черновик → сохранение), редактор критериев,
// проверка суммы весов, переоценка диалогов выбранной версией. Критерии НЕ зашиты в HTML: всё строится из данных API.
// Сохранённые версии не изменяются (immutable): правка всегда порождает новую версию.
// Сумма весов проверяется только как конфигурация (должна быть 100, критерий-флаг не в счёте); баллы диалогов здесь не считаются.
import { t, applyDom, getLang } from '../../../src/i18n/index.js';
import { getMode, getApiBase, setMode, isAbort, getChecklists, saveChecklist, runEvaluation } from '../../api.js';
import { esc, icon, num, loc, tr, dateTime, errorState, errorText, emptyState, mockBanner, loadingState, chip } from '../ui.js';
import { openModal, confirmDialog, toast } from '../overlay.js';

const TYPES = ['yes_no', 'scale', 'flag'];
const clone = (o) => JSON.parse(JSON.stringify(o));
const setLoc = (cur, v) => (cur && typeof cur === 'object' ? { ...cur, [getLang()]: v } : v);
const textOf = (v) => (v && typeof v === 'object' ? loc(v) : String(v ?? ''));
const sumOf = (list) => Math.round(list.filter((c) => c.answer_type !== 'flag').reduce((s, c) => s + (Number(c.weight) || 0), 0) * 100) / 100;

export function mountChecklist(root, ctx) {
  let items = [], sel = null, draft = null, base = null, err = null, loading = true, ac = new AbortController(), saving = false, dirty = false, mock = false, seq = 0;

  root.innerHTML = '<div class="cl" data-body></div>';
  const body = root.querySelector('[data-body]');
  const current = () => items.find((c) => c.version === sel) || items[0];

  async function load(keep = true) {
    ac.abort(); ac = new AbortController();
    loading = true; err = null; render();
    try {
      const r = await getChecklists({ signal: ac.signal });
      items = (r.items || []).slice().sort((a, b) => b.version - a.version);
      mock = r.meta?.source === 'mock';
      if (!keep || !items.some((c) => c.version === sel)) sel = (items.find((c) => c.is_current) || items[0])?.version ?? null;
    } catch (e) { if (isAbort(e)) return; err = e; items = []; }
    loading = false; render();
  }

  // ---------- проверки черновика ----------
  function issues() {
    const out = [];
    const sum = sumOf(draft);
    if (!draft.length) out.push(t('ws.cl.vEmpty'));
    if (sum !== 100) out.push(t('ws.cl.vSum', { sum: num(sum, 2) }));
    draft.forEach((c, i) => {
      const n = i + 1;
      if (!textOf(c.name).trim()) out.push(t('ws.cl.vName', { n }));
      if (c.answer_type !== 'flag' && !(Number(c.weight) >= 0 && Number(c.weight) <= 100)) out.push(t('ws.cl.vWeight', { n }));
      if (c.answer_type === 'scale' && !(Number(c.scale?.min) < Number(c.scale?.max))) out.push(t('ws.cl.vScale', { n }));
      if (c.na_allowed && !textOf(c.na_condition).trim()) out.push(t('ws.cl.vNa', { n }));
    });
    return out;
  }
  function refreshStatus() {
    const box = body.querySelector('[data-status]');
    if (!box || !draft) return;
    const list = issues();
    const sum = sumOf(draft);
    box.innerHTML = `<div class="sumbar sumbar--${sum === 100 ? 'ok' : 'bad'}" role="status"><b>${esc(t('ws.cl.sum'))}: ${esc(num(sum, 2))} / 100</b><i><i style="width:${Math.min(100, sum)}%"></i></i></div>
      ${list.length ? `<ul class="form-errs" aria-live="polite">${list.map((x) => `<li>${esc(x)}</li>`).join('')}</ul>` : `<p class="form-ok">${esc(t('ws.cl.valid'))}</p>`}`;
    const btn = body.querySelector('[data-act="save"]');
    if (btn) btn.disabled = saving || list.length > 0;
  }

  // ---------- отрисовка ----------
  const critView = (c) => `<article class="crit${c.gross || c.answer_type === 'flag' ? ' crit--gross' : ''}">
      <header class="crit__head"><h4>${esc(textOf(c.name))}</h4><span class="crit__pills">${c.answer_type === 'flag' ? '' : `<span class="pill">${esc(t('ws.crit.weightN', { n: num(c.weight) }))}</span>`}<span class="pill pill--type">${esc(tr(`ws.atype.${c.answer_type}`, c.answer_type))}</span>${c.builtin ? `<span class="pill">${esc(t('ws.cl.builtin'))}</span>` : `<span class="pill pill--custom">${esc(t('ws.cl.custom'))}</span>`}</span></header>
      ${c.description ? `<p class="crit__desc">${esc(textOf(c.description))}</p>` : ''}
      <p class="crit__res">${c.answer_type === 'scale' ? `<span>${esc(t('ws.cl.scale'))}: <b>${esc(num(c.scale?.min ?? 0))}–${esc(num(c.scale?.max ?? 5))}</b></span>` : ''}<span>${esc(t('ws.cl.naAllowed'))}: <b>${esc(t(c.na_allowed ? 'ws.common.yes' : 'ws.common.no'))}</b></span></p>
      ${c.na_allowed && textOf(c.na_condition) ? `<p class="crit__exp"><b>${esc(t('ws.cl.naCond'))}:</b> ${esc(textOf(c.na_condition))}</p>` : ''}</article>`;

  const critEdit = (c, i) => {
    const flag = c.answer_type === 'flag';
    return `<fieldset class="ce" data-i="${i}"><legend>${esc(t('ws.cl.critN', { n: i + 1 }))}${c.builtin ? ` · ${esc(t('ws.cl.builtin'))}` : ` · ${esc(t('ws.cl.custom'))}`}</legend>
      <label class="field"><span>${esc(t('ws.cl.name'))}</span><input type="text" data-f="name" value="${esc(textOf(c.name))}" maxlength="120" required></label>
      <label class="field"><span>${esc(t('ws.cl.desc'))}</span><textarea data-f="description" rows="2" maxlength="500">${esc(textOf(c.description))}</textarea></label>
      <div class="ce__row">
        <label class="field"><span>${esc(t('ws.cl.type'))}</span><select data-f="answer_type">${TYPES.map((x) => `<option value="${x}"${c.answer_type === x ? ' selected' : ''}>${esc(tr(`ws.atype.${x}`, x))}</option>`).join('')}</select></label>
        <label class="field"><span>${esc(t('ws.cl.weight'))}</span><input type="number" data-f="weight" min="0" max="100" step="0.5" value="${esc(flag ? 0 : c.weight)}" ${flag ? 'disabled' : ''} inputmode="decimal"></label>
        ${c.answer_type === 'scale' ? `<label class="field"><span>${esc(t('ws.cl.scaleMin'))}</span><input type="number" data-f="scale_min" step="1" value="${esc(c.scale?.min ?? 0)}"></label><label class="field"><span>${esc(t('ws.cl.scaleMax'))}</span><input type="number" data-f="scale_max" step="1" value="${esc(c.scale?.max ?? 5)}"></label>` : ''}
      </div>
      ${flag ? `<p class="form-hint">${esc(t('ws.cl.flagHint'))}</p>` : ''}
      <label class="radio ce__na"><input type="checkbox" data-f="na_allowed"${c.na_allowed ? ' checked' : ''}><span>${esc(t('ws.cl.naAllowed'))}</span></label>
      ${c.na_allowed ? `<label class="field"><span>${esc(t('ws.cl.naCond'))}</span><input type="text" data-f="na_condition" value="${esc(textOf(c.na_condition))}" maxlength="240" placeholder="${esc(t('ws.cl.naCondPh'))}"></label>` : ''}
      <div class="ce__foot">${c.builtin ? `<span class="form-hint">${esc(t('ws.cl.noDelete'))}</span>` : `<button class="btn btn--ghost btn--sm" type="button" data-act="del" data-i="${i}">${icon('trash')}${esc(t('ws.common.delete'))}</button>`}</div></fieldset>`;
  };

  function render() {
    if (loading) { body.innerHTML = loadingState(0); return; }
    if (err) { body.innerHTML = errorState(err, { mode: getMode(), base: getApiBase() }); return; }
    if (!items.length) { body.innerHTML = emptyState(t('ws.cl.emptyTitle'), t('ws.cl.emptyText')); return; }
    const cur = current();
    if (draft) {
      body.innerHTML = `${mock ? mockBanner() : ''}<div class="cd__head"><div class="cd__id"><h2>${esc(t('ws.cl.draftTitle', { n: num(items[0].version + 1) }))}</h2>${chip('evaluating', t('ws.cl.draft'))}</div><p class="muted">${esc(t('ws.cl.basedOn', { n: num(base) }))}</p></div>
        <div class="ce-wrap" data-edit>${draft.map(critEdit).join('')}
          <div class="ce__add"><button class="btn btn--ghost" type="button" data-act="add">${icon('plus')}${esc(t('ws.cl.add'))}</button></div>
          <label class="field"><span>${esc(t('ws.cl.note'))}</span><textarea data-note rows="2" maxlength="300" placeholder="${esc(t('ws.cl.notePh'))}"></textarea></label>
          <div data-status></div>
          <div class="form-actions"><button class="btn btn--ghost" type="button" data-act="cancel">${esc(t('ws.common.cancel'))}</button><button class="btn" type="button" data-act="save">${esc(saving ? t('ws.cl.saving') : t('ws.cl.save'))}</button></div></div>`;
      const note = body.querySelector('[data-note]'); if (note) note.value = draft.__note || '';
      refreshStatus();
      return;
    }
    const sum = sumOf(cur.criteria || []);
    body.innerHTML = `${mock ? mockBanner() : ''}<div class="lp__bar cl__bar">
        <label class="fsel"><span>${esc(t('ws.cl.version'))}</span><select data-ver>${items.map((c) => `<option value="${c.version}"${c.version === cur.version ? ' selected' : ''}>v${num(c.version)}${c.is_current ? ` · ${esc(t('ws.eval.current'))}` : ''}</option>`).join('')}</select></label>
        <div class="lp__extra"><button class="btn btn--ghost" type="button" data-act="reeval">${icon('refresh')}${esc(t('ws.cl.reeval'))}</button><button class="btn" type="button" data-act="new">${icon('plus')}${esc(t('ws.cl.newVersion', { n: num(cur.version) }))}</button></div></div>
      <div class="cd__head"><div class="cd__id"><h2>${esc(t('ws.cl.title', { n: num(cur.version) }))}</h2>${cur.is_current ? chip('evaluated', t('ws.eval.current')) : ''}</div>
        <dl class="cd__meta"><div><dt>${esc(t('ws.col.date'))}</dt><dd>${esc(dateTime(cur.created_at))}</dd></div><div><dt>${esc(t('ws.cl.count'))}</dt><dd>${num((cur.criteria || []).length)}</dd></div><div><dt>${esc(t('ws.cl.sum'))}</dt><dd>${esc(num(sum, 2))} / 100</dd></div></dl></div>
      ${cur.note ? `<p class="form-hint">${esc(t('ws.cl.note'))}: ${esc(cur.note)}</p>` : ''}
      <p class="form-hint">${esc(t('ws.cl.immutable'))}</p>
      <div class="crit-list">${(cur.criteria || []).map(critView).join('')}</div>`;
  }

  // ---------- действия ----------
  function startDraft() {
    const cur = current();
    base = cur.version;
    draft = clone(cur.criteria || []);
    draft.__note = '';
    dirty = false; render();
    body.querySelector('[data-f]')?.focus();
  }
  function addCriterion() {
    const used = new Set(draft.map((c) => c.id));
    let n = draft.length + 1; while (used.has(`x${n}`)) n++;
    draft.push({ id: `x${n}`, name: '', description: '', weight: 0, answer_type: 'yes_no', scale: null, na_allowed: false, na_condition: '', gross: false, builtin: false });
    dirty = true; render();
    const last = body.querySelectorAll('.ce'); last[last.length - 1]?.querySelector('[data-f="name"]')?.focus();
    last[last.length - 1]?.scrollIntoView({ block: 'center' });
  }
  async function removeCriterion(i) {
    const c = draft[i];
    if (!c || c.builtin) return;
    const ok = await confirmDialog({ title: t('ws.cl.delTitle'), text: t('ws.cl.delText', { name: textOf(c.name) || t('ws.cl.critN', { n: i + 1 }) }), confirm: t('ws.common.delete'), danger: true });
    if (!ok) return;
    draft.splice(i, 1); dirty = true; render();
  }
  async function cancelDraft() {
    if (dirty && !(await confirmDialog({ title: t('ws.cl.cancelTitle'), text: t('ws.cl.cancelText'), confirm: t('ws.cl.discard') }))) return;
    draft = null; dirty = false; render();
  }
  async function save() {
    if (saving || issues().length) return;
    saving = true; render();
    try {
      const payload = { base_version: base, note: (draft.__note || '').trim(), criteria: draft.map((c) => ({ ...c, name: c.name, weight: c.answer_type === 'flag' ? 0 : Number(c.weight) || 0, gross: c.answer_type === 'flag' ? true : !!c.gross, scale: c.answer_type === 'scale' ? { min: Number(c.scale?.min ?? 0), max: Number(c.scale?.max ?? 5) } : null, na_condition: c.na_allowed ? c.na_condition : '' })) };
      const saved = await saveChecklist(payload);
      draft = null; dirty = false; saving = false;
      toast(t('ws.cl.saved', { n: num(saved.version) }));
      sel = saved.version; await load(true);
    } catch (e) {
      if (isAbort(e)) return;
      saving = false; render();
      toast(e.kind === 'http' && e.status === 422 ? t('ws.cl.rejected') : errorText(e, getApiBase()), { kind: 'error' });
    }
  }

  // ---------- переоценка выбранной версией ----------
  function openReeval() {
    const cur = current();
    let busy = false, stop = false;
    const m = openModal({
      title: t('ws.cl.reTitle'), wide: true, onClose: () => { stop = true; },
      body: `<p class="modal__text">${esc(t('ws.cl.reText'))}</p>
        <label class="field"><span>${esc(t('ws.cl.version'))}</span><select data-rv>${items.map((c) => `<option value="${c.version}"${c.version === cur.version ? ' selected' : ''}>v${num(c.version)}${c.is_current ? ` · ${esc(t('ws.eval.current'))}` : ''}</option>`).join('')}</select></label>
        <label class="field"><span>${esc(t('ws.cl.reIds'))}</span><textarea data-ids rows="4" placeholder="DEMO-1001, DEMO-1002"></textarea></label>
        <p class="form-hint">${esc(t('ws.cl.reHint'))}</p><ul class="re-res" data-res aria-live="polite"></ul>`,
      actions: `<button class="btn btn--ghost" type="button" data-modal-close>${esc(t('ws.common.close'))}</button><button class="btn" type="button" data-run>${esc(t('ws.cl.reRun'))}</button>`,
    });
    const q = (s) => m.el.querySelector(s);
    q('[data-run]').addEventListener('click', async () => {
      if (busy) return;
      const ids = [...new Set(q('[data-ids]').value.split(/[\s,;]+/).map((s) => s.trim()).filter(Boolean))];
      const res = q('[data-res]');
      if (!ids.length) { res.innerHTML = `<li class="re--bad">${esc(t('ws.cl.reNone'))}</li>`; return; }
      if (ids.length > 50) { res.innerHTML = `<li class="re--bad">${esc(t('ws.cl.reMax', { n: 50 }))}</li>`; return; }
      busy = true; q('[data-run]').disabled = true; res.innerHTML = '';
      const version = Number(q('[data-rv]').value);
      let ok = 0;
      for (const id of ids) {
        if (stop) break;
        const li = document.createElement('li'); li.textContent = `${id}: …`; res.append(li);
        try {
          await runEvaluation({ conversation_id: id, checklist_version: version });
          li.className = 're--ok'; li.textContent = `${id}: ${t('ws.cl.reOk')}`; ok++;
        } catch (e) {
          if (isAbort(e)) return;
          li.className = 're--bad';
          li.textContent = `${id}: ${e.kind === 'http' && e.status === 409 ? t('ws.cl.reExists') : e.kind === 'http' && e.status === 404 ? t('ws.state.notFound') : errorText(e, getApiBase())}`;
        }
      }
      busy = false; q('[data-run]').disabled = false;
      if (!stop) toast(t('ws.cl.reDone', { ok: num(ok), n: num(ids.length) }), { kind: ok ? 'success' : 'error' });
    });
  }

  // ---------- события ----------
  function onInput(e) {
    if (!draft) return;
    if (e.target.matches('[data-note]')) { draft.__note = e.target.value; return; }
    const f = e.target.dataset?.f, fs = e.target.closest('.ce');
    if (!f || !fs) return;
    const c = draft[Number(fs.dataset.i)];
    const v = e.target.type === 'checkbox' ? e.target.checked : e.target.value;
    if (f === 'name' || f === 'description' || f === 'na_condition') c[f] = setLoc(c[f], v);
    else if (f === 'weight') c.weight = v === '' ? '' : Number(v);
    else if (f === 'scale_min' || f === 'scale_max') c.scale = { min: c.scale?.min ?? 0, max: c.scale?.max ?? 5, [f === 'scale_min' ? 'min' : 'max']: v === '' ? '' : Number(v) };
    else if (f === 'answer_type' || f === 'na_allowed') {
      c[f] = v;
      if (f === 'answer_type') { if (v === 'flag') { c.weight = 0; c.gross = true; } else if (c.gross && !c.builtin) c.gross = false; c.scale = v === 'scale' ? (c.scale || { min: 0, max: 5 }) : null; }
      dirty = true; const st = { y: scrollY }; const idx = fs.dataset.i; render(); scrollTo(0, st.y); body.querySelector(`.ce[data-i="${idx}"] [data-f="${f}"]`)?.focus(); return;
    }
    dirty = true; refreshStatus();
  }
  function onClick(e) {
    const act = e.target.closest('[data-act]')?.dataset.act;
    if (act === 'retry') load(); else if (act === 'to-demo') setMode('demo');
    else if (act === 'new') startDraft();
    else if (act === 'add') addCriterion();
    else if (act === 'del') removeCriterion(Number(e.target.closest('[data-act]').dataset.i));
    else if (act === 'cancel') cancelDraft();
    else if (act === 'save') save();
    else if (act === 'reeval') openReeval();
  }
  function onChange(e) {
    if (e.target.matches('[data-ver]')) { sel = Number(e.target.value); render(); }
    else onInput(e);
  }
  root.addEventListener('input', onInput);
  root.addEventListener('change', onChange);
  root.addEventListener('click', onClick);
  load(false);

  return {
    relang() { applyDom(root); if (!loading) { const y = scrollY; render(); scrollTo(0, y); } },
    destroy() { ac.abort(); seq++; root.removeEventListener('input', onInput); root.removeEventListener('change', onChange); root.removeEventListener('click', onClick); root.replaceChildren(); },
  };
}
