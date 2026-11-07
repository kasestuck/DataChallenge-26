// Единая точка для всех обращений Workspace к данным. Meta AI вызывает только Backend; ключа во Frontend нет.
//
// Режимы (индикатор в шапке Workspace, localStorage["cqa-api-mode"]):
//   demo — Mock Adapter (js/mockAdapter.js): вымышленные данные, в UI всегда помечены «Демо»;
//   api  — Real API Adapter (js/realApiAdapter.js): настоящий Backend, по умолчанию /api на том же origin.
// В режиме api ошибки НИКОГДА не маскируются моками: UI показывает состояние ошибки или «не поддерживается Backend».
// Контракт: docs/api-contract.md, интеграция: docs/backend-integration.md.
import { MODE_KEY, DEFAULT_API_BASE, getApiBase } from './config.js';
import { ApiError, isAbort, request } from './http.js';
import * as mockAdapter from './mockAdapter.js';
import * as realAdapter from './realApiAdapter.js';

export { ApiError, isAbort, request, getApiBase };
export const API_BASE_DEFAULT = DEFAULT_API_BASE;

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

const A = () => (mode === 'demo' ? mockAdapter : realAdapter);

export const getOverview = (o) => A().getOverview(o);
export const getConversations = (p, o) => A().getConversations(p, o);
export const getConversation = (id, o) => A().getConversation(id, o);
export const assignOperator = (id, op, o) => A().assignOperator(id, op, o);
export const importConversations = (b, o) => A().importConversations(b, o);
export const getImportRecords = (b) => realAdapter.getImportRecords(b);
export const getEvaluations = (p, o) => A().getEvaluations(p, o);
export const getEvaluation = (id, o) => A().getEvaluation(id, o);
export const runEvaluation = (b, o) => A().runEvaluation(b, o);
export const getOperators = (p, o) => A().getOperators(p, o);
export const getOperator = (id, o) => A().getOperator(id, o);
export const getAppeals = (p, o) => A().getAppeals(p, o);
export const getAppeal = (id, o) => A().getAppeal(id, o);
export const createAppeal = (b, o) => A().createAppeal(b, o);
export const decideAppeal = (id, b, o) => A().decideAppeal(id, b, o);
export const getChecklists = (o) => A().getChecklists(o);
export const saveChecklist = (b, o) => A().saveChecklist(b, o);
export const activateChecklist = (id, o) => A().activateChecklist(id, o);
export const getHealth = (o) => realAdapter.health(o);
export { mockAdapter, realAdapter };

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
