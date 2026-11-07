// Единая точка для всех обращений к Backend (FastAPI, разрабатывается отдельно).
// Все запросы Workspace идут ТОЛЬКО через этот файл. Meta AI вызывается на Backend, frontend к нему не обращается.
//
// Режимы:
//   demo — Mock Adapter (js/workspace/mock/), данные вымышленные, в UI помечаются «Демо»;
//   api  — реальный Backend: window.CQA_API_BASE или http://localhost:8001/api/v1.
// В режиме api ошибки НЕ маскируются моками: UI показывает состояние ошибки.
// Контракты (временные, предложение frontend, не согласованы с Backend): docs/api-contract.md.
import * as mock from './workspace/mock/index.js';

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
  constructor(kind, status = 0, url = '', detail = '') {
    super(`API ${kind}${status ? ` ${status}` : ''}`);
    this.name = 'ApiError';
    this.kind = kind;       // 'network' | 'timeout' | 'http'
    this.status = status;
    this.url = url;
    this.detail = detail;   // сообщение Backend (FastAPI: {"detail": ...}), если есть
  }
}
export const isAbort = (e) => e?.name === 'AbortError';
const abortError = () => new DOMException('Aborted', 'AbortError');

const sleep = (ms, signal) => new Promise((resolve, reject) => {
  if (signal?.aborted) return reject(abortError());
  const id = setTimeout(resolve, ms);
  signal?.addEventListener('abort', () => { clearTimeout(id); reject(abortError()); }, { once: true });
});

const clean = (obj) => Object.fromEntries(Object.entries(obj || {}).filter(([, v]) => v != null && v !== ''));

export async function request(path, { method = 'GET', query, body, signal, timeout = TIMEOUT_MS } = {}) {
  const url = new URL(`${getApiBase()}${path}`);
  Object.entries(clean(query)).forEach(([k, v]) => url.searchParams.set(k, v));
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
  if (!res.ok) {
    let detail = '';
    try { const j = await res.json(); detail = typeof j?.detail === 'string' ? j.detail : JSON.stringify(j?.detail ?? ''); } catch { /* тело не JSON */ }
    throw new ApiError('http', res.status, url.origin, detail);
  }
  return res.status === 204 ? null : res.json();
}

// Единая развилка demo / api. demo-функции синхронны и живут в mock/; UI о моках не знает.
async function call({ demo, path, method = 'GET', query, body, signal, delay = 260 }) {
  if (mode === 'demo') {
    await sleep(delay, signal);
    try {
      return { ...demo(), meta: { source: 'mock' } };
    } catch (e) {
      if (e?.name === 'MockHttp') throw new ApiError('http', e.status, 'demo', e.message);
      throw e;
    }
  }
  const data = await request(path, { method, query, body, signal });
  return { ...(data || {}), meta: { ...(data?.meta || {}), source: 'api' } };
}
const enc = encodeURIComponent;

// ---------- Overview ----------
export const getOverview = ({ days = 30, signal } = {}) => call({ demo: () => mock.overview(days), path: '/overview', query: { days }, signal, delay: 320 });

// ---------- Conversations ----------
export const getConversations = (params = {}, { signal } = {}) => call({ demo: () => mock.conversations(params), path: '/conversations', query: params, signal });
export const getConversation = (id, { signal } = {}) => call({ demo: () => mock.conversation(id), path: `/conversations/${enc(id)}`, signal });
export const assignOperator = (id, operator_id, { signal } = {}) => call({ demo: () => mock.assignOperator(id, { operator_id }), path: `/conversations/${enc(id)}/operator`, method: 'PUT', body: { operator_id }, signal, delay: 300 });
// Endpoint импорта НЕ согласован с Backend — предложение (docs/api-contract.md).
export const importConversations = (body, { signal } = {}) => call({ demo: () => mock.importConversations(body), path: '/conversations/import', method: 'POST', body, signal, delay: 500 });

// ---------- Evaluations ----------
export const getEvaluations = (params = {}, { signal } = {}) => call({ demo: () => mock.evaluations(params), path: '/evaluations', query: params, signal });
export const getEvaluation = (id, { signal } = {}) => call({ demo: () => mock.evaluation(id), path: `/evaluations/${enc(id)}`, signal, delay: 120 });
export const runEvaluation = (body, { signal } = {}) => call({ demo: () => mock.runEvaluation(body), path: `/conversations/${enc(body.conversation_id)}/evaluations`, method: 'POST', body: { checklist_version: body.checklist_version }, signal, delay: 350 });

// ---------- Operators ----------
export const getOperators = (params = {}, { signal } = {}) => call({ demo: () => mock.operators(params), path: '/operators', query: params, signal });
export const getOperator = (id, { signal } = {}) => call({ demo: () => mock.operator(id), path: `/operators/${enc(id)}`, signal });

// ---------- Appeals ----------
export const getAppeals = (params = {}, { signal } = {}) => call({ demo: () => mock.appeals(params), path: '/appeals', query: params, signal });
export const getAppeal = (id, { signal } = {}) => call({ demo: () => mock.appeal(id), path: `/appeals/${enc(id)}`, signal });
export const createAppeal = (body, { signal } = {}) => call({ demo: () => mock.createAppeal(body), path: '/appeals', method: 'POST', body, signal, delay: 350 });
export const decideAppeal = (id, body, { signal } = {}) => call({ demo: () => mock.decideAppeal(id, body), path: `/appeals/${enc(id)}/decision`, method: 'POST', body, signal, delay: 350 });

// ---------- Checklists ----------
export const getChecklists = ({ signal } = {}) => call({ demo: () => mock.checklists(), path: '/checklists', signal });
export const saveChecklist = (body, { signal } = {}) => call({ demo: () => mock.saveChecklist(body), path: '/checklists', method: 'POST', body, signal, delay: 400 });

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
