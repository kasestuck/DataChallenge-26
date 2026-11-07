// Deterministic contract checks. All transcripts and responses below are synthetic test data.
// Run: node tests/contract_adapter_node.mjs (no server, database, model or network needed).
import assert from 'node:assert/strict';
import { test } from 'node:test';

const storage = new Map();
globalThis.localStorage = { getItem: (key) => storage.get(key) ?? null, setItem: (key, value) => storage.set(key, String(value)), removeItem: (key) => storage.delete(key) };
globalThis.window = { location: { protocol: 'http:', origin: 'http://127.0.0.1:8911' } };
const real = await import('../js/realApiAdapter.js');
const api = await import('../js/api.js');
const { getApiBase } = await import('../js/config.js');
const { ApiError, request } = await import('../js/http.js');
const response = (body, status = 200) => new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });
const record = (id, extra = {}) => ({ conversation_id: id, language: 'RU', channel: 'agent chat', audio_url: null, transcript: [{ timestamp: '00:00', speaker: 'client', text: 'Синтетический вопрос.' }, { timestamp: '00:01', speaker: 'operator', text: 'Синтетический ответ.' }], ...extra });
const withFetch = async (impl, fn) => {
  const previous = globalThis.fetch;
  globalThis.fetch = impl;
  real.resetCache();
  try { return await fn(); } finally { globalThis.fetch = previous; real.resetCache(); api.setMode('demo'); }
};

test('same-origin API default, explicit override and unknown values preserved', () => {
  assert.equal(getApiBase(), 'http://127.0.0.1:8911/api');
  window.CQA_API_BASE = 'http://other.test/api/';
  assert.equal(getApiBase(), 'http://other.test/api');
  delete window.CQA_API_BASE;
  const mapped = real.mapConversationItem({ conversation_id: 'synthetic', status: 'not_evaluated', total: null, dataset_group_id: 'UPLOAD', channel: 'whatsapp', language: 'KZ' });
  assert.equal(mapped.gross_violation, null);
  assert.equal(mapped.score, null);
  assert.equal(mapped.dataset_group_id, 'UPLOAD');
  assert.equal(mapped.channel, 'whatsapp');
  const invalid = real.mapCriterion({ criterion_id: 'unknown', type: 'yes_no', answer: null, valid: false }, { allow_na: true });
  assert.equal(invalid.answer, null);
  assert.equal(invalid.allow_na, true);
  assert.equal(real.mapCriterion({ criterion_id: 'flag', type: 'flag', answer: 'no' }, { allow_na: true }).allow_na, false);
});

test('official, array and legacy imports; timestamps and original transcript indices retained', () => {
  const official = record('synthetic-import', { channel: 'whatsapp', language: 'KZ' });
  official.transcript = [{ speaker: 'system', text: 'Synthetic event.', timestamp: null }, { speaker: 'client', text: 'Сәлем.' }, { speaker: 'operator', text: 'Сәлеметсіз бе.', timestamp: '02:03' }];
  for (const envelope of [[official], { records: [official] }, { conversations: [official] }]) assert.equal(real.getImportRecords(envelope)[0], official);
  assert.equal(api.getImportRecords({ records: [official] })[0], official);
  const uploaded = real.toUploadRequest(official);
  assert.equal(uploaded.channel, 'whatsapp');
  assert.equal(uploaded.conversation_id, 'synthetic-import');
  assert.deepEqual(uploaded.transcript.map((m) => m.timestamp), [null, '', '02:03']);
  assert.deepEqual(uploaded.transcript.map((m) => m.speaker), ['system', 'client', 'operator']);
  const legacy = real.toUploadRequest({ id: 'legacy', language: 'kk', channel: 'call', messages: [{ role: 'client', text: 'Synthetic question.' }, { role: 'operator', text: 'Synthetic answer.', ts: '2026-01-02T01:02:03+05:00' }] });
  assert.equal(legacy.channel, 'voice call');
  assert.equal(legacy.language, 'KZ');
  assert.equal(legacy.transcript[1].timestamp, '2026-01-02T01:02:03+05:00');
  assert.throws(() => real.toUploadRequest(record('broken', { transcript: [{ speaker: 'client', text: '' }] })), /Реплика 1/);
  assert.throws(() => real.toUploadRequest(record('broken', { language: 'DE' })), /language/);
  assert.throws(() => real.getImportRecords({ records: [] }), /непустой/);
});

test('sequential imports continue after row validation and backend collision; primary error retained', async () => {
  const calls = []; let active = 0, peak = 0;
  await withFetch(async (url, options) => {
    active += 1; peak = Math.max(peak, active);
    assert.equal(new URL(url).pathname, '/api/conversations/upload');
    const body = JSON.parse(options.body); calls.push(body);
    await new Promise((resolve) => setTimeout(resolve, 2)); active -= 1;
    return body.conversation_id === 'collision'
      ? response({ error: { code: 'UPLOAD_ID_CONFLICT', message: 'ID занят набором DEV.' } }, 409)
      : response({ conversation_id: body.conversation_id, dataset_group_id: 'UPLOAD' });
  }, async () => {
    const result = await real.importConversations({ records: [record('accepted-1'), record('invalid', { transcript: [{ speaker: 'operator', text: '' }] }), record('collision'), record('accepted-2', { channel: 'custom channel' })] });
    assert.equal(result.imported, 2); assert.equal(result.skipped, 2);
    assert.deepEqual(result.results.map((r) => [r.index, r.conversation_id, r.status]), [[0, 'accepted-1', 'accepted'], [1, 'invalid', 'rejected'], [2, 'collision', 'rejected'], [3, 'accepted-2', 'accepted']]);
    assert.equal(result.errors[1].code, 'UPLOAD_ID_CONFLICT');
    assert.equal(result.errors[1].error, 'ID занят набором DEV.');
    assert.equal(peak, 1); assert.equal(calls.length, 3);
    assert.equal(calls[2].channel, 'custom channel');
    assert.ok(calls.every((c) => !Object.hasOwn(c, 'dataset_group_id')));
  });
});

test('overview aggregates actual snapshot, excludes stale/invalid scores, retains unknown cost', async () => {
  const conversations = [
    { conversation_id: 'a', language: 'RU', channel: 'agent chat', status: 'completed', total: '20', gross_violation: true },
    { conversation_id: 'b', language: 'KZ', channel: 'voice call', status: 'completed', total: '100', gross_violation: false },
    { conversation_id: 'c', language: 'EN', channel: 'whatsapp', status: 'completed', total: '1', gross_violation: true, evaluation_stale: true },
    { conversation_id: 'd', language: 'RU', channel: 'agent chat', status: 'needs_review', total: '99', gross_violation: null },
    { conversation_id: 'e', language: 'RU', channel: 'agent chat', status: 'failed', total: null },
    { conversation_id: 'f', language: 'RU', channel: 'agent chat', status: 'not_evaluated', total: null },
    { conversation_id: 'g', language: 'RU', channel: 'agent chat', status: 'running', total: null },
  ];
  const cost = { prompt_tokens: 123, completion_tokens: 456, total_tokens: 579, cost: null, price_message: 'цена не задана', calls: 3, retry_calls: 1 };
  const paths = [];
  await withFetch(async (url) => {
    const path = new URL(url).pathname; paths.push(path);
    if (path === '/api/conversations') return response(conversations);
    if (path === '/api/appeals') return response([{ appeal_id: 'appeal-a', evaluation_id: 'eval-a', criterion_id: '01', status: 'open', conversation_id: 'a' }]);
    if (path === '/api/stats/cost') return response({ total: cost });
    assert.fail(`Unexpected request: ${path}`);
  }, async () => {
    api.setMode('api'); const overview = await api.getOverview({ days: 7 });
    assert.equal(overview.kpis.total_count, 7); assert.equal(overview.kpis.evaluated_count, 3);
    assert.equal(overview.kpis.scored_count, 2); assert.equal(overview.kpis.avg_score, 60); assert.equal(overview.kpis.median_score, 60);
    assert.equal(overview.kpis.needs_review_count, 1); assert.equal(overview.kpis.failed_count, 1); assert.equal(overview.kpis.not_evaluated_count, 1); assert.equal(overview.kpis.running_count, 1);
    assert.equal(overview.kpis.gross_violations_count, 1); assert.equal(overview.kpis.pending_appeals_count, 1);
    assert.deepEqual(overview.score_distribution.map((v) => v.count), [0, 1, 0, 0, 1]);
    assert.deepEqual(overview.cost, cost); assert.deepEqual(overview.trend, []);
    assert.equal(overview.meta.period_supported, false); assert.equal(overview.meta.source, 'api');
    assert.equal(overview.kpis.avg_score_delta, null);
    assert.equal(overview.by_channel.find((v) => v.channel === 'whatsapp').avg_score, null);
    assert.deepEqual(paths.sort(), ['/api/appeals', '/api/conversations', '/api/stats/cost']);
  });
  const empty = real.mapOverview([], [], { total: cost });
  assert.equal(empty.kpis.avg_score, null); assert.equal(empty.kpis.median_score, null);
  assert.equal(empty.kpis.total_count, 0);
});

test('API errors stay errors: no demo overview, backend status/code/message retained', async () => {
  await withFetch(async () => response({ error: { code: 'DATABASE_UNAVAILABLE', message: 'База недоступна.' } }, 503), async () => {
    api.setMode('api');
    await assert.rejects(api.getOverview(), (e) => e instanceof ApiError && e.status === 503 && e.code === 'DATABASE_UNAVAILABLE' && e.detail === 'База недоступна.');
  });
  await withFetch(async () => { throw new TypeError('offline'); }, async () => {
    api.setMode('api'); await assert.rejects(api.getOverview(), (e) => e.kind === 'network');
  });
  await assert.rejects(request('/synthetic', { fetchImpl: async () => response({ error: { code: 'CONVERSATION_NOT_FOUND', message: 'Нет диалога.' } }, 404) }), (e) => e.code === 'CONVERSATION_NOT_FOUND' && e.message === 'Нет диалога.');
});

test('filters include arbitrary channel choices, canonical language and explicit source', async () => {
  await withFetch(async (url) => {
    const parsed = new URL(url);
    assert.equal(parsed.searchParams.get('channel'), null);
    assert.equal(parsed.searchParams.get('language'), 'KZ');
    assert.equal(parsed.searchParams.get('dataset_group_id'), 'UPLOAD');
    return response([
      { conversation_id: 'synthetic-chat', channel: 'agent chat', language: 'KZ', status: 'not_evaluated' },
      { conversation_id: 'synthetic-whatsapp', channel: 'whatsapp', language: 'KZ', status: 'not_evaluated' },
      { conversation_id: 'synthetic-email', channel: 'email', language: 'KZ', status: 'not_evaluated' },
    ]);
  }, async () => {
    const result = await real.getConversations({ channel: 'whatsapp', language: 'KZ', dataset_group_id: 'UPLOAD' });
    assert.deepEqual(result.meta.available_channels, ['chat', 'email', 'whatsapp']);
    assert.equal(result.total, 1);
    assert.equal(result.items[0].id, 'synthetic-whatsapp');
  });
});

test('checklist edits preserve explicit definitions, sparse scales, inactive rules and backend weight semantics', () => {
  const raw = { checklist_version_id: 'synthetic-v1', checklist_id: 'synthetic', version: 1, name: 'Synthetic checklist', sections: [{ id: 's', name: 'Section' }], criteria: [
    { criterion_id: 'a', name: 'A', type: 'yes_no', weight: 150, scored: true, active: true, section: 's', yes_text: 'Old yes', no_text: 'Old no' },
    { criterion_id: 'b', name: 'B', type: 'scale', weight: 5, scored: true, active: true, section: 's', scale: { 0: 'No action', 4: 'Partial', 10: 'Complete' } },
    { criterion_id: 'hidden', name: 'Hidden', type: 'yes_no', weight: 1, scored: false, active: false, section: 's' },
  ] };
  const vm = real.mapChecklist(raw, raw.checklist_version_id);
  assert.deepEqual(vm.criteria[1].scale_definition, raw.criteria[1].scale);
  vm.criteria[0].yes_text = 'New yes'; vm.criteria[0].no_text = 'New no';
  vm.criteria[1].scale_definition = { 0: 'None', 3: 'Done' };
  const body = real.toChecklistRequest({ criteria: vm.criteria }, vm);
  assert.equal(body.criteria[0].yes_text, 'New yes'); assert.equal(body.criteria[0].no_text, 'New no');
  assert.equal(body.criteria[0].weight, 150);
  assert.deepEqual(body.criteria[1].scale, { 0: 'None', 3: 'Done' });
  assert.deepEqual(body.criteria[2], raw.criteria[2]);
});

test('appeal final answer and points remain consistent; disputed snapshot is retained separately', () => {
  const evaluation = real.mapEvaluation({ evaluation_id: 'synthetic-eval', conversation_id: 'synthetic-conversation', checklist_version_id: 'v', status: 'completed', original_total: '50', final_total: '100',
    original: { criteria: [{ criterion_id: 'a', answer: 'no', points: '0' }] },
    criteria: [{ criterion_id: 'a', type: 'yes_no', answer: 'yes', points: '10', effective_weight: '10', reasoning: 'Final reason.', decisions: [{ appeal_id: 'a1' }] }],
  }, new Map([['v', 1]]), { criteria: [{ criterion_id: 'a', name: 'Synthetic', type: 'yes_no' }] });
  assert.equal(evaluation.criteria[0].answer, true);
  assert.equal(evaluation.criteria[0].score, 10);
  assert.equal(evaluation.criteria[0].original_score, 0);
  assert.equal(evaluation.criteria[0].revised.original_answer, 'no');
  const appeal = real.mapAppeal({ appeal_id: 'a1', criterion_id: 'a', status: 'reviewed', disputed_result: { points: '0', effective_weight: '10' }, decision: { decision: 'change', original_answer: 'no', final_answer: 'yes' }, current_result: { points: '10' } }, evaluation);
  assert.equal(appeal.original_score, 0); assert.equal(appeal.decision.new_score, 10);
  const naAppeal = real.mapAppeal({ appeal_id: 'na-appeal', criterion_id: 'a', status: 'reviewed', criterion: { type: 'yes_no', allow_na: true },
    disputed_result: { answer: 'na', reasoning: 'Synthetic original N/A reason.', points: '0', effective_weight: '0', evidence: [{ message_id: 'm1', quote: 'Original evidence.' }] },
    original_result: { answer: 'no', points: '0', effective_weight: '10' },
    current_result: { answer: 'yes', points: '10', effective_weight: '10' },
    decision: { decision: 'change', original_answer: 'na', final_answer: 'yes' },
  }, evaluation);
  assert.equal(naAppeal.original_answer, 'na');
  assert.equal(naAppeal.max_score, 0); assert.equal(naAppeal.final_max_score, 10);
  assert.equal(naAppeal.disputed_criterion.answer_key, 'na');
  assert.equal(naAppeal.disputed_criterion.na.reason, 'Synthetic original N/A reason.');
  assert.equal(naAppeal.disputed_criterion.explanation, 'Synthetic original N/A reason.');
  assert.equal(naAppeal.disputed_criterion.evidence[0].message_id, 'm1');
  assert.equal(naAppeal.criterion.answer_key, 'yes');
  assert.equal(naAppeal.criterion.explanation, 'Final reason.');
});

test('evaluation detail takes language and channel from its snapshot without inventing missing metadata', () => {
  const mapped = real.mapEvaluation({ evaluation_id: 'synthetic-eval', conversation_id: 'synthetic-call', criteria: [], conversation_snapshot: { language: 'RU', channel: 'voice call' } });
  assert.equal(mapped.language, 'ru'); assert.equal(mapped.channel, 'call');
  const kazakh = real.mapEvaluation({ criteria: [], conversation_snapshot: { language: 'KZ', channel: 'whatsapp' } });
  assert.equal(kazakh.language, 'kk'); assert.equal(kazakh.channel, 'whatsapp');
  const missing = real.mapEvaluation({ criteria: [], output_language: 'en' });
  assert.equal(missing.language, null); assert.equal(missing.channel, null);
});

test('appeals include all records beyond 100; linked evaluation errors are never swallowed', async () => {
  const raws = Array.from({ length: 101 }, (_, i) => ({ appeal_id: `synthetic-appeal-${i}`, conversation_id: 'synthetic-conversation', evaluation_id: 'synthetic-eval', criterion_id: 'a', status: 'open', comment: 'Synthetic comment.' }));
  const checklist = { checklist_version_id: 'synthetic-v1', version: 1, criteria: [] };
  const paths = [];
  await withFetch(async (url) => {
    const path = new URL(url).pathname; paths.push(path);
    if (path === '/api/appeals') return response(raws);
    if (path === '/api/evaluations/synthetic-eval') return response({ evaluation_id: 'synthetic-eval', conversation_id: 'synthetic-conversation', checklist_version_id: 'synthetic-v1', criteria: [], status: 'completed' });
    if (path === '/api/checklists') return response([checklist]);
    if (path === '/api/checklists/active') return response(checklist);
    assert.fail(`Unexpected redundant request: ${path}`);
  }, async () => {
    const list = await real.getAppeals({ page_size: 200 });
    assert.equal(list.total, 101); assert.equal(list.items.length, 101);
    assert.equal(paths.filter((p) => p === '/api/evaluations/synthetic-eval').length, 1);
  });
  await withFetch(async (url) => new URL(url).pathname === '/api/appeals' ? response(raws.slice(0, 1)) : response({ error: { code: 'EVALUATION_NOT_FOUND', message: 'Нет оценки.' } }, 404), async () => {
    await assert.rejects(real.getAppeals(), (e) => e.code === 'EVALUATION_NOT_FOUND' && e.detail === 'Нет оценки.');
  });
});
