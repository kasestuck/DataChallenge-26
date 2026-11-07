// ВРЕМЕННЫЕ демо-данные Mock Adapter. Backend (FastAPI) ещё не опубликовал OpenAPI, поэтому структуры —
// предложение Frontend (docs/api-contract.md). Всё здесь вымышленное: идентификаторы DEMO-*, реплики — шаблонные,
// результаты «AI-оценки» генерируются локально и в UI всегда помечаются как «Демо». Реальную Meta AI вызывает только Backend.

export const DAY = 86400000;
export const clamp = (v, a, b) => Math.min(b, Math.max(a, v));
export const round1 = (v) => Math.round(v * 10) / 10;
export const pad = (n) => String(n).padStart(2, '0');
export const dayKey = (d) => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;

// детерминированный ГПСЧ: одинаковый набор при каждом запуске
export function rng(seed) {
  let s = seed | 0;
  return () => {
    s = (s + 0x6d2b79f5) | 0;
    let t = Math.imul(s ^ (s >>> 15), 1 | s);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}
const L = (ru, kk, en) => ({ ru, kk, en });

// Условная структура чек-листа: 7 оцениваемых критериев (сумма весов 100) + 1 флаг грубого нарушения.
// Реальные критерии Freedom Backend отдаёт сам; названия здесь не являются официальными.
export const BASE_CRITERIA = [
  { id: 'c1', name: L('Приветствие и представление', 'Сәлемдесу және таныстыру', 'Greeting and introduction'), description: L('Оператор поприветствовал клиента и обозначил свою роль.', 'Оператор клиентпен сәлемдесіп, өз рөлін атады.', 'The operator greeted the customer and stated their role.'), weight: 10, answer_type: 'yes_no', scale: null, na_allowed: false, na_condition: '', gross: false, builtin: true },
  { id: 'c2', name: L('Выявление потребности', 'Қажеттілікті анықтау', 'Needs identification'), description: L('Оператор уточнил суть обращения и задал необходимые вопросы.', 'Оператор өтініштің мәнін нақтылап, қажетті сұрақтар қойды.', 'The operator clarified the request and asked the necessary questions.'), weight: 15, answer_type: 'scale', scale: { min: 0, max: 5 }, na_allowed: false, na_condition: '', gross: false, builtin: true },
  { id: 'c3', name: L('Корректность информации', 'Ақпараттың дұрыстығы', 'Accuracy of information'), description: L('Сообщённая клиенту информация точна и актуальна.', 'Клиентке айтылған ақпарат дәл әрі өзекті.', 'The information given to the customer is accurate and up to date.'), weight: 20, answer_type: 'scale', scale: { min: 0, max: 5 }, na_allowed: false, na_condition: '', gross: false, builtin: true },
  { id: 'c4', name: L('Полнота решения', 'Шешімнің толықтығы', 'Completeness of the solution'), description: L('Вопрос клиента решён полностью или назван следующий шаг.', 'Клиент мәселесі толық шешілді немесе келесі қадам аталды.', 'The issue is resolved or the next step is named.'), weight: 15, answer_type: 'scale', scale: { min: 0, max: 5 }, na_allowed: false, na_condition: '', gross: false, builtin: true },
  { id: 'c5', name: L('Тон и вежливость', 'Тон және сыпайылық', 'Tone and courtesy'), description: L('Корректный, доброжелательный тон общения.', 'Дұрыс, ізгі ниетті қарым-қатынас реңкі.', 'A correct, friendly tone of communication.'), weight: 15, answer_type: 'scale', scale: { min: 0, max: 5 }, na_allowed: false, na_condition: '', gross: false, builtin: true },
  { id: 'c6', name: L('Предупреждение о рисках', 'Тәуекелдер туралы ескерту', 'Risk disclosure'), description: L('Оператор напомнил, что инвестиции связаны с риском.', 'Оператор инвестицияның тәуекелмен байланысты екенін еске салды.', 'The operator reminded the customer that investing involves risk.'), weight: 15, answer_type: 'yes_no', scale: null, na_allowed: true, na_condition: L('Клиент не обсуждал инвестиционные продукты', 'Клиент инвестициялық өнімдерді талқыламады', 'The customer did not discuss investment products'), gross: false, builtin: true },
  { id: 'c7', name: L('Завершение диалога', 'Диалогты аяқтау', 'Closing the conversation'), description: L('Оператор корректно завершил диалог и предложил обратиться снова.', 'Оператор диалогты дұрыс аяқтап, қайта хабарласуды ұсынды.', 'The operator closed the conversation properly and invited the customer to return.'), weight: 10, answer_type: 'yes_no', scale: null, na_allowed: false, na_condition: '', gross: false, builtin: true },
  { id: 'c8', name: L('Грубое нарушение', 'Өрескел бұзушылық', 'Gross violation'), description: L('Грубость, оскорбления или разглашение конфиденциальных данных.', 'Дөрекілік, қорлау немесе құпия деректерді жария ету.', 'Rudeness, insults or disclosure of confidential data.'), weight: 0, answer_type: 'flag', scale: null, na_allowed: false, na_condition: '', gross: true, builtin: true },
];

// Шаблонные диалоги (исходный язык реплик сохраняется). ev — индексы реплик, на которые ссылаются критерии.
// roles: b=bot, c=client, o=operator, s=system
const S = (lines, ev, rude = null) => ({ lines, ev, rude });
export const SCRIPTS = {
  ru: {
    good: S([
      ['b', 'Здравствуйте! Я виртуальный помощник Freedom. Опишите ваш вопрос, и я передам его специалисту.'],
      ['c', 'Добрый день. Не могу вывести деньги со счёта, заявка висит уже два дня.'],
      ['s', 'Диалог передан оператору'],
      ['o', 'Здравствуйте! Вас приветствует служба поддержки. Давайте разберёмся с вашей заявкой.'],
      ['c', 'Номер заявки 4471, сумма 150 000 тенге.'],
      ['o', 'Спасибо. Вижу заявку: она ожидает дополнительной проверки реквизитов. Подтвердите, пожалуйста, что реквизиты указаны верно.'],
      ['c', 'Да, реквизиты верные.'],
      ['o', 'Благодарю. Я передал заявку на ускоренную обработку, средства поступят в течение рабочего дня. Также напоминаю: инвестиции связаны с риском, доходность не гарантируется.'],
      ['c', 'Понятно, спасибо.'],
      ['o', 'Рады были помочь. Если появятся вопросы, обращайтесь снова. Хорошего дня!'],
      ['s', 'Диалог завершён'],
    ], { c1: 3, c2: 5, c3: 5, c4: 7, c5: 3, c6: 7, c7: 9 }),
    poor: S([
      ['b', 'Здравствуйте! Я виртуальный помощник Freedom. Опишите ваш вопрос, и я передам его специалисту.'],
      ['c', 'Почему мои деньги до сих пор не пришли? Уже третий день!'],
      ['s', 'Диалог передан оператору'],
      ['o', 'Что у вас?'],
      ['c', 'Заявка на вывод, номер 4471.'],
      ['o', 'Ну подождите, не я же это делаю. Сами должны были проверять реквизиты.'],
      ['c', 'Мне нужен ответ, а не претензии.'],
      ['o', 'Ждите, всё обработают когда-нибудь.'],
      ['c', 'Спасибо за «помощь»...'],
      ['s', 'Диалог завершён'],
    ], { c1: 3, c2: 3, c3: 5, c4: 7, c5: 5, c7: 7 }, 5),
  },
  kk: {
    good: S([
      ['b', 'Сәлеметсіз бе! Мен Freedom виртуалды көмекшісімін. Сұрағыңызды жазыңыз, мен оны маманға жіберемін.'],
      ['c', 'Қайырлы күн. Шотымнан ақша шығара алмай отырмын, өтінім екі күннен бері тұр.'],
      ['s', 'Диалог операторға жіберілді'],
      ['o', 'Сәлеметсіз бе! Сізді қолдау қызметі қарсы алады. Өтінімді бірге қарастырайық.'],
      ['c', 'Өтінім нөмірі 4471, сомасы 150 000 теңге.'],
      ['o', 'Рахмет. Өтінімді көріп тұрмын: ол деректемелерді қосымша тексеруді күтуде. Деректемелердің дұрыс көрсетілгенін растаңызшы.'],
      ['c', 'Иә, деректемелер дұрыс.'],
      ['o', 'Рахмет. Өтінімді жеделдетілген өңдеуге жібердім, қаражат жұмыс күні ішінде түседі. Сондай-ақ еске саламын: инвестиция тәуекелмен байланысты, табыстылыққа кепілдік берілмейді.'],
      ['c', 'Түсінікті, рахмет.'],
      ['o', 'Көмектескенімізге қуаныштымыз. Сұрақтар туындаса, қайта хабарласыңыз. Күніңіз сәтті болсын!'],
      ['s', 'Диалог аяқталды'],
    ], { c1: 3, c2: 5, c3: 5, c4: 7, c5: 3, c6: 7, c7: 9 }),
    poor: S([
      ['b', 'Сәлеметсіз бе! Мен Freedom виртуалды көмекшісімін. Сұрағыңызды жазыңыз, мен оны маманға жіберемін.'],
      ['c', 'Неге ақшам әлі түскен жоқ? Бүгін үшінші күн!'],
      ['s', 'Диалог операторға жіберілді'],
      ['o', 'Не керек?'],
      ['c', 'Шығаруға өтінім, нөмірі 4471.'],
      ['o', 'Күте тұрыңыз, мен жасап отырған жоқпын. Деректемелерді өзіңіз тексеруіңіз керек еді.'],
      ['c', 'Маған жауап керек, талап емес.'],
      ['o', 'Күтіңіз, бір кезде өңдеп қояды.'],
      ['c', 'Көмегіңізге рахмет...'],
      ['s', 'Диалог аяқталды'],
    ], { c1: 3, c2: 3, c3: 5, c4: 7, c5: 5, c7: 7 }, 5),
  },
  en: {
    good: S([
      ['b', 'Hello! I am the Freedom virtual assistant. Describe your question and I will pass it to a specialist.'],
      ['c', 'Good afternoon. I cannot withdraw money from my account, the request has been pending for two days.'],
      ['s', 'Conversation transferred to an operator'],
      ['o', 'Hello! This is the support team. Let us look into your request together.'],
      ['c', 'The request number is 4471, the amount is 150,000 tenge.'],
      ['o', 'Thank you. I can see the request: it is waiting for an additional check of the bank details. Could you confirm that the details are correct?'],
      ['c', 'Yes, the details are correct.'],
      ['o', 'Thank you. I have sent the request for fast-track processing, the funds will arrive within one business day. Please also remember that investing involves risk and returns are not guaranteed.'],
      ['c', 'Understood, thank you.'],
      ['o', 'Glad to help. If you have more questions, please contact us again. Have a great day!'],
      ['s', 'Conversation closed'],
    ], { c1: 3, c2: 5, c3: 5, c4: 7, c5: 3, c6: 7, c7: 9 }),
    poor: S([
      ['b', 'Hello! I am the Freedom virtual assistant. Describe your question and I will pass it to a specialist.'],
      ['c', 'Why has my money still not arrived? It is already the third day!'],
      ['s', 'Conversation transferred to an operator'],
      ['o', 'What do you want?'],
      ['c', 'A withdrawal request, number 4471.'],
      ['o', 'Well, wait. It is not me who does this. You should have checked the details yourself.'],
      ['c', 'I need an answer, not accusations.'],
      ['o', 'Just wait, they will process it sometime.'],
      ['c', 'Thanks for the "help"...'],
      ['s', 'Conversation closed'],
    ], { c1: 3, c2: 3, c3: 5, c4: 7, c5: 5, c7: 7 }, 5),
  },
};

export const EXPLAIN = {
  ru: { good: 'Критерий выполнен: подтверждающая реплика указана в доказательстве.', partial: 'Критерий выполнен частично: в реплике есть признаки требования, но не всё.', bad: 'Критерий не выполнен: подтверждающих реплик в диалоге не найдено.', flagYes: 'Обнаружено грубое нарушение: см. реплику в доказательстве.', flagNo: 'Грубых нарушений не обнаружено.' },
  kk: { good: 'Өлшем орындалды: растайтын реплика дәлелде көрсетілген.', partial: 'Өлшем ішінара орындалды: репликада талап белгілері бар, бірақ толық емес.', bad: 'Өлшем орындалмады: диалогта растайтын реплика табылмады.', flagYes: 'Өрескел бұзушылық анықталды: дәлелдегі репликаны қараңыз.', flagNo: 'Өрескел бұзушылықтар анықталмады.' },
  en: { good: 'Criterion met: the supporting message is cited as evidence.', partial: 'Criterion partly met: the message shows signs of the requirement, but not all of it.', bad: 'Criterion not met: no supporting messages were found in the conversation.', flagYes: 'A gross violation was detected: see the cited message.', flagNo: 'No gross violations detected.' },
};
export const APPEAL_COMMENT = {
  ru: 'Не согласен с оценкой по этому критерию: считаю, что требование было выполнено.',
  kk: 'Бұл өлшем бойынша бағамен келіспеймін: талап орындалды деп санаймын.',
  en: 'I disagree with the score for this criterion: I believe the requirement was met.',
};

// ---------- генерация набора ----------
export const OPERATORS = Array.from({ length: 12 }, (_, i) => `OP-DEMO-${pad(i + 1)}`);

// Оценка критериев по «качеству» диалога q (0..1). Итог считает «Backend» (mock), не UI.
export function scoreCriteria(criteria, q, rand) {
  const rows = criteria.map((c) => {
    if (c.na_allowed && rand() < 0.28) return { criterion_id: c.id, answer: null, score: null, na: true };
    if (c.answer_type === 'flag') return { criterion_id: c.id, answer: q < 0.32 && rand() < 0.7, score: 0, na: false };
    if (c.answer_type === 'yes_no') { const yes = rand() < 0.15 + 0.85 * q; return { criterion_id: c.id, answer: yes, score: yes ? c.weight : 0, na: false }; }
    const { min, max } = c.scale || { min: 0, max: 5 };
    const v = clamp(Math.round(min + (max - min) * clamp(q + (rand() - 0.5) * 0.35, 0, 1)), min, max);
    return { criterion_id: c.id, answer: v, score: round1((c.weight * (v - min)) / (max - min || 1)), na: false };
  });
  return rows;
}
export function totalOf(criteria, rows) {
  let earned = 0, applicable = 0;
  rows.forEach((r) => {
    const c = criteria.find((x) => x.id === r.criterion_id);
    if (!c || c.answer_type === 'flag' || r.na) return;
    applicable += c.weight; earned += r.score ?? 0;
  });
  return applicable ? Math.round((earned / applicable) * 100) : null;
}

export function buildStore() {
  const rand = rng(20261107);
  const today = new Date(); today.setHours(0, 0, 0, 0);
  const conversations = [];
  const evalCores = new Map();   // evaluation_id -> { id, conversation_id, checklist_version, rows, total, gross, created_at, status }
  let n = 1000;
  for (let back = 179; back >= 0; back--) {
    const day = new Date(today); day.setDate(day.getDate() - back);
    const base = 79 + 6 * Math.sin(back / 19) + (179 - back) * 0.025;
    const count = 7 + Math.floor(rand() * 9);
    for (let k = 0; k < count; k++) {
      const dip = rand() < 0.1 ? rand() * 38 : 0;
      const q = clamp(base + (rand() - 0.5) * 30 - dip, 18, 100) / 100;
      const r = rand();
      const at = new Date(day.getTime() + (8 + Math.floor(rand() * 11)) * 3600000 + Math.floor(rand() * 60) * 60000);
      const id = `DEMO-${++n}`;
      const conv = {
        id, language: r < 0.5 ? 'ru' : r < 0.82 ? 'kk' : 'en', channel: rand() < 0.55 ? 'call' : 'chat',
        operator_id: rand() < 0.14 ? null : OPERATORS[Math.floor(rand() * OPERATORS.length)],
        status: 'new', score: null, gross_violation: false, started_at: at.toISOString(), _q: q, _day: dayKey(day),
        audio_url: null,   // записей нет: плеер не имитируется
      };
      const roll = rand();
      if (roll > 0.3 || back > 6) {
        if (roll > 0.975 && back < 60) conv.status = 'failed';
        else {
          const rows = scoreCriteria(BASE_CRITERIA, q, rand);
          const total = totalOf(BASE_CRITERIA, rows);
          const gross = rows.some((x) => x.criterion_id === 'c8' && x.answer === true);
          const evId = `EV-${id}`;
          evalCores.set(evId, { id: evId, conversation_id: id, checklist_version: 1, rows, total, gross, created_at: new Date(at.getTime() + 600000).toISOString(), status: 'done', revisions: {} });
          Object.assign(conv, { status: 'evaluated', score: total, gross_violation: gross });
        }
      }
      conversations.push(conv);
    }
  }
  // апелляции: ~9% оценённых диалогов, один критерий (с наименьшей долей баллов)
  const appeals = [];
  conversations.forEach((c) => {
    if (c.status !== 'evaluated' || rand() >= 0.09) return;
    const core = evalCores.get(`EV-${c.id}`);
    const cand = core.rows.map((r, i) => ({ r, c: BASE_CRITERIA[i] })).filter((x) => !x.r.na && x.c.answer_type !== 'flag' && x.r.score < x.c.weight);
    if (!cand.length) return;
    const pick = cand.sort((a, b) => a.r.score / a.c.weight - b.r.score / b.c.weight)[0];
    const back = Math.round((Date.now() - new Date(c.started_at).getTime()) / DAY);
    const pending = back < 25 ? rand() < 0.6 : rand() < 0.12;
    const created = new Date(new Date(c.started_at).getTime() + 3600000 * (2 + Math.floor(rand() * 30)));
    const ap = {
      id: `APL-${c.id}`, conversation_id: c.id, evaluation_id: core.id, criterion_id: pick.c.id, original_score: pick.r.score, max_score: pick.c.weight,
      status: pending ? 'pending' : rand() < 0.45 ? 'approved' : 'rejected', comment: APPEAL_COMMENT[c.language], created_at: created.toISOString(), decision: null,
    };
    if (ap.status !== 'pending') {
      const approved = ap.status === 'approved';
      ap.decision = { reviewer: 'demo-reviewer', reason: approved ? 'DEMO: подтверждающая реплика найдена' : 'DEMO: оценка оставлена без изменений', new_score: approved ? pick.c.weight : null, decided_at: new Date(created.getTime() + 86400000).toISOString() };
      if (approved) core.revisions[pick.c.id] = { score: pick.c.weight, appeal_id: ap.id };
      c.status = 'reviewed';
    } else c.status = 'appealed';
    appeals.push(ap);
  });
  const checklists = [{ id: 'CL-DEMO-1', version: 1, created_at: new Date(today.getTime() - 200 * DAY).toISOString(), is_current: true, note: '', criteria: JSON.parse(JSON.stringify(BASE_CRITERIA)) }];
  return { conversations, evalCores, appeals, checklists, rand: rng(777), today };
}
