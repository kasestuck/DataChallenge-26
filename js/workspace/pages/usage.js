// Render recorded backend usage without inferring missing token prices or currency.
import { t } from '../../../src/i18n/index.js';
import { esc, num } from '../ui.js';

export function usageSummary(usage) {
  if (!usage) return '';
  const fields = ['prompt_tokens', 'completion_tokens', 'total_tokens'];
  const rows = fields.map((key) => `<div><dt>${esc(t(`ws.contract.${key}`))}</dt><dd>${esc(num(usage[key]))}</dd></div>`);
  const cost = usage.cost == null
    ? t(usage.price_status === 'price_not_set' ? 'ws.contract.priceMissing' : 'ws.contract.costUnknown')
    : String(usage.cost);
  rows.push(`<div><dt>${esc(t('ws.contract.cost'))}</dt><dd>${esc(cost)}</dd></div>`);
  return `<dl class="cd__meta" data-usage>${rows.join('')}</dl>${usage.usage_complete === false ? `<p class="form-hint">${esc(t('ws.contract.usageIncomplete'))}</p>` : ''}`;
}
