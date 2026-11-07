// Real API Adapter: Frontend view-model ⇄ реальный контракт Backend (ветка BACK, API_CONTRACT.md, OpenAPI /openapi.json).
// Здесь НЕТ моков и НЕТ тихих подстановок: если Backend не умеет операцию, бросается ApiError('unsupported').
// Итоговые баллы берутся из ответов Backend (total / original_total / final_total) и нигде не пересчитываются.
// Проверено на живом Backend (без модели): health, upload, conversations (list/detail/filters), checklists, ошибки.
// НЕ проверено на живом Backend (нужен ключ Meta AI): содержимое оценок и апелляций — разбор написан по API_CONTRACT.md.
import { request, ApiError, sleep } from './http.js';
import { LONG_TIMEOUT_MS } from './config.js';

const src = (data) => ({ ...data, meta: { ...(data?.meta || {}), source: 'api' } });
const num = (v) => (v == null || v === '' || !Number.isFinite(Number(v)) ? null : Number(v));
const enc = encodeURIComponent;
const low = (v) => String(v ?? '').toLowerCase();
const unsupported = (what) => new ApiError('unsupported', 0, '', what);

// ---------- словари значений ----------
export const LANG_OUT = { RU: 'ru', KZ: 'kk', EN: 'en' };
export const LANG_IN = { ru: 'RU', kk: 'KZ', kz: 'KZ', en: 'EN' };
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
    status: convStatus(r), backend_status: r.status, score: num(r.total), gross_violation: r.gross_violation ?? null,
    started_at: null, updated_at: r.updated_at || null, evaluation_id: r.evaluation_id || null,
    has_appeal: !!r.has_appeal, has_open_appeal: !!r.has_open_appeal, evaluation_stale: !!r.evaluation_stale,
    dataset_group_id: r.dataset_group_id ?? null,
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
    inactive_criteria: (r.criteria || []).filter((c) => c.active === false),
    criteria: (r.criteria || []).filter((c) => c.active !== false).map((c) => ({
      id: c.criterion_id, name: c.name, description: c.description || '', weight: num(c.weight) ?? 0, answer_type: c.type,
      scale: c.type === 'scale' ? scaleRange(c.scale) : null, na_allowed: !!c.allow_na, na_condition: c.na_condition || '',
      scale_definition: c.type === 'scale' ? { ...c.scale } : null, yes_text: c.yes_text ?? '', no_text: c.no_text ?? '',
      gross: c.type === 'flag', builtin: true, section: c.section, _raw: c,
    })),
  };
}

// vm → POST /checklists. Ступени и формулировки передаются явно; пропущенные поля сохраняются из исходной версии.
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
      scale = c.scale_definition ?? raw.scale ?? {};
    }
    const allowNa = !flag && !!c.na_allowed;
    return {
      criterion_id: String(c.id), name, description: desc, section: raw.section || (flag ? flagSection : sections[0].id), type: c.answer_type,
      weight: flag ? 0 : Number(c.weight) || 0, allow_na: allowNa, na_condition: allowNa ? (c.na_condition || raw.na_condition || null) : null,
      yes_text: c.answer_type === 'scale' ? null : (c.yes_text ?? raw.yes_text ?? (desc || name)), no_text: c.answer_type === 'scale' ? null : (c.no_text ?? raw.no_text ?? (desc || name)),
      scale, scored: flag ? false : (raw.scored ?? true), order: i + 1, active: true,
    };
  });
  const ids = new Set(criteria.map((c) => c.criterion_id));
  criteria.push(...(baseVm?.inactive_criteria || []).filter((c) => !ids.has(c.criterion_id)));
  return { name: baseVm?.name || 'Checklist', checklist_id: baseVm?.checklist_id, sections, criteria };
}

export function mapCriterion(c, checklistCriterion) {
  const type = c.type || checklistCriterion?.type || 'yes_no';
  const na = c.answer === 'na';
  const range = type === 'scale' ? scaleRange(checklistCriterion?.scale) : null;
  const answer = na ? null : type === 'scale' ? num(c.answer) : c.answer === 'yes' ? true : c.answer === 'no' ? false : null;
  const invalid = c.valid === false;
  const weight = na ? num(c.weight) : (num(c.effective_weight) ?? num(c.weight));
  const decided = (c.decisions || []).length > 0;
  return {
    criterion_id: c.criterion_id, name: c.name || checklistCriterion?.name || c.criterion_id, description: checklistCriterion?.description || '',
    weight, declared_weight: num(c.weight), answer_type: type, scale: range, scale_keys: type === 'scale' ? Object.keys(checklistCriterion?.scale || {}) : null,
    allow_na: type !== 'flag' && !!checklistCriterion?.allow_na,
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
    const o = origById.get(c.criterion_id);
    m.original_score = num(o?.points);
    m.original_answer = o?.answer ?? null;
    if (m.revised) Object.assign(m.revised, { original_score: m.original_score, original_answer: m.original_answer });
    delete m._decided;
    return m;
  });
  return {
    id: r.evaluation_id, conversation_id: r.conversation_id, checklist_version: versions?.get(r.checklist_version_id) ?? null, checklist_version_id: r.checklist_version_id,
    language: r.conversation_snapshot?.language ? (LANG_OUT[r.conversation_snapshot.language] || low(r.conversation_snapshot.language)) : null,
    channel: r.conversation_snapshot?.channel ? (CHANNEL_OUT[r.conversation_snapshot.channel] || r.conversation_snapshot.channel) : null,
    status: EVAL_STATUS[r.status] || r.status, backend_status: r.status, output_language: r.output_language || null,
    total_score: num(r.original_total ?? r.total), final_total_score: num(r.final_total ?? r.total), gross_violation: r.gross_violation ?? null,
    created_at: r.created_at || r.updated_at || null, error: r.error ? { message: r.error.message || String(r.error) } : null, usage: r.usage || null, criteria, sections: r.sections || [],
  };
}

export function mapAppeal(raw, ev) {
  const criterion = ev?.criteria?.find((c) => c.criterion_id === raw.criterion_id) || null;
  const disputed = raw.disputed_result ?? raw.original_result ?? null;
  const disputedCriterion = disputed ? mapCriterion({ ...disputed, criterion_id: raw.criterion_id }, raw.criterion) : null;
  if (disputedCriterion) delete disputedCriterion._decided;
  const d = raw.decision || null;
  const status = raw.status === 'open' ? 'pending' : d?.decision === 'change' ? 'approved' : d ? 'rejected' : 'reviewed';
  return {
    id: raw.appeal_id, conversation_id: raw.conversation_id ?? ev?.conversation_id ?? null, evaluation_id: raw.evaluation_id, criterion_id: raw.criterion_id,
    criterion_name: criterion?.name || raw.criterion_name || raw.criterion?.name || raw.criterion_id, status, comment: raw.comment, created_at: raw.created_at || null, operator_id: null,
    original_score: num(raw.disputed_result?.points ?? raw.original_result?.points ?? criterion?.original_score ?? criterion?.score),
    original_answer: disputed ? (disputed.answer ?? null) : (criterion?.original_answer ?? criterion?.answer_key ?? null),
    max_score: num(raw.disputed_result?.effective_weight ?? raw.original_result?.effective_weight ?? criterion?.weight),
    final_max_score: num(raw.current_result?.effective_weight ?? criterion?.weight),
    decision: d ? { reviewer: d.decided_by || null, reason: d.reason || '', new_score: d.decision === 'change' ? num(raw.current_result?.points ?? criterion?.revised?.score) : null, new_answer: d.final_answer ?? null, original_answer: d.original_answer ?? null, decided_at: d.created_at || null } : null,
    original_total_score: num(raw.original_total ?? ev?.total_score), final_total_score: num(raw.final_total ?? ev?.final_total_score), criterion, disputed_criterion: disputedCriterion, evaluation: ev || null,
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
  const raw = await request('/conversations', { query: { language: LANG_IN[low(p.language)] || p.language, channel: CHANNEL_IN[p.channel] || p.channel, dataset_group_id: p.dataset_group_id, q: p.q }, signal });
  return raw.map(mapConversationItem);
}
export async function getConversations(p = {}, { signal } = {}) {
  // Keep the actual channel choices available when one channel is selected.
  // Backend still applies text/language/source filters; pagination is already client-side.
  let items = await fetchConversations({ ...p, channel: undefined }, signal);
  const availableChannels = [...new Set(items.map((c) => c.channel))].filter(Boolean).sort();
  if (p.channel) items = items.filter((c) => c.channel === (CHANNEL_OUT[p.channel] || p.channel));
  if (p.status) items = items.filter((c) => c.status === p.status);
  if (p.operator_id) items = p.operator_id === 'none' ? items : [];   // операторов в Backend нет
  items = sortItems(items, { sort: p.sort === 'id' ? 'id' : p.sort, order: p.order }, ['score', 'id', 'updated_at'], 'updated_at');
  return src({ ...paginate(items, p), meta: { client_side_paging: true, available_channels: availableChannels } });
}
export async function getConversation(id, { signal } = {}) {
  const [c, hist, cl] = await Promise.all([request(`/conversations/${enc(id)}`, { signal }), request(`/conversations/${enc(id)}/evaluations`, { signal }), loadChecklists({ signal })]);
  const versions = new Map(cl.items.map((x) => [x.id, x.version]));
  const lr = c.latest_result;
  const latest = lr ? { status: lr.status, total: lr.total, gross: lr.gross_violation } : null;
  const evaluations = hist.map((e) => ({ id: e.evaluation_id, checklist_version: versions.get(e.checklist_version_id) ?? null, status: EVAL_STATUS[e.status] || e.status, total_score: num(e.original_total ?? e.total), created_at: e.created_at || null }));
  const hasOpen = !!lr?.criteria?.some((x) => x.has_open_appeal);
  const base = mapConversationItem({ conversation_id: c.conversation_id, language: c.language, channel: c.channel, total: latest?.total ?? null, gross_violation: latest?.gross ?? null, status: lr?.status || 'not_evaluated', evaluation_id: lr?.evaluation_id || null, has_appeal: !!lr?.decisions?.length || hasOpen, has_open_appeal: hasOpen, updated_at: c.updated_at, dataset_group_id: c.dataset_group_id, evaluation_stale: c.evaluation_stale });
  return src({ ...base, audio_url: c.audio_url || null, messages: (c.transcript || []).map(mapMessage), evaluations });
}
export const assignOperator = () => Promise.reject(unsupported('conversation operator assignment'));
const importError = (detail) => new ApiError('http', 422, '', detail, 'VALIDATION_ERROR');
export function getImportRecords(body) {
  const list = Array.isArray(body) ? body : body?.records ?? body?.conversations;
  if (!Array.isArray(list) || !list.length) throw importError('Ожидается непустой массив диалогов или объект с массивом records / conversations.');
  return list;
}
export function toUploadRequest(c) {
  if (!c || typeof c !== 'object' || Array.isArray(c)) throw importError('Запись диалога должна быть объектом.');
  const organizer = Object.hasOwn(c, 'transcript');
  const messages = organizer ? c.transcript : c.messages;
  if (!Array.isArray(messages) || !messages.length) throw importError('transcript / messages должен быть непустым массивом.');
  const language = LANG_IN[low(c.language)];
  if (!language) throw importError('language: допустимы RU, KZ, EN (или ru, kk, en).');
  if (typeof c.channel !== 'string' || !c.channel.trim()) throw importError('channel должен быть непустой строкой.');
  const id = c.conversation_id ?? c.id;
  if ((organizer || id != null) && (typeof id !== 'string' || !id.trim())) throw importError('conversation_id должен быть непустой строкой.');
  const transcript = messages.map((m, i) => {
    if (!m || typeof m !== 'object' || Array.isArray(m)) throw importError(`Реплика ${i + 1}: ожидается объект.`);
    const speaker = organizer ? m.speaker : m.role;
    if (!['client', 'operator', 'bot', 'system'].includes(speaker)) throw importError(`Реплика ${i + 1}: недопустимая роль.`);
    if (typeof m.text !== 'string' || !m.text.trim()) throw importError(`Реплика ${i + 1}: text должен быть непустой строкой.`);
    const timestamp = Object.hasOwn(m, 'timestamp') ? m.timestamp : Object.hasOwn(m, 'ts') ? m.ts : '';
    if (timestamp != null && typeof timestamp !== 'string') throw importError(`Реплика ${i + 1}: timestamp должен быть строкой или null.`);
    return { timestamp, speaker, text: m.text };
  });
  if (c.audio_url != null && typeof c.audio_url !== 'string') throw importError('audio_url должен быть строкой или null.');
  return { ...(id != null ? { conversation_id: id } : {}), language, channel: organizer ? c.channel : (CHANNEL_IN[c.channel] || c.channel), transcript, audio_url: c.audio_url ?? null };
}
// Импорт реализован через реальный POST /conversations/upload (по одному диалогу).
export async function importConversations(body, { signal } = {}) {
  const list = getImportRecords(body);
  let imported = 0, skipped = 0; const errors = [], results = [];
  for (let i = 0; i < list.length; i += 1) {
    const c = list[i];
    try {
      const saved = await request('/conversations/upload', { method: 'POST', body: toUploadRequest(c), signal });
      imported += 1;
      results.push({ index: i, conversation_id: saved.conversation_id, status: 'accepted' });
    } catch (e) {
      if (e?.name === 'AbortError') throw e;
      skipped += 1;
      const error = { index: i, conversation_id: c?.conversation_id ?? c?.id ?? null, error: e.detail || e.message || 'Ошибка импорта.', code: e.code || e.kind || 'IMPORT_ERROR' };
      errors.push(error); results.push({ ...error, status: 'rejected' });
    }
  }
  return src({ imported, skipped, results, errors });
}

// ---------- Evaluations ----------
export async function getEvaluations(p = {}, { signal } = {}) {
  const convs = await fetchConversations({ language: p.language, q: p.q }, signal);
  let items = convs.filter((c) => c.evaluation_id).map((c) => ({
    id: c.evaluation_id, conversation_id: c.id, checklist_version: null, status: EVAL_STATUS[c.backend_status] || c.backend_status, total_score: c.score, final_total_score: null,
    gross_violation: c.gross_violation, created_at: null, updated_at: c.updated_at, language: c.language, operator_id: null, error: null,
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

// ---------- Overview: текущий снимок БД, без выдуманного временного ряда ----------
const mean = (values) => values.length ? values.reduce((sum, v) => sum + v, 0) / values.length : null;
export function mapOverview(conversations, appeals, stats) {
  const eligible = conversations.filter((c) => c.status === 'completed' && !c.evaluation_stale && num(c.total) != null);
  const scores = eligible.map((c) => num(c.total)).sort((a, b) => a - b);
  const middle = Math.floor(scores.length / 2);
  const median = scores.length ? (scores.length % 2 ? scores[middle] : (scores[middle - 1] + scores[middle]) / 2) : null;
  const breakdown = (key, mapValue) => [...new Set(conversations.map((c) => c[key]))].map((value) => ({
    [key]: mapValue(value), count: conversations.filter((c) => c[key] === value).length,
    avg_score: mean(eligible.filter((c) => c[key] === value).map((c) => num(c.total))),
  }));
  const recent = (items) => sortItems(items, { sort: 'created_at', order: 'desc' }, ['created_at'], 'created_at').slice(0, 6);
  const recentConversations = sortItems(conversations.map(mapConversationItem), { sort: 'updated_at', order: 'desc' }, ['updated_at'], 'updated_at').slice(0, 6);
  return src({
    kpis: {
      total_count: conversations.length, evaluated_count: conversations.filter((c) => c.status === 'completed').length,
      scored_count: scores.length, stale_count: conversations.filter((c) => c.evaluation_stale).length,
      needs_review_count: conversations.filter((c) => c.status === 'needs_review').length,
      failed_count: conversations.filter((c) => c.status === 'failed').length,
      not_evaluated_count: conversations.filter((c) => c.status === 'not_evaluated').length,
      running_count: conversations.filter((c) => ['pending', 'running'].includes(c.status)).length,
      avg_score: mean(scores), median_score: median, avg_score_delta: null,
      gross_violations_count: conversations.filter((c) => !c.evaluation_stale && c.gross_violation === true).length,
      pending_appeals_count: appeals.filter((a) => a.status === 'open').length,
    },
    trend: [],
    score_distribution: [0, 20, 40, 60, 80].map((min) => ({ min, max: min + 20, upper_inclusive: min === 80, count: scores.filter((v) => v >= min && (min === 80 ? v <= 100 : v < min + 20)).length })),
    by_language: breakdown('language', (v) => LANG_OUT[v] || low(v)),
    by_channel: breakdown('channel', (v) => CHANNEL_OUT[v] || v),
    recent_conversations: recentConversations, recent_appeals: recent(appeals.map((a) => mapAppeal(a))),
    cost: stats.total, meta: { period: 'all', period_supported: false, trend_available: false, score_basis: 'latest completed non-stale evaluation', cost_basis: 'all recorded LLM calls, including repeats and appeals' },
  });
}
export async function getOverview({ signal } = {}) {
  const [conversations, appeals, stats] = await Promise.all([
    request('/conversations', { signal }), request('/appeals', { signal }), request('/stats/cost', { signal }),
  ]);
  return mapOverview(conversations, appeals, stats);
}
// В исходных данных нет стабильного идентификатора оператора.
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
  const evCache = new Map();
  let items = await Promise.all(raws.map(async (r) => {
    let ev = evCache.get(r.evaluation_id);
    if (!ev) { ev = request(`/evaluations/${enc(r.evaluation_id)}`, { signal }).then((x) => evaluationVm(x, signal)); evCache.set(r.evaluation_id, ev); }
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
