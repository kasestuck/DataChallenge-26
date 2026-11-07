// CQA Workspace: оболочка (sidebar, topbar, режим данных, меню проверяющего) и маршрутизация разделов.
// Подключается из main.js: enter() вызывается при показе view «dashboard», leave() — при уходе.
// Все слушатели навешиваются в enter() и снимаются в leave() (нет утечек), постоянных таймеров/rAF нет.
// Маршруты: #/dashboard[/<раздел>[/<id>]][?query]; страницы получают ctx и возвращают { destroy, relang, search?, query? }.
import { t, applyDom, onLangChange } from '../../src/i18n/index.js';
import { getMode, setMode, onModeChange } from '../api.js';
import { closeAllOverlays } from './overlay.js';
import { mountOverview } from './pages/overview.js';
import { mountConversations, mountConversation } from './pages/conversations.js';
import { mountEvaluations, mountEvaluation } from './pages/evaluations.js';
import { mountOperators, mountOperator } from './pages/operators.js';
import { mountAppeals, mountAppealDetail } from './pages/appeals.js';
import { mountSoon } from './pages/soon.js';
const soon = (stage) => (root) => mountSoon(root, { stage });
const mountChecklist = soon(3);

// list — страница списка/раздела, detail — страница объекта (#/dashboard/<раздел>/<id>)
const SECTIONS = {
  overview: { list: mountOverview },
  conversations: { list: mountConversations, detail: mountConversation },
  evaluations: { list: mountEvaluations, detail: mountEvaluation },
  operators: { list: mountOperators, detail: mountOperator },
  appeals: { list: mountAppeals, detail: mountAppealDetail },
  checklist: { list: mountChecklist },
};
const COLLAPSE_KEY = 'cqa-ws-collapsed';
const USER_KEY = 'cqa-demo-user';

const store = {
  get(k) { try { return localStorage.getItem(k); } catch { return null; } },
  set(k, v) { try { localStorage.setItem(k, v); } catch { /* storage недоступен */ } },
};

export function initWorkspace() {
  const $ = (s) => document.querySelector(s);
  const ws = $('#ws'), side = $('#ws-side'), outlet = $('#ws-main'), title = $('#dash-title');
  const search = $('#ws-search'), searchWrap = $('.ws-search'), burger = $('#ws-burger'), scrim = $('#ws-scrim');
  const collapseBtn = $('#ws-collapse'), collapseText = $('#ws-collapse-t'), closeBtn = $('#ws-close');
  const modeBtn = $('#ws-mode'), modeText = $('#ws-mode-text'), userBtn = $('#ws-user-btn'), menu = $('#ws-menu');
  const skip = $('#ws-skip'), avatar = $('#ws-avatar'), email = $('#ws-email');
  const links = [...side.querySelectorAll('[data-section]')];
  const mobile = matchMedia('(max-width: 900px)');

  let active = false, page = null, current = null, offs = [], searchTimer = 0, titleOverride = null;

  const on = (target, type, fn, opts) => { target.addEventListener(type, fn, opts); offs.push(() => target.removeEventListener(type, fn, opts)); };

  // ---------- маршрут ----------
  function parseRoute() {
    const [path, qs = ''] = location.hash.replace(/^#\/?/, '').split('?');
    const parts = path.split('/');
    const key = SECTIONS[parts[1]] ? parts[1] : 'overview';
    return { key, id: parts[2] ? decodeURIComponent(parts[2]) : null, query: new URLSearchParams(qs) };
  }

  function makeCtx({ key, id, query }) {
    return {
      key, id, query,
      nav: (href) => { location.hash = href.replace(/^#/, ''); },
      // обновляет query-параметры без перемонтирования страницы и без записи в историю
      setQuery(obj) {
        const p = new URLSearchParams();
        Object.entries(obj).forEach(([k, v]) => { if (v != null && v !== '') p.set(k, v); });
        const base = `#/dashboard${key === 'overview' ? '' : `/${key}`}${id ? `/${encodeURIComponent(id)}` : ''}`;
        history.replaceState(null, '', p.toString() ? `${base}?${p}` : base);
      },
      setTitle(text) { titleOverride = text; title.textContent = text; },
    };
  }

  function route(focus = false) {
    if (!active) return;
    const r = parseRoute();
    const sig = `${r.key}/${r.id || ''}`;
    if (sig === current) { page?.query?.(r.query); return; }   // тот же объект: только смена параметров (например, evidence)
    current = sig;
    page?.destroy();
    closeAllOverlays();
    clearTimeout(searchTimer);
    titleOverride = null;
    const isOverview = r.key === 'overview';
    search.disabled = !isOverview;
    if (!isOverview) search.value = '';
    syncSearchHint();
    links.forEach((a) => (a.dataset.section === r.key ? a.setAttribute('aria-current', 'page') : a.removeAttribute('aria-current')));
    title.textContent = t(`ws.nav.${r.key}`);
    const sec = SECTIONS[r.key];
    const mount = r.id && sec.detail ? sec.detail : sec.list;
    const ctx = makeCtx(r);
    // Overview использует поиск в шапке (ctx.query() → строка), остальные страницы — query-параметры из URL
    page = mount(outlet, isOverview ? { ...ctx, query: () => search.value.trim() } : ctx);
    closeDrawer();
    window.scrollTo(0, 0);
    if (focus) title.focus({ preventScroll: true });
  }

  // ---------- подписи, зависящие от языка / режима ----------
  function syncSearchHint() {
    const off = search.disabled;
    search.placeholder = t('ws.top.search');
    search.setAttribute('aria-label', t('ws.top.searchLabel'));
    searchWrap.title = off ? t('ws.top.searchOff') : '';
  }
  function syncMode() {
    const m = getMode();
    modeBtn.dataset.mode = m;
    modeText.textContent = t(`ws.mode.${m}`);
    const next = m === 'demo' ? t('ws.mode.toApi') : t('ws.mode.toDemo');
    modeBtn.title = `${t(m === 'demo' ? 'ws.mode.demoHint' : 'ws.mode.apiHint')} ${next}.`;
    modeBtn.setAttribute('aria-label', `${t('ws.mode.label')}: ${t(`ws.mode.${m}`)}. ${next}`);
  }
  function syncCollapse() {
    const c = ws.classList.contains('is-collapsed');
    collapseBtn.setAttribute('aria-expanded', String(!c));
    collapseText.textContent = t(c ? 'ws.side.expand' : 'ws.side.collapse');
    collapseBtn.title = collapseText.textContent;
  }
  function syncUser() {
    let mail = '';
    try { mail = sessionStorage.getItem(USER_KEY) || ''; } catch { /* storage недоступен */ }
    email.textContent = mail;
    email.hidden = !mail;
    avatar.textContent = (mail.trim()[0] || t('ws.top.user')[0] || '?').toUpperCase();
  }
  function syncAll() {
    applyDom(ws);
    if (active) title.textContent = t(`ws.nav.${parseRoute().key}`);
    syncSearchHint(); syncMode(); syncCollapse(); syncUser();
  }

  // ---------- sidebar ----------
  function openDrawer() {
    if (!mobile.matches) return;
    ws.classList.add('is-open');
    scrim.hidden = false;
    burger.setAttribute('aria-expanded', 'true');
    document.body.classList.add('ws-lock');
    (side.querySelector('[aria-current="page"]') || links[0]).focus({ preventScroll: true });
  }
  function closeDrawer(restoreFocus = false) {
    if (!ws.classList.contains('is-open')) return;
    ws.classList.remove('is-open');
    scrim.hidden = true;
    burger.setAttribute('aria-expanded', 'false');
    document.body.classList.remove('ws-lock');
    if (restoreFocus) burger.focus({ preventScroll: true });
  }
  function closeMenu(restoreFocus = false) {
    if (menu.hidden) return;
    menu.hidden = true;
    userBtn.setAttribute('aria-expanded', 'false');
    if (restoreFocus) userBtn.focus({ preventScroll: true });
  }

  // ---------- жизненный цикл ----------
  function enter() {
    if (active) return;
    active = true;
    ws.classList.toggle('is-collapsed', store.get(COLLAPSE_KEY) === '1');

    on(window, 'hashchange', () => { if (location.hash.startsWith('#/dashboard')) route(true); });
    on(burger, 'click', () => (ws.classList.contains('is-open') ? closeDrawer(true) : openDrawer()));
    on(closeBtn, 'click', () => closeDrawer(true));
    on(scrim, 'click', () => closeDrawer(true));
    on(collapseBtn, 'click', () => {
      ws.classList.toggle('is-collapsed');
      store.set(COLLAPSE_KEY, ws.classList.contains('is-collapsed') ? '1' : '0');
      syncCollapse();
    });
    on(side, 'click', (e) => { if (e.target.closest('[data-section]')) closeDrawer(); });
    on(modeBtn, 'click', () => setMode(getMode() === 'demo' ? 'api' : 'demo'));
    on(userBtn, 'click', () => {
      menu.hidden = !menu.hidden;
      userBtn.setAttribute('aria-expanded', String(!menu.hidden));
    });
    on(document, 'click', (e) => { if (!menu.hidden && !e.target.closest('.ws-user')) closeMenu(); });
    on(document, 'keydown', (e) => {
      if (e.key !== 'Escape' || document.querySelector('.modal-back')) return;   // Esc закрывает модальное окно, а не меню
      if (!menu.hidden) closeMenu(true); else closeDrawer(true);
    });
    on(search, 'input', () => {
      clearTimeout(searchTimer);
      searchTimer = setTimeout(() => page?.search?.(search.value.trim()), 120);
    });
    on(skip, 'click', () => { outlet.tabIndex = -1; outlet.focus({ preventScroll: false }); });
    on(mobile, 'change', () => { if (!mobile.matches) closeDrawer(); });
    offs.push(onLangChange(() => { syncAll(); page?.relang?.(); }));
    offs.push(onModeChange(() => { syncMode(); current = null; route(); }));

    syncAll();
    route();
  }

  function leave() {
    if (!active) return;
    active = false;
    clearTimeout(searchTimer);
    offs.forEach((off) => off());
    offs = [];
    page?.destroy();
    closeAllOverlays();
    page = null;
    current = null;
    closeDrawer();
    closeMenu();
  }

  return { enter, leave };
}
