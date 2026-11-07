// Fish Eye typography: каждая буква — span, масштаб только через transform: scale().
const MAX_SCALE = 1.4;
const SIGMA = 85;       // px — гладкое гауссово затухание влияния
const REACH = 3.2;      // дальше REACH*SIGMA курсор игнорируется

export function initFishEye(el) {
  const text = el.textContent.trim();
  const mq = window.matchMedia('(hover: hover) and (pointer: fine) and (min-width: 769px)');
  const reduce = window.matchMedia('(prefers-reduced-motion: reduce)');
  el.setAttribute('aria-label', text);
  el.textContent = '';
  const letters = [...text].map((c) => {
    const s = document.createElement('span');
    s.className = 'ch';
    s.setAttribute('aria-hidden', 'true');
    s.textContent = c;
    el.appendChild(s);
    return { s, cx: 0, cy: 0, cur: 1, target: 1 };
  });

  let raf = 0, active = false, px = 0, py = 0, inside = false, measureRaf = 0;
  let box = { l: 0, r: 0, t: 0, b: 0 };     // page-координаты: на скролле пересчёт не нужен
  const enabled = () => mq.matches && !reduce.matches && !document.hidden;
  const apply = (l) => { l.s.style.transform = l.cur === 1 ? '' : `scale(${l.cur.toFixed(4)})`; };

  function measure() {
    // scale идёт от центра, но сбрасываем для точности
    letters.forEach((l) => { l.s.style.transform = ''; });
    letters.forEach((l) => {
      const r = l.s.getBoundingClientRect();
      l.cx = r.left + r.width / 2;
      l.cy = r.top + r.height / 2 + scrollY;
    });
    const er = el.getBoundingClientRect();
    box = { l: er.left, r: er.right, t: er.top + scrollY, b: er.bottom + scrollY };
    letters.forEach(apply);
  }
  // при прокрутке позиции в viewport меняются — пересчитываем один раз за кадр
  const measureSoon = () => {
    if (measureRaf) return;
    measureRaf = requestAnimationFrame(() => { measureRaf = 0; measure(); });
  };

  function tick() {
    let moving = false;
    for (const l of letters) {
      if (inside) {
        const d = Math.hypot(px - l.cx, (py - (l.cy - scrollY)) * 0.8);
        l.target = d > SIGMA * REACH ? 1 : 1 + (MAX_SCALE - 1) * Math.exp(-(d * d) / (2 * SIGMA * SIGMA));
      } else {
        l.target = 1;
      }
      l.cur += (l.target - l.cur) * 0.18;
      if (Math.abs(l.target - l.cur) < 0.001) l.cur = l.target; else moving = true;
      apply(l);
    }
    raf = moving || inside ? requestAnimationFrame(tick) : 0;
  }
  const kick = () => { if (!raf) raf = requestAnimationFrame(tick); };

  function onMove(e) {
    px = e.clientX;
    py = e.clientY;
    const m = SIGMA * REACH, top = box.t - scrollY, bottom = box.b - scrollY;
    inside = px > box.l - m && px < box.r + m && py > top - m && py < bottom + m;
    kick();
  }
  const onLeave = () => { inside = false; kick(); };
  const onOut = (e) => { if (!e.relatedTarget) onLeave(); };

  function attach() {
    if (active) return;
    active = true;
    measure();
    window.addEventListener('pointermove', onMove, { passive: true });
    document.addEventListener('pointerout', onOut);
    window.addEventListener('blur', onLeave);
    window.addEventListener('resize', measureSoon);
  }
  function detach() {
    if (!active) return;
    active = false;
    inside = false;
    window.removeEventListener('pointermove', onMove);
    document.removeEventListener('pointerout', onOut);
    window.removeEventListener('blur', onLeave);
    window.removeEventListener('resize', measureSoon);
    cancelAnimationFrame(raf);
    raf = 0;
    letters.forEach((l) => { l.cur = l.target = 1; apply(l); });
  }
  const sync = () => (enabled() && !el.closest('[hidden]') ? attach() : detach());

  mq.addEventListener('change', sync);
  reduce.addEventListener('change', sync);
  document.addEventListener('visibilitychange', sync);
  if (document.fonts && document.fonts.ready) document.fonts.ready.then(() => active && measure());

  return { refresh() { sync(); if (active) measure(); }, destroy: detach };
}
