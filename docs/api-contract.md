# CQA Workspace — временный API-контракт (предложение Frontend)

> **Статус: DRAFT.** OpenAPI от Backend в репозитории пока нет, поэтому структуры ниже — это то,
> что Frontend ожидает получить и что сейчас отдаёт Mock Adapter (`js/workspace/mock.js`).
> Когда Backend опубликует реальные схемы, меняются только `js/api.js` и `js/workspace/mock.js`
> (и нормализация в страницах). Мок-данные всегда помечаются в UI как **«Демо»** и никогда не выдаются за реальные.

* Base URL: `http://localhost:8001/api/v1` (переопределяется `window.CQA_API_BASE`).
* Все запросы идут только через `js/api.js` (`request()` + функции раздела). Поддерживается отмена (`AbortSignal`) и таймаут 12 с.
* Режим данных (`demo` / `api`) переключается индикатором в Topbar, значение хранится в `localStorage['cqa-api-mode']`.
* В режиме `api` ошибки **не** маскируются моками: UI показывает состояние ошибки с кнопкой «Повторить».
* Итоговые баллы и счётчики считает Backend. Frontend ничего не пересчитывает — только форматирует и раскрашивает.
* Язык интерфейса не влияет на язык реплик диалогов — реплики выводятся как есть (`ru` / `kk` / `en`).

## Этап 1 — реализовано на Frontend

### `GET /overview?days=7|30|90`

```jsonc
{
  "period": { "from": "2026-10-09", "to": "2026-11-07", "days": 30 },
  "kpis": {
    "avg_score": 85.9,              // 0..100, среднее по оценённым диалогам периода
    "avg_score_delta": 1.8,         // к предыдущему периоду той же длины; null если нет данных
    "evaluated_count": 338,
    "gross_violations_count": 5,    // диалоги с грубым нарушением
    "pending_appeals_count": 39     // текущий backlog апелляций (не привязан к периоду)
  },
  "trend": [ { "date": "2026-10-09", "avg_score": 84.2, "count": 12 } ],          // по дням
  "score_distribution": [ { "min": 0, "max": 49, "count": 6 } ],                  // диапазоны баллов
  "by_language": [ { "language": "ru", "count": 170, "avg_score": 86.3 } ],        // ru | kk | en
  "recent_conversations": [
    {
      "id": "string",
      "operator_id": "string | null",   // null допустим: ручная привязка появится на этапе 3
      "language": "ru | kk | en",
      "channel": "call | chat",
      "status": "new | evaluated | appealed | reviewed",
      "score": 77,                      // итог Backend, 0..100, null если не оценён
      "evaluated_at": "2026-11-07T14:20:00Z"
    }
  ],
  "recent_appeals": [
    {
      "id": "string",
      "conversation_id": "string",
      "operator_id": "string | null",
      "status": "pending | accepted | rejected",
      "created_at": "2026-11-07T15:05:00Z"
    }
  ]
}
```

Frontend сам добавляет `meta.source = "mock" | "api"`.

Ошибки: любой не-2xx код → состояние ошибки (`ws.state.errorHttp`), сетевой сбой/таймаут → `ws.state.errorText`.

## Этапы 2–3 — endpoints, которые понадобятся от Backend (черновик, не реализовано)

| Раздел | Endpoint | Назначение |
|---|---|---|
| Conversations | `GET /conversations?q=&language=&channel=&status=&sort=&page=` | список, поиск, фильтры, сортировка |
| Conversation Detail | `GET /conversations/{id}` | транскрипт: `messages[{id, role: client\|operator\|bot\|system, text, ts}]`, `audio_url?` |
| Evaluation | `POST /conversations/{id}/evaluations` → `GET /evaluations/{id}` | запуск AI-оценки (Meta AI вызывается на Backend); критерии: `name, weight, answer, score, explanation, evidence{message_id, quote}, na_reason?`; итог `total_score`, `gross_violation` |
| Operators | `GET /operators`, `GET /operators/{id}` | список, профиль, динамика, сильные/слабые критерии |
| Operator link | `PUT /conversations/{id}/operator` | ручная привязка диалога к профилю, если `operator_id` отсутствует |
| Appeals | `GET /appeals?status=`, `POST /appeals`, `POST /appeals/{id}/decision` | создание, решение; исходная оценка не изменяется, возвращается `before`/`after` |
| Checklist | `GET /checklists/current`, `POST /checklists` | редактор критериев, сохранение новой версии; валидация суммы весов на Backend |

Открытые вопросы к Backend: пагинация (page/cursor), формат ошибок (`{detail}`?), авторизация (токен/cookie), шкала баллов (предполагается 0–100).
