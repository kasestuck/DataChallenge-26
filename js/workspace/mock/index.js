// Mock Adapter: реализует предполагаемые endpoints Backend поверх демо-набора из seed.js.
// Подключается ТОЛЬКО из js/api.js (режим demo). Ошибки бросаются как MockHttp — api.js превращает их в ApiError.
// Контракт временный: docs/api-contract.md.
import { BASE_CRITERIA, SCRIPTS, EXPLAIN, DAY, OPERATORS, buildStore, clamp, round1, dayKey, rng, scoreCriteria, totalOf } from './seed.js';

let S = null;
const st = () => (S ||= buildStore());
export const mockReset = () => { S = null; msgCache.clear(); };

const fail = (status, message) => Object.assign(new Error(message || `HTTP ${status}`), { name: 'MockHttp', status });
const low = (v) => String(v ?? '').toLowerCase();
const numId = (id) => Number(String(id).replace(/\D/g, '')) || 1;
const byId = (id) => st().conversations.find((c) => c.id === id);
const doneStatus = (c) => c.score != null && ['evaluated', 'appealed', 'reviewed'].includes(c.status);

function paginate(items, q, def = 20) {
  const size = clamp(Number(q.page_size) || def, 1, 100);
  const total = items.length;
  const pages = Math.max(1, Math.ceil(total / size));
  const page = clamp(Number(q.page) || 1, 1, pages);
  return { items: items.slice((page - 1) * size, page * size), total, page, page_size: size };
}
function sortItems(items, q, fields, def) {
  const key = fields.includes(q.sort) ? q.sort : def;
  const dir = q.order === 'asc' ? 1 : -1;
  return items.sort((a, b) => {
    const x = a[key], y = b[key];
    if (x == null && y == null) return 0;
    if (x == null) return 1;           // пустые значения всегда в конце
    if (y == null) return -1;
    return dir * (typeof x === 'number' ? x - y : String(x).localeCompare(String(y)));
  });
}

// ---------- диалоги ----------
const scenarioOf = (c) => (c.gross_violation || c._q < 0.55 ? 'poor' : 'good');
const msgCache = new Map();
function messagesOf(c) {
  if (c._messages) return c._messages;
  if (msgCache.has(c.id)) return msgCache.get(c.id);
  const sc = SCRIPTS[c.language][scenarioOf(c)];
  const r = rng(numId(c.id));
  let t = new Date(c.started_at).getTime(), off = 0;
  const out = sc.lines.map(([role, text], i) => {
    const step = 8 + Math.floor(r() * 50);
    t += step * 1000; off += step;
    return { id: `m${i + 1}`, role: { b: 'bot', c: 'client', o: 'operator', s: 'system' }[role], text, ts: new Date(t).toISOString(), ...(c.channel === 'call' ? { offset_sec: off } : {}) };
  });
  msgCache.set(c.id, out);
  return out;
}
function convSummary(c) {
  const { _q, _day, _messages, ...rest } = c;
  return { ...rest, messages_count: messagesOf(c).length };
}
const latestEval = (cid) => [...st().evalCores.values()].filter((e) => e.conversation_id === cid).sort((a, b) => b.created_at.localeCompare(a.created_at))[0];

export function conversations(q = {}) {
  const needle = low(q.q).trim();
  let list = st().conversations.filter((c) => {
    if (q.language && c.language !== q.language) return false;
    if (q.channel && c.channel !== q.channel) return false;
    if (q.status && c.status !== q.status) return false;
    if (q.operator_id === 'none' ? c.operator_id : q.operator_id && c.operator_id !== q.operator_id) return false;
    if (q.gross === '1' && !c.gross_violation) return false;
    if (needle && !(low(c.id).includes(needle) || low(c.operator_id).includes(needle) || messagesOf(c).some((m) => low(m.text).includes(needle)))) return false;
    return true;
  }).map((c) => ({ ...c }));
  sortItems(list, q, ['started_at', 'score', 'id'], 'started_at');
  const res = paginate(list, q);
  res.items = res.items.map(convSummary);
  return res;
}

export function conversation(id) {
  const c = byId(id);
  if (!c) throw fail(404, 'conversation not found');
  return {
    ...convSummary(c),
    messages: messagesOf(c),
    evaluations: [...st().evalCores.values()].filter((e) => e.conversation_id === id).map((e) => { maybeFinish(e); return e; }).map((e) => ({ id: e.id, checklist_version: e.checklist_version, status: e.status, total_score: e.total, created_at: e.created_at })),
  };
}

export function assignOperator(id, body = {}) {
  const c = byId(id);
  if (!c) throw fail(404, 'conversation not found');
  const op = String(body.operator_id ?? '').trim();
  if (!op || op.length > 64) throw fail(422, 'operator_id is required');
  c.operator_id = op;
  return convSummary(c);
}

export function importConversations(body = {}) {
  const arr = body.conversations;
  if (!Array.isArray(arr) || !arr.length) throw fail(422, 'conversations must be a non-empty array');
  const errors = []; let imported = 0;
  arr.forEach((x, index) => {
    const okMsgs = Array.isArray(x?.messages) && x.messages.length && x.messages.every((m) => ['client', 'operator', 'bot', 'system'].includes(m?.role) && typeof m?.text === 'string' && m.text.trim());
    if (!['ru', 'kk', 'en'].includes(x?.language)) return errors.push({ index, error: 'language' });
    if (!['chat', 'call'].includes(x?.channel)) return errors.push({ index, error: 'channel' });
    if (!okMsgs) return errors.push({ index, error: 'messages' });
    const id = `IMP-${String(st().conversations.length + 1).padStart(4, '0')}`;
    const start = x.started_at && !Number.isNaN(Date.parse(x.started_at)) ? new Date(x.started_at) : new Date();
    st().conversations.push({
      id, language: x.language, channel: x.channel, operator_id: x.operator_id || null, status: 'new', score: null, gross_violation: false, started_at: start.toISOString(), _q: 0.7, _day: dayKey(start), audio_url: x.audio_url || null,
      _messages: x.messages.map((m, i) => ({ id: `m${i + 1}`, role: m.role, text: m.text, ts: m.ts || start.toISOString(), ...(m.offset_sec != null ? { offset_sec: m.offset_sec } : {}) })),
    });
    imported++;
  });
  return { imported, skipped: errors.length, errors };
}

// ---------- чек-листы ----------
const checklistOf = (v) => st().checklists.find((c) => c.version === v);
export const checklists = () => ({ items: st().checklists.map((c) => ({ ...c, criteria: c.criteria.map((x) => ({ ...x })) })).sort((a, b) => b.version - a.version) });

export function saveChecklist(body = {}) {
  const crit = body.criteria;
  if (!Array.isArray(crit) || !crit.length) throw fail(422, 'criteria required');
  const bad = crit.find((c) => !String((typeof c.name === 'object' ? Object.values(c.name || {}).join('') : c.name) || '').trim());
  if (bad) throw fail(422, 'criterion name required');
  const sum = crit.filter((c) => c.answer_type !== 'flag').reduce((s, c) => s + Number(c.weight || 0), 0);
  if (Math.round(sum * 100) !== 10000) throw fail(422, `weights must sum to 100 (got ${sum})`);
  const next = Math.max(...st().checklists.map((c) => c.version)) + 1;
  st().checklists.forEach((c) => { c.is_current = false; });
  const cl = { id: `CL-DEMO-${next}`, version: next, created_at: new Date().toISOString(), is_current: true, note: String(body.note || ''), criteria: crit.map((c) => ({ ...c })) };
  st().checklists.push(cl);
  return { ...cl };
}

// ---------- оценки ----------
const loc = (v, lang) => (v && typeof v === 'object' ? v[lang] || v.ru || '' : v || '');
const revisedRows = (core) => core.rows.map((r) => (core.revisions[r.criterion_id] ? { ...r, score: core.revisions[r.criterion_id].score } : r));

function maybeFinish(core) {
  if (core.status !== 'running' || Date.now() < core.ready_at) return;
  const conv = byId(core.conversation_id);
  const cl = checklistOf(core.checklist_version);
  core.rows = scoreCriteria(cl.criteria, conv._q, rng(numId(conv.id) + core.checklist_version));
  core.total = totalOf(cl.criteria, core.rows);
  core.gross = core.rows.some((x) => x.criterion_id === cl.criteria.find((c) => c.answer_type === 'flag')?.id && x.answer === true);
  core.status = 'done';
  conv.status = 'evaluated'; conv.score = core.total; conv.gross_violation = core.gross;
}

function evalSummary(core) {
  const conv = byId(core.conversation_id);
  const rows = revisedRows(core);
  const cl = checklistOf(core.checklist_version);
  return {
    id: core.id, conversation_id: core.conversation_id, checklist_version: core.checklist_version, status: core.status,
    total_score: core.total ?? null, final_total_score: core.status === 'done' ? totalOf(cl.criteria, rows) : null, gross_violation: !!core.gross, created_at: core.created_at,
    language: conv.language, operator_id: conv.operator_id, error: core.error || null,
  };
}

function evalFull(core) {
  maybeFinish(core);
  const conv = byId(core.conversation_id);
  const cl = checklistOf(core.checklist_version);
  const msgs = messagesOf(conv);
  const sc = SCRIPTS[conv.language][scenarioOf(conv)];
  const ex = EXPLAIN[conv.language];
  const out = { ...evalSummary(core), source: 'mock', criteria: [] };
  if (core.status !== 'done') return out;
  const firstOp = msgs.find((m) => m.role === 'operator');
  out.criteria = cl.criteria.map((c) => {
    const r = core.rows.find((x) => x.criterion_id === c.id) || { answer: null, score: null, na: true };
    const base = { criterion_id: c.id, name: c.name, description: c.description, weight: c.weight, answer_type: c.answer_type, scale: c.scale, gross: !!c.gross };
    if (r.na) return { ...base, answer: null, score: null, na: { reason: loc(c.na_condition, conv.language) || '—' }, explanation: '', evidence: [] };
    let quality, evidence = [];
    if (c.answer_type === 'flag') quality = r.answer ? 'flagYes' : 'flagNo';
    else if (c.answer_type === 'yes_no') quality = r.answer ? 'good' : 'bad';
    else quality = r.answer >= (c.scale?.max ?? 5) ? 'good' : r.answer <= (c.scale?.min ?? 0) ? 'bad' : 'partial';
    if (quality !== 'bad' && quality !== 'flagNo') {
      const idx = c.gross ? sc.rude : sc.ev[c.id];
      const m = idx != null && c.builtin ? msgs[idx] : firstOp;
      if (m) evidence = [{ message_id: m.id, quote: m.text.slice(0, 80) }];
    }
    const rev = core.revisions[c.id];
    return { ...base, answer: r.answer, score: r.score, na: null, explanation: ex[quality], evidence, ...(rev ? { revised: { score: rev.score, appeal_id: rev.appeal_id } } : {}) };
  });
  return out;
}

export function evaluation(id) {
  const core = st().evalCores.get(id);
  if (!core) throw fail(404, 'evaluation not found');
  return evalFull(core);
}

export function runEvaluation(body = {}) {
  const conv = byId(body.conversation_id);
  if (!conv) throw fail(404, 'conversation not found');
  if (typeof window !== 'undefined' && window.CQA_MOCK_FAIL_AI) throw fail(503, 'AI service unavailable');
  const cur = Math.max(...st().checklists.map((c) => c.version));
  const version = Number(body.checklist_version) || cur;
  if (!checklistOf(version)) throw fail(404, 'checklist version not found');
  const id = version === 1 ? `EV-${conv.id}` : `EV-${conv.id}-v${version}`;
  if (st().evalCores.has(id)) throw fail(409, 'evaluation for this version already exists');
  const core = { id, conversation_id: conv.id, checklist_version: version, rows: [], total: null, gross: false, created_at: new Date().toISOString(), status: 'running', ready_at: Date.now() + 1600, revisions: {} };
  st().evalCores.set(id, core);
  conv.status = 'evaluating';
  return evalFull(core);
}

export function evaluations(q = {}) {
  const needle = low(q.q).trim();
  const lo = q.score_min !== undefined && q.score_min !== '' ? Number(q.score_min) : null;
  const hi = q.score_max !== undefined && q.score_max !== '' ? Number(q.score_max) : null;
  const items = [...st().evalCores.values()].map((c) => { maybeFinish(c); return evalSummary(c); }).filter((e) => {
    if (q.language && e.language !== q.language) return false;
    if (q.status && e.status !== q.status) return false;
    if (q.checklist_version && e.checklist_version !== Number(q.checklist_version)) return false;
    if (lo != null && (e.total_score == null || e.total_score < lo)) return false;
    if (hi != null && (e.total_score == null || e.total_score > hi)) return false;
    if (q.gross === '1' && !e.gross_violation) return false;
    if (needle && !(low(e.id).includes(needle) || low(e.conversation_id).includes(needle) || low(e.operator_id).includes(needle))) return false;
    return true;
  });
  sortItems(items, q, ['created_at', 'total_score', 'conversation_id'], 'created_at');
  return paginate(items, q);
}

// ---------- операторы ----------
function operatorRows() {
  const map = new Map();
  st().conversations.forEach((c) => {
    if (!c.operator_id) return;
    const o = map.get(c.operator_id) || { id: c.operator_id, name: null, languages: new Set(), conversations_count: 0, scores: [], gross_violations_count: 0 };
    o.languages.add(c.language); o.conversations_count++;
    if (doneStatus(c)) { o.scores.push(c.score); if (c.gross_violation) o.gross_violations_count++; }
    map.set(c.operator_id, o);
  });
  const apByOp = new Map();
  st().appeals.forEach((a) => { const c = byId(a.conversation_id); if (c?.operator_id) apByOp.set(c.operator_id, (apByOp.get(c.operator_id) || 0) + 1); });
  return [...map.values()].map((o) => ({
    id: o.id, name: o.name, languages: [...o.languages].sort(), conversations_count: o.conversations_count, evaluated_count: o.scores.length,
    avg_score: o.scores.length ? round1(o.scores.reduce((a, b) => a + b, 0) / o.scores.length) : null, gross_violations_count: o.gross_violations_count, appeals_count: apByOp.get(o.id) || 0,
  }));
}

export function operators(q = {}) {
  const needle = low(q.q).trim();
  const items = operatorRows().filter((o) => !needle || low(o.id).includes(needle) || low(o.name).includes(needle));
  sortItems(items, q, ['avg_score', 'evaluated_count', 'gross_violations_count', 'appeals_count', 'id'], 'avg_score');
  const res = paginate(items, q, 50);
  res.unassigned_count = st().conversations.filter((c) => !c.operator_id).length;
  return res;
}

export function operator(id) {
  const row = operatorRows().find((o) => o.id === id);
  if (!row) throw fail(404, 'operator not found');
  const convs = st().conversations.filter((c) => c.operator_id === id);
  const done = convs.filter(doneStatus);
  const byDay = new Map();
  done.forEach((c) => { (byDay.get(c._day) || byDay.set(c._day, []).get(c._day)).push(c.score); });
  const trend = [...byDay.keys()].sort().slice(-60).map((d) => ({ date: d, avg_score: round1(byDay.get(d).reduce((a, b) => a + b, 0) / byDay.get(d).length), count: byDay.get(d).length }));
  const agg = new Map(); const versions = new Set();
  done.forEach((c) => {
    const core = [...st().evalCores.values()].filter((e) => e.conversation_id === c.id && e.status === 'done').sort((a, b) => b.created_at.localeCompare(a.created_at))[0];
    if (!core) return;
    versions.add(core.checklist_version);
    const cl = checklistOf(core.checklist_version);
    core.rows.forEach((r) => {
      const cr = cl.criteria.find((x) => x.id === r.criterion_id);
      if (!cr || cr.answer_type === 'flag' || r.na || !cr.weight) return;
      const a = agg.get(cr.id) || { criterion_id: cr.id, name: cr.name, earned: 0, max: 0, count: 0 };
      a.earned += r.score; a.max += cr.weight; a.count++; agg.set(cr.id, a);
    });
  });
  const criteria_stats = [...agg.values()].map((a) => ({ criterion_id: a.criterion_id, name: a.name, avg_pct: round1((a.earned / a.max) * 100), count: a.count })).sort((a, b) => b.avg_pct - a.avg_pct);
  return {
    ...row, trend, criteria_stats, checklist_versions: [...versions].sort(), mixed_versions: versions.size > 1,
    conversations: convs.slice().sort((a, b) => b.started_at.localeCompare(a.started_at)).slice(0, 10).map(convSummary),
    appeals: st().appeals.filter((a) => byId(a.conversation_id)?.operator_id === id).sort((a, b) => b.created_at.localeCompare(a.created_at)).slice(0, 10).map(appealItem),
  };
}

// ---------- апелляции ----------
function criterionName(a) {
  const core = st().evalCores.get(a.evaluation_id);
  const cr = core && checklistOf(core.checklist_version)?.criteria.find((c) => c.id === a.criterion_id);
  return cr ? cr.name : a.criterion_id;
}
const appealItem = (a) => ({ ...a, criterion_name: criterionName(a), operator_id: byId(a.conversation_id)?.operator_id ?? null });

export function appeals(q = {}) {
  const needle = low(q.q).trim();
  const items = st().appeals.map(appealItem).filter((a) => {
    if (q.status && a.status !== q.status) return false;
    if (q.conversation_id && a.conversation_id !== q.conversation_id) return false;
    if (q.operator_id && a.operator_id !== q.operator_id) return false;
    if (needle && !(low(a.id).includes(needle) || low(a.conversation_id).includes(needle) || low(a.comment).includes(needle) || low(a.operator_id).includes(needle))) return false;
    return true;
  });
  sortItems(items, q, ['created_at', 'status', 'original_score'], 'created_at');
  return paginate(items, q);
}

export function appeal(id) {
  const a = st().appeals.find((x) => x.id === id);
  if (!a) throw fail(404, 'appeal not found');
  const full = evalFull(st().evalCores.get(a.evaluation_id));
  return { ...appealItem(a), evaluation: full, original_total_score: full.total_score, final_total_score: full.final_total_score, criterion: full.criteria.find((c) => c.criterion_id === a.criterion_id) || null };
}

export function createAppeal(body = {}) {
  const core = st().evalCores.get(body.evaluation_id);
  if (!core) throw fail(404, 'evaluation not found');
  const full = evalFull(core);
  const cr = full.criteria.find((c) => c.criterion_id === body.criterion_id);
  if (!cr) throw fail(404, 'criterion not found');
  if (cr.na) throw fail(422, 'criterion is not applicable');
  if (String(body.comment || '').trim().length < 10) throw fail(422, 'comment too short');
  if (st().appeals.some((a) => a.evaluation_id === core.id && a.criterion_id === cr.criterion_id && a.status === 'pending')) throw fail(409, 'appeal already pending');
  const a = { id: `APL-${core.conversation_id}-${cr.criterion_id}-${st().appeals.length + 1}`, conversation_id: core.conversation_id, evaluation_id: core.id, criterion_id: cr.criterion_id, original_score: cr.score, max_score: cr.weight, status: 'pending', comment: String(body.comment).trim(), created_at: new Date().toISOString(), decision: null };
  st().appeals.push(a);
  byId(core.conversation_id).status = 'appealed';
  return appealItem(a);
}

export function decideAppeal(id, body = {}) {
  const a = st().appeals.find((x) => x.id === id);
  if (!a) throw fail(404, 'appeal not found');
  if (a.status !== 'pending') throw fail(409, 'appeal already decided');
  if (!['approved', 'rejected'].includes(body.decision)) throw fail(422, 'decision must be approved or rejected');
  if (!String(body.reason || '').trim()) throw fail(422, 'reason required');
  let newScore = null;
  if (body.decision === 'approved') {
    newScore = Number(body.new_score);
    if (!Number.isFinite(newScore) || newScore < 0 || newScore > a.max_score) throw fail(422, 'new_score out of range');
    st().evalCores.get(a.evaluation_id).revisions[a.criterion_id] = { score: newScore, appeal_id: a.id };
  }
  a.status = body.decision;
  a.decision = { reviewer: 'demo-reviewer', reason: String(body.reason).trim(), new_score: newScore, decided_at: new Date().toISOString() };
  const conv = byId(a.conversation_id);
  conv.status = st().appeals.some((x) => x.conversation_id === conv.id && x.status === 'pending') ? 'appealed' : 'reviewed';
  return appeal(id);
}

// ---------- обзор (Workspace → Overview) ----------
const BUCKETS = [[0, 49], [50, 64], [65, 79], [80, 89], [90, 100]];
const avg = (xs) => (xs.length ? xs.reduce((a, b) => a + b, 0) / xs.length : null);
export function overview(days) {
  const { conversations: all, appeals: aps, today } = st();
  const from = new Date(today); from.setDate(from.getDate() - (days - 1));
  const prevFrom = new Date(from); prevFrom.setDate(prevFrom.getDate() - days);
  const fk = dayKey(from), pk = dayKey(prevFrom);
  const scored = all.filter(doneStatus);
  const cur = scored.filter((c) => c._day >= fk), prev = scored.filter((c) => c._day >= pk && c._day < fk);
  const byDay = new Map();
  cur.forEach((c) => { (byDay.get(c._day) || byDay.set(c._day, []).get(c._day)).push(c.score); });
  const curAvg = avg(cur.map((c) => c.score)), prevAvg = avg(prev.map((c) => c.score));
  const strip = (c) => { const { _q, _day, _messages, ...r } = c; return r; };
  return {
    period: { from: fk, to: dayKey(today), days },
    kpis: {
      avg_score: round1(curAvg), avg_score_delta: curAvg != null && prevAvg != null ? round1(curAvg - prevAvg) : null, evaluated_count: cur.length,
      gross_violations_count: cur.filter((c) => c.gross_violation).length, pending_appeals_count: aps.filter((a) => a.status === 'pending').length,
    },
    trend: [...byDay.keys()].sort().map((d) => ({ date: d, avg_score: round1(avg(byDay.get(d))), count: byDay.get(d).length })),
    score_distribution: BUCKETS.map(([min, max]) => ({ min, max, count: cur.filter((c) => c.score >= min && c.score <= max).length })),
    by_language: ['ru', 'kk', 'en'].map((l) => { const xs = cur.filter((c) => c.language === l).map((c) => c.score); return { language: l, count: xs.length, avg_score: round1(avg(xs)) }; }),
    recent_conversations: cur.slice().sort((a, b) => b.started_at.localeCompare(a.started_at)).slice(0, 6).map((c) => ({ ...strip(c), evaluated_at: c.started_at })),
    recent_appeals: aps.filter((a) => a.created_at.slice(0, 10) >= fk).sort((a, b) => b.created_at.localeCompare(a.created_at)).slice(0, 5).map(appealItem),
  };
}

export { OPERATORS, BASE_CRITERIA, DAY, latestEval };
