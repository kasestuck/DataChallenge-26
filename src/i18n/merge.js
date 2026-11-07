// Глубокое слияние словарей: используется, чтобы дополнять ws.* этапами разработки Workspace, не трогая основной словарь.
export function merge(base, extra) {
  const out = { ...base };
  for (const [k, v] of Object.entries(extra)) {
    out[k] = v && typeof v === 'object' && base[k] && typeof base[k] === 'object' ? merge(base[k], v) : v;
  }
  return out;
}
