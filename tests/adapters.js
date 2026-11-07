// Unit- и интеграционные тесты адаптеров (браузер, без сборки). Запуск: tests/adapters.html  (+ ?live=1 для живого Backend на localhost).
// Unit: чистые мапперы realApiAdapter на фикстурах по API_CONTRACT.md, http-клиент на подменном fetch, фасад api.js.
// Live (?live=1): настоящий Backend БЕЗ вызовов модели — health, upload, список/деталь, фильтры, чек-листы, ошибки.
import * as real from '../js/realApiAdapter.js';
import * as mockA from '../js/mockAdapter.js';
import * as api from '../js/api.js';
import { request, ApiError, parseErrorBody } from '../js/http.js';

const out = document.getElementById('out');
const results = [];
let group = '';
const log = (ok, name, extra = '') => { results.push({ ok, name: `${group}: ${name}`, extra }); out.append(Object.assign(document.createElement('div'), { className: ok ? 'ok' : 'bad', textContent: `${ok ? 'PASS' : 'FAIL'} ${group}: ${name}${extra ? ` — ${extra}` : ''}` })); };
const check = (cond, name, extra) => log(!!cond, name, cond ? '' : extra);
const eq = (a, b, name) => check(JSON.stringify(a) === JSON.stringify(b), name, `${JSON.stringify(a)} ≠ ${JSON.stringify(b)}`);
const rejects = async (p, pred, name) => { try { await p; log(false, name, 'не отклонено'); } catch (e) { check(pred(e), name, `${e?.name}/${e?.kind}/${e?.status}`); } };
const test = async (name, fn) => { group = name; try { await fn(); } catch (e) { log(false, 'исключение', e?.stack || String(e)); } };
const live = new URLSearchParams(location.search).get('live') === '1';

// ---------- фикстуры: структура из API_CONTRACT.md / OpenAPI (тексты вымышленные, не данные организаторов) ----------
const CL_RAW = {
  checklist_version_id: 'v1', checklist_id: 'freedom-broker', version: 1, name: 'Freedom', weight_sum: '100', created_at: '2026-11-07T00:00:00Z', change_summary: null,
  sections: [{ id: 's1', name: 'A' }, { id: 's4', name: 'Грубые' }],
  criteria: [
    { criterion_id: '01', name: 'Контакт', description: '', section: 's1', type: 'yes_no', weight: 40, allow_na: false, na_condition: null, yes_text: 'да', no_text: 'нет', scale: {}, scored: true, order: 1, active: true },
    { criterion_id: '06', name: 'Ведение', description: 'd', section: 's1', type: 'scale', weight: 60, allow_na: true, na_condition: 'нет запроса', yes_text: null, no_text: null, scale: { 10: 'a', 7: 'b', 4: 'c', 0: 'd' }, scored: true, order: 2, active: true },
    { criterion_id: '08', name: 'Грубые', description: '', section: 's4', type: 'flag', weight: 0, allow_na: false, na_condition: null, yes_text: 'нарушение', no_text: 'нет', scale: {}, scored: false, order: 3, active: true },
    { criterion_id: '09', name: 'Выключен', description: '', section: 's1', type: 'yes_no', weight: 0, allow_na: false, na_condition: null, yes_text: 'x', no_text: 'y', scale: {}, scored: false, order: 4, active: false },
  ],
};
const EV_RAW = {
  evaluation_id: 'e1', conversation_id: 'c1', checklist_version_id: 'v1', status: 'completed', output_language: 'ru', total: '81.25', original_total: '70.00', final_total: '81.25', gross_violation: false,
  usage: { calls: 1 }, sections: [],
  original: { criteria: [{ criterion_id: '06', answer: '4', points: '7.50' }] },
  criteria: [
    { criterion_id: '01', answer: 'yes', reasoning: 'ok', evidence: [{ message_id: 'm2', quote: 'Добрый день', speaker: 'operator' }], effective_weight: '40.00', points: '40.00', has_open_appeal: false, name: 'Контакт', type: 'yes_no', weight: '40', valid: true, validation_errors: [], decisions: [] },
    { criterion_id: '06', answer: '10', reasoning: 'rev', evidence: [], effective_weight: '60.00', points: '41.25', has_open_appeal: false, name: 'Ведение', type: 'scale', weight: '60', valid: true, validation_errors: [], decisions: [{ appeal_id: 'a1', decision: 'change' }] },
    { criterion_id: '08', answer: 'no', reasoning: '', evidence: [], effective_weight: '0.00', points: '0.00', type: 'flag', weight: '0', valid: true, decisions: [] },
    { criterion_id: '03', answer: 'na', reasoning: 'нет вопроса', evidence: [], effective_weight: '0.00', points: '0.00', type: 'yes_no', weight: '20', valid: true, decisions: [] },
    { criterion_id: '05', answer: null, reasoning: null, evidence: [], effective_weight: null, points: null, type: 'yes_no', weight: '10', valid: false, validation_errors: ['bad evidence'], decisions: [] },
  ],
};

await test('mappers: conversations', () => {
  const m = real.mapConversationItem({ conversation_id: 'p1', language: 'KZ', channel: 'voice call', total: '92.31', gross_violation: false, status: 'completed', evaluation_id: 'e', has_appeal: true, has_open_appeal: false, evaluation_stale: true, updated_at: 't' });
  eq([m.id, m.language, m.channel, m.status, m.score, m.operator_id, m.evaluation_stale], ['p1', 'kk', 'call', 'reviewed', 92.31, null, true], 'язык KZ→kk, канал, статус, строковый балл → число, оператора нет');
  eq(real.convStatus({ status: 'not_evaluated' }), 'new', 'not_evaluated → new');
  eq(real.convStatus({ status: 'running' }), 'evaluating', 'running → evaluating');
  eq(real.convStatus({ status: 'completed', has_open_appeal: true }), 'appealed', 'открытая апелляция');
  eq(real.convStatus({ status: 'needs_review' }), 'needs_review', 'needs_review не скрывается');
  eq(real.mapConversationItem({ conversation_id: 'x', language: 'RU', channel: 'agent chat', total: null, status: 'not_evaluated' }).score, null, 'null-итог остаётся null (не 0)');
  eq(real.mapMessage({ message_id: 'm1', timestamp: '', speaker: 'client', text: 'hi', offset_sec: null, audio_url: null }), { id: 'm1', role: 'client', text: 'hi', ts: null, offset_sec: null, audio_url: null }, 'реплика: message_id сохраняется');
});

await test('mappers: checklist', () => {
  const c = real.mapChecklist(CL_RAW, 'v1');
  eq([c.id, c.version, c.is_current, c.criteria.length], ['v1', 1, true, 3], 'неактивные критерии скрыты, версия активна');
  eq(c.criteria.map((x) => x.answer_type), ['yes_no', 'scale', 'flag'], 'типы');
  eq(c.criteria[1].scale, { min: 0, max: 10 }, 'диапазон шкалы из ключей');
  check(c.criteria[2].gross && c.criteria[2].weight === 0, 'flag → gross, вес 0');
  check(real.mapChecklist(CL_RAW, 'other').is_current === false, 'неактивная версия');
});

await test('mappers: checklist → POST body (round-trip)', () => {
  const base = real.mapChecklist(CL_RAW, 'v1');
  const body = { base_version: 1, note: 'n', criteria: base.criteria.map((c) => ({ ...c })) };
  const r = real.toChecklistRequest(body, base);
  eq([r.name, r.checklist_id, r.sections.length, r.criteria.length], ['Freedom', 'freedom-broker', 2, 3], 'имя/семейство/секции сохраняются');
  eq(r.criteria[0].yes_text, 'да', 'yes_text берётся из исходного критерия');
  eq(r.criteria[1].scale, { 10: 'a', 7: 'b', 4: 'c', 0: 'd' }, 'нетронутая шкала не пересоздаётся (описания не теряются)');
  eq([r.criteria[1].allow_na, r.criteria[1].na_condition], [true, 'нет запроса'], 'N/A и условие');
  eq([r.criteria[2].weight, r.criteria[2].scored, r.criteria[2].allow_na, r.criteria[2].section], [0, false, false, 's4'], 'flag: вес 0, не оценивается, секция сохранена');
  body.criteria[1].scale = { min: 0, max: 3 };
  const r2 = real.toChecklistRequest(body, base);
  eq(Object.keys(r2.criteria[1].scale), ['0', '1', '2', '3'], 'изменённая шкала генерируется по диапазону');
  body.criteria.push({ id: 'x1', name: { ru: 'Новый', kk: '', en: '' }, description: 'описание', weight: 0, answer_type: 'yes_no', scale: null, na_allowed: false, na_condition: '', gross: false });
  const r3 = real.toChecklistRequest(body, base);
  const n = r3.criteria[3];
  eq([n.criterion_id, n.name, n.section, n.order, n.active, n.yes_text], ['x1', 'Новый', 's1', 4, true, 'описание'], 'новый критерий: локализованное имя → ru, секция по умолчанию');
});

await test('mappers: evaluation (баллы только из Backend)', () => {
  const ev = real.mapEvaluation(EV_RAW, new Map([['v1', 1]]), CL_RAW);
  eq([ev.id, ev.status, ev.checklist_version, ev.total_score, ev.final_total_score], ['e1', 'done', 1, 70, 81.25], 'original_total и final_total берутся как есть');
  const [c1, c6, c8, c3, c5] = ev.criteria;
  eq([c1.answer, c1.score, c1.weight, c1.evidence[0].message_id], [true, 40, 40, 'm2'], 'yes_no: ответ, points, effective_weight, evidence');
  eq([c6.answer, c6.score, c6.revised?.score, c6.weight, c6.original_answer], [10, 7.5, 41.25, 60, '4'], 'решение проверяющего: исходный балл сохранён, итоговый отдельно');
  check(c6.scale && c6.scale.max === 10 && c6.scale_keys.length === 4, 'шкала из чек-листа');
  check(c8.gross === false && c8.answer === false, 'flag без нарушения');
  check(c3.na && c3.na.reason === 'нет вопроса' && c3.score === null && c3.answer === null, 'N/A с причиной');
  check(c5.invalid && c5.score === null && c5.explanation.includes('bad evidence'), 'невалидный критерий не выдаёт балл');
  check(real.mapEvaluation({ ...EV_RAW, status: 'needs_review', total: null, original_total: null, final_total: null }, new Map(), CL_RAW).total_score === null, 'needs_review: итог null, не 0');
  eq(real.mapEvaluation({ ...EV_RAW, status: 'running' }, null, null).status, 'running', 'running');
  check(real.mapEvaluation({ ...EV_RAW, criteria: [{ ...EV_RAW.criteria[0], answer: 'yes', type: 'flag' }] }, null, null).criteria[0].gross, 'flag=yes → грубое нарушение');
});

await test('mappers: appeal', () => {
  const ev = real.mapEvaluation(EV_RAW, new Map([['v1', 1]]), CL_RAW);
  const open = real.mapAppeal({ appeal_id: 'a0', evaluation_id: 'e1', criterion_id: '01', comment: 'cc', status: 'open' }, ev);
  eq([open.status, open.conversation_id, open.original_score, open.max_score, open.decision], ['pending', 'c1', 40, 40, null], 'открытая → pending');
  const rev = real.mapAppeal({ appeal_id: 'a1', evaluation_id: 'e1', criterion_id: '06', comment: 'cc', status: 'reviewed', decision: { decision: 'change', original_answer: '4', final_answer: '10', reason: 'r', decided_by: 'reviewer', created_at: 't' }, original_total: '70.00', final_total: '81.25' }, ev);
  eq([rev.status, rev.original_score, rev.decision.new_answer, rev.original_total_score, rev.final_total_score], ['approved', 7.5, '10', 70, 81.25], 'change → approved; исходный балл сохранён; итог от Backend');
  eq(real.mapAppeal({ appeal_id: 'a2', evaluation_id: 'e1', criterion_id: '01', comment: 'c', status: 'reviewed', decision: { decision: 'confirm' } }, ev).status, 'rejected', 'confirm → rejected');
});

await test('http: формат ошибок и транспорт', async () => {
  eq(parseErrorBody({ error: { code: 'X', message: 'm' } }), { code: 'X', detail: 'm' }, '{error:{code,message}}');
  eq(parseErrorBody({ detail: 'old' }), { code: '', detail: 'old' }, 'FastAPI detail');
  const f = (res) => async () => res;
  const mk = (status, body) => ({ ok: status < 400, status, json: async () => body });
  window.CQA_API_BASE = 'http://x.test/api';
  await rejects(request('/a', { fetchImpl: f(mk(409, { error: { code: 'APPEAL_ALREADY_REVIEWED', message: 'уже' } })) }), (e) => e instanceof ApiError && e.kind === 'http' && e.status === 409 && e.code === 'APPEAL_ALREADY_REVIEWED' && e.detail === 'уже', '409 → ApiError(http,409,code)');
  await rejects(request('/a', { fetchImpl: async () => { throw new TypeError('fail'); } }), (e) => e.kind === 'network', 'сеть недоступна → network');
  await rejects(request('/a', { timeout: 30, fetchImpl: (u, o) => new Promise((_, rej) => o.signal.addEventListener('abort', () => rej(new DOMException('x', 'AbortError')))) }), (e) => e.kind === 'timeout', 'таймаут → timeout');
  const ac = new AbortController();
  const p = request('/a', { signal: ac.signal, fetchImpl: (u, o) => new Promise((_, rej) => o.signal.addEventListener('abort', () => rej(new DOMException('x', 'AbortError')))) });
  ac.abort();
  await rejects(p, (e) => e.name === 'AbortError', 'отмена устаревшего запроса → AbortError (не ApiError)');
  await rejects(request('/a', { fetchImpl: async () => ({ ok: true, status: 200, json: async () => { throw new Error('bad'); } }) }), (e) => e.kind === 'invalid', 'невалидный JSON → invalid');
  let seen;
  await request('/q', { query: { a: 1, b: '', c: null }, fetchImpl: async (u) => { seen = String(u); return { ok: true, status: 200, json: async () => ({}) }; } });
  eq(seen, 'http://x.test/api/q?a=1', 'пустые query-параметры не отправляются');
  delete window.CQA_API_BASE;
});

await test('facade: режимы и честные ограничения', async () => {
  api.setMode('demo');
  const r = await api.getConversations({ page_size: 2 });
  check(r.meta.source === 'mock' && r.items.length === 2, 'demo → mock');
  window.CQA_API_BASE = 'http://127.0.0.1:9/api';
  api.setMode('api');
  await rejects(api.getConversations({}), (e) => e.kind === 'network', 'api + Backend недоступен → network (мок НЕ подставляется)');
  await rejects(api.getOperators({}), (e) => e.kind === 'unsupported', 'операторы: unsupported');
  await rejects(api.getOverview({}), (e) => e.kind === 'unsupported', 'overview: unsupported');
  await rejects(api.assignOperator('x', 'OP-1'), (e) => e.kind === 'unsupported', 'ручная привязка: unsupported');
  delete window.CQA_API_BASE;
  api.setMode('demo');
  check(!!mockA.getConversations && !!real.getConversations, 'оба адаптера экспортируют одинаковый набор');
  const names = Object.keys(mockA).filter((k) => typeof mockA[k] === 'function').sort();
  const missing = names.filter((k) => typeof real[k] !== 'function');
  eq(missing, [], 'у реального адаптера есть все методы мока');
});

if (live) {
  window.CQA_API_BASE = 'http://localhost:8001/api';
  api.setMode('api');
  const id = `fe-live-${Date.now().toString(36)}`;
  await test('LIVE: health и ошибки Backend', async () => {
    const h = await api.getHealth();
    eq([h.status, h.database], ['ok', 'ok'], 'GET /health');
    await rejects(api.getConversation('no-such-dialog'), (e) => e.kind === 'http' && e.status === 404 && e.code === 'CONVERSATION_NOT_FOUND' && !!e.detail, '404 CONVERSATION_NOT_FOUND в едином формате');
    await rejects(api.getEvaluation('no-such-eval'), (e) => e.status === 404, '404 для оценки');
    await rejects(api.getAppeal('no-such'), (e) => e.status === 404, '404 для апелляции');
  });
  await test('LIVE: upload → list → detail', async () => {
    const imp = await api.importConversations({ conversations: [{ id, language: 'kk', channel: 'chat', messages: [{ role: 'client', text: '<img src=x onerror=alert(1)> Сәлем' }, { role: 'operator', text: 'Сәлеметсіз бе' }] }, { bad: true }] });
    eq([imp.imported, imp.skipped, imp.errors[0].index], [1, 1, 1], 'импорт: 1 принят, 1 отклонён локально');
    const list = await api.getConversations({ q: id });
    eq([list.total, list.items[0].id, list.items[0].language, list.items[0].channel, list.items[0].status, list.items[0].score], [1, id, 'kk', 'chat', 'new', null], 'в списке: KZ→kk, agent chat→chat, not_evaluated→new, итога нет');
    check(list.meta.source === 'api', 'meta.source = api');
    const kz = await api.getConversations({ language: 'kk', q: id });
    eq(kz.total, 1, 'фильтр language=kk → KZ');
    const en = await api.getConversations({ language: 'en', q: id });
    eq(en.total, 0, 'фильтр language=en исключает');
    const call = await api.getConversations({ channel: 'call', q: id });
    eq(call.total, 0, 'фильтр channel=call исключает');
    const d = await api.getConversation(id);
    eq([d.messages.length, d.messages[0].id, d.messages[1].id, d.messages[0].role, d.evaluations.length, d.score], [2, 'm1', 'm2', 'client', 0, null], 'деталь: message_id m1/m2, роли, нет оценок, нет итога');
    check(d.messages[0].text.includes('<img'), 'текст реплики приходит как есть (экранирование — на стороне UI)');
    const pg = await api.getConversations({ page: 1, page_size: 1 });
    check(pg.items.length === 1 && pg.total >= 1 && pg.meta.client_side_paging, 'клиентская пагинация помечена в meta');
    const evs = await api.getEvaluations({});
    check(Array.isArray(evs.items) && evs.meta.derived_from, 'список оценок производный и помечен');
  });
  await test('LIVE: чек-листы', async () => {
    const before = await api.getChecklists();
    const cur = before.items.find((c) => c.is_current);
    check(cur && cur.criteria.length >= 1, 'есть активная версия');
    check(cur.criteria.every((c) => ['yes_no', 'scale', 'flag'].includes(c.answer_type)), 'типы критериев известны UI');
    const sum = cur.criteria.filter((c) => c.answer_type !== 'flag').reduce((s, c) => s + c.weight, 0);
    eq(sum, Number(cur.weight_sum), 'сумма весов оцениваемых критериев = weight_sum от Backend (Backend нормирует итог к 100 сам)');
    const draft = JSON.parse(JSON.stringify(cur.criteria));
    draft[0].description = `live-test ${id}`;
    const saved = await api.saveChecklist({ base_version: cur.version, note: 'live', criteria: draft });
    eq(saved.version, cur.version + 1, 'сохранение создаёт НОВУЮ версию');
    const after = await api.getChecklists();
    const old = after.items.find((c) => c.version === cur.version);
    eq(JSON.stringify(old.criteria.map((c) => c._raw)), JSON.stringify(cur.criteria.map((c) => c._raw)), 'историческая версия не изменилась');
    check(after.items.find((c) => c.version === saved.version).is_current, 'новая версия активна');
    const bad = JSON.parse(JSON.stringify(draft));
    bad.push({ ...bad[0] });   // дубликат criterion_id: Backend обязан отклонить (веса он НЕ требует = 100, а нормирует итог сам)
    await rejects(api.saveChecklist({ base_version: saved.version, note: '', criteria: bad }), (e) => e.kind === 'http' && e.status === 422, 'дубликат criterion_id → 422 от Backend, версия не создана');
    const still = await api.getChecklists();
    eq(still.items.length, after.items.length, 'после 422 число версий прежнее');
  });
  await test('LIVE: оценка без модели', async () => {
    await rejects(api.runEvaluation({ conversation_id: id, checklist_version: 9999 }), (e) => e.status === 404, 'несуществующая версия чек-листа → 404');
    await rejects(api.createAppeal({ evaluation_id: 'nope', criterion_id: '01', comment: 'комментарий достаточной длины' }), (e) => e.kind === 'http' && e.status >= 400, 'апелляция на несуществующую оценку отклоняется');
  });
  delete window.CQA_API_BASE;
  api.setMode('demo');
}

const failed = results.filter((r) => !r.ok);
const summary = `ADAPTERS ${failed.length ? 'FAIL' : 'PASS'} ${results.length - failed.length}/${results.length}${live ? ' (live)' : ''}`;
document.title = summary;
out.prepend(Object.assign(document.createElement('h2'), { textContent: summary }));
window.__ADAPTERS__ = { summary, failed, total: results.length };
