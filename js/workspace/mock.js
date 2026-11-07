// ВРЕМЕННЫЙ Mock Adapter (Workspace, этап 1).
// Backend (FastAPI) ещё не опубликовал OpenAPI, поэтому структуры ниже — ПРЕДЛОЖЕНИЕ frontend,
// зафиксированное в docs/api-contract.md. Когда Backend определит реальные поля — меняются только
// этот файл и нормализация в js/workspace/pages/*. Все данные здесь вымышленные и всегда помечаются в UI как «Демо».
// Идентификаторы имеют префикс DEMO-/APL-DEMO-, чтобы их нельзя было спутать с реальными.

const DAY = 86400000;
const OPERATORS = Array.from({ length: 12 }, (_, i) => `OP-DEMO-${String(i + 1).padStart(2, '0')}`);

// детерминированный ГПСЧ: один и тот же «набор» при каждом запуске
function rng(seed) {
  let s = seed | 0;
  return () => {
    s = (s + 0x6d2b79f5) | 0;
    let t = Math.imul(s ^ (s >>> 15), 1 | s);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}
const clamp = (v, a, b) => Math.min(b, Math.max(a, v));
const pad = (n) => String(n).padStart(2, '0');
const dateKey = (d) => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;

let pool = null;

function build() {
  const rand = rng(20261107);
  const today = new Date();
  today.setHours(0, 0, 0, 0);
  const conversations = [];
  const appeals = [];
  let n = 1000;
  for (let back = 179; back >= 0; back--) {
    const day = new Date(today);
    day.setDate(day.getDate() - back);
    const base = 79 + 6 * Math.sin(back / 19) + (179 - back) * 0.025;
    const count = 7 + Math.floor(rand() * 9);
    for (let k = 0; k < count; k++) {
      const dip = rand() < 0.1 ? rand() * 38 : 0;   // редкие «плохие» диалоги, чтобы были и низкие оценки
      const score = clamp(Math.round(base + (rand() - 0.5) * 30 - dip), 18, 100);
      const r = rand();
      const language = r < 0.5 ? 'ru' : r < 0.82 ? 'kk' : 'en';
      const at = new Date(day.getTime() + (8 + Math.floor(rand() * 11)) * 3600000 + Math.floor(rand() * 60) * 60000);
      const id = `DEMO-${++n}`;
      const conv = {
        id,
        operator_id: rand() < 0.14 ? null : OPERATORS[Math.floor(rand() * OPERATORS.length)],
        language,
        channel: rand() < 0.55 ? 'call' : 'chat',
        status: 'evaluated',
        score,
        gross_violation: (score < 55 && rand() < 0.5) || rand() < 0.01,
        evaluated_at: at.toISOString(),
        _day: dateKey(day),
      };
      if (rand() < 0.09) {
        const pending = back < 25 ? rand() < 0.6 : rand() < 0.12;
        const status = pending ? 'pending' : rand() < 0.45 ? 'accepted' : 'rejected';
        conv.status = pending ? 'appealed' : 'reviewed';
        appeals.push({
          id: `APL-${id}`,
          conversation_id: id,
          operator_id: conv.operator_id,
          status,
          created_at: new Date(at.getTime() + 3600000 * (2 + Math.floor(rand() * 30))).toISOString(),
        });
      }
      conversations.push(conv);
    }
  }
  return { conversations, appeals, today };
}

const avg = (xs) => (xs.length ? xs.reduce((a, b) => a + b, 0) / xs.length : null);
const round1 = (v) => (v == null ? null : Math.round(v * 10) / 10);

const BUCKETS = [[0, 49], [50, 64], [65, 79], [80, 89], [90, 100]];

export function mockOverview(days) {
  pool ||= build();
  const { conversations, appeals, today } = pool;
  const from = new Date(today); from.setDate(from.getDate() - (days - 1));
  const prevFrom = new Date(from); prevFrom.setDate(prevFrom.getDate() - days);
  const fromKey = dateKey(from), prevKey = dateKey(prevFrom);
  const cur = conversations.filter((c) => c._day >= fromKey);
  const prev = conversations.filter((c) => c._day >= prevKey && c._day < fromKey);
  const strip = ({ _day, gross_violation, ...c }) => c;   // служебные поля наружу не отдаём

  const byDay = new Map();
  cur.forEach((c) => { (byDay.get(c._day) || byDay.set(c._day, []).get(c._day)).push(c.score); });
  const trend = [...byDay.keys()].sort().map((d) => ({ date: d, avg_score: round1(avg(byDay.get(d))), count: byDay.get(d).length }));

  const byLang = ['ru', 'kk', 'en'].map((l) => {
    const xs = cur.filter((c) => c.language === l).map((c) => c.score);
    return { language: l, count: xs.length, avg_score: round1(avg(xs)) };
  });
  const curAvg = avg(cur.map((c) => c.score)), prevAvg = avg(prev.map((c) => c.score));

  return {
    period: { from: fromKey, to: dateKey(today), days },
    kpis: {
      avg_score: round1(curAvg),
      avg_score_delta: curAvg != null && prevAvg != null ? round1(curAvg - prevAvg) : null,
      evaluated_count: cur.length,
      gross_violations_count: cur.filter((c) => c.gross_violation).length,
      pending_appeals_count: appeals.filter((a) => a.status === 'pending').length,
    },
    trend,
    score_distribution: BUCKETS.map(([min, max]) => ({ min, max, count: cur.filter((c) => c.score >= min && c.score <= max).length })),
    by_language: byLang,
    recent_conversations: cur.slice().sort((a, b) => b.evaluated_at.localeCompare(a.evaluated_at)).slice(0, 6).map(strip),
    recent_appeals: appeals.filter((a) => a.created_at.slice(0, 10) >= fromKey).sort((a, b) => b.created_at.localeCompare(a.created_at)).slice(0, 5),
  };
}
