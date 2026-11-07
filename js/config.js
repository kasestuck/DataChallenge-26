// Конфигурация Frontend. Секретов здесь нет и быть не должно: ключ Meta AI живёт только в .env Backend.
//
// Базовый URL Backend (приоритет): window.CQA_API_BASE → localStorage['cqa-api-base'] → значение по умолчанию.
// Реальный Backend (ветка BACK, API_CONTRACT.md) отдаёт маршруты под /api (не /api/v1).
export const DEFAULT_API_BASE = typeof window !== 'undefined' && /^https?:$/.test(window.location?.protocol)
  ? `${window.location.origin}/api`
  : 'http://localhost:8001/api';
export const TIMEOUT_MS = 12000;          // обычные запросы
export const LONG_TIMEOUT_MS = 180000;    // POST /evaluations синхронный: ответ приходит после вызова модели
export const MODE_KEY = 'cqa-api-mode';   // 'demo' | 'api'
export const BASE_KEY = 'cqa-api-base';

export function getApiBase() {
  let base = '';
  try { base = (typeof window !== 'undefined' && window.CQA_API_BASE) || localStorage.getItem(BASE_KEY) || ''; } catch { /* storage недоступен */ }
  return String(base || DEFAULT_API_BASE).replace(/\/+$/, '');
}
export function setApiBase(url) {
  try { if (url) localStorage.setItem(BASE_KEY, String(url)); else localStorage.removeItem(BASE_KEY); } catch { /* storage недоступен */ }
}
