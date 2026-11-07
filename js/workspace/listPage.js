// Универсальная страница-список: поиск, фильтры, сортировка, пагинация, состояния loading/empty/error.
// Состояние хранится в query-параметрах URL (replaceState), запросы отменяются при смене параметров/уходе со страницы,
// обработчики вешаются один раз делегированием на корень и снимаются в destroy().
import { t, applyDom } from '../../src/i18n/index.js';
import { getMode, getApiBase, setMode, isAbort } from '../api.js';
import { esc, searchInput, filterSelect, dataTable, pagination, loadingList, emptyState, errorState, mockBanner } from './ui.js';

const label = (l) => (l.startsWith('ws.') ? t(l) : l);

/* cfg: {
     titleKey, fetch(params, {signal}), columns(): [...], rowHref(item), sort: {key, order}, pageSize,
     filters: [{ name, labelKey, options: [[value, labelKeyOrText], ...] | (data, selected) => options }], searchKey, searchLabelKey,
     toolbarExtra?(): html, summary?(data): html, onClick?(e, api): void, emptyKeys: {title, text}, tableLabelKey } */
export function mountList(root, ctx, cfg) {
  const P = (n) => ctx.query.get(n) || '';
  const st = { q: P('q'), page: Number(P('page')) || 1, sort: P('sort') || cfg.sort.key, order: P('order') || cfg.sort.order, f: {} };
  cfg.filters.forEach((f) => { st.f[f.name] = P(f.name); });
  let data = null, err = null, ac = null, timer = 0;

  root.innerHTML = '<div class="lp"><div class="lp__bar" data-toolbar></div><div data-banner></div><div data-body></div></div>';
  const $ = (s) => root.querySelector(s);
  const body = $('[data-body]'), banner = $('[data-banner]'), bar = $('[data-toolbar]');

  const hasFilters = () => !!st.q || cfg.filters.some((f) => st.f[f.name]);
  const filterOptions = (f) => typeof f.options === 'function' ? f.options(data, st.f[f.name]) : f.options;
  const syncUrl = () => ctx.setQuery({
    q: st.q, page: st.page > 1 ? st.page : '', ...(st.sort !== cfg.sort.key || st.order !== cfg.sort.order ? { sort: st.sort, order: st.order } : {}), ...st.f,
  });

  function renderToolbar() {
    bar.innerHTML = `${searchInput({ value: st.q, placeholder: t(cfg.searchKey), label: t(cfg.searchLabelKey || 'ws.top.searchLabel') })}
      ${cfg.filters.map((f) => filterSelect({ name: f.name, value: st.f[f.name], label: t(f.labelKey), options: filterOptions(f).map(([v, l]) => [v, label(l)]) })).join('')}
      ${cfg.toolbarExtra ? `<div class="lp__extra">${cfg.toolbarExtra()}</div>` : ''}`;
  }

  async function load() {
    ac?.abort();
    const mine = (ac = new AbortController());
    body.innerHTML = loadingList();
    banner.innerHTML = '';
    syncUrl();
    const params = { q: st.q, page: st.page, page_size: cfg.pageSize || 20, sort: st.sort, order: st.order, ...st.f };
    try {
      const res = await cfg.fetch(params, { signal: mine.signal });
      if (mine.signal.aborted) return;
      data = res; err = null;
      // Update only dynamic selects: leave the search field and its focus intact.
      cfg.filters.filter((f) => typeof f.options === 'function').forEach((f) => {
        const select = bar.querySelector(`[data-filter="${f.name}"]`);
        if (select) select.innerHTML = filterOptions(f).map(([v, l]) => `<option value="${esc(v)}"${String(v) === String(st.f[f.name]) ? ' selected' : ''}>${esc(label(l))}</option>`).join('');
      });
    } catch (e) {
      if (isAbort(e)) return;
      data = null; err = e;
    }
    render();
  }

  function render() {
    if (err) { banner.innerHTML = ''; body.innerHTML = errorState(err, { mode: getMode(), base: getApiBase() }); return; }
    banner.innerHTML = data.meta?.source === 'mock' ? mockBanner() : '';
    if (!data.items.length) {
      const reset = hasFilters() ? `<button class="btn btn--ghost" type="button" data-act="reset">${esc(t('ws.list.reset'))}</button>` : '';
      body.innerHTML = `${cfg.summary?.(data) || ''}${emptyState(t(hasFilters() ? 'ws.list.noMatchTitle' : cfg.emptyKeys.title), t(hasFilters() ? 'ws.list.noMatchText' : cfg.emptyKeys.text), reset)}`;
      return;
    }
    body.innerHTML = `${cfg.summary?.(data) || ''}${dataTable({ columns: cfg.columns(), rows: data.items, sort: { key: st.sort, order: st.order }, rowHref: cfg.rowHref, label: t(cfg.tableLabelKey || cfg.titleKey) })}${pagination(data)}`;
  }

  function onInput(e) {
    if (!e.target.matches('[data-q]')) return;
    clearTimeout(timer);
    timer = setTimeout(() => { st.q = e.target.value.trim(); st.page = 1; load(); }, 280);
  }
  function onChange(e) {
    const name = e.target.dataset?.filter;
    if (!name) return;
    st.f[name] = e.target.value; st.page = 1; load();
  }
  function onClick(e) {
    const sort = e.target.closest('[data-sort]');
    if (sort) { const k = sort.dataset.sort; st.order = st.sort === k && st.order === 'desc' ? 'asc' : 'desc'; st.sort = k; st.page = 1; load(); return; }
    const pg = e.target.closest('[data-page]');
    if (pg && !pg.disabled) { st.page = Number(pg.dataset.page); load(); root.closest('.ws-main')?.scrollIntoView?.({ block: 'start' }); return; }
    const act = e.target.closest('[data-act]')?.dataset.act;
    if (act === 'retry') { load(); return; }
    if (act === 'to-demo') { setMode('demo'); return; }
    if (act === 'reset') { st.q = ''; st.page = 1; Object.keys(st.f).forEach((k) => { st.f[k] = ''; }); renderToolbar(); load(); return; }
    if (cfg.onClick?.(e, { reload: load, state: st })) return;
    const tr = e.target.closest('tr[data-href]');
    if (tr && !e.target.closest('a, button, input, select')) ctx.nav(tr.dataset.href);
  }

  root.addEventListener('input', onInput);
  root.addEventListener('change', onChange);
  root.addEventListener('click', onClick);
  renderToolbar();
  load();

  return {
    relang() { renderToolbar(); applyDom(root); if (data || err) render(); },
    destroy() { ac?.abort(); clearTimeout(timer); root.removeEventListener('input', onInput); root.removeEventListener('change', onChange); root.removeEventListener('click', onClick); root.replaceChildren(); },
  };
}
