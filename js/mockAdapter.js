// Mock Adapter (режим «Демо»): локальные вымышленные данные из js/workspace/mock/. Backend не нужен.
// Всё, что отсюда возвращается, помечается meta.source = 'mock', а UI показывает баннер «Демо».
import * as mock from './workspace/mock/index.js';
import { ApiError, sleep } from './http.js';

async function run(fn, signal, delay = 260) {
  await sleep(delay, signal);
  try {
    return { ...fn(), meta: { source: 'mock' } };
  } catch (e) {
    if (e?.name === 'MockHttp') throw new ApiError('http', e.status, 'demo', e.message);
    throw e;
  }
}

export const getOverview = ({ days = 30, signal } = {}) => run(() => mock.overview(days), signal, 320);
export const getConversations = (p = {}, { signal } = {}) => run(() => mock.conversations(p), signal);
export const getConversation = (id, { signal } = {}) => run(() => mock.conversation(id), signal);
export const assignOperator = (id, operator_id, { signal } = {}) => run(() => mock.assignOperator(id, { operator_id }), signal, 300);
export const importConversations = (body, { signal } = {}) => run(() => mock.importConversations(body), signal, 500);
export const getEvaluations = (p = {}, { signal } = {}) => run(() => mock.evaluations(p), signal);
export const getEvaluation = (id, { signal } = {}) => run(() => mock.evaluation(id), signal, 120);
export const runEvaluation = (body, { signal } = {}) => run(() => mock.runEvaluation(body), signal, 350);
export const getOperators = (p = {}, { signal } = {}) => run(() => mock.operators(p), signal);
export const getOperator = (id, { signal } = {}) => run(() => mock.operator(id), signal);
export const getAppeals = (p = {}, { signal } = {}) => run(() => mock.appeals(p), signal);
export const getAppeal = (id, { signal } = {}) => run(() => mock.appeal(id), signal);
export const createAppeal = (body, { signal } = {}) => run(() => mock.createAppeal(body), signal, 350);
export const decideAppeal = (id, body, { signal } = {}) => run(() => mock.decideAppeal(id, body), signal, 350);
export const getChecklists = ({ signal } = {}) => run(() => mock.checklists(), signal);
export const saveChecklist = (body, { signal } = {}) => run(() => mock.saveChecklist(body), signal, 400);
export const activateChecklist = () => Promise.resolve({ ok: true, meta: { source: 'mock' } });
export { mock };
