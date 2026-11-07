// Единая точка для всех обращений к Backend (FastAPI, разрабатывается отдельно).
// Все запросы Workspace идут ТОЛЬКО через этот файл. Meta AI вызывается на Backend, frontend к нему не обращается.
//
// Режимы:
//   demo — Mock Adapter (js/workspace/mock.js), данные вымышленные, в UI помечаются «Демо»;
//   api  — реальный Backend: window.CQA_API_BASE или http://localhost:8001/api/v1.
// В режиме api ошибки НЕ маскируются моками: UI показывает состояние ошибки.
// Контракты (временные, предложение frontend): docs/api-contract.md.
import { mockOverview } from './workspace/mock.js';

export const API_BASE_DEFAULT = 'http://localhost:8001/api/v1';
const MODE_KEY = 'cqa-api-mode';
const TIMEOUT_MS = 12000;

export const getApiBase = () => ((typeof window !== 'undefined' && window.CQA_API_BASE) || API_BASE_DEFAULT).replace(/\/+$/, '');

// ---------- режим ----------
let mode = 'demo';
try { if (localStorage.getItem(MODE_KEY) === 'api') mode = 'api'; } catch { /* storage недоступен */ }
const modeListeners = new Set();
export const getMode = () => mode;
export function setMode(next) {
  if (next !== 'demo' && next !== 'api') return;
  if (next === mode) return;
  mode = next;
  try { localStorage.setItem(MODE_KEY, mode); } catch { /* storage недоступен */ }
  modeListeners.forEach((fn) => fn(mode));
}
export function onModeChange(fn) { modeListeners.add(fn); return () => modeListeners.delete(fn); }

// ---------- ошибки и низкоуровневый запрос ----------
export class ApiError extends Error {
  constructor(kind, status = 0, url = '') {
    super(`API ${kind}${status ? ` ${status}` : ''}`);
    this.name = 'ApiError';
    this.kind = kind;       // 'network' | 'timeout' | 'http'
    this.status = status;
    this.url = url;
  }
}
export const isAbort = (e) => e?.name === 'AbortError';
const abortError = () => new DOMException('Aborted', 'AbortError');

const sleep = (ms, signal) => new Promise((resolve, reject) => {
  if (signal?.aborted) return reject(abortError());
  const id = setTimeout(resolve, ms);
  signal?.addEventListener('abort', () => { clearTimeout(id); reject(abortError()); }, { once: true });
});

export async function request(path, { method = 'GET', query, body, signal, timeout = TIMEOUT_MS } = {}) {
  const url = new URL(`${getApiBase()}${path}`);
  Object.entries(query || {}).forEach(([k, v]) => { if (v != null && v !== '') url.searchParams.set(k, v); });
  const ctl = new AbortController();
  const forward = () => ctl.abort();
  signal?.addEventListener('abort', forward, { once: true });
  let timedOut = false;
  const timer = setTimeout(() => { timedOut = true; ctl.abort(); }, timeout);
  let res;
  try {
    res = await fetch(url, {
      method,
      headers: { Accept: 'application/json', ...(body ? { 'Content-Type': 'application/json' } : {}) },
      body: body ? JSON.stringify(body) : undefined,
      signal: ctl.signal,
    });
  } catch (e) {
    if (signal?.aborted) throw e;                       // отмена устаревшего запроса — не ошибка Backend
    throw new ApiError(timedOut ? 'timeout' : 'network', 0, url.origin);
  } finally {
    clearTimeout(timer);
    signal?.removeEventListener('abort', forward);
  }
  if (!res.ok) throw new ApiError('http', res.status, url.origin);
  return res.status === 204 ? null : res.json();
}

// ---------- Workspace: Overview ----------
// GET /overview?days=7|30|90 → см. docs/api-contract.md. Результат всегда содержит meta.source: 'mock' | 'api'.
export async function getOverview({ days = 30, signal } = {}) {
  if (mode === 'demo') {
    await sleep(320, signal);                            // имитация сети, чтобы был виден Loading state
    return { ...mockOverview(days), meta: { source: 'mock' } };
  }
  const data = await request('/overview', { query: { days }, signal });
  return { ...data, meta: { ...(data?.meta || {}), source: 'api' } };
}

// ---------- Landing: демо-превью (не Workspace) ----------
// isDemo=true => в UI показывается пометка «Демо».
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
