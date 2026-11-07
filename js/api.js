// Единая точка для всех обращений к Backend (FastAPI, разрабатывается отдельно).
// Согласованные endpoints здесь НЕ определены — их пока нет в репозитории, поэтому ничего не выдумываем.
// Meta AI вызывается только на Backend; frontend к нему не обращается.
const API_BASE = (typeof window !== 'undefined' && window.CQA_API_BASE) || '';

export async function request(path, options = {}) {
  if (!API_BASE) throw new Error('API_BASE is not configured');
  const res = await fetch(`${API_BASE}${path}`, {
    headers: { 'Content-Type': 'application/json', ...(options.headers || {}) },
    ...options,
  });
  if (!res.ok) throw new Error(`API ${res.status}`);
  return res.json();
}

// Демонстрационные данные для превью на лендинге. isDemo=true => в UI показывается пометка «Демо».
// Когда Backend предоставит агрегаты, заменить тело функции на request(<согласованный endpoint>).
export async function getAnalyticsPreview() {
  return {
    isDemo: true,
    avgScore: 87.4,
    delta: 4.6,
    checked: 1284,
    trend: [78, 80, 79, 83, 85, 84, 86, 88, 87, 90, 89, 92],
    statuses: [
      { key: 'approved', count: 912 },
      { key: 'review', count: 247 },
      { key: 'appealed', count: 125 },
    ],
    dialogs: [
      { id: 'D-20481', operator: 'OP-014', lang: 'ru', score: 94, status: 'approved', date: '2026-11-03' },
      { id: 'D-20480', operator: 'OP-007', lang: 'kk', score: 81, status: 'review', date: '2026-11-03' },
      { id: 'D-20476', operator: 'OP-022', lang: 'en', score: 88, status: 'approved', date: '2026-11-02' },
      { id: 'D-20471', operator: 'OP-014', lang: 'ru', score: 67, status: 'appealed', date: '2026-11-02' },
      { id: 'D-20465', operator: 'OP-031', lang: 'kk', score: 90, status: 'approved', date: '2026-11-01' },
    ],
  };
}
