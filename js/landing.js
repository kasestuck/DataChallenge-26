import { createWave } from './wave.js';
import { initFishEye } from './fisheye.js';
import { getAnalyticsPreview } from './api.js';
import { t, formatNumber, formatDate, onLangChange } from '../src/i18n/index.js';

const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];
const STATUS_COLOR = { approved: 'var(--c-green)', review: '#e0a526', appealed: '#4a6fd6' };
const reduceMq = window.matchMedia('(prefers-reduced-motion: reduce)');
const clamp = (v, a, b) => Math.min(b, Math.max(a, v));
const hash = (n) => { const s = Math.sin(n * 127.1 + 311.7) * 43758.5453; return s - Math.floor(s); };

const NS = 'http://www.w3.org/2000/svg';
const el = (name, attrs = {}) => {
  const n = document.createElementNS(NS, name);
  Object.entries(attrs).forEach(([k, v]) => n.setAttribute(k, v));
  return n;
};

/* ---------- charts ---------- */
function trendPoints(data, w, h, pad = 6) {
  const min = Math.min(...data) - 3, max = Math.max(...data) + 2;
  return data.map((v, i) => [pad + (i * (w - pad * 2)) / (data.length - 1), h - pad - ((v - min) / (max - min)) * (h - pad * 2)]);
}
const toPath = (pts) => pts.map(([x, y], i) => `${i ? 'L' : 'M'}${x.toFixed(1)} ${y.toFixed(1)}`).join(' ');

function drawChart(svg, data) {
  const W = 600, H = 200;
  const pts = trendPoints(data, W, H, 12);
  const g = el('linearGradient', { id: 'cg', x1: 0, y1: 0, x2: 0, y2: 1 });
  g.append(el('stop', { offset: '0', 'stop-color': '#4fb84f', 'stop-opacity': '.3' }), el('stop', { offset: '1', 'stop-color': '#4fb84f', 'stop-opacity': '0' }));
  const defs = el('defs'); defs.append(g);
  const grid = [0.25, 0.5, 0.75].map((f) => el('line', { class: 'grid', x1: 0, x2: W, y1: H * f, y2: H * f }));
  const line = toPath(pts);
  const dots = [3, 7, 11].map((i) => el('circle', { class: 'dot', cx: pts[i][0], cy: pts[i][1], r: 6 }));
  svg.replaceChildren(
    defs, ...grid,
    el('path', { class: 'area', d: `${line} L${pts.at(-1)[0]} ${H} L${pts[0][0]} ${H} Z` }),
    el('path', { class: 'line', d: line, pathLength: 1 }),
    ...dots,
  );
}

/* ---------- Analytics preview (демо-данные из api.js) ---------- */
function renderPreview(data) {
  $('#kpi-avg').textContent = formatNumber(data.avgScore, { minimumFractionDigits: 1, maximumFractionDigits: 1 });
  $('#kpi-delta').textContent = formatNumber(data.delta, { signDisplay: 'always', minimumFractionDigits: 1, maximumFractionDigits: 1 });
  $('#kpi-checked').textContent = formatNumber(data.checked);
  $('#kpi-appeals').textContent = formatNumber(data.statuses.find((s) => s.key === 'appealed')?.count ?? 0);
  $('#preview-demo').hidden = !data.isDemo;

  const total = data.statuses.reduce((s, x) => s + x.count, 0);
  $('#statuses').replaceChildren(...data.statuses.map((s) => {
    const li = document.createElement('li');
    const row = document.createElement('div'); row.className = 'row';
    const name = document.createElement('span'); name.textContent = t(`status.${s.key}`);
    const cnt = document.createElement('b'); cnt.textContent = formatNumber(s.count);
    row.append(name, cnt);
    const bar = document.createElement('div'); bar.className = 'bar';
    const i = document.createElement('i');
    i.style.setProperty('--w', `${(s.count / total) * 100}%`);
    i.style.setProperty('--c', STATUS_COLOR[s.key]);
    bar.append(i);
    li.append(row, bar);
    return li;
  }));

  $('#dialog-rows').replaceChildren(...data.dialogs.slice(0, 3).map((d) => {
    const tr = document.createElement('tr');
    const cell = (txt, cls) => { const td = document.createElement('td'); td.textContent = txt; if (cls) td.className = cls; return td; };
    const st = document.createElement('td');
    const badge = document.createElement('span'); badge.className = `st st--${d.status}`; badge.textContent = t(`status.${d.status}`);
    st.append(badge);
    // язык диалога — данные, а не язык интерфейса: код показывается как есть
    tr.append(cell(d.id), cell(d.operator, 'hide-sm'), cell(d.lang.toUpperCase(), 'hide-sm lang-code'), cell(formatNumber(d.score)), st, cell(formatDate(d.date, { day: 'numeric', month: 'short' }), 'hide-sm'));
    return tr;
  }));
}

/* ---------- About: визуальная демонстрация аудио (звук НЕ воспроизводится) ---------- */
function initAudioDemo() {
  const box = $('#audio-wave'), btn = $('#audio-btn'), bar = $('#audio-bar'), now = $('#audio-now');
  const N = 56;
  const bars = Array.from({ length: N }, (_, i) => {
    const b = document.createElement('i');
    const env = 0.35 + 0.65 * Math.abs(Math.sin(i * 0.37) * Math.cos(i * 0.11 + 1));
    b.style.setProperty('--h', `${Math.round((0.18 + env * 0.82 * (0.7 + 0.3 * hash(i))) * 100)}%`);
    b.style.setProperty('--d', `${-(hash(i + 5) * 2.6).toFixed(2)}s`);
    return b;
  });
  box.append(...bars);

  const DURATION = 12000;           // миллисекунд демонстрации
  const TOTAL_S = 134;              // «2:14»
  let playing = false, raf = 0, startAt = 0, startP = 0, p = 0;
  const label = () => btn.setAttribute('aria-label', t(playing ? 'about.audio.pause' : 'about.audio.play'));
  const fmt = (s) => `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, '0')}`;
  const paint = () => {
    const k = Math.floor(p * N);
    bars.forEach((b, i) => b.classList.toggle('is-played', i < k));
    bar.style.width = `${p * 100}%`;
    now.textContent = fmt(p * TOTAL_S);
  };
  function frame(ts) {
    p = clamp(startP + (ts - startAt) / DURATION, 0, 1);
    paint();
    if (p >= 1) { stop(true); return; }
    raf = requestAnimationFrame(frame);
  }
  function play() {
    if (p >= 1) p = 0;
    playing = true; startP = p; startAt = performance.now();
    btn.setAttribute('aria-pressed', 'true'); btn.querySelector('use').setAttribute('href', '#i-pause'); label();
    raf = requestAnimationFrame(frame);
  }
  function stop(reset = false) {
    playing = false; cancelAnimationFrame(raf);
    if (reset) { p = 0; paint(); }
    btn.setAttribute('aria-pressed', 'false'); btn.querySelector('use').setAttribute('href', '#i-play'); label();
  }
  btn.addEventListener('click', () => (playing ? stop() : play()));
  document.addEventListener('visibilitychange', () => { if (document.hidden && playing) stop(); });
  onLangChange(label);
  return { stop };
}

/* ---------- Continuous Green Flow ---------- */
function catmull(pts) {
  let d = `M${pts[0][0].toFixed(1)} ${pts[0][1].toFixed(1)}`;
  for (let i = 0; i < pts.length - 1; i++) {
    const p0 = pts[i - 1] || pts[i], p1 = pts[i], p2 = pts[i + 1], p3 = pts[i + 2] || p2;
    const c1 = [p1[0] + (p2[0] - p0[0]) / 6, p1[1] + (p2[1] - p0[1]) / 6];
    const c2 = [p2[0] - (p3[0] - p1[0]) / 6, p2[1] - (p3[1] - p1[1]) / 6];
    d += ` C${c1[0].toFixed(1)} ${c1[1].toFixed(1)} ${c2[0].toFixed(1)} ${c2[1].toFixed(1)} ${p2[0].toFixed(1)} ${p2[1].toFixed(1)}`;
  }
  return d;
}

function initFlow(root) {
  const svg = $('#flow'), ghost = $('#flow-ghost'), band = $('#flow-band'), line = $('#flow-line');
  let len = 0, endY = 1, rootTop = 0;

  const box = (sel) => {
    const e = $(sel, root); if (!e) return null;
    const r = e.getBoundingClientRect(), rr = root.getBoundingClientRect();
    return { top: r.top - rr.top, bottom: r.bottom - rr.top, h: r.height, cy: r.top - rr.top + r.height / 2 };
  };

  function build() {
    if (root.hidden) return;
    const W = root.clientWidth, H = root.scrollHeight;
    if (!W || !H) return;
    svg.setAttribute('viewBox', `0 0 ${W} ${H}`);
    const hero = box('.hero'), about = box('.about__grid'), steps = box('.steps'), bento = box('.bento'), prev = box('.appwin');
    if (!hero || !about || !steps || !bento || !prev) return;
    const x = (f) => f * W;
    const mobile = W < 768;
    let pts;
    if (mobile) {
      pts = [[x(-0.05), hero.bottom - 100], [x(0.88), about.cy], [x(0.08), steps.cy], [x(0.92), bento.cy], [x(0.1), prev.cy], [x(0.9), prev.bottom - 20]];
    } else {
      pts = [
        [x(-0.04), hero.bottom - 220], [x(0.22), hero.bottom - 40],            // Hero → About: лента выходит из первого экрана
        [x(0.48), about.top + about.h * 0.5], [x(0.78), about.bottom + 10],
        [x(0.96), steps.cy - 30], [x(0.74), steps.cy], [x(0.5), steps.cy], [x(0.26), steps.cy], [x(0.04), steps.cy + 40], // → линия процесса
        [x(0.1), bento.top + 60], [x(0.5), bento.cy], [x(0.92), bento.bottom - 20],       // проходит за карточками
        [x(0.06), prev.top + prev.h * 0.92],                                                // → линия графика
        [x(0.28), prev.top + prev.h * 0.8], [x(0.4), prev.top + prev.h * 0.88], [x(0.55), prev.top + prev.h * 0.62],
        [x(0.68), prev.top + prev.h * 0.7], [x(0.82), prev.top + prev.h * 0.4], [x(0.96), prev.top + prev.h * 0.22],
      ];
    }
    const d = catmull(pts);
    [ghost, band, line].forEach((p) => p.setAttribute('d', d));
    band.style.strokeWidth = mobile ? 22 : 46;
    len = line.getTotalLength();
    endY = pts.at(-1)[1];
    [band, line].forEach((p) => { p.style.strokeDasharray = `${len}`; });
    update();
  }

  function update() {
    if (!len) return;
    let p = 1;
    if (!reduceMq.matches) {
      rootTop = root.getBoundingClientRect().top;
      p = clamp((-rootTop + innerHeight * 0.72) / endY, 0, 1);
    }
    const off = len * (1 - p);
    line.style.strokeDashoffset = off;
    band.style.strokeDashoffset = off;
  }
  return { build, update };
}

export function initLanding() {
  const root = $('[data-view="welcome"]');
  const heroWave = createWave($('#wave'));
  const fish = initFishEye($('[data-fisheye]'));
  const audio = initAudioDemo();
  const flow = initFlow(root);
  const topbar = $('#topbar');

  let active = false;
  const waves = [{ canvas: $('#wave'), wave: heroWave, visible: false }];
  const syncWaves = () => waves.forEach((w) => (active && w.visible ? w.wave.start() : w.wave.stop()));
  const waveIO = new IntersectionObserver((entries) => {
    entries.forEach((e) => { waves.find((w) => w.canvas === e.target).visible = e.isIntersecting; });
    syncWaves();
  }, { rootMargin: '80px' });
  waves.forEach((w) => waveIO.observe(w.canvas));

  // reveal on scroll
  const revealIO = new IntersectionObserver((entries) => {
    entries.forEach((e) => { if (e.isIntersecting) { e.target.classList.add('in'); revealIO.unobserve(e.target); } });
  }, { threshold: 0.18, rootMargin: '0px 0px -6% 0px' });
  $$('.reveal', root).forEach((n) => revealIO.observe(n));

  // дуга в Final CTA
  const ctaIO = new IntersectionObserver((entries) => entries.forEach((e) => { if (e.isIntersecting) { e.target.classList.add('arc-in'); ctaIO.disconnect(); } }), { threshold: 0.2 });
  ctaIO.observe($('.cta', root));

  // scroll: лента + прозрачность topbar (один rAF на кадр)
  let ticking = false;
  const onScroll = () => {
    if (ticking) return;
    ticking = true;
    requestAnimationFrame(() => {
      ticking = false;
      topbar.classList.toggle('is-solid', scrollY > 24);
      flow.update();
    });
  };

  // перестраиваем ленту при изменении размеров/высоты контента
  let buildRaf = 0;
  const rebuild = () => { cancelAnimationFrame(buildRaf); buildRaf = requestAnimationFrame(flow.build); };
  const ro = new ResizeObserver(rebuild);

  // чек-лист: мини-редактор критериев
  $$('.critlist button').forEach((b) => b.addEventListener('click', () => b.setAttribute('aria-checked', String(b.getAttribute('aria-checked') !== 'true'))));
  $('#scroll-hint').addEventListener('click', () => $('#about').scrollIntoView({ behavior: reduceMq.matches ? 'auto' : 'smooth' }));
  reduceMq.addEventListener('change', flow.update);

  // данные превью (демо до появления Backend API)
  let data = null;
  getAnalyticsPreview().then((d) => { data = d; drawChart($('#chart'), d.trend); renderPreview(d); rebuild(); });
  onLangChange(() => { if (data) renderPreview(data); rebuild(); });

  return {
    enter() {
      active = true;
      heroWave.reset();
      syncWaves();
      fish.refresh();
      window.addEventListener('scroll', onScroll, { passive: true });
      ro.observe(root);
      topbar.classList.toggle('is-solid', scrollY > 24);
      rebuild();
    },
    leave() {
      active = false;
      syncWaves();
      fish.destroy();
      audio.stop();
      window.removeEventListener('scroll', onScroll);
      ro.disconnect();
      topbar.classList.remove('is-solid');
    },
    calm(v) { heroWave.setCalm(v); },
  };
}
