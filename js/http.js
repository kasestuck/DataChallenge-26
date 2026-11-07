// Низкоуровневый HTTP-клиент: таймаут, AbortController, единый формат ошибки ApiError.
import { getApiBase, TIMEOUT_MS } from './config.js';

export class ApiError extends Error {
  constructor(kind, status = 0, url = '', detail = '', code = '') {
    super(detail || `API ${kind}${status ? ` ${status}` : ''}`);
    this.name = 'ApiError';
    this.kind = kind;       // 'network' | 'timeout' | 'http' | 'unsupported' | 'invalid'
    this.status = status;
    this.url = url;
    this.detail = detail;   // сообщение Backend, если есть
    this.code = code;       // код Backend (APPEAL_ALREADY_REVIEWED, MODEL_UNAVAILABLE …)
  }
}
export const isAbort = (e) => e?.name === 'AbortError';
export const abortError = () => new DOMException('Aborted', 'AbortError');
export const sleep = (ms, signal) => new Promise((resolve, reject) => {
  if (signal?.aborted) return reject(abortError());
  const id = setTimeout(resolve, ms);
  signal?.addEventListener('abort', () => { clearTimeout(id); reject(abortError()); }, { once: true });
});

const clean = (obj) => Object.fromEntries(Object.entries(obj || {}).filter(([, v]) => v != null && v !== ''));

// Достаёт код и сообщение из {"error":{"code","message"}} (контракт Backend) или FastAPI {"detail": ...}.
export function parseErrorBody(j) {
  if (j?.error && typeof j.error === 'object') return { code: String(j.error.code || ''), detail: String(j.error.message || '') };
  if (typeof j?.detail === 'string') return { code: '', detail: j.detail };
  if (j?.detail != null) return { code: '', detail: JSON.stringify(j.detail) };
  return { code: '', detail: '' };
}

export async function request(path, { method = 'GET', query, body, signal, timeout = TIMEOUT_MS, fetchImpl } = {}) {
  const url = new URL(`${getApiBase()}${path}`);
  Object.entries(clean(query)).forEach(([k, v]) => url.searchParams.set(k, v));
  const ctl = new AbortController();
  const forward = () => ctl.abort();
  if (signal?.aborted) throw abortError();
  signal?.addEventListener('abort', forward, { once: true });
  let timedOut = false;
  const timer = setTimeout(() => { timedOut = true; ctl.abort(); }, timeout);
  let res;
  try {
    res = await (fetchImpl || fetch)(url, {
      method,
      headers: { Accept: 'application/json', ...(body != null ? { 'Content-Type': 'application/json' } : {}) },
      body: body != null ? JSON.stringify(body) : undefined,
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
    let parsed = { code: '', detail: '' };
    try { parsed = parseErrorBody(await res.json()); } catch { /* тело не JSON */ }
    throw new ApiError('http', res.status, url.origin, parsed.detail, parsed.code);
  }
  if (res.status === 204) return null;
  try { return await res.json(); } catch { throw new ApiError('invalid', res.status, url.origin, 'invalid JSON'); }
}
