// Conversations: список диалогов (поиск, фильтры, сортировка, пагинация, импорт) и карточка диалога
// (транскрипт + AI-оценка, evidence navigation по стабильному message_id, аудио только при наличии audio_url).
import { t, applyDom } from '../../../src/i18n/index.js';
import { getMode, getApiBase, setMode, isAbort, getConversations, getConversation, getChecklists, getEvaluation, runEvaluation, importConversations, getImportRecords, assignOperator, getOperators, getAppeals } from '../../api.js';
import { esc, icon, num, tr, loc, chip, langTag, opCell, statusBadge, scoreIndicator, grossBadge, dateTime, criteriaCard, errorState, errorText, emptyState, mockBanner } from '../ui.js';
import { mountList } from '../listPage.js';
import { openModal, toast } from '../overlay.js';
import { usageSummary } from './usage.js';

const LANG_OPTS = [['', 'ws.f.all'], ['ru', 'ws.lang.ru'], ['kk', 'ws.lang.kk'], ['en', 'ws.lang.en']];
const channelCell = (c) => `<span class="chan">${icon(c === 'call' ? 'headset' : 'chat')}${esc(tr(`ws.channel.${c}`, c))}</span>`;
const reduced = () => matchMedia('(prefers-reduced-motion: reduce)').matches;

// ======================= список =======================
export function mountConversations(root, ctx) {
  const knownChannels = new Set();
  const channelOptions = (data, selected) => {
    (data?.meta?.available_channels || []).forEach((value) => knownChannels.add(value));
    if (selected) knownChannels.add(selected);
    return [['', 'ws.f.all'], ...[...knownChannels].sort().map((value) => [value, ['chat', 'call'].includes(value) ? `ws.channel.${value}` : value])];
  };
  return mountList(root, ctx, {
    titleKey: 'ws.nav.conversations', searchKey: 'ws.conv.search', sort: { key: 'started_at', order: 'desc' },
    fetch: getConversations,
    rowHref: (c) => `#/dashboard/conversations/${encodeURIComponent(c.id)}`,
    emptyKeys: { title: 'ws.conv.emptyTitle', text: 'ws.conv.emptyText' },
    filters: [
      { name: 'language', labelKey: 'ws.f.language', options: LANG_OPTS },
      { name: 'channel', labelKey: 'ws.f.channel', options: getMode() === 'api' ? channelOptions : [['', 'ws.f.all'], ['chat', 'ws.channel.chat'], ['call', 'ws.channel.call']] },
      { name: 'status', labelKey: 'ws.f.status', options: [['', 'ws.f.all'], ...['new', 'evaluating', 'evaluated', 'needs_review', 'appealed', 'reviewed', 'failed'].map((s) => [s, `ws.status.conv.${s}`])] },
      ...(getMode() === 'api' ? [] : [{ name: 'operator_id', labelKey: 'ws.f.operator', options: [['', 'ws.f.all'], ['none', 'ws.unassigned']] }]),
    ],
    columns: () => [
      { label: t('ws.col.id'), sortKey: 'id', render: (c) => `<a class="row__id" href="#/dashboard/conversations/${encodeURIComponent(c.id)}">${esc(c.id)}</a>` },
      { label: t('ws.col.language'), render: (c) => langTag(c.language) },
      { label: t('ws.col.channel'), render: (c) => channelCell(c.channel) },
      { label: t('ws.col.operator'), render: (c) => getMode() === 'api' ? `<em class="muted">${esc(t('ws.contract.operatorMissing'))}</em>` : opCell(c.operator_id) },
      { label: t('ws.col.status'), render: (c) => statusBadge('conv', c.status) },
      { label: t('ws.col.score'), sortKey: 'score', render: (c) => scoreIndicator(c.score) },
      { label: t('ws.col.gross'), render: (c) => (c.gross_violation ? grossBadge() : '<span class="muted">—</span>') },
      { label: t('ws.col.date'), sortKey: 'started_at', render: (c) => (c.started_at ? `<time datetime="${esc(c.started_at)}">${esc(dateTime(c.started_at))}</time>` : '<span class="muted">—</span>') },
    ],
    toolbarExtra: () => `<button class="btn btn--ghost" type="button" data-act="import">${icon('upload')}${esc(t('ws.conv.import'))}</button>`,
    onClick(e, { reload }) {
      if (!e.target.closest('[data-act="import"]')) return false;
      openImport(reload);
      return true;
    },
  });
}

// Real adapter validates each record and POSTs it to /api/conversations/upload.
function openImport(reload) {
  const sample = getMode() === 'api'
    ? '{"records":[{"conversation_id":"upload-example","language":"RU","channel":"agent chat","audio_url":null,"transcript":[{"timestamp":"00:01","speaker":"client","text":"…"}]}]}'
    : '[{"language":"ru","channel":"chat","messages":[{"role":"client","text":"…"},{"role":"operator","text":"…"}]}]';
  const m = openModal({
    title: t('ws.conv.importTitle'), wide: true,
    body: `<p class="modal__text">${esc(t(getMode() === 'api' ? 'ws.contract.importText' : 'ws.conv.importText'))}</p><pre class="code">${esc(sample)}</pre>
      <label class="field"><span>${esc(t('ws.conv.importFile'))}</span><input type="file" accept="application/json,.json" data-file></label>
      <p class="form-hint" data-info></p><div data-result></div>`,
    actions: `<button class="btn btn--ghost" type="button" data-modal-close>${esc(t('ws.common.cancel'))}</button><button class="btn" type="button" data-run disabled>${esc(t('ws.conv.importRun'))}</button>`,
  });
  const $ = (s) => m.el.querySelector(s);
  let payload = null, busy = false;
  m.el.querySelectorAll('[data-modal-close]').forEach((b) => b.addEventListener('click', () => m.close('x')));
  $('[data-file]').addEventListener('change', async (e) => {
    payload = null; $('[data-run]').disabled = true; $('[data-result]').innerHTML = '';
    const f = e.target.files?.[0];
    if (!f) return;
    try {
      const j = JSON.parse(await f.text());
      const arr = getImportRecords(j);
      if (!Array.isArray(arr) || !arr.length) throw new Error('shape');
      payload = { conversations: arr };
      $('[data-info]').textContent = t('ws.conv.importReady', { n: num(arr.length) });
      $('[data-run]').disabled = false;
    } catch {
      $('[data-info]').textContent = '';
      $('[data-result]').innerHTML = `<p class="form-error" role="alert">${esc(t('ws.contract.importBadFile'))}</p>`;
    }
  });
  $('[data-run]').addEventListener('click', async () => {
    if (!payload || busy) return;
    busy = true; $('[data-file]').disabled = true; $('[data-run]').disabled = true; $('[data-run]').textContent = t('ws.state.loading');
    try {
      const r = await importConversations(payload);
      const results = r.results || (r.errors || []).map((x) => ({ ...x, status: 'rejected' }));
      const rows = results.map((x) => `<tr><td>${esc(num(x.index + 1))}</td><td>${esc(x.conversation_id ?? '—')}</td><td>${esc(t(x.status === 'accepted' ? 'ws.contract.accepted' : 'ws.contract.rejected'))}</td><td>${esc(x.error || '')}</td></tr>`).join('');
      $('[data-result]').innerHTML = `<p class="form-ok" role="status">${esc(t('ws.contract.importDone', { n: num(r.imported), s: num(r.skipped) }))}</p>${rows ? `<div class="dt-wrap"><table class="dt"><thead><tr><th>#</th><th>conversation_id</th><th>${esc(t('ws.col.status'))}</th><th>${esc(t('ws.contract.reason'))}</th></tr></thead><tbody>${rows}</tbody></table></div>` : ''}`;
      if (r.imported) { toast(t('ws.conv.importDone', { n: num(r.imported), s: num(r.skipped) })); reload(); }
    } catch (e) {
      $('[data-result]').innerHTML = `<p class="form-error" role="alert">${esc(e.kind === 'http' ? `${t('ws.conv.importFail')} (${e.detail || e.status})` : errorText(e, getApiBase()))}</p>`;
    }
    busy = false; $('[data-file]').disabled = false; $('[data-run]').textContent = t('ws.conv.importRun'); $('[data-run]').disabled = false;
  });
}

// ======================= карточка диалога =======================
const mmss = (s) => `${String(Math.floor(s / 60)).padStart(2, '0')}:${String(s % 60).padStart(2, '0')}`;

export function mountConversation(root, ctx) {
  const id = ctx.id;
  let conv = null, checklists = [], appeals = [], ev = null, evId = null, err = null, evErr = null, tab = 'transcript', running = false;
  let ac = new AbortController(), pollT = 0, hlT = 0, hlEl = null, evidenceWarn = '';
  const sig = () => ac.signal;

  root.innerHTML = `<div class="cd" data-tab="transcript">
    <a class="back" href="#/dashboard/conversations">${icon('arrow-left')}<span data-i18n="ws.conv.back"></span></a>
    <div data-head></div><div data-banner></div>
    <div class="tabs" role="tablist"><button type="button" role="tab" data-tab="transcript" aria-selected="true" data-i18n="ws.conv.tabTranscript"></button><button type="button" role="tab" data-tab="evaluation" aria-selected="false" data-i18n="ws.conv.tabEval"></button></div>
    <div class="cd__grid"><section class="cd__tr" data-pane="transcript"></section><aside class="cd__ev" data-pane="evaluation"></aside></div></div>`;
  applyDom(root);
  const $ = (s) => root.querySelector(s);
  const head = $('[data-head]'), banner = $('[data-banner]'), trPane = $('[data-pane="transcript"]'), evPane = $('[data-pane="evaluation"]'), shell = $('.cd');

  // ---------- загрузка ----------
  async function load() {
    ac.abort(); ac = new AbortController();
    head.innerHTML = ''; trPane.innerHTML = `<i class="skel skel--table"></i>`; evPane.innerHTML = `<i class="skel skel--chart"></i>`;
    try {
      const [c, cl] = await Promise.all([getConversation(id, { signal: sig() }), getChecklists({ signal: sig() }).catch((e) => { if (isAbort(e) || getMode() === 'api') throw e; return { items: [] }; })]);
      conv = c; checklists = cl.items || []; err = null;
    } catch (e) {
      if (isAbort(e)) return;
      err = e; conv = null;
    }
    if (!err) {
      const latest = [...(conv.evaluations || [])].sort((a, b) => String(b.created_at).localeCompare(String(a.created_at)))[0];
      evId = evId || latest?.id || null;
      if (evId) await loadEval(evId, false);
    }
    render();
    if (!err) {
      const mid = ctx.query.get('evidence');
      if (mid) focusMessage(mid, { fromUrl: true });   // DOM уже построен синхронно в render(); rAF не нужен (в скрытой вкладке он не срабатывает)
    }
  }

  async function loadEval(eid, rerender = true) {
    evId = eid; evErr = null;
    try { ev = await getEvaluation(eid, { signal: sig() }); await loadAppeals(); } catch (e) { if (isAbort(e)) return; ev = null; evErr = e; }
    if (rerender) renderEval();
    if (ev?.status === 'running') schedulePoll();
  }

  async function loadAppeals() {
    try { appeals = (await getAppeals({ conversation_id: id, page_size: 100 }, { signal: sig() })).items || []; } catch (e) { if (isAbort(e) || getMode() === 'api') throw e; appeals = []; }
  }

  function schedulePoll(tries = 0) {
    clearTimeout(pollT);
    if (tries > 45) { running = false; evErr = Object.assign(new Error('timeout'), { kind: 'timeout' }); renderEval(); return; }
    pollT = setTimeout(async () => {
      try {
        const r = await getEvaluation(evId, { signal: sig() });
        ev = r;
        if (r.status === 'running') { schedulePoll(tries + 1); return; }
        running = false;
        const c = await getConversation(id, { signal: sig() });   // статус и итог диалога обновились на Backend
        conv = c; renderHead(); renderEval();
        if (r.status === 'done') toast(t('ws.eval.done'));
      } catch (e) { if (isAbort(e)) return; running = false; evErr = e; renderEval(); }
    }, 800);
  }

  // ---------- отрисовка ----------
  function render() {
    if (err) {
      head.innerHTML = ''; banner.innerHTML = '';
      const nf = err.kind === 'http' && err.status === 404;
      trPane.innerHTML = nf ? emptyState(t('ws.conv.notFoundTitle'), t('ws.conv.notFoundText', { id })) : errorState(err, { mode: getMode(), base: getApiBase() });
      evPane.innerHTML = '';
      return;
    }
    ctx.setTitle(`${t('ws.nav.conversations')} · ${conv.id}`);
    banner.innerHTML = conv.meta?.source === 'mock' ? mockBanner() : '';
    renderHead(); renderTranscript(); renderEval();
  }

  function renderHead() {
    head.innerHTML = `<div class="cd__head"><div class="cd__id"><h2>${esc(conv.id)}</h2>${statusBadge('conv', conv.status)}${conv.gross_violation ? grossBadge() : ''}</div>
      <dl class="cd__meta"><div><dt>${esc(t('ws.col.language'))}</dt><dd>${langTag(conv.language)} ${esc(tr(`ws.lang.${conv.language}`, conv.language))}</dd></div>
      <div><dt>${esc(t('ws.col.channel'))}</dt><dd>${channelCell(conv.channel)}</dd></div>
      <div><dt>${esc(t('ws.col.operator'))}</dt><dd>${getMode() === 'api' ? esc(t('ws.contract.operatorMissing')) : `${opCell(conv.operator_id)} <button class="btn btn--ghost btn--sm" type="button" data-act="assign">${esc(t(conv.operator_id ? 'ws.conv.reassign' : 'ws.conv.assign'))}</button>`}</dd></div>
      <div><dt>${esc(t('ws.col.date'))}</dt><dd>${esc(dateTime(conv.started_at))}</dd></div>
      <div><dt>${esc(t('ws.col.score'))}</dt><dd>${scoreIndicator(conv.score)}</dd></div></dl></div>`;
  }

  function renderTranscript() {
    const audio = conv.channel === 'call'
      ? (conv.audio_url ? `<figure class="audio-box"><figcaption>${esc(t('ws.conv.recording'))}</figcaption><audio controls preload="none" src="${esc(conv.audio_url)}"></audio></figure>`
        : `<p class="audio-none">${icon('headset')}${esc(t('ws.conv.noAudio'))}</p>`) : '';
    const rows = (conv.messages || []).map((m) => `<li class="msg msg--${esc(m.role)}" data-mid="${esc(m.id)}"><div class="msg__meta"><span class="msg__role">${esc(tr(`ws.role.${m.role}`, m.role))}</span>
      ${m.ts || m.offset_sec != null ? `<time${m.ts ? ` datetime="${esc(m.ts)}" title="${esc(m.ts)}"` : ''}>${esc([m.ts || '', m.offset_sec != null ? mmss(m.offset_sec) : ''].filter(Boolean).join(' · '))}</time>` : ''}<span class="msg__id">#${esc(m.id)}</span></div>
      <p class="msg__text" lang="${esc(conv.language)}">${esc(m.text)}</p></li>`).join('');
    trPane.innerHTML = `<header class="pane__head"><h3>${esc(t('ws.conv.transcript'))}</h3><span class="muted">${esc(t('ws.conv.msgCount', { n: num(conv.messages?.length || 0) }))}</span></header>
      ${audio}<div data-warn>${evidenceWarn ? `<p class="banner banner--warn" role="alert">${icon('alert')}<span>${esc(evidenceWarn)}</span></p>` : ''}</div>
      ${rows ? `<ol class="tr" role="log" aria-label="${esc(t('ws.conv.transcript'))}">${rows}</ol>` : emptyState(t('ws.conv.noMessages'), '')}`;
  }

  function evidenceBannerSet(msg) { evidenceWarn = msg; const w = trPane.querySelector('[data-warn]'); if (w) w.innerHTML = msg ? `<p class="banner banner--warn" role="alert">${icon('alert')}<span>${esc(msg)}</span></p>` : ''; }

  function appealedSet() { return new Set(appeals.filter((a) => a.evaluation_id === evId && a.status === 'pending').map((a) => a.criterion_id)); }

  function renderEval() {
    if (!conv) return;
    const evs = [...(conv.evaluations || [])].sort((a, b) => String(b.created_at).localeCompare(String(a.created_at)));
    const used = new Set(evs.map((e) => e.checklist_version));
    const free = checklists.filter((c) => !used.has(c.version));
    const sel = evs.length > 1 ? `<label class="fsel fsel--inline"><span>${esc(t('ws.eval.version'))}</span><select data-ev-select>${evs.map((e) => `<option value="${esc(e.id)}"${e.id === evId ? ' selected' : ''}>v${num(e.checklist_version)} · ${esc(tr(`ws.status.eval.${e.status}`, e.status))}${e.total_score != null ? ` · ${num(e.total_score)}` : ''}</option>`).join('')}</select></label>` : '';
    const runBox = `<div class="evp__run"><label class="fsel fsel--inline"><span>${esc(t('ws.eval.runWith'))}</span><select data-run-version ${free.length && !running ? '' : 'disabled'}>${free.map((c) => `<option value="${c.version}">v${num(c.version)}${c.is_current ? ` · ${esc(t('ws.eval.current'))}` : ''}</option>`).join('') || `<option>—</option>`}</select></label>
      <button class="btn" type="button" data-act="run" ${free.length && !running ? '' : 'disabled'}>${esc(running ? t('ws.eval.running') : t(evs.length ? 'ws.eval.runAnother' : 'ws.eval.run'))}</button></div>
      ${!free.length ? `<p class="form-hint">${esc(t('ws.eval.noFree'))}</p>` : ''}`;
    let inner = '';
    if (evErr) inner = `<div class="state state--error" role="alert">${icon('alert', 'ico--lg')}<h3>${esc(t('ws.eval.failTitle'))}</h3><p>${esc(errorText(evErr, getApiBase()))}</p></div>`;
    else if (!ev) inner = `<div class="state state--empty">${icon('sparkle', 'ico--lg')}<h3>${esc(t('ws.eval.noneTitle'))}</h3><p>${esc(t('ws.eval.noneText'))}</p></div>`;
    else if (ev.status === 'running') inner = `<div class="state state--run" aria-busy="true"><span class="spin" aria-hidden="true"></span><h3>${esc(t('ws.eval.runningTitle'))}</h3><p>${esc(t('ws.eval.runningText'))}</p></div>`;
    else if (ev.status === 'failed') inner = `<div class="state state--error" role="alert">${icon('alert', 'ico--lg')}<h3>${esc(t('ws.eval.failTitle'))}</h3><p>${esc(ev.error?.message || t('ws.state.aiDown'))}</p></div>`;
    else {
      const changed = ev.final_total_score != null && ev.final_total_score !== ev.total_score;
      inner = `<div class="evp__total"><div><span class="muted">${esc(t('ws.eval.total'))}</span>${scoreIndicator(ev.total_score)}${changed ? `<span class="evp__final">${esc(t('ws.eval.afterAppeals'))}: <b>${esc(num(ev.final_total_score))}</b></span>` : ''}</div>
        <div class="evp__flag evp__flag--${ev.gross_violation ? 'bad' : 'ok'}">${ev.gross_violation == null ? esc(t('ws.contract.unknown')) : ev.gross_violation ? `${icon('alert')}${esc(t('ws.gross.found'))}` : `${icon('check-circle')}${esc(t('ws.gross.none'))}`}</div></div>
        <p class="form-hint">${esc(t('ws.eval.fromBackend'))} · ${esc(t('ws.eval.versionN', { n: num(ev.checklist_version) }))}${ev.source === 'mock' ? ` · ${esc(t('ws.mock.short'))}` : ''}</p>
        <div class="crit-list">${(ev.criteria || []).map((c) => criteriaCard(c, { conversationId: conv.id, appeal: true, appealed: appealedSet(), showScore: true })).join('')}</div>`;
    }
    evPane.innerHTML = `<header class="pane__head"><h3>${icon('sparkle')}${esc(t('ws.eval.title'))}</h3>${sel}</header>${runBox}${inner}${usageSummary(ev?.usage)}`;
  }

  // ---------- evidence navigation ----------
  function focusMessage(mid, { fromUrl = false } = {}) {
    if (!conv) return;
    const el = [...trPane.querySelectorAll('[data-mid]')].find((x) => x.dataset.mid === mid);
    if (!el) { evidenceBannerSet(t('ws.ev.notFound', { id: mid })); setTab('transcript'); return; }
    evidenceBannerSet('');
    setTab('transcript');
    clearTimeout(hlT); hlEl?.classList.remove('is-hl');
    hlEl = el;
    el.classList.remove('is-hl'); void el.offsetWidth;          // перезапуск CSS-анимации при повторном клике
    el.classList.add('is-hl');
    el.scrollIntoView({ block: 'center', behavior: reduced() || fromUrl ? 'auto' : 'smooth' });
    hlT = setTimeout(() => { el.classList.remove('is-hl'); if (hlEl === el) hlEl = null; }, 3600);   // подсветка плавно гаснет
    ctx.setQuery({ evidence: mid });
  }

  function setTab(name) {
    tab = name; shell.dataset.tab = name;
    root.querySelectorAll('.tabs [role="tab"]').forEach((b) => b.setAttribute('aria-selected', String(b.dataset.tab === name)));
  }

  // ---------- действия ----------
  async function run() {
    const v = Number(evPane.querySelector('[data-run-version]')?.value) || undefined;
    running = true; evErr = null; renderEval();
    try {
      const r = await runEvaluation({ conversation_id: id, checklist_version: v }, { signal: sig() });
      ev = r; evId = r.id;
      conv.evaluations = [...(conv.evaluations || []), { id: r.id, checklist_version: r.checklist_version, status: r.status, total_score: r.total_score, created_at: r.created_at }];
      conv.status = 'evaluating'; renderHead();
      if (r.status === 'running') { renderEval(); schedulePoll(); } else {
        // Backend выполняет оценку синхронно: результат уже готов — статус и балл берём из ответа Backend, без пересчёта.
        running = false; renderEval();
        try { conv = await getConversation(id, { signal: sig() }); renderHead(); renderEval(); } catch (x) { if (isAbort(x)) return; }   // статус и итог диалога — снова из Backend
        if (r.status === 'done') toast(t('ws.eval.done'));
      }
    } catch (e) {
      if (isAbort(e)) return;
      running = false; evErr = e; renderEval();
      toast(errorText(e, getApiBase()), { kind: 'error' });
    }
  }

  function openAssign() {
    const m = openModal({
      title: t('ws.assign.title'),
      body: `<p class="modal__text">${esc(t('ws.assign.text'))}</p><label class="field"><span>${esc(t('ws.assign.known'))}</span><select data-known><option value="">—</option></select></label>
        <label class="field"><span>${esc(t('ws.assign.custom'))}</span><input data-custom type="text" maxlength="64" value="${esc(conv.operator_id || '')}" autocomplete="off"></label><p class="form-error" data-err hidden role="alert"></p>`,
      actions: `<button class="btn btn--ghost" type="button" data-modal-close>${esc(t('ws.common.cancel'))}</button><button class="btn" type="button" data-save>${esc(t('ws.common.save'))}</button>`,
    });
    const q = (s) => m.el.querySelector(s);
    m.el.querySelectorAll('[data-modal-close]').forEach((b) => b.addEventListener('click', () => m.close('x')));
    getOperators({ page_size: 100 }, { signal: sig() }).then((r) => {
      const sel = q('[data-known]'); if (!sel) return;
      sel.innerHTML = `<option value="">—</option>${(r.items || []).map((o) => `<option value="${esc(o.id)}"${o.id === conv.operator_id ? ' selected' : ''}>${esc(o.id)}</option>`).join('')}`;
    }).catch(() => { /* список известных операторов необязателен: можно ввести ID вручную */ });
    q('[data-known]').addEventListener('change', (e) => { if (e.target.value) q('[data-custom]').value = e.target.value; });
    q('[data-save]').addEventListener('click', async () => {
      const val = q('[data-custom]').value.trim();
      const er = q('[data-err]');
      if (!val) { er.hidden = false; er.textContent = t('ws.assign.required'); return; }
      q('[data-save]').disabled = true;
      try {
        const r = await assignOperator(id, val, { signal: sig() });
        conv.operator_id = r.operator_id; renderHead(); m.close('ok'); toast(t('ws.assign.done', { id: val }));
      } catch (e) {
        if (isAbort(e)) return;
        er.hidden = false; er.textContent = errorText(e, getApiBase()); q('[data-save]').disabled = false;
      }
    });
  }

  async function openAppeal(criterionId) {
    if (!ev || !['done', 'needs_review'].includes(ev.status)) return;
    const { openAppealForm } = await import('../appealForm.js');
    openAppealForm({ evaluation: ev, criterionId, onDone: async () => { await load(); } });
  }

  function onClick(e) {
    const tb = e.target.closest('.tabs [role="tab"]');
    if (tb) { setTab(tb.dataset.tab); return; }
    const evl = e.target.closest('[data-evidence]');
    if (evl) { e.preventDefault(); focusMessage(evl.dataset.evidence); return; }
    const ap = e.target.closest('[data-appeal]');
    if (ap) { openAppeal(ap.dataset.appeal); return; }
    const act = e.target.closest('[data-act]')?.dataset.act;
    if (act === 'run') run();
    else if (act === 'assign') openAssign();
    else if (act === 'retry') load();
    else if (act === 'to-demo') setMode('demo');
  }
  function onChange(e) { if (e.target.matches('[data-ev-select]')) loadEval(e.target.value); }
  root.addEventListener('click', onClick);
  root.addEventListener('change', onChange);
  load();

  return {
    query(p) { const mid = p.get('evidence'); if (mid && conv) focusMessage(mid); },
    relang() { applyDom(root); if (conv) { ctx.setTitle(`${t('ws.nav.conversations')} · ${conv.id}`); renderHead(); renderTranscript(); renderEval(); } else if (err) render(); },
    destroy() { ac.abort(); clearTimeout(pollT); clearTimeout(hlT); root.removeEventListener('click', onClick); root.removeEventListener('change', onChange); root.replaceChildren(); },
  };
}
