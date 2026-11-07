// Дополнение словаря Workspace (этапы 2–3). Глубоко сливается с ws.* из ru.js (src/i18n/merge.js).
// Тексты оригинальных диалогов не переводятся — только интерфейс.
export default {
  common: { close: 'Закрыть', cancel: 'Отмена', confirm: 'Подтвердить', save: 'Сохранить', back: 'Назад', delete: 'Удалить', add: 'Добавить', yes: 'Да', no: 'Нет' },
  mock: { short: 'демо-данные' },
  list: { reset: 'Сбросить фильтры', noMatchTitle: 'Ничего не найдено', noMatchText: 'Измените запрос или сбросьте фильтры.' },
  f: { all: 'Все', language: 'Язык', channel: 'Канал', status: 'Статус', operator: 'Оператор' },
  col: { id: 'ID', language: 'Язык', channel: 'Канал', operator: 'Оператор', status: 'Статус', score: 'Балл', gross: 'Грубое нарушение', date: 'Дата' },
  pager: { label: 'Страницы', prev: 'Предыдущая страница', next: 'Следующая страница', range: '{from}–{to} из {total}', page: 'Стр. {page} из {pages}' },
  state: { aiDown: 'AI-сервис оценки временно недоступен. Попробуйте позже.', conflict: 'Действие конфликтует с текущим состоянием данных.', invalid: 'Проверьте введённые данные.', notFound: 'Объект не найден.', errorTimeout: 'Backend не ответил вовремя ({url}).' },
  status: {
    conv: { evaluating: 'Оценивается', failed: 'Ошибка оценки' },
    appeal: { approved: 'Одобрена' },
    eval: { running: 'Выполняется', done: 'Готово', failed: 'Ошибка' },
  },
  role: { client: 'Клиент', operator: 'Оператор', bot: 'Бот', system: 'Система' },
  gross: { short: 'Грубое', title: 'Зафиксировано грубое нарушение', found: 'Обнаружено грубое нарушение', none: 'Грубых нарушений нет' },
  ans: { yes: 'Да', no: 'Нет' },
  atype: { yes_no: 'Да/нет', scale: 'Шкала', flag: 'Флаг нарушения' },
  crit: { weight: 'Вес критерия', weightN: 'Вес {n}', answer: 'Ответ', points: 'Баллы', na: 'N/A', naReason: 'Причина N/A', noEvidence: 'Подтверждающих цитат нет', violation: 'Нарушение', noViolation: 'Нарушения нет', revised: 'Пересмотрено по апелляции: {score}' },
  appeal: { open: 'Подать апелляцию', exists: 'Апелляция подана', alreadyTip: 'По этому критерию уже есть апелляция в работе.' },
  conv: {
    back: 'К диалогам', tabTranscript: 'Диалог', tabEval: 'AI-оценка', 
    search: 'Поиск по ID диалога и тексту реплик', emptyTitle: 'Диалогов пока нет', emptyText: 'Импортируйте диалоги или дождитесь их поступления из Backend.',
    import: 'Импорт диалогов', importTitle: 'Импорт диалогов', importText: 'Загрузите JSON-файл со списком диалогов. Формат ожидается такой (контракт ещё не согласован с Backend):',
    importFile: 'JSON-файл', importReady: 'Диалогов в файле: {n}', importRun: 'Импортировать', importDone: 'Импортировано: {n}, пропущено: {s}', importFail: 'Не удалось импортировать', importBadFile: 'Файл не похож на список диалогов: нужен JSON-массив.',
    transcript: 'Диалог', msgCount: 'Сообщений: {n}', noMessages: 'В диалоге нет сообщений', recording: 'Запись звонка', noAudio: 'Аудиозапись недоступна: Backend не передал audio_url.',
    notFoundTitle: 'Диалог не найден', notFoundText: 'Диалога «{id}» нет в системе.', assign: 'Назначить оператора', reassign: 'Сменить оператора',
  },
  assign: { title: 'Привязка оператора', text: 'Backend не передаёт оператора для этого диалога — укажите его вручную.', known: 'Из известных операторов', custom: 'Или введите ID оператора', required: 'Укажите оператора', done: 'Оператор {id} назначен' },
  ev: { notFound: 'В диалоге нет сообщения «{id}». Цитата из оценки не может быть показана.' },
  eval: {
    title: 'AI-оценка', version: 'Версия', versionN: 'чек-лист v{n}', current: 'текущая', run: 'Запустить оценку', runAnother: 'Оценить по другой версии', runWith: 'Версия чек-листа', running: 'Оценка идёт…',
    noFree: 'Диалог уже оценён по всем версиям чек-листа.', noneTitle: 'Диалог ещё не оценён', noneText: 'Запустите оценку — результат появится здесь.', runningTitle: 'Идёт оценка', runningText: 'Обычно это занимает несколько секунд.',
    failTitle: 'Оценка не выполнена', done: 'Оценка готова', total: 'Итоговый балл', afterAppeals: 'После апелляций', fromBackend: 'Балл рассчитан на стороне Backend',
  },
};
