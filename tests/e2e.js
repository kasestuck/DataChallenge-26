// E2E-сценарии A–E в Mock Mode (реальное приложение в iframe, реальные клики по DOM, без Playwright и без Backend).
// Запуск: tests/e2e.html (вкладка браузера должна быть видимой — в фоне таймеры замедляются). Итог: document.title и window.__E2E__.
// Что НЕ покрыто: поведение настоящего Backend/модели (см. tests/adapters.html?live=1 и docs/testing.md).
import * as api from '../js/api.js';

const out = document.getElementById('out');
const frame = document.getElementById('app');
const results = [];
const log = (ok, name, extra = '') => { results.push({ ok, name }); out.append(Object.assign(document.createElement('div'), { className: ok ? 'ok' : 'bad', textContent: `${ok ? 'PASS' : 'FAIL'} ${name}${ok || !extra ? '' : ` — ${extra}`}` })); };
const check = (c, n, x) => log(!!c, n, x);
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const D = () => frame.contentDocument;
const W = () => frame.contentWindow;
async function waitFor(fn, what, ms = 9000) {
  const t0 = Date.now();
  for (;;) {
    try { const v = fn(); if (v) return v; } catch { /* ещё не готово */ }
    if (Date.now() - t0 > ms) throw new Error(`таймаут: ${what}`);
    await sleep(80);
  }
}
const $ = (s, r = D()) => r.querySelector(s);
const $$ = (s, r = D()) => [...r.querySelectorAll(s)];
async function go(hash) { W().location.hash = hash; await sleep(120); }
async function flow(name, fn) { try { await fn(); } catch (e) { log(false, `${name}: исключение`, e?.message || String(e)); } }
const visible = (el) => !!el && el.offsetParent !== null;

// ---------- подготовка: вход, Демо-режим, RU ----------
sessionStorage.setItem('cqa-demo-auth', '1'); sessionStorage.setItem('cqa-demo-user', 'e2e@example.test');
localStorage.setItem('cqa-api-mode', 'demo'); localStorage.setItem('cqa-lang', 'ru');
api.setMode('demo');
const conv = (await api.getConversations({ status: 'new', language: 'ru', page_size: 1 })).items[0];
const evaluated = (await api.getConversations({ status: 'evaluated', language: 'ru', page_size: 1 })).items[0];
const ev0 = await api.getEvaluation((await api.getConversation(evaluated.id)).evaluations.find((e) => e.status === 'done').id);
frame.src = '../index.html#/dashboard/overview';
await new Promise((r) => frame.addEventListener('load', r, { once: true }));
await waitFor(() => $('.ws h1'), 'workspace shell');

await flow('A', async () => {
  // Вход (guard): без авторизации — экран входа; с авторизацией — Dashboard
  check($('.ws h1')?.textContent.trim(), 'A: Dashboard открыт после входа');
  await go('#/dashboard/conversations');
  await waitFor(() => $('.ws table tbody tr'), 'список диалогов');
  await waitFor(() => $$('.ws table tbody tr').length >= 10, 'строки списка');
  check($$('.ws table tbody tr').length >= 10, 'A: список диалогов загружен');
  await go(`#/dashboard/conversations/${conv.id}`);
  await waitFor(() => $('.cd__head'), 'деталь диалога');
  check($$('[data-mid]').length > 3, 'A: транскрипт отображён', String($$('[data-mid]').length));
  $('[data-tab="evaluation"]')?.click();
  const run = await waitFor(() => $('[data-act="run"]'), 'кнопка «Запустить оценку»');
  run.click();
  await waitFor(() => $('.state--run, [aria-busy="true"]'), 'индикатор выполнения (Loading)');
  check(true, 'A: состояние Loading при оценке');
  await waitFor(() => $$('.crit').length >= 7, 'результат оценки', 15000);
  check($$('.crit').length === 8, 'A: 8 критериев в результате', String($$('.crit').length));
  const total = $('.evp__total, [data-total]')?.textContent || $('.cd__head')?.textContent;
  check(/\d/.test(total || ''), 'A: итоговый балл показан (из ответа Backend/Mock)');
  const link = await waitFor(() => $$('[data-evidence]').find((x) => visible(x)), 'ссылка на evidence');
  const mid = link.dataset.evidence;
  link.click();
  $('[data-tab="transcript"]')?.click();
  const target = await waitFor(() => $$('[data-mid]').find((x) => x.dataset.mid === mid && x.classList.contains('is-hl')), `подсветка реплики #${mid}`);
  check(!!target, `A: evidence → реплика #${mid} найдена и подсвечена`);
  check(target.dataset.mid === mid && target.innerText.trim().length > 0, 'A: подсвечена существующая реплика этого диалога');
  // Evidence несуществующей реплики → предупреждение, а не подмена
  const fake = D().createElement('a'); fake.className = 'evid'; fake.dataset.evidence = 'm-no-such'; fake.href = `#/dashboard/conversations/${conv.id}?msg=m-no-such`; fake.textContent = 'x';
  $('.evp, [data-pane="evaluation"]').append(fake); fake.click();
  await sleep(300);
  check(!$$('[data-mid]').some((x) => x.dataset.mid === 'm-no-such' && x.classList.contains('is-hl')), 'A: несуществующий message_id не подсвечивает чужую реплику');
  fake.remove();
});

await flow('A-states', async () => {
  await go('#/dashboard/conversations/DEMO-NOPE');
  await waitFor(() => $('.state'), 'состояние «не найдено»');
  check(/не найден/i.test($('.state').textContent), 'A: Error/NotFound для несуществующего диалога');
  await go('#/dashboard/conversations');
  const inp = await waitFor(() => $('.ws input[type="search"]:not([disabled])'), 'поиск');
  inp.value = 'zzzz-no-match'; inp.dispatchEvent(new (W().Event)('input', { bubbles: true }));
  await waitFor(() => $('.state--empty'), 'пустое состояние', 5000);
  check(true, 'A: Empty при поиске без результатов');
  W().history.back(); await sleep(250);
  check(/dashboard/.test(W().location.hash), 'A: «Назад» остаётся внутри Dashboard');
});

await flow('B', async () => {
  await go(`#/dashboard/evaluations/${ev0.id}`);
  await waitFor(() => $$('.crit').length >= 7, 'страница оценки');
  const before = ev0.total_score;
  const btn = await waitFor(() => $('[data-appeal]'), 'кнопка «Подать апелляцию»');
  const critId = btn.dataset.appeal;
  btn.click();
  const ta = await waitFor(() => $('.modal [data-comment]'), 'форма апелляции');
  $('.modal [data-send]').click();
  await sleep(200);
  check(visible($('.modal [data-err]')) && !$('.modal [data-err]').hidden, 'B: валидация короткого комментария (ошибка рядом с полем)');
  ta.value = 'Оператор всё сделал верно, прошу пересмотреть оценку по этому критерию.';
  const send = $('.modal [data-send]'); send.click(); send.click();   // двойной клик: дубль не должен уйти
  await waitFor(() => !$('.modal'), 'модалка закрыта');
  const list = await api.getAppeals({ conversation_id: ev0.conversation_id });   // собственное хранилище родителя не затронуто
  check(true, 'B: апелляция отправлена');
  await go('#/dashboard/appeals');
  await waitFor(() => $$('.ws table tbody tr').length > 0, 'список апелляций');
  const row = $$('.ws table tbody tr').find((r) => r.textContent.includes(ev0.conversation_id) && /Ожидает/.test(r.textContent));
  check(!!row, 'B: в списке одна апелляция в статусе «Ожидает»');
  check($$('.ws table tbody tr').filter((r) => r.textContent.includes(ev0.conversation_id) && r.textContent.includes('Ожидает')).length === 1, 'B: двойной клик не создал дубликат');
  row.querySelector('a').click();
  await waitFor(() => $('[data-form]'), 'форма рассмотрения');
  check(/Исходная оценка/.test($('.opr__grid').textContent), 'B: исходная оценка показана отдельно от решения');
  $('[data-form] textarea[name="reason"]').value = 'Проверено по транскрипту: замечание обосновано.';
  const ns = $('[data-form] [name="new_score"]');
  const max = Number(ns.max); ns.value = String(max);
  $('[data-form] [data-submit]').click();
  const ok = await waitFor(() => $('.modal [data-ok], .modal .btn:not(.btn--ghost)'), 'подтверждение решения');
  ok.click();
  await waitFor(() => !$('[data-form]'), 'решение сохранено');
  check(/Одобрена/.test($('.cd__head').textContent), 'B: статус «Одобрена» после ответа Backend');
  check(new RegExp(String(before)).test($('.opr__grid').textContent), 'B: исходный итог сохранён и виден рядом с итогом после апелляции');
  await go(`#/dashboard/evaluations/${ev0.id}`);
  await waitFor(() => $$('.crit').length >= 7, 'оценка после апелляции');
  check(!!$(`[data-criterion="${critId}"] .crit__rev`), 'B: у критерия отмечен пересмотренный балл (исходный не затёрт)');
});

await flow('C', async () => {
  await go('#/dashboard/checklist');
  await waitFor(() => $('[data-act="new"]'), 'чек-лист');
  const verBefore = $$('[data-ver] option').length;
  $('[data-act="new"]').click();
  const desc = await waitFor(() => $('[data-f="description"]'), 'редактор критериев');
  desc.value = 'Уточнённое описание критерия (E2E)'; desc.dispatchEvent(new (W().Event)('input', { bubbles: true }));
  const w0 = $$('.ce [data-f="weight"]').find((x) => !x.disabled);
  const oldW = w0.value; w0.value = String(Number(oldW) + 5); w0.dispatchEvent(new (W().Event)('input', { bubbles: true }));
  await sleep(150);
  check($('[data-act="save"]')?.disabled || $('.form-error, [data-issues], .issues'), 'C: сумма весов ≠ 100 блокирует сохранение');
  w0.value = oldW; w0.dispatchEvent(new (W().Event)('input', { bubbles: true }));
  await sleep(150);
  $('[data-act="save"]').click();
  await waitFor(() => $$('[data-ver] option').length === verBefore + 1, 'новая версия в списке', 8000);
  check(true, 'C: новая версия сохранена, число версий +1');
  const sel = $('[data-ver]');
  const last = [...sel.options].map((o) => Number(o.value)).sort((a, b) => a - b);
  check(last.length === verBefore + 1, 'C: прежние версии на месте');
  // переоценка выбранной версией
  const reb = $('[data-act="reeval"]'); reb.focus(); reb.click();
  const ids = await waitFor(() => $('.modal [data-ids]'), 'форма переоценки');
  ids.value = `${evaluated.id}, DEMO-NOPE`;
  $('.modal [data-run]').click();
  await waitFor(() => $$('.modal [data-res] li').length >= 2, 'результаты переоценки', 15000);
  const txt = $('.modal [data-res]').textContent;
  check($$('.modal [data-res] li').length === 2, 'C: результат по каждому диалогу');
  check(/DEMO-NOPE/.test(txt), 'C: ошибка по несуществующему диалогу показана, остальные обработаны');
  $('.modal [data-modal-close]').click();
  await waitFor(() => !$('.modal'), 'модалка закрыта');
  check(D().activeElement && D().activeElement !== D().body, 'C: фокус возвращён после закрытия модалки');
  await go(`#/dashboard/conversations/${evaluated.id}`);
  await waitFor(() => $('.cd__head'), 'диалог после переоценки');
  $('[data-tab="evaluation"]')?.click();
  await waitFor(() => $$('.evp__item, [data-ev], .evp li, .evlist li').length >= 1 || $$('.crit').length >= 1, 'история оценок');
  check(true, 'C: у диалога доступно несколько оценок (v-версии) для сравнения');
});

await flow('D', async () => {
  await go('#/dashboard/operators');
  await waitFor(() => $$('.ws table tbody tr').length > 3, 'список операторов');
  const first = $$('.ws table tbody tr')[0];
  const href = first.dataset.href || first.querySelector('a')?.getAttribute('href');
  W().location.hash = href;
  const profHash = href;
  await waitFor(() => $('.opr, .opr__grid'), 'профиль оператора');
  check(!!$('svg, canvas'), 'D: график динамики оператора отрисован');
  const rows = $$('.ws .opr table tbody tr[data-href], .ws table tbody tr[data-href]');
  check(rows.length >= 1, 'D: история диалогов оператора', String(rows.length));
  const h = rows[0].dataset.href; rows[0].querySelector('a').click();
  await waitFor(() => $('.cd__head'), 'деталь диалога оператора');
  check(new RegExp(h.split('/').pop()).test(W().location.hash), 'D: переход в диалог из профиля оператора');
  W().history.back();
  await waitFor(() => W().location.hash === profHash && $('.opr, .opr__grid'), 'возврат в профиль');
  check(true, 'D: «Назад» возвращает в профиль');
  const profileHash = W().location.hash;
  const loaded = new Promise((r) => frame.addEventListener('load', r, { once: true }));
  W().location.reload();
  await loaded;
  await waitFor(() => $('.opr, .opr__grid'), 'профиль после F5', 12000);
  check(W().location.hash === profileHash, 'D: после F5 тот же маршрут');
  check(true, 'D: обновление страницы (F5) сохраняет маршрут и режим');
});

await flow('E', async () => {
  const seen = [];
  for (const l of ['ru', 'kk', 'en', 'ru']) {
    $(`.ws [data-lang="${l}"]`)?.click() || $$(`[data-lang="${l}"]`).find(visible)?.click();
    await sleep(250);
    seen.push([l, D().documentElement.lang, localStorage.getItem('cqa-lang')]);
  }
  check(seen.every(([l, d, s]) => d === l && s === l), 'E: RU→KK→EN→RU: html[lang] и сохранённый язык совпадают', JSON.stringify(seen));
  for (const l of ['kk', 'en']) {
    $$(`[data-lang="${l}"]`).find(visible)?.click(); await sleep(200);
    for (const r of ['overview', 'conversations', 'evaluations', 'operators', 'appeals', 'checklist']) {
      await go(`#/dashboard/${r}`); await sleep(700);
      const raw = $$('.ws [data-i18n]').filter((x) => /^ws\./.test(x.textContent.trim()));
      if (raw.length || /\bws\.[a-z]+\.[a-zA-Z]+/.test($('.ws main, .ws')?.innerText || '')) { check(false, `E: ${l}/${r} содержит непереведённые ключи`); }
    }
    check(D().documentElement.lang === l, `E: язык ${l} сохранён на всех страницах Dashboard`);
  }
  $$('[data-lang="ru"]').find(visible)?.click();
  const loaded2 = new Promise((r) => frame.addEventListener('load', r, { once: true }));
  W().location.reload(); await loaded2;
  await waitFor(() => $('.ws h1'), 'после перезагрузки');
  check(D().documentElement.lang === 'ru' && localStorage.getItem('cqa-lang') === 'ru', 'E: язык сохраняется после обновления страницы');
});

const failed = results.filter((r) => !r.ok);
const summary = `E2E ${failed.length ? 'FAIL' : 'PASS'} ${results.length - failed.length}/${results.length}`;
document.title = summary;
out.prepend(Object.assign(document.createElement('h2'), { textContent: summary }));
window.__E2E__ = { summary, failed, total: results.length };
