// Честная заглушка для разделов следующих этапов: никаких фиктивных данных и неработающих кнопок.
import { t } from '../../../src/i18n/index.js';
import { esc, icon } from '../ui.js';

export function mountSoon(root, { stage }) {
  const draw = () => {
    root.innerHTML = `<div class="state state--soon">${icon('clock', 'ico--lg')}
      <span class="chip chip--soon">${esc(t('ws.soon.stage', { stage }))}</span>
      <h2>${esc(t('ws.soon.title'))}</h2><p>${esc(t('ws.soon.text', { stage }))}</p>
      <a class="btn" href="#/dashboard">${esc(t('ws.soon.back'))}</a></div>`;
  };
  draw();
  return { search() {}, relang: draw, destroy() { root.replaceChildren(); } };
}
