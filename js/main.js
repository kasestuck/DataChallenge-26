import { initLanding } from './landing.js';
import { initWorkspace } from './workspace/app.js';
import { initI18n, setLang, getLang, onLangChange, LANGS } from '../src/i18n/index.js';

const AUTH_KEY = 'cqa-demo-auth';   // ДЕМО: флаг в sessionStorage, настоящей авторизации нет
const $ = (s) => document.querySelector(s);
const views = Object.fromEntries([...document.querySelectorAll('.view')].map((v) => [v.dataset.view, v]));

initI18n();
const landing = initLanding();
const workspace = initWorkspace();

// ---------- переключатель языка ----------
const langButtons = [...document.querySelectorAll('[data-lang]')];
const markLang = () => langButtons.forEach((b) => b.setAttribute('aria-pressed', String(b.dataset.lang === getLang())));
langButtons.forEach((b) => b.addEventListener('click', () => { if (LANGS.includes(b.dataset.lang)) setLang(b.dataset.lang); }));
onLangChange(markLang);
markLang();

// ---------- роутер (hash) ----------
const reduced = () => matchMedia('(prefers-reduced-motion: reduce)').matches;
const dur = () => (reduced() ? 0 : parseFloat(getComputedStyle(document.documentElement).getPropertyValue('--dur')) || 450);
const wait = (ms) => new Promise((r) => setTimeout(r, ms));
const nextFrame = () => new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r)));

let current = null;
let token = 0;   // защита от гонок при быстрых переходах / Back
const authed = () => { try { return sessionStorage.getItem(AUTH_KEY) === '1'; } catch { return false; } };

function routeFromHash() {
  const r = location.hash.replace(/^#\/?/, '').split(/[/?]/)[0] || 'welcome';   // dashboard/<раздел> обслуживает Workspace
  const name = views[r] ? r : 'welcome';
  return name === 'dashboard' && !authed() ? 'login' : name;
}

async function show(name) {
  if (name === current) return;
  const my = ++token;
  const from = current ? views[current] : null;
  const to = views[name];

  if (from) {
    if (current === 'welcome') landing.calm(0.15);   // 1. уменьшить амплитуду волны
    from.classList.remove('is-active');              // 2. плавно скрыть контент
    await wait(dur());
    if (my !== token) return;
    from.hidden = true;
    if (current === 'welcome') landing.leave();
    if (current === 'dashboard') workspace.leave();
  }

  current = name;
  document.body.dataset.view = name;
  to.hidden = false;
  window.scrollTo(0, 0);
  if (name === 'welcome') landing.enter();
  if (name === 'dashboard') workspace.enter();
  await nextFrame();
  if (my !== token) return;
  to.classList.add('is-active');                     // 3. fade + translateY
  if (name === 'login') $('#login-email').focus({ preventScroll: true });   // 4. фокус на 1-м поле
  if (name === 'dashboard') { const h = $('#dash-title'); h.tabIndex = -1; h.focus({ preventScroll: true }); }
}

const go = (r) => (location.hash === `#/${r}` ? show(routeFromHash()) : (location.hash = `#/${r}`));

// Let's start и финальная CTA ведут на один и тот же Login
document.querySelectorAll('[data-nav]').forEach((b) => b.addEventListener('click', () => go(b.dataset.nav)));
$('#brand-link').addEventListener('click', (e) => {
  e.preventDefault();
  if (current === 'welcome') scrollTo({ top: 0, behavior: reduced() ? 'auto' : 'smooth' }); else go('welcome');
});
$('#logout-btn').addEventListener('click', () => {
  try { sessionStorage.removeItem(AUTH_KEY); sessionStorage.removeItem('cqa-demo-user'); } catch { /* storage недоступен */ }
  go('welcome');
});
$('#login-form').addEventListener('submit', (e) => {
  e.preventDefault();
  const email = $('#login-email'), pass = $('#login-password');
  const ok = email.value.trim() && pass.value;
  $('#login-error').hidden = !!ok;
  if (!ok) { (email.value.trim() ? pass : email).focus(); return; }
  try { sessionStorage.setItem(AUTH_KEY, '1'); sessionStorage.setItem('cqa-demo-user', email.value.trim()); } catch { /* storage недоступен */ }
  pass.value = '';
  go('dashboard');
});
window.addEventListener('hashchange', () => show(routeFromHash()));

// первичная загрузка — без анимации ухода; работает и после refresh на любом маршруте
const first = routeFromHash();
if (location.hash && location.hash.replace(/^#\/?/, '').split(/[/?]/)[0] !== first) history.replaceState(null, '', `#/${first}`);
current = first;
document.body.dataset.view = first;
Object.entries(views).forEach(([name, v]) => { v.hidden = name !== first; });
if (first === 'welcome') landing.enter();
if (first === 'dashboard') workspace.enter();
requestAnimationFrame(() => {
  views[first].classList.add('is-active');
  if (first === 'login') $('#login-email').focus({ preventScroll: true });
});
