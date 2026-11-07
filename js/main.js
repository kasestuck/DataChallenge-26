import { createWave } from './wave.js';
import { initFishEye } from './fisheye.js';

const AUTH_KEY = 'cqa-auth';   // заглушка: реального API/логина в репозитории пока нет
const $ = (s) => document.querySelector(s);
const views = Object.fromEntries([...document.querySelectorAll('.view')].map((v) => [v.dataset.view, v]));
const wave = createWave($('#wave'));
const fish = initFishEye($('[data-fisheye]'));

const reduced = () => matchMedia('(prefers-reduced-motion: reduce)').matches;
const dur = () => (reduced() ? 0 : parseFloat(getComputedStyle(document.documentElement).getPropertyValue('--dur')) || 450);
const wait = (ms) => new Promise((r) => setTimeout(r, ms));
const nextFrame = () => new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r)));

let current = null;
let token = 0;   // защита от гонок при быстрых переходах / Back
const authed = () => { try { return sessionStorage.getItem(AUTH_KEY) === '1'; } catch { return false; } };

function routeFromHash() {
  const r = location.hash.replace(/^#\/?/, '') || 'welcome';
  const name = views[r] ? r : 'welcome';
  return name === 'dashboard' && !authed() ? 'login' : name;
}

async function show(name) {
  if (name === current) return;
  const my = ++token;
  const from = current ? views[current] : null;
  const to = views[name];

  if (from) {
    if (current === 'welcome') wave.setCalm(0.15);   // 1. уменьшить амплитуду волны
    from.classList.remove('is-active');              // 2. плавно скрыть контент
    await wait(dur());
    if (my !== token) return;
    from.hidden = true;
    if (current === 'welcome') { wave.stop(); fish.destroy(); }
  }

  current = name;
  to.hidden = false;
  if (name === 'welcome') { wave.reset(); wave.start(); fish.refresh(); }
  await nextFrame();
  if (my !== token) return;
  to.classList.add('is-active');                     // 3. fade + translateY
  if (name === 'login') $('#login-email').focus({ preventScroll: true });   // 4. фокус на 1-м поле
  if (name === 'dashboard') { const h = $('#dash-title'); h.tabIndex = -1; h.focus({ preventScroll: true }); }
  if (name === 'welcome') $('#start-btn').focus({ preventScroll: true });
}

const go = (r) => (location.hash === `#/${r}` ? show(routeFromHash()) : (location.hash = `#/${r}`));

$('#start-btn').addEventListener('click', () => go('login'));
$('#logout-btn').addEventListener('click', () => {
  try { sessionStorage.removeItem(AUTH_KEY); } catch { /* storage недоступен */ }
  go('login');
});
$('#login-form').addEventListener('submit', (e) => {
  e.preventDefault();
  const email = $('#login-email'), pass = $('#login-password');
  const ok = email.value.trim() && pass.value;
  $('#login-error').hidden = !!ok;
  if (!ok) { (email.value.trim() ? pass : email).focus(); return; }
  try { sessionStorage.setItem(AUTH_KEY, '1'); } catch { /* storage недоступен */ }
  pass.value = '';
  go('dashboard');
});
window.addEventListener('hashchange', () => show(routeFromHash()));

// первичная загрузка — без анимации ухода
const first = routeFromHash();
current = first;
views[first].hidden = false;
if (first === 'welcome') { wave.start(); fish.refresh(); }
requestAnimationFrame(() => {
  views[first].classList.add('is-active');
  if (first === 'login') $('#login-email').focus({ preventScroll: true });
});
