// Real API Adapter: Frontend view-model ⇄ реальный контракт Backend (ветка BACK, API_CONTRACT.md, OpenAPI /openapi.json).
// Здесь НЕТ моков и НЕТ тихих подстановок: если Backend не умеет операцию, бросается ApiError('unsupported').
// Итоговые баллы берутся из ответов Backend (total / original_total / final_total) и нигде не пересчитываются.
// Проверено на живом Backend (без модели): health, upload, conversations (list/detail/filters), checklists, ошибки.
// НЕ проверено на живом Backend (нужен ключ Meta AI): содержимое оценок и апелляций — разбор написан по API_CONTRACT.md.
import { request, ApiError, sleep } from './http.js';
import { LONG_TIMEOUT_MS } from './config.js';

const src = (data) => ({ ...data, meta: { ...(data?.meta || {}), source: 'api' } });
const num = (v) => (v == null || v === '' || Number.isNaN(Number(v)) ? null : Number(v));
const enc = encodeURIComponent;
const low = (v) => String(v ?? '').toLowerCase();
const unsupported = (what) => new ApiError('unsupported', 0, '', what);

// ---------- словари значений ----------
export const LANG_OUT = { RU: 'ru', KZ: 'kk', EN: 'en' };
export const LANG_IN = { ru: 'RU', kk: 'KZ', en: 'EN' };
export const CHANNEL_OUT = { 'agent chat': 'chat', 'voice call': 'call' };
export const CHANNEL_IN = { chat: 'agent chat', call: 'voice call' };
const EVAL_STATUS = { completed: 'done', needs_review: 'needs_review', failed: 'failed', pending: 'running', running: 'running' };

export function convStatus(item) {
  switch (item.status) {
    case 'not_evaluated': return 'new';
    case 'pending': case 'running': return 'evaluating';
    case 'failed': return 'failed';
    case 'needs_review': return 'needs_review';
    case 'completed': return item.has_open_appeal ? 'appealed' : item.has_appeal ? 'reviewed' : 'evaluated';
    default: return item.status || 'new';
  }
}
const outLang = () => { try { const l = localStorage.getItem('cqa-lang'); return ['ru', 'kk', 'en'].includes(l) ? l : 'ru'; } catch { return 'ru'; } };

// ---------- mappers (чистые функции, покрыты unit-тестами) ----------
export function mapConversationItem(r) {
  return {
    id: r.conversation_id, language: LANG_OUT[r.language] || low(r.language), channel: CHANNEL_OUT[r.channel] || r.channel,
    operator_id: null,                       // Backend не хранит операторов — связь не выдумываем
    status: convStatus(r), backend_status: r.status, score: num(r.total), gross_violation: r.gross_violation ?? false,
    started_at: null, updated_at: r.updated_at || null, evaluation_id: r.evaluation_id || null,
    has_appeal: !!r.has_appeal, has_open_appeal: !!r.has_open_appeal, evaluation_stale: !!r.evaluation_stale,
  };
}
export const mapMessage = (m) => ({ id: m.message_id, role: m.speaker, text: m.text, ts: m.timestamp || null, offset_sec: m.offset_sec ?? null, audio_url: m.audio_url || null });

export function scaleRange(scale) {
  const keys = Object.keys(scale || {}).map(Number).filter(Number.isFinite);
  return keys.length ? { min: Math.min(...keys), max: Math.max(...keys) } : null;
}

export function mapChecklist(r, activeId) {
  return {
    id: r.checklist_version_id, checklist_id: r.checklist_id, version: r.version, created_at: r.created_at, is_current: r.checklist_version_id === activeId,
    note: r.name || '', name: r.name, weight_sum: r.weight_sum, sections: r.sections || [], change_summary: r.change_summary || null,
    criteria: (r.criteria || []).filter((c) => c.active !== false).map((c) => ({
      id: c.criterion_id, name: c.name, description: c.description || '', weight: num(c.weight) ?? 0, answer_type: c.type,
      scale: c.type === 'scale' ? scaleRange(c.scale) : null, na_allowed: !!c.allow_na, na_condition: c.na_condition || '',
      gross: c.type === 'flag', builtin: true, section: c.section, _raw: c,
    })),
  };
}

// vm → тело POST /checklists. Поля, которых нет в редакторе (yes_text/no_text/section/порядок/шкала), берём из исходного критерия.
export function toChecklistRequest(body, baseVm) {
  const sections = baseVm?.sections?.length ? baseVm.sections : [{ id: 's1', name: 'Критерии' }];
  const flagSection = sections[sections.length - 1].id;
  const txt = (v) => (v && typeof v === 'object' ? v.ru || Object.values(v).find(Boolean) || '' : v || '');
  const rawById = new Map((baseVm?.criteria || []).map((c) => [String(c.id), c._raw]));
  const criteria = (body.criteria || []).map((c, i) => {
    const raw = c._raw || rawById.get(String(c.id)) || {};
    const flag = c.answer_type === 'flag';
    const name = String(txt(c.name)).trim();
    const desc = String(txt(c.description)).trim();
    let scale = {};
    if (c.answer_type === 'scale') {
      const range = scaleRange(raw.scale);
      const same = range && Number(c.scale?.min) === range.min && Number(c.scale?.max) === range.max;
      if (same) scale = raw.scale;
      else { for (let k = Number(c.scale?.min); k <= Number(c.scale?.max); k += 1) scale[String(k)] = `${name}: ${k}`; }
    }
    const allowNa = !flag && !!c.na_allowed;
    return {
      criterion_id: String(c.id), name, description: desc, section: raw.section || (flag ? flagSection : sections[0].id), type: c.answer_type,
      weight: flag ? 0 : Number(c.weight) || 0, allow_na: allowNa, na_condition: allowNa ? (c.na_condition || raw.na_condition || null) : null,
      yes_text: c.answer_type === 'scale' ? null : (raw.yes_text ?? (desc || name)), no_text: c.answer_type === 'scale' ? null : (raw.no_text ?? (desc || name)),
      scale, scored: !flag, order: i + 1, active: true,
    };
  });
  return { name: baseVm?.name || 'Checklist', checklist_id: baseVm?.checklist_id, sections, criteria };
}

export function mapCriterion(c, checklistCriterion) {
  const type = c.type || checklistCriterion?.type || 'yes_no';
  const na = c.answer === 'na';
  const range = type === 'scale' ? scaleRange(checklistCriterion?.scale) : null;
  const answer = na ? null : type === 'scale' ? num(c.answer) : c.answer === 'yes';
  const invalid = c.valid === false;
  const weight = na ? num(c.weight) : (num(c.effective_weight) ?? num(c.weight));
  const decided = (c.decisions || []).length > 0;
  return {
    criterion_id: c.criterion_id, name: c.name || checklistCriterion?.name || c.criterion_id, description: checklistCriterion?.description || '',
    weight, declared_weight: num(c.weight), answer_type: type, scale: range, scale_keys: type === 'scale' ? Object.keys(checklistCriterion?.scale || {}) : null,
    answer, answer_key: c.answer ?? null, score: na || invalid ? null : num(c.points),
    na: na ? { reason: c.reasoning || '' } : null, gross: type === 'flag' && c.answer === 'yes',
    explanation: invalid ? (c.validation_errors || []).join('; ') : (c.reasoning || ''), invalid,
    evidence: (c.evidence || []).map((e) => ({ message_id: e.message_id, quote: e.quote, speaker: e.speaker || null })),
    checked_range: c.checked_range || null, has_open_appeal: !!c.has_open_appeal, source: c.source || null,
    revised: decided ? { score: num(c.points), appeal_id: c.decisions[c.decisions.length - 1]?.appeal_id || null, answer: c.answer ?? null } : null,
    _decided: decided,
  };
}

export function mapEvaluation(r, versions, checklistRaw) {
  const byId = new Map((checklistRaw?.criteria || []).map((c) => [c.criterion_id, c]));
  const origById = new Map(((r.original && r.original.criteria) || []).map((c) => [c.criterion_id, c]));
  const criteria = (r.criteria || []).map((c) => {
    const m = mapCriterion(c, byId.get(c.criterion_id));
    if (m.revised) { const o = origById.get(c.criterion_id); m.score = num(o?.points) ?? m.score; m.original_answer = o?.answer ?? null; }
    delete m._decided;
    return m;
  });
  return {
    id: r.evaluation_id, conversation_id: r.conversation_id, checklist_version: versions?.get(r.checklist_version_id) ?? null, checklist_version_id: r.checklist_version_id,
    status: EVAL_STATUS[r.status] || r.status, backend_status: r.status, output_language: r.output_language || null,
    total_score: num(r.original_total ?? r.total), final_total_score: num(r.final_total ?? r.total), gross_violation: r.gross_violation ?? null,
    created_at: r.created_at || r.updated_at || null, error: r.error ? { message: r.error.message || String(r.error) } : null, usage: r.usage || null, criteria, sections: r.sections || [],
  };
}

export function mapAppeal(raw, ev) {
  const criterion = ev?.criteria?.find((c) => c.criterion_id === raw.criterion_id) || null;
  const d = raw.decision || null;
  const status = raw.status === 'open' ? 'pending' : d?.decision === 'change' ? 'approved' : d ? 'rejected' : 'reviewed';
  return {
    id: raw.appeal_id, conversation_id: raw.conversation_id ?? ev?.conversation_id ?? null, evaluation_id: raw.evaluation_id, criterion_id: raw.criterion_id,
    criterion_name: criterion?.name || raw.criterion_name || raw.criterion_id, status, comment: raw.comment, created_at: raw.created_at || null, operator_id: null,
    original_score: criterion ? criterion.score : num(raw.original_result?.points), max_score: criterion ? criterion.weight : num(raw.original_result?.effective_weight),
    decision: d ? { reviewer: d.decided_by || null, reason: d.reason || '', new_score: d.decision === 'change' ? num(raw.current_result?.points ?? criterion?.revised?.score) : null, new_answer: d.final_answer ?? null, original_answer: d.original_answer ?? null, decided_at: d.created_at || null } : null,
    original_total_score: num(raw.original_total ?? ev?.total_score), final_total_score: num(raw.final_total ?? ev?.final_total_score), criterion, evaluation: ev || null,
  };
}

// ---------- кэш чек-листов (версии неизменяемы, активная версия обновляется при сохранении) ----------
let clCache = null;
const clStamp = () => Date.now();
async function loadChecklists({ signal, force = false } = {}) {
  if (!force && clCache && clStamp() - clCache.at < 15000) return clCache;
  const [list, active] = await Promise.all([request('/checklists', { signal }), request('/checklists/active', { signal })]);
  const activeId = active?.checklist_version_id;
  clCache = { at: clStamp(), raw: list, active: activeId, items: list.map((r) => mapChecklist(r, activeId)).sort((a, b) => b.version - a.version) };
  return clCache;
}
export const resetCache = () => { clCache = null; };

// ---------- клиентская пагинация (Backend отдаёт массивы без пагинации) ----------
function paginate(items, q, def = 20) {
  const page = Math.max(1, Number(q.page) || 1), size = Math.min(200, Math.max(1, Number(q.page_size) || def));
  return { items: items.slice((page - 1) * size, page * size), total: items.length, page, page_size: size };
}
function sortItems(items, q, fields, def) {
  const f = fields.includes(q.sort) ? q.sort : def, dir = q.order === 'asc' ? 1 : -1;
  return items.sort((a, b) => {
    const x = a[f], y = b[f];
    if (x == null && y == null) return 0;
    if (x == null) return 1;
    if (y == null) return -1;
    return (x > y ? 1 : x < y ? -1 : 0) * dir;
  });
}

// ---------- Conversations ----------
async function fetchConversations(p, signal) {
  const raw = await request('/conversations', { query: { language: LANG_IN[p.language], channel: CHANNEL_IN[p.channel], q: p.q }, signal });
  return raw.map(mapConversationItem);
}
export async function getConversations(p = {}, { signal } = {}) {
  let items = await fetchConversations(p, signal);
  if (p.status) items = items.filter((c) => c.status === p.status);
  if (p.operator_id) items = p.operator_id === 'none' ? items : [];   // операторов в Backend нет
  items = sortItems(items, { sort: p.sort === 'id' ? 'id' : p.sort, order: p.order }, ['score', 'id', 'updated_at'], 'updated_at');
  return src({ ...paginate(items, p), meta: { client_side_paging: true } });
}
export async function getConversation(id, { signal } = {}) {
  const [c, hist, cl] = await Promise.all([request(`/conversations/${enc(id)}`, { signal }), request(`/conversations/${enc(id)}/evaluations`, { signal }), loadChecklists({ signal })]);
  const versions = new Map(cl.items.map((x) => [x.id, x.version]));
  const lr = c.latest_result;
  const latest = lr ? { status: lr.status, total: lr.total, gross: lr.gross_violation } : null;
  const evaluations = hist.map((e) => ({ id: e.evaluation_id, checklist_version: versions.get(e.checklist_version_id) ?? null, status: EVAL_STATUS[e.status] || e.status, total_score: num(e.original_total ?? e.total), created_at: e.created_at || null }));
  const hasOpen = !!lr?.criteria?.some((x) => x.has_open_appeal);
  const base = mapConversationItem({ conversation_id: c.conversation_id, language: c.language, channel: c.channel, total: latest?.total ?? null, gross_violation: latest?.gross ?? null, status: lr?.status || 'not_evaluated', evaluation_id: lr?.evaluation_id || null, has_appeal: !!lr?.decisions?.length || hasOpen, has_open_appeal: hasOpen, updated_at: c.updated_at });
  return src({ ...base, audio_url: c.audio_url || null, messages: (c.transcript || []).map(mapMessage), evaluations });
}
export const assignOperator = () => Promise.reject(unsupported('conversation operator assignment'));
// Импорт реализован через реальный POST /conversations/upload (по одному диалогу).
export async function importConversations(body, { signal } = {}) {
  const list = Array.isArray(body?.conversations) ? body.conversations : [];
  if (!list.length) throw new ApiError('http', 422, '', 'conversations required', 'VALIDATION_ERROR');
  let imported = 0, skipped = 0; const errors = [];
  for (let i = 0; i < list.length; i += 1) {
    const c = list[i];
    const transcript = (c.messages || []).filter((m) => m && m.text).map((m) => ({ speaker: m.role, text: m.text, ...(m.ts ? { timestamp: m.ts } : {}) }));
    if (!transcript.length || !LANG_IN[c.language] || !CHANNEL_IN[c.channel]) { skipped += 1; errors.push({ index: i, error: !transcript.length ? 'messages' : !LANG_IN[c.language] ? 'language' : 'channel' }); continue; }
    try {
      await request('/conversations/upload', { method: 'POST', body: { ...(c.id ? { conversation_id: c.id } : {}), language: LANG_IN[c.language], channel: CHANNEL_IN[c.channel], transcript, ...(c.audio_url ? { audio_url: c.audio_url } : {}) }, signal });
      imported += 1;
    } catch (e) { if (e?.name === 'AbortError') throw e; skipped += 1; errors.push({ index: i, error: e.code || e.detail || 'error' }); }
  }
  return src({ imported, skipped, errors });
}

// ---------- Evaluations ----------
export async function getEvaluations(p = {}, { signal } = {}) {
  const convs = await fetchConversations({ language: p.language, q: p.q }, signal);
  let items = convs.filter((c) => c.evaluation_id).map((c) => ({
    id: c.evaluation_id, conversation_id: c.id, checklist_version: null, status: EVAL_STATUS[c.backend_status] || c.backend_status, total_score: c.score, final_total_score: null,
    gross_violation: c.gross_violation, created_at: c.updated_at, language: c.language, operator_id: null, error: null,
  }));
  if (p.status) items = items.filter((e) => e.status === p.status);
  if (p.gross) items = items.filter((e) => e.gross_violation);
  if (p.score_min != null && p.score_min !== '') items = items.filter((e) => e.total_score != null && e.total_score >= Number(p.score_min));
  if (p.score_max != null && p.score_max !== '') items = items.filter((e) => e.total_score != null && e.total_score <= Number(p.score_max));
  sortItems(items, p, ['created_at', 'total_score', 'conversation_id'], 'created_at');
  return src({ ...paginate(items, p), meta: { client_side_paging: true, derived_from: 'GET /conversations (Backend не имеет списка оценок)' } });
}
async function evaluationVm(raw, signal) {
  const cl = await loadChecklists({ signal });
  const versions = new Map(cl.items.map((x) => [x.id, x.version]));
  return mapEvaluation(raw, versions, cl.raw.find((x) => x.checklist_version_id === raw.checklist_version_id));
}
export async function getEvaluation(id, { signal } = {}) { return src(await evaluationVm(await request(`/evaluations/${enc(id)}`, { signal }), signal)); }
export async function runEvaluation(body, { signal } = {}) {
  const cl = await loadChecklists({ signal });
  const target = cl.items.find((x) => x.version === Number(body.checklist_version));
  if (!target) throw new ApiError('http', 404, '', 'checklist version not found', 'CHECKLIST_NOT_FOUND');
  // Backend запускает оценку синхронно: ответ придёт после вызова модели (десятки секунд). Результат НЕ имитируется.
  const raw = await request('/evaluations', { method: 'POST', body: { conversation_id: body.conversation_id, checklist_version_id: target.id, output_language: outLang() }, signal, timeout: LONG_TIMEOUT_MS });
  return src(await evaluationVm(raw, signal));
}

// ---------- Operators / Overview — Backend этого не предоставляет ----------
export const getOverview = () => Promise.reject(unsupported('overview'));
export const getOperators = () => Promise.reject(unsupported('operators'));
export const getOperator = () => Promise.reject(unsupported('operators'));

// ---------- Appeals ----------
async function appealVm(raw, signal) {
  const ev = await evaluationVm(await request(`/evaluations/${enc(raw.evaluation_id)}`, { signal }), signal);
  return mapAppeal(raw, ev);
}
export async function getAppeals(p = {}, { signal } = {}) {
  const backendStatus = p.status === 'pending' ? 'open' : p.status ? 'reviewed' : undefined;
  const raws = await request('/appeals', { query: { status: backendStatus }, signal });
  const full = await Promise.all(raws.slice(0, 100).map((r) => request(`/appeals/${enc(r.appeal_id)}`, { signal }).catch(() => r)));
  const evCache = new Map();
  let items = await Promise.all(full.map(async (r) => {
    let ev = evCache.get(r.evaluation_id);
    if (!ev) { ev = request(`/evaluations/${enc(r.evaluation_id)}`, { signal }).then((x) => evaluationVm(x, signal)).catch(() => null); evCache.set(r.evaluation_id, ev); }
    return mapAppeal(r, await ev);
  }));
  const needle = low(p.q).trim();
  items = items.filter((a) => (!p.status || a.status === p.status || (p.status !== 'pending' && a.status === 'reviewed')) && (!p.conversation_id || a.conversation_id === p.conversation_id) && (!needle || low(a.id).includes(needle) || low(a.conversation_id).includes(needle) || low(a.comment).includes(needle)));
  sortItems(items, p, ['created_at', 'status', 'original_score'], 'created_at');
  return src({ ...paginate(items, p), meta: { client_side_paging: true } });
}
export async function getAppeal(id, { signal } = {}) { return src(await appealVm(await request(`/appeals/${enc(id)}`, { signal }), signal)); }
export async function createAppeal(body, { signal } = {}) {
  const raw = await request('/appeals', { method: 'POST', body: { evaluation_id: body.evaluation_id, criterion_id: body.criterion_id, comment: body.comment }, signal });
  return src(await appealVm(raw, signal));
}
// UI: decision approved|rejected (+ final_answer, original_answer). Backend: change|confirm. Итоги пересчитывает Backend.
export async function decideAppeal(id, body, { signal } = {}) {
  const change = body.decision === 'approved';
  const final_answer = String(change ? body.final_answer : body.original_answer ?? '');
  if (!final_answer) throw new ApiError('http', 422, '', 'final_answer required', 'INVALID_FINAL_ANSWER');
  await request(`/appeals/${enc(id)}/decision`, { method: 'POST', body: { decision: change ? 'change' : 'confirm', final_answer, reason: body.reason }, signal });
  return src(await appealVm(await request(`/appeals/${enc(id)}`, { signal }), signal));
}

// ---------- Checklists ----------
export async function getChecklists({ signal } = {}) { const c = await loadChecklists({ signal, force: true }); return src({ items: c.items }); }
export async function saveChecklist(body, { signal } = {}) {
  const cl = await loadChecklists({ signal });
  const base = cl.items.find((x) => x.version === Number(body.base_version)) || cl.items.find((x) => x.is_current) || cl.items[0];
  const raw = await request('/checklists', { method: 'POST', body: toChecklistRequest(body, base), signal });   // Backend сам валидирует веса и создаёт НОВУЮ версию
  resetCache();
  return src(mapChecklist(raw, raw.checklist_version_id));
}
export async function activateChecklist(versionId, { signal } = {}) { await request(`/checklists/${enc(versionId)}/activate`, { method: 'POST', signal }); resetCache(); return src({ ok: true }); }
export const health = ({ signal } = {}) => request('/health', { signal });
export { sleep };
