// Локальная i18n: словари хранятся в репозитории, машинный перевод не используется.
// Язык интерфейса НЕ влияет на язык оцениваемых диалогов — реплики выводятся как есть.
import ru from './ru.js';
import kk from './kk.js';
import en from './en.js';

export const LANGS = ['ru', 'kk', 'en'];
const DICTS = { ru, kk, en };
const LOCALES = { ru: 'ru-RU', kk: 'kk-KZ', en: 'en-US' };
const STORAGE_KEY = 'cqa-lang';
const FALLBACK = 'ru';
const DEV = ['localhost', '127.0.0.1', '[::1]'].includes(location.hostname);

let lang = FALLBACK;
const listeners = new Set();
const warned = new Set();

const lookup = (dict, key) => key.split('.').reduce((o, k) => (o && typeof o === 'object' ? o[k] : undefined), dict);

function warnOnce(msg) {
  if (!DEV || warned.has(msg)) return;
  warned.add(msg);
  console.warn(`[i18n] ${msg}`);
}

export function t(key, params) {
  let v = lookup(DICTS[lang], key);
  if (typeof v !== 'string') {
    warnOnce(`missing key "${key}" for "${lang}", falling back to "${FALLBACK}"`);
    v = lookup(DICTS[FALLBACK], key);
    if (typeof v !== 'string') return key;
  }
  return params ? v.replace(/\{(\w+)\}/g, (m, k) => (k in params ? params[k] : m)) : v;
}

export const getLang = () => lang;

// В некоторых сборках браузеров (урезанный ICU) данных для kk-KZ нет: месяцы выводятся как "M11",
// а разделители чисел — как в en-US. Определяем это и безопасно деградируем.
const KK_HAS_MONTHS = !/M\d/.test(new Intl.DateTimeFormat('kk-KZ', { month: 'long' }).format(new Date(2026, 10, 1)));
const KK_HAS_NUMBERS = new Intl.NumberFormat('kk-KZ').format(1234.5) !== new Intl.NumberFormat('en-US').format(1234.5);

export function formatNumber(n, opts) {
  const loc = lang === 'kk' && !KK_HAS_NUMBERS ? 'ru-RU' : LOCALES[lang];   // тот же формат: пробел и запятая
  return new Intl.NumberFormat(loc, opts).format(n);
}
export function formatDate(d, opts = { day: 'numeric', month: 'short', year: 'numeric' }) {
  const date = typeof d === 'string' ? new Date(d) : d;
  if (lang === 'kk' && !KK_HAS_MONTHS) {
    // числовой формат дд.мм[.гггг] одинаков для русского и казахского
    return new Intl.DateTimeFormat('ru-RU', { day: '2-digit', month: '2-digit', ...(opts.year ? { year: 'numeric' } : {}) }).format(date);
  }
  return new Intl.DateTimeFormat(LOCALES[lang], opts).format(date);
}

// data-i18n="key" · data-i18n-attr="placeholder:key;aria-label:key2"
// data-num="87.4" data-digits="1" · data-date="2026-11-03"
export function applyDom(root = document) {
  root.querySelectorAll('[data-i18n]').forEach((el) => { el.textContent = t(el.dataset.i18n); });
  root.querySelectorAll('[data-i18n-attr]').forEach((el) => {
    el.dataset.i18nAttr.split(';').forEach((pair) => {
      const [attr, key] = pair.split(':').map((s) => s.trim());
      if (attr && key) el.setAttribute(attr, t(key));
    });
  });
  root.querySelectorAll('[data-num]').forEach((el) => {
    const d = Number(el.dataset.digits || 0);
    el.textContent = formatNumber(Number(el.dataset.num), { minimumFractionDigits: d, maximumFractionDigits: d });
  });
  root.querySelectorAll('[data-date]').forEach((el) => {
    el.textContent = formatDate(el.dataset.date, { day: 'numeric', month: 'short' });
  });
  if (root === document) {
    document.title = t('meta.title');
    const m = document.querySelector('meta[name="description"]');
    if (m) m.setAttribute('content', t('meta.description'));
  }
}

export function onLangChange(fn) { listeners.add(fn); return () => listeners.delete(fn); }

export function setLang(next, { persist = true } = {}) {
  if (!LANGS.includes(next)) return;
  lang = next;
  document.documentElement.lang = next;
  if (persist) { try { localStorage.setItem(STORAGE_KEY, next); } catch { /* storage недоступен */ } }
  applyDom();
  listeners.forEach((fn) => fn(next));
}

export function initI18n() {
  let saved = null;
  try { saved = localStorage.getItem(STORAGE_KEY); } catch { /* storage недоступен */ }
  if (DEV) checkDictionaries();
  setLang(LANGS.includes(saved) ? saved : FALLBACK, { persist: false });
}

// dev-проверка: все ключи ru должны быть в kk/en
function flat(obj, prefix = '') {
  return Object.entries(obj).flatMap(([k, v]) => (v && typeof v === 'object' ? flat(v, `${prefix}${k}.`) : [`${prefix}${k}`]));
}
export function checkDictionaries() {
  const base = flat(ru);
  const report = {};
  LANGS.filter((l) => l !== FALLBACK).forEach((l) => {
    const keys = new Set(flat(DICTS[l]));
    const missing = base.filter((k) => !keys.has(k));
    const extra = [...keys].filter((k) => !base.includes(k));
    report[l] = { missing, extra };
    if (missing.length) warnOnce(`${l}: missing keys: ${missing.join(', ')}`);
    if (extra.length) warnOnce(`${l}: unknown keys: ${extra.join(', ')}`);
  });
  return report;
}
