// Living Audio Wave — Canvas 2D. Чисто декоративная: без аудио и микрофона.
const BAR_GAP = 6;          // CSS px между барами
const BAR_W = 1.6;          // толщина бара
const MAX_DPR = 2;
const HOVER_SIGMA = 130;    // радиус влияния курсора, px
const HOVER_GAIN = 1.1;     // +110% амплитуды в центре влияния
const COLOR = '79, 174, 62';

// детерминированный шум 0..1: форма стабильна и не похожа на хаотичный эквалайзер
const hash = (n) => {
  const s = Math.sin(n * 127.1 + 311.7) * 43758.5453;
  return s - Math.floor(s);
};

// opts.interactive — реакция на курсор; opts.amp — общий множитель амплитуды; opts.seed — сдвиг формы
export function createWave(canvas, { interactive = true, amp: baseAmp = 1, seed = 0 } = {}) {
  const ctx = canvas.getContext('2d');
  const reduceMq = window.matchMedia('(prefers-reduced-motion: reduce)');
  let w = 0, h = 0, bars = [];
  let raf = 0, running = false, wantRun = false, last = 0, t = seed * 10;
  let calm = 1, calmTarget = 1;                  // множитель амплитуды (переход на Login)
  let px = 0, infl = 0, inflTarget = 0;
  let hasPointer = false, pointerX = 0, pointerY = 0;

  function resize() {
    const r = canvas.getBoundingClientRect();
    if (!r.width || !r.height) return;
    const dpr = Math.min(window.devicePixelRatio || 1, MAX_DPR);
    w = r.width; h = r.height;
    canvas.width = Math.round(w * dpr);
    canvas.height = Math.round(h * dpr);
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    const n = Math.floor(w / BAR_GAP);
    bars = Array.from({ length: n }, (_, i) => ({
      x: Math.round(i * BAR_GAP + BAR_GAP / 2) + 0.5,
      u: n > 1 ? i / (n - 1) : 0,
      a: hash(i + seed),
      up: 0.55 + 0.45 * hash(i + 999 + seed),
      down: 0.55 + 0.45 * hash(i + 4321 + seed),
    }));
    draw();
  }

  function draw() {
    ctx.clearRect(0, 0, w, h);
    const mid = h / 2, maxAmp = h * 0.46 * baseAmp;
    const hover = interactive && !reduceMq.matches && infl > 0.001;
    ctx.lineWidth = BAR_W;
    ctx.lineCap = 'round';
    ctx.strokeStyle = `rgba(${COLOR}, .5)`;
    ctx.beginPath();
    for (const bar of bars) {
      // плавная огибающая: бегущие «фразы» + медленный дрейф по времени
      const e1 = 0.5 + 0.5 * Math.sin(bar.u * 18 + t * 0.55);
      const e2 = 0.5 + 0.5 * Math.sin(bar.u * 7.5 - t * 0.35 + 1.3);
      const env = Math.pow(e1 * 0.65 + e2 * 0.35, 2.2);
      const tex = 0.6 + 0.4 * Math.sin(t * 0.8 + bar.a * 6.283);
      let amp = (0.06 + env * tex) * maxAmp * calm;
      if (hover) {
        const d = bar.x - px;
        amp *= 1 + HOVER_GAIN * infl * Math.exp(-(d * d) / (2 * HOVER_SIGMA * HOVER_SIGMA));
      }
      amp = Math.min(amp, h * 0.5 - 2);
      ctx.moveTo(bar.x, mid - Math.max(1.5, amp * bar.up));
      ctx.lineTo(bar.x, mid + Math.max(1.5, amp * bar.down));
    }
    ctx.stroke();
    ctx.fillStyle = `rgba(${COLOR}, .55)`;
    ctx.fillRect(0, mid - 0.5, w, 1);
  }

  function frame(now) {
    if (!running) return;
    const dt = Math.min((now - last) / 1000, 0.05);   // не прыгаем после паузы
    last = now;
    t += dt;
    calm += (calmTarget - calm) * (1 - Math.exp(-dt * 7));
    if (interactive) {
      if (hasPointer) {
        const r = canvas.getBoundingClientRect();
        px += (pointerX - r.left - px) * (1 - Math.exp(-dt * 10));
        inflTarget = Math.abs(pointerY - (r.top + r.height / 2)) < r.height / 2 + 60 ? 1 : 0;
      } else {
        inflTarget = 0;
      }
      infl += (inflTarget - infl) * (1 - Math.exp(-dt * 6));
    }
    draw();
    raf = requestAnimationFrame(frame);
  }

  function onMove(e) {
    if (!hasPointer) px = e.clientX - canvas.getBoundingClientRect().left;
    hasPointer = true;
    pointerX = e.clientX;
    pointerY = e.clientY;
  }
  const onLeave = () => { hasPointer = false; };
  const onOut = (e) => { if (!e.relatedTarget) onLeave(); };

  function startLoop() {
    if (reduceMq.matches) { draw(); return; }          // статичный кадр
    if (running || document.hidden) return;
    running = true;
    last = performance.now();
    raf = requestAnimationFrame(frame);
  }
  function pauseLoop() { running = false; cancelAnimationFrame(raf); }
  function onVisibility() { if (document.hidden) pauseLoop(); else if (wantRun) startLoop(); }
  function onMotionPref() { pauseLoop(); if (wantRun) startLoop(); draw(); }

  const ro = new ResizeObserver(resize);

  return {
    start() {
      if (wantRun) return;
      wantRun = true;
      ro.observe(canvas);
      if (interactive) {
        window.addEventListener('pointermove', onMove, { passive: true });
        window.addEventListener('pointerup', onLeave);
        window.addEventListener('pointercancel', onLeave);
        window.addEventListener('blur', onLeave);
        document.addEventListener('pointerout', onOut);
      }
      document.addEventListener('visibilitychange', onVisibility);
      reduceMq.addEventListener('change', onMotionPref);
      resize();
      startLoop();
    },
    stop() {
      if (!wantRun) return;
      wantRun = false;
      pauseLoop();
      ro.disconnect();
      window.removeEventListener('pointermove', onMove);
      window.removeEventListener('pointerup', onLeave);
      window.removeEventListener('pointercancel', onLeave);
      window.removeEventListener('blur', onLeave);
      document.removeEventListener('pointerout', onOut);
      document.removeEventListener('visibilitychange', onVisibility);
      reduceMq.removeEventListener('change', onMotionPref);
      hasPointer = false; infl = 0;
    },
    setCalm(v) { calmTarget = v; if (reduceMq.matches) { calm = v; draw(); } },
    reset() { calm = calmTarget = 1; },
  };
}
