// Модальные окна, диалог подтверждения и Toast. Один набор на приложение; всё закрывается при выходе из Workspace.
import { t } from '../../src/i18n/index.js';
import { esc, icon } from './ui.js';

const stack = [];
let toastBox = null;
const timers = new Set();
const FOCUSABLE = 'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

// ---------- Toast ----------
export function toast(message, { kind = 'success', timeout = 4500 } = {}) {
  toastBox ||= document.getElementById('ws-toasts');
  if (!toastBox) return;
  const el = document.createElement('div');
  el.className = `toast toast--${kind}`;
  el.setAttribute('role', kind === 'error' ? 'alert' : 'status');
  el.innerHTML = `${icon(kind === 'error' ? 'alert' : 'check-circle')}<span>${esc(message)}</span><button type="button" class="toast__x" aria-label="${esc(t('ws.common.close'))}">${icon('close')}</button>`;
  const kill = () => { el.remove(); };
  el.querySelector('.toast__x').addEventListener('click', kill);
  toastBox.append(el);
  while (toastBox.children.length > 4) toastBox.firstElementChild.remove();
  const id = setTimeout(() => { timers.delete(id); kill(); }, timeout);
  timers.add(id);
}

// ---------- Modal ----------
// openModal({ title, body: html, actions: html, wide, onClose }) → { el, close }
export function openModal({ title, body = '', actions = '', wide = false, onClose } = {}) {
  const prev = document.activeElement;
  const back = document.createElement('div');
  back.className = 'modal-back';
  const uid = `m${Math.random().toString(36).slice(2, 8)}`;
  back.innerHTML = `<div class="modal${wide ? ' modal--wide' : ''}" role="dialog" aria-modal="true" aria-labelledby="${uid}">
    <header class="modal__head"><h2 id="${uid}">${esc(title)}</h2><button type="button" class="iconbtn" data-modal-close aria-label="${esc(t('ws.common.close'))}">${icon('close')}</button></header>
    <div class="modal__body">${body}</div>${actions ? `<footer class="modal__foot">${actions}</footer>` : ''}</div>`;
  document.body.append(back);
  document.body.classList.add('ws-lock');
  const dlg = back.firstElementChild;
  let closed = false;
  const entry = { el: dlg };
  const close = (result) => {
    if (closed) return;
    closed = true;
    back.removeEventListener('keydown', onKey);
    back.removeEventListener('mousedown', onBack);
    back.remove();
    stack.splice(stack.indexOf(entry), 1);
    if (!stack.length) document.body.classList.remove('ws-lock');
    if (prev && document.contains(prev)) prev.focus({ preventScroll: true });
    onClose?.(result);
  };
  function onKey(e) {
    if (e.key === 'Escape') { e.stopPropagation(); close('esc'); return; }
    if (e.key !== 'Tab') return;
    const f = [...dlg.querySelectorAll(FOCUSABLE)].filter((x) => x.offsetParent !== null);
    if (!f.length) return;
    const first = f[0], last = f[f.length - 1];
    if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
    else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
  }
  function onBack(e) { if (e.target === back) close('backdrop'); }
  back.addEventListener('keydown', onKey);
  back.addEventListener('mousedown', onBack);
  dlg.querySelector('[data-modal-close]').addEventListener('click', () => close('x'));
  entry.close = close;
  stack.push(entry);
  const auto = dlg.querySelector('[data-autofocus]') || dlg.querySelector('.modal__body input, .modal__body select, .modal__body textarea') || dlg.querySelector(FOCUSABLE);
  auto?.focus({ preventScroll: true });
  return { el: dlg, close };
}

// ---------- ConfirmDialog → Promise<boolean> ----------
export function confirmDialog({ title, text, confirm, cancel, danger = false }) {
  return new Promise((resolve) => {
    const m = openModal({
      title, body: `<p class="modal__text">${esc(text)}</p>`,
      actions: `<button class="btn btn--ghost" type="button" data-no>${esc(cancel || t('ws.common.cancel'))}</button><button class="btn${danger ? ' btn--danger' : ''}" type="button" data-yes data-autofocus>${esc(confirm || t('ws.common.confirm'))}</button>`,
      onClose: (r) => resolve(r === 'yes'),
    });
    m.el.querySelector('[data-yes]').addEventListener('click', () => m.close('yes'));
    m.el.querySelector('[data-no]').addEventListener('click', () => m.close('no'));
  });
}

export function closeAllOverlays() {
  [...stack].forEach((m) => m.close('leave'));
  timers.forEach(clearTimeout); timers.clear();
  toastBox?.replaceChildren();
}
