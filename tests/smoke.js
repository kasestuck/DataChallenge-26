// Автоматические smoke-тесты Workspace. Запуск: поднять статический сервер из корня проекта и открыть /tests/smoke.html
// (например, `python -m http.server 5173`). Тесты идут в Mock Mode, Backend не нужен; сеть не используется.
// Результат: таблица на странице, document.title = "SMOKE PASS|FAIL x/y" и window.__SMOKE__ = { passed, failed, results }.
import { setMode, getConversations, getConversation, getEvaluations, getEvaluation, runEvaluation, getOperators, getOperator, getAppeals, getAppeal, createAppeal, decideAppeal, getChecklists, saveChecklist, assignOperator, importConversations, getOverview } from '../js/api.js';
import { mockReset } from '../js/workspace/mock/index.js';
import { esc, criteriaCard, dataTable, pagination, statusBadge, scoreIndicator, errorText } from '../js/workspace/ui.js';
import ru from '../src/i18n/ru.js';
import kk from '../src/i18n/kk.js';
import en from '../src/i18n/en.js';

const results = [];
const out = document.getElementById('out');
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
async function test(group, name, fn) {
  const t0 = performance.now();
  let ok = true, msg = '';
  try { await fn(); } catch (e) { ok = false; msg = e?.message || String(e); }
  results.push({ group, name, ok, msg, ms: Math.round(performance.now() - t0) });
}
const assert = (c, m = 'assertion failed') => { if (!c) throw new Error(m); };
const eq = (a, b, m = '') => assert(a === b, `${m} expected ${JSON.stringify(b)}, got ${JSON.stringify(a)}`);
const rejects = async (p, status) => { try { await p; } catch (e) { assert(e.kind === 'http' && (!status || e.status === status), `expected HTTP ${status}, got ${e.kind}/${e.status}`); return e; } throw new Error(`expected HTTP ${status} but resolved`); };

setMode('demo');
mockReset();
// образцы берутся ДО мутирующих тестов: приложение в iframe строит собственное (детерминированное) хранилище из того же seed
const sampleConv = (await getConversations({ status: 'evaluated', page_size: 1 })).items[0].id;
const sampleEv = (await getEvaluations({ status: 'done', page_size: 1 })).items[0];
const sampleAppeal = (await getAppeals({ status: 'pending', page_size: 1 })).items[0];
const sampleEv2 = (await getEvaluations({ status: 'done', language: 'ru', page_size: 5 })).items.find((x) => x.id !== sampleEv.id);

// ---------------- слой данных (Mock Adapter через api.js) ----------------
await test('data', 'Conversations: пагинация, фильтры, сортировка, поиск', async () => {
  const a = await getConversations({ page: 1, page_size: 20 });
  eq(a.items.length, 20); assert(a.total > 1000, 'ожидается большой набор'); eq(a.page, 1);
  const kkOnly = await getConversations({ language: 'kk', page_size: 50 });
  assert(kkOnly.items.length && kkOnly.items.every((c) => c.language === 'kk'), 'фильтр языка');
  const calls = await getConversations({ channel: 'call', page_size: 50 });
  assert(calls.items.every((c) => c.channel === 'call'), 'фильтр канала');
  const sorted = await getConversations({ sort: 'score', order: 'desc', status: 'evaluated', page_size: 30 });
  assert(sorted.items.every((c, i, arr) => i === 0 || arr[i - 1].score >= c.score), 'сортировка по баллу');
  const one = await getConversations({ q: a.items[3].id });
  assert(one.items.some((c) => c.id === a.items[3].id), 'поиск по ID');
  const none = await getConversations({ operator_id: 'none', page_size: 50 });
  assert(none.items.every((c) => !c.operator_id), 'диалоги без оператора');
  const p2 = await getConversations({ page: 2, page_size: 20 });
  assert(p2.items[0].id !== a.items[0].id, 'вторая страница отличается');
});

await test('data', 'Conversation: стабильные message_id, роли, аудио только при audio_url', async () => {
  const list = await getConversations({ status: 'evaluated', page_size: 30 });
  for (const s of list.items.slice(0, 15)) {
    const c = await getConversation(s.id);
    const ids = c.messages.map((m) => m.id);
    eq(new Set(ids).size, ids.length, 'message_id уникальны');
    assert(c.messages.every((m) => ['client', 'operator', 'bot', 'system'].includes(m.role)), 'роли');
    assert(c.messages.every((m) => m.ts), 'timestamps');
    eq(c.audio_url, null, 'мок не выдумывает аудио');
  }
});

await test('data', 'Evidence: каждый message_id существует, цитата — подстрока реплики', async () => {
  const list = await getEvaluations({ status: 'done', page_size: 40 });
  let checked = 0;
  for (const e of list.items) {
    const ev = await getEvaluation(e.id);
    const conv = await getConversation(e.conversation_id);
    const byId = new Map(conv.messages.map((m) => [m.id, m]));
    for (const c of ev.criteria) for (const x of c.evidence || []) {
      const m = byId.get(x.message_id);
      assert(m, `${e.id}: нет сообщения ${x.message_id}`);
      assert(m.text.includes(x.quote), `${e.id}: цитата не из реплики ${x.message_id}`);
      checked++;
    }
  }
  assert(checked > 20, `проверено цитат: ${checked}`);
});

await test('data', 'Evaluation: итог приходит с Backend, N/A не набирает баллов, флаг не в сумме', async () => {
  const list = await getEvaluations({ status: 'done', page_size: 30 });
  for (const e of list.items) {
    const ev = await getEvaluation(e.id);
    assert(ev.total_score >= 0 && ev.total_score <= 100, 'диапазон итога');
    eq(ev.criteria.filter((c) => c.gross).length, 1, 'ровно один флаг грубого нарушения');
    eq(ev.criteria.length, 8, '8 критериев');
    assert(ev.criteria.every((c) => (c.na ? c.score == null && c.na.reason : true)), 'N/A без балла и с причиной');
    assert(ev.criteria.filter((c) => !c.gross).reduce((s, c) => s + c.weight, 0) === 100, 'веса v1 = 100');
  }
});

await test('data', 'Evaluations list: фильтры язык/статус/диапазон/версия + сортировка', async () => {
  const lo = await getEvaluations({ score_min: 0, score_max: 59, page_size: 50 });
  assert(lo.items.every((e) => e.total_score >= 0 && e.total_score <= 59), 'диапазон баллов');
  const v1 = await getEvaluations({ checklist_version: 1, language: 'en', page_size: 50 });
  assert(v1.items.every((e) => e.checklist_version === 1 && e.language === 'en'), 'версия+язык');
  const s = await getEvaluations({ sort: 'total_score', order: 'asc', status: 'done', page_size: 30 });
  assert(s.items.every((e, i, a) => i === 0 || a[i - 1].total_score <= e.total_score), 'сортировка');
});

await test('data', 'Run evaluation: асинхронный статус, повтор → 409, AI недоступен → 503', async () => {
  const fresh = (await getConversations({ status: 'new', page_size: 5 })).items;
  assert(fresh.length >= 3, 'нужны неоценённые диалоги');
  const r = await runEvaluation({ conversation_id: fresh[0].id });
  eq(r.status, 'running');
  await rejects(runEvaluation({ conversation_id: fresh[0].id }), 409);
  await sleep(1800);
  const done = await getEvaluation(r.id);
  eq(done.status, 'done'); assert(done.criteria.length === 8, 'критерии после завершения');
  const c = await getConversation(fresh[0].id); eq(c.status, 'evaluated');
  window.CQA_MOCK_FAIL_AI = true;
  try { await rejects(runEvaluation({ conversation_id: fresh[1].id }), 503); } finally { window.CQA_MOCK_FAIL_AI = false; }
  await rejects(runEvaluation({ conversation_id: 'NOPE-1' }), 404);
});

await test('data', 'Assign operator + Import: валидация и результат', async () => {
  const c = (await getConversations({ operator_id: 'none', page_size: 3 })).items[0];
  await rejects(assignOperator(c.id, ' '), 422);
  const r = await assignOperator(c.id, 'OP-DEMO-05');
  eq(r.operator_id, 'OP-DEMO-05'); eq((await getConversation(c.id)).operator_id, 'OP-DEMO-05');
  await rejects(importConversations({ conversations: [] }), 422);
  const imp = await importConversations({ conversations: [{ language: 'ru', channel: 'chat', messages: [{ role: 'client', text: 'Здравствуйте' }, { role: 'operator', text: 'Добрый день' }] }, { bad: true }] });
  eq(imp.imported, 1, 'импортировано'); eq(imp.skipped, 1, 'некорректная запись отклонена'); assert(imp.errors[0].index === 1, 'индекс ошибки');
});

await test('data', 'Appeals: создание, валидация, дубликат, решение не меняет исходную оценку', async () => {
  const e = (await getEvaluations({ status: 'done', language: 'ru', page_size: 50 })).items.find((x) => x.total_score < 100);
  const ev = await getEvaluation(e.id);
  const crit = ev.criteria.find((c) => !c.na && !c.gross && c.score < c.weight) || ev.criteria.find((c) => !c.na && !c.gross);
  await rejects(createAppeal({ evaluation_id: ev.id, criterion_id: crit.criterion_id, comment: 'коротко' }), 422);
  await rejects(createAppeal({ evaluation_id: 'EV-NONE', criterion_id: crit.criterion_id, comment: 'достаточно длинный комментарий' }), 404);
  const a = await createAppeal({ evaluation_id: ev.id, criterion_id: crit.criterion_id, comment: 'Оператор всё сделал правильно, оценка занижена.' });
  eq(a.status, 'pending'); eq(a.original_score, crit.score);
  await rejects(createAppeal({ evaluation_id: ev.id, criterion_id: crit.criterion_id, comment: 'повторная апелляция по тому же критерию' }), 409);
  await rejects(decideAppeal(a.id, { decision: 'approved', reason: '', new_score: crit.weight }), 422);
  await rejects(decideAppeal(a.id, { decision: 'approved', reason: 'ок', new_score: crit.weight + 50 }), 422);
  const before = (await getEvaluation(ev.id)).total_score;
  const d = await decideAppeal(a.id, { decision: 'approved', reason: 'Пересмотр по расшифровке', new_score: crit.weight });
  eq(d.status, 'approved'); eq(d.decision.new_score, crit.weight);
  const after = await getEvaluation(ev.id);
  eq(after.total_score, before, 'исходный total_score не изменён');
  const pc = after.criteria.find((c) => c.criterion_id === crit.criterion_id);
  eq(pc.score, crit.score, 'исходный балл критерия не изменён'); assert(pc.revised && pc.revised.score === crit.weight, 'пересмотр хранится отдельно');
  await rejects(decideAppeal(a.id, { decision: 'rejected', reason: 'поздно' }), 409);
  assert((await getAppeals({ status: 'approved', page_size: 50 })).items.some((x) => x.id === a.id), 'фильтр по статусу');
  eq((await getAppeal(a.id)).criterion.criterion_id, crit.criterion_id);
});

await test('data', 'Operators: список, сортировка, профиль только по завершённым оценкам', async () => {
  const l = await getOperators({ sort: 'avg_score', order: 'desc', page_size: 50 });
  assert(l.items.length >= 10, 'операторы'); assert(l.items.every((o, i, a) => i === 0 || a[i - 1].avg_score >= o.avg_score), 'сортировка по среднему');
  assert(typeof l.unassigned_count === 'number', 'счётчик диалогов без оператора');
  const p = await getOperator(l.items[0].id);
  eq(p.id, l.items[0].id); assert(p.trend.length && p.criteria_stats.length, 'динамика и критерии'); assert(p.conversations.length <= 10, 'история ограничена');
  await rejects(getOperator('OP-NOPE'), 404);
});

await test('data', 'Checklists: сумма весов = 100, версии неизменяемы', async () => {
  const before = (await getChecklists()).items;
  const v1 = before.find((c) => c.version === 1);
  const draft = JSON.parse(JSON.stringify(v1.criteria));
  await rejects(saveChecklist({ criteria: draft.map((c, i) => (i === 0 ? { ...c, weight: c.weight + 5 } : c)) }), 422);
  await rejects(saveChecklist({ criteria: [] }), 422);
  draft.push({ id: 'x9', name: 'Проверка личности', description: '', weight: 0, answer_type: 'yes_no', scale: null, na_allowed: false, na_condition: '', gross: false, builtin: false });
  draft[0].weight -= 0; draft[1].weight -= 0;
  const bad = draft.map((c) => (c.id === 'x9' ? { ...c, weight: 5 } : c.id === 'c1' ? { ...c, weight: c.weight - 5 } : c));
  const saved = await saveChecklist({ criteria: bad, note: 'smoke' });
  eq(saved.version, 2); assert(saved.is_current, 'новая версия текущая');
  const after = (await getChecklists()).items;
  eq(JSON.stringify(after.find((c) => c.version === 1).criteria), JSON.stringify(v1.criteria), 'v1 не изменилась');
  eq(after.filter((c) => c.is_current).length, 1);
});

await test('data', 'Overview: форма ответа Stage 1 сохранена', async () => {
  const o = await getOverview(30);
  assert(o.kpis && o.trend.length && o.score_distribution.length && o.by_language.length && o.recent_conversations.length, 'структура');
});

// ---------------- UI-компоненты ----------------
await test('ui', 'esc экранирует HTML (защита от XSS в репликах)', () => {
  eq(esc('<img src=x onerror=alert(1)>"\''), '&lt;img src=x onerror=alert(1)&gt;&quot;&#39;');
});
await test('ui', 'criteriaCard: evidence-ссылки, N/A, апелляция', async () => {
  const ev = await getEvaluation((await getEvaluations({ status: 'done', page_size: 1 })).items[0].id);
  const c = ev.criteria.find((x) => x.evidence?.length);
  const h = criteriaCard(c, { conversationId: 'D-1', appeal: true, appealed: new Set() });
  assert(h.includes('data-evidence="' + c.evidence[0].message_id + '"') && h.includes('href="#/dashboard/conversations/D-1?evidence='), 'evidence-ссылка');
  assert(h.includes('data-appeal='), 'кнопка апелляции');
  assert(!criteriaCard(c, { conversationId: 'D-1', appeal: true, appealed: new Set([c.criterion_id]) }).includes('data-appeal='), 'нет дубля кнопки');
  const na = criteriaCard({ ...c, na: { reason: 'нет вопросов' }, answer: null, score: null, evidence: [] }, { conversationId: 'D-1' });
  assert(na.includes('crit--na') && na.includes('нет вопросов') && !na.includes('data-evidence'), 'N/A без цитат');
});
await test('ui', 'dataTable/pagination/status/score: разметка и доступность', () => {
  const t = dataTable({ columns: [{ label: 'ID', sortKey: 'id', render: (r) => esc(r.id) }], rows: [{ id: 'A' }], sort: { key: 'id', order: 'asc' }, rowHref: (r) => '#/x/' + r.id, label: 'T' });
  assert(t.includes('aria-sort="ascending"') && t.includes('data-href="#/x/A"') && t.includes('data-label="ID"'), 'таблица');
  assert(pagination({ page: 1, page_size: 20, total: 45 }).includes('data-page="2"') && pagination({ page: 1, page_size: 20, total: 45 }).includes('disabled'), 'пагинация');
  assert(statusBadge('appeal', 'approved').includes('chip--ap-approved'), 'статус'); assert(scoreIndicator(null).includes('—') && scoreIndicator(88).includes('88'), 'балл');
  assert(errorText({ kind: 'http', status: 503 }).length > 5 && errorText({ kind: 'timeout' }, 'http://x').includes('http://x'), 'тексты ошибок');
});

// ---------------- i18n: паритет ключей ws.* ----------------
const keys = (o, p = '') => Object.entries(o).flatMap(([k, v]) => (v && typeof v === 'object' ? keys(v, `${p}${k}.`) : [`${p}${k}`]));
await test('i18n', 'RU/KK/EN: одинаковый набор ключей ws.*, нет пустых строк', () => {
  const base = new Set(keys(ru.ws));
  for (const [name, d] of [['kk', kk], ['en', en]]) {
    const ks = new Set(keys(d.ws));
    const miss = [...base].filter((k) => !ks.has(k)), extra = [...ks].filter((k) => !base.has(k));
    assert(!miss.length && !extra.length, `${name}: нет ${miss.slice(0, 5)} лишние ${extra.slice(0, 5)}`);
    const flat = keys(d.ws).filter((k) => { const v = k.split('.').reduce((a, x) => a[x], d.ws); return typeof v !== 'string' || !v.trim(); });
    assert(!flat.length, `${name}: пустые ${flat.slice(0, 5)}`);
  }
  assert(base.size > 300, `ключей ws.*: ${base.size}`);
});

// ---------------- маршруты и поведение страниц (iframe с приложением) ----------------
const frame = document.getElementById('app');
sessionStorage.setItem('cqa-demo-auth', '1'); sessionStorage.setItem('cqa-demo-user', 'smoke@freedom.kz'); localStorage.setItem('cqa-api-mode', 'demo');
async function openApp(hash) {
  await new Promise((res) => { frame.onload = res; frame.src = `../index.html?smoke=${Date.now()}${hash}`; });
}
const jsErrors = [], i18nWarn = new Set();
function hook(w) {
  w.addEventListener('error', (e) => jsErrors.push(e.message));
  w.addEventListener('unhandledrejection', (e) => jsErrors.push(String(e.reason)));
  const ce = w.console.error, cw = w.console.warn;
  w.console.error = (...a) => { jsErrors.push(a.join(' ')); ce.apply(w.console, a); };
  w.console.warn = (...a) => { const s = a.join(' '); if (s.includes('[i18n]')) i18nWarn.add(s); cw.apply(w.console, a); };
}
const doc = () => frame.contentDocument;
async function until(fn, ms = 6000) { const t0 = performance.now(); while (performance.now() - t0 < ms) { try { const v = fn(); if (v) return v; } catch { /* ждём */ } await sleep(60); } throw new Error('timeout: ' + fn.toString().slice(0, 80)); }
const go = async (hash) => { frame.contentWindow.location.hash = hash; await sleep(80); };
const hscroll = () => doc().documentElement.scrollWidth > doc().documentElement.clientWidth + 1;
const ready = () => until(() => !doc().querySelector('#ws-main [aria-busy="true"]') && doc().querySelector('#ws-main').children.length);

await openApp('#/dashboard'); hook(frame.contentWindow);

const ROUTES = [
  ['overview', '#/dashboard', '.wk-kpis'],
  ['conversations', '#/dashboard/conversations', '.dt tbody tr'],
  ['conversation detail', `#/dashboard/conversations/${sampleConv}`, '.crit'],
  ['evaluations', '#/dashboard/evaluations', '.dt tbody tr'],
  ['evaluation detail', `#/dashboard/evaluations/${sampleEv.id}`, '.crit'],
  ['operators', '#/dashboard/operators', '.dt tbody tr'],
  ['operator profile', '#/dashboard/operators/OP-DEMO-01', '.wk-chart'],
  ['appeals', '#/dashboard/appeals', '.dt tbody tr'],
  ['appeal review', `#/dashboard/appeals/${sampleAppeal.id}`, '[data-form]'],
  ['checklist', '#/dashboard/checklist', '.crit'],
];
for (const [width, label] of [[1280, 'desktop'], [390, 'mobile']]) {
  frame.style.width = `${width}px`; await sleep(150);
  for (const lang of ['ru', 'kk', 'en']) {
    for (const [name, hash, sel] of ROUTES) {
      await test(`routes ${label}`, `${name} [${lang}]`, async () => {
        const w = frame.contentWindow;
        w.localStorage.setItem('cqa-lang', lang);
        const btn = [...doc().querySelectorAll('.ws-top .langswitch button')].find((b) => b.dataset.lang === lang);
        if (btn && doc().documentElement.lang !== lang) btn.click();
        await go(hash);
        await until(() => doc().querySelector(`#ws-main ${sel}`));
        await ready();
        assert(!doc().querySelector('#ws-main .state--error'), 'состояние ошибки на странице');
        assert(!hscroll(), `горизонтальный скролл ${doc().documentElement.scrollWidth}>${doc().documentElement.clientWidth}`);
        assert(doc().getElementById('dash-title').textContent.trim().length > 1, 'заголовок раздела');
      });
    }
  }
}
frame.style.width = '1280px';

await test('behavior', 'Навигация: все пункты sidebar открывают свои разделы, без бейджей «Скоро»', async () => {
  await go('#/dashboard'); await ready();
  const links = [...doc().querySelectorAll('#ws-side [data-section]')];
  eq(links.length, 6); assert(!doc().querySelector('.ws-soon'), 'остался бейдж «Скоро»');
  for (const a of links) { a.click(); await sleep(250); await until(() => a.getAttribute('aria-current') === 'page'); await ready(); }
});

await test('behavior', 'Conversations: поиск, фильтр, сортировка, пагинация меняют выдачу и URL', async () => {
  await go('#/dashboard/conversations'); await until(() => doc().querySelector('.dt tbody tr')); await ready();
  const d = doc(); const first = d.querySelector('.dt tbody tr .row__id').textContent;
  const q = d.querySelector('[data-q]'); q.value = first; q.dispatchEvent(new Event('input', { bubbles: true }));
  await until(() => d.querySelectorAll('.dt tbody tr').length === 1); assert(frame.contentWindow.location.hash.includes('q='), 'поиск в URL');
  q.value = ''; q.dispatchEvent(new Event('input', { bubbles: true })); await until(() => d.querySelectorAll('.dt tbody tr').length === 20);
  const f = d.querySelector('[data-filter=language]'); f.value = 'en'; f.dispatchEvent(new Event('change', { bubbles: true }));
  await until(() => [...d.querySelectorAll('.dt tbody tr .ltag')].every((x) => x.textContent === 'EN') && d.querySelector('.dt tbody tr'));
  d.querySelector('[data-sort=score]').click(); await until(() => d.querySelector('[data-sort=score].is-on'));
  d.querySelector('[data-page="2"]').click(); await until(() => d.querySelector('.pager__cur')?.textContent.includes('2'));
  q.value = 'zzzz-no-such'; q.dispatchEvent(new Event('input', { bubbles: true })); await until(() => d.querySelector('.state--empty')); assert(d.querySelector('[data-act=reset]'), 'кнопка сброса');
  d.querySelector('[data-act=reset]').click(); await until(() => d.querySelectorAll('.dt tbody tr').length === 20);
});

await test('behavior', 'Evidence navigation: клик → диалог → прокрутка → подсветка → плавное затухание; неверный ID → предупреждение', async () => {
  const w = frame.contentWindow, d = doc();
  await go(`#/dashboard/evaluations/${sampleEv.id}`); await until(() => d.querySelector('a[data-evidence]'));
  const link = d.querySelector('a[data-evidence]'); const mid = link.dataset.evidence; link.click();
  await until(() => w.location.hash.includes(`conversations/${encodeURIComponent(sampleEv.conversation_id)}`) && d.querySelector(`[data-mid="${mid}"]`));
  const el = d.querySelector(`[data-mid="${mid}"]`); await until(() => el.classList.contains('is-hl'));
  assert(getComputedStyle(el).animationName === 'wkhl' || matchMedia('(prefers-reduced-motion: reduce)').matches, 'анимация затухания');
  await until(() => !el.classList.contains('is-hl'), 6000);
  await go(`#/dashboard/conversations/${sampleEv.conversation_id}?evidence=m999`);
  await until(() => d.querySelector('[data-warn] .banner--warn')); assert(d.querySelector('[data-warn]').textContent.includes('m999'), 'предупреждение с ID');
});

await test('behavior', 'AI Evaluation UI: итог на странице = total_score из API (не пересчитывается)', async () => {
  const d = doc(); await go(`#/dashboard/evaluations/${sampleEv.id}`); await until(() => d.querySelector('.evp__total .score'));
  const api = await getEvaluation(sampleEv.id);
  eq(Number(d.querySelector('.evp__total .score').textContent.replace(/\s/g, '').replace(',', '.')), api.total_score, 'итог');
});

await test('behavior', 'Audio: плеер только при audio_url (в моке записей нет → пояснение)', async () => {
  const d = doc(); const call = (await getConversations({ channel: 'call', status: 'evaluated', page_size: 1 })).items[0];
  await go(`#/dashboard/conversations/${call.id}`); await until(() => d.querySelector('.msg'));
  assert(!d.querySelector('audio') && d.querySelector('.audio-none'), 'без audio_url нет плеера');
});

await test('behavior', 'Appeal flow в UI: форма, валидация, отправка, повтор заблокирован', async () => {
  const d = doc(); const e = sampleEv2;
  await go(`#/dashboard/evaluations/${e.id}`); await until(() => d.querySelector('[data-appeal]'));
  const crit = d.querySelector('[data-appeal]').dataset.appeal; d.querySelector('[data-appeal]').click();
  await until(() => d.querySelector('.modal [data-send]')); d.querySelector('.modal [data-send]').click();
  await until(() => !d.querySelector('.modal [data-err]').hidden);
  d.querySelector('.modal [data-comment]').value = 'Автотест: оценка по критерию занижена, цитаты подтверждают.'; d.querySelector('.modal [data-send]').click();
  await until(() => !d.querySelector('.modal-back')); await until(() => d.querySelector(`[data-criterion="${crit}"] button[disabled]`));
});

await test('behavior', 'Checklist Builder: проверка суммы весов блокирует сохранение', async () => {
  const d = doc(); await go('#/dashboard/checklist'); await until(() => d.querySelector('[data-act=new]'));
  d.querySelector('[data-act=new]').click(); await until(() => d.querySelector('.ce'));
  const w = d.querySelector('.ce [data-f=weight]'); w.value = String(Number(w.value) + 7); w.dispatchEvent(new Event('input', { bubbles: true }));
  await until(() => d.querySelector('[data-act=save]').disabled); assert(d.querySelector('.sumbar--bad'), 'индикатор суммы');
  d.querySelector('[data-act=add]').click(); await until(() => d.querySelectorAll('.ce').length === 9);
  assert(d.querySelectorAll('.ce [data-act=del]').length === 1, 'удалять можно только пользовательский критерий');
});

await test('behavior', 'Состояния: Error в API-режиме (Backend недоступен) → кнопки «Повторить» и «Демо»; возврат в демо', async () => {
  const d = doc(); const w = frame.contentWindow;
  w.localStorage.setItem('cqa-api-mode', 'api'); w.CQA_API_BASE = 'http://127.0.0.1:9/api/v1';
  d.getElementById('ws-mode').click(); await sleep(150);
  await go('#/dashboard/appeals'); await until(() => d.querySelector('.state--error'), 9000);
  assert(d.querySelector('[data-act=retry]') && d.querySelector('[data-act=to-demo]'), 'кнопки'); assert(!d.querySelector('.dt'), 'нет фальшивых данных в API-режиме');
  d.querySelector('[data-act=to-demo]').click(); await until(() => d.querySelector('.dt tbody tr'));
});

await test('behavior', 'Лента: нет утечек слушателей и обработчиков при повторных переходах (20 циклов)', async () => {
  const d = doc(); let calls = 0; const orig = d.addEventListener.bind(d);
  for (let i = 0; i < 20; i++) { await go(i % 2 ? '#/dashboard/conversations' : '#/dashboard/operators'); await sleep(120); }
  d.addEventListener = (...a) => { calls++; return orig(...a); };
  for (let i = 0; i < 6; i++) { await go(i % 2 ? '#/dashboard/appeals' : '#/dashboard/evaluations'); await sleep(150); }
  d.addEventListener = orig;
  assert(calls <= 6, `подозрительно много document-слушателей: ${calls}`);
});

await test('global', 'Нет JS-ошибок и нет отсутствующих ключей i18n за весь прогон', () => {
  const errs = jsErrors.filter((e) => !/Failed to fetch|NetworkError|ERR_CONNECTION|ERR_|Load failed|aborted/i.test(e));
  assert(!errs.length, `JS-ошибки: ${errs.slice(0, 3).join(' | ')}`);
  assert(!i18nWarn.size, `i18n: ${[...i18nWarn].slice(0, 3).join(' | ')}`);
});

// ---------------- вывод ----------------
const failed = results.filter((r) => !r.ok), passed = results.length - failed.length;
document.title = `SMOKE ${failed.length ? 'FAIL' : 'PASS'} ${passed}/${results.length}`;
window.__SMOKE__ = { passed, failed: failed.length, total: results.length, results };
out.innerHTML = `<h2>${document.title}</h2><table><thead><tr><th>Группа</th><th>Тест</th><th>Результат</th><th>мс</th></tr></thead><tbody>${results.map((r) => `<tr class="${r.ok ? 'ok' : 'bad'}"><td>${esc(r.group)}</td><td>${esc(r.name)}</td><td>${r.ok ? 'PASS' : `FAIL — ${esc(r.msg)}`}</td><td>${r.ms}</td></tr>`).join('')}</tbody></table>`;
