# CQA Insight — контракт Frontend ⇄ Backend

Действующие источники: [API_CONTRACT.md](../API_CONTRACT.md), `backend/main.py` и
`/openapi.json` запущенного приложения. `docs/backend-openapi.json` — исторический
снимок, а не гарантия актуальной схемы. Ниже описан текущий `js/realApiAdapter.js`.
В конце сохранён отдельный архив демонстрационного view-model; его endpoints
не являются текущими маршрутами REST API.

## Подключение и ошибки

| Параметр | Текущий контракт |
|---|---|
| Base URL | `${location.origin}/api` для HTTP(S); единому запуску не нужны настройки в консоли |
| Явный override | `window.CQA_API_BASE`, затем сохранённый `cqa-api-base`; только для другого адреса API |
| Маршруты | `/api`, без `/v1` |
| Вход | Demo Auth и переключатель «Демо» → «API» в шапке; Demo Auth не защищает REST API |
| Ошибки | `{"error":{"code","message"}}` → `ApiError{kind,status,code,detail}`; сохраняются код и сообщение бэкенда |
| Таймаут | 12 секунд обычные запросы, 180 секунд синхронная оценка; отмена через `AbortController` |
| Ключ | `MODEL_API_KEY` только на бэкенде, в браузер не передаётся |
| Режим API | Сбой остаётся ошибкой: нет подмены mock-результатом, нулём или выдуманным счётчиком |

## Реальные запросы экранов

Пути ниже указаны относительно `/api`. Это карта реализации, не перечень выполненных
в текущем прогоне вызовов модели. Результаты проверок вынесены в [testing.md](testing.md).

| Endpoint | Использование |
|---|---|
| `GET /health` | Проверка доступности |
| `GET /conversations?language=&channel=&status=&q=&dataset_group_id=` | Список диалогов; адаптер дополняет фильтры, сортировку и пагинацию на клиенте |
| `POST /conversations/upload` | Импорт по одной записи с индивидуальным результатом |
| `GET /conversations/{id}` | Реплики, источник, последний результат |
| `GET /conversations/{id}/evaluations` | История оценок в карточке диалога |
| `GET /checklists`, `GET /checklists/active` | Версии и активный чек-лист |
| `POST /checklists` | Новая неизменяемая версия; валидация конфигурации бэкендом |
| `POST /checklists/{id}/activate` | Метод адаптера для активации версии |
| `POST /evaluations` | Полная оценка одного диалога, синхронный ответ |
| `GET /evaluations/{id}` | Полная карточка оценки, исходный/финальный результат и usage |
| `GET /appeals`, `GET /appeals/{id}`, `POST /appeals` | Список, карточка и создание апелляции |
| `POST /appeals/{id}/decision` | Решение `confirm/change` и `final_answer`; баллы считает бэкенд |
| `GET /stats/cost` | Токены и стоимость в Overview |
| `POST /evaluations/batch`, `GET /jobs/{id}` | Есть в API; batch/progress не подключены к кнопкам UI |
| `POST /appeals/{id}/ai-suggest`, `POST /dataset/load` | Есть в API/Swagger; отдельные кнопки UI не подключены |

Отдельные endpoints `/overview`, `/operators`, `/conversations/import` и список
`GET /evaluations` не используются и не требуются для текущих экранов.

Список диалогов возвращает `meta.available_channels` из фактических записей до
выбранного фильтра канала. Для этого адаптер применяет канал локально, сохраняя
серверные фильтры языка, текста и источника и один запрос списка. Произвольные
каналы доступны в фильтре без заранее придуманного перечня.

## Overview

`getOverview()` получает три реальных ответа: `/conversations`, `/appeals`, `/stats/cost`.
Адаптер вычисляет агрегаты из этих данных, не пересчитывая официальные баллы диалогов:

- `kpis.total_count`, `evaluated_count`, `needs_review_count`, `failed_count`,
  `not_evaluated_count`, `running_count` — статусы последних результатов.
- `scored_count`, `avg_score`, `median_score`, `score_distribution` — завершённые,
  неустаревшие оценки с известным числовым итогом. Завершённый результат с `null`
  не становится нулём. `stale_count` возвращается отдельно.
- `gross_violations_count` — последние неустаревшие флаги `true`;
  `pending_appeals_count` — апелляции со статусом `open`.
- `by_language`, `by_channel` — количество всех диалогов; среднее использует тот же
  набор допустимых итогов. Произвольные каналы сохраняют своё название.
- `cost` — `stats.total` без выдумывания тарифов; охватывает все записанные вызовы,
  в том числе повторы и апелляции. `cost:null` означает неизвестную стоимость.
- `trend:[]`, `avg_score_delta:null`, `meta.period_supported:false`: текущий снимок
  всей БД, без временного ряда и сравнения периодов.

Интервалы распределения: `[0,20)`, `[20,40)`, `[40,60)`, `[60,80)`, `[80,100]`.
Средний балл — оценка качества диалогов по чек-листу, не точность AI относительно
Ground Truth. Времена обновления диалогов не подменяют время их оценки.

## Импорт и разделение источников

Адаптер принимает массив, `{"records":[...]}` или `{"conversations":[...]}`.
Официальная запись: `conversation_id`, `language:RU|KZ|EN`, произвольный непустой
`channel`, `audio_url:null|string`, `transcript:[{timestamp,speaker,text}]`.
Поддерживается старый формат `id`, `language:ru|kk|en`, `channel:chat|call`,
`messages:[{role,text,ts?}]`.

Официальные ID и каналы не меняются. В старом формате `chat/call` переводятся в
`agent chat/voice call`, `kk` — в `KZ`. Пропуск времени не заменяется выдуманным MM:SS.
Все роли, включая bot/system, участвуют в исходном порядке реплик. Если реплика
невалидна, отклоняется запись целиком, без удаления реплики и сдвига evidence.

Записи отправляются последовательно. Возвращаются `imported`, `skipped`, `results`
и `errors`. Элемент `results`: `{index,conversation_id,status:"accepted"|"rejected",
error?,code?}`. Индекс начинается с 0; интерфейс показывает номер строки с 1.
Ошибка отдельной записи не останавливает остальные; backend code/message сохраняются.

Источник загрузки всегда `UPLOAD` и не принимается из файла. ID существующего DEV
или другого источника защищён: `409 UPLOAD_ID_CONFLICT`, без изменений записи.
Существующий `UPLOAD` можно обновить повторным импортом. `dataset_group_id` приходит
в списке/детали и поддерживается как фильтр списка.

Batch `all:true` без источника исключает `UPLOAD`. Явный `all:true` вместе с
`dataset_group_id:"UPLOAD"` или явные `conversation_ids` позволяют выбрать загрузки.
`conversation_ids` вместе с `dataset_group_id` дают 422. Частичная переоценка
`criterion_ids` остаётся API-возможностью.

## Оценки и апелляции

| Поле / операция | Преобразование |
|---|---|
| `conversation_id`, `speaker`, `transcript` | UI: `id`, `role`, `messages`; `message_id` не меняется |
| Строковый Decimal / `null` | Число для форматирования / `null` без замены нулём |
| `completed`, `needs_review`, `failed` | UI-статусы сохраняют смысл; needs_review не становится успехом |
| `points`, `effective_weight` | Баллы и нормированный вес приходят с бэкенда |
| `answer:"na"` | `answer:null`, отдельные `na` и причина; неизвестный ответ также остаётся `null`, но не объявляется N/A |
| `original_total`, `final_total` | Раздельные исходный и финальный итоги |
| Критерий после решения | Финальные ответ, баллы и причина согласованы; `original_score/original_answer` сохранены отдельно |
| Апелляция | Оспариваемые баллы берутся из `disputed_result`, не подменяются новым результатом |
| Решение UI | approved → `change`, rejected → `confirm`; передаётся ответ, не число баллов |

Evidence не генерируется на клиенте. Невалидное значение не отображается как «Нет».
Форма решения предлагает N/A лишь при `allow_na` и никогда для flag. Список апелляций
не обрезается до первых 100; ошибки связанных оценок не скрываются.

Страница «Оценки» показывает последние результаты из списка диалогов, с
`meta.derived_from`. Полная история — в карточке диалога. В таком списке неизвестны
дата создания оценки и номер версии; они не выдумываются. В полной карточке эти
данные берутся из реального ответа оценки.

## Чек-листы и остальные ограничения

Редактор API передаёт `yes_text`, `no_text` и точный `scale_definition` как backend
`scale` — словарь числовых ступеней и описаний. Min/max не превращаются в набор
сгенерированных промежуточных ступеней. Неотрицательные веса могут суммироваться
не в 100; нормирование делает бэкенд. Версия неизменяема, сохранение создаёт новую.
Секции, неактивные критерии и исходные метаданные сохраняются.

Операторы не группируются по именам из реплик: стабильного `operator_id` нет.
Экран объясняет ограничение без фиктивных профилей. Пагинация/сортировка клиентские;
batch/progress, partial re-evaluation и ai-suggest доступны через API. Отдельного
поля заметки к версии нет, его ввод в API-режиме скрыт. Список оценок показывает
последний результат каждого диалога; недоступные фильтр версии, колонки версии/даты
и сортировка по дате скрыты. История и метаданные доступны в карточках.
Demo Auth не является аутентификацией проверяющего.

## Проверки

Текущие проверки адаптера: `node tests/contract_adapter_node.mjs` — 10/10;
существующий `tests/adapters.js` под Node с DOM shim — 47/47. Это не подтверждение
нового вызова модели или текущего браузерного прогона. Разделение текущих и прошлых
результатов, безопасный запуск browser harness: [testing.md](testing.md).

---

# Архив: демонстрационный view-model и первоначальное предложение

**Все endpoints, требования и примеры ниже исторические. Это Demo view-model,
а не текущий REST-контракт.** В частности, `/overview`, `/operators`,
`/conversations/import`, обязательная сумма весов 100 и старые правила шкал
не описывают текущий API. Используйте таблицы выше и [API_CONTRACT.md](../API_CONTRACT.md).

<details>
<summary>Открыть сохранённое первоначальное описание Demo UI</summary>



> Это формат, которым оперируют страницы UI и Mock Adapter. Реальный Backend отдаёт другой формат; перевод делает
> `js/realApiAdapter.js`. Раздел сохранён как описание view-model и список того, что UI умеет отображать.

* (устар.) Base URL черновика: `/api/v1` — реальный Backend использует `/api`, см. §1.
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
      "status": "pending | approved | rejected",   // в Stage 1 было accepted — Frontend понимает оба
      "created_at": "2026-11-07T15:05:00Z"
    }
  ]
}
```

Frontend сам добавляет `meta.source = "mock" | "api"`.

Ошибки: любой не-2xx код → состояние ошибки (`ws.state.errorHttp`), сетевой сбой/таймаут → `ws.state.errorText`.

## Этапы 2–3 — реализовано на Frontend (в Mock Mode), контракт предложен Backend'у

> Все структуры ниже — **предложение Frontend, не согласованный контракт**. Это то, что уже умеют разбирать экраны и что отдаёт
> Mock Adapter (`js/workspace/mock/`). Любые расхождения с реальным Backend правятся в `js/api.js` (маршруты, параметры) и
> в mock-адаптере; UI-компоненты не содержат данных и не зависят от источника.
> Названия критериев в Mock — **условные, не официальные** критерии Freedom: настоящий чек-лист приходит от Backend (`GET /checklists`).

### Общие правила

* Списки: `GET …?q=&page=1&page_size=20&sort=<поле>&order=asc|desc&<фильтры>` → `{ "items": [...], "total": 1985, "page": 1, "page_size": 20 }`.
* Ошибки: HTTP-статус + `{ "detail": "..." }` (если есть, показывается мелким шрифтом). Статусы, для которых есть особые тексты: `404` не найдено, `409` конфликт (дубликат оценки/апелляции, повторное решение), `422` невалидные данные, `503` AI-сервис недоступен.
* Итоговые баллы (`total_score`, `final_total_score`, `score`, `avg_score`) всегда приходят с Backend. Frontend их **не пересчитывает**.
* `message_id` — стабильный идентификатор реплики внутри диалога (строка). По нему строится evidence navigation; если ID в диалоге нет, UI показывает предупреждение и ничего не выдумывает.
* Названия/описания критериев допускаются в виде строки **или** `{ "ru": "", "kk": "", "en": "" }`.

### Conversations

| Endpoint | Параметры / тело | Ответ |
|---|---|---|
| `GET /conversations` | `q` (по ID и тексту реплик), `language=ru\|kk\|en`, `channel=chat\|call`, `status=new\|evaluating\|evaluated\|appealed\|reviewed\|failed`, `operator_id` (`none` — без оператора), `sort=started_at\|score\|id`, `order`, `page`, `page_size` | список `ConversationSummary` |
| `GET /conversations/{id}` | — | `ConversationDetail` |
| `PUT /conversations/{id}/operator` | `{ "operator_id": "OP-17" }` | `{ "id", "operator_id" }` — ручная привязка, если Backend не знает оператора |
| `POST /conversations/import` ⚠ | `{ "conversations": [ { "language", "channel", "operator_id?", "started_at?", "audio_url?", "messages": [ { "role", "text", "ts?", "offset_sec?" } ] } ] }` | `{ "imported": 1, "skipped": 1, "errors": [ { "index": 1, "error": "language" } ] }` |

⚠ — endpoint импорта **не согласован**, кнопка работает в Mock Mode; в API Mode при `404/405` пользователь увидит ошибку, а не ложный успех.

```jsonc
// ConversationSummary
{ "id": "DEMO-1001", "language": "ru", "channel": "call", "operator_id": "OP-17 | null", "status": "evaluated",
  "score": 87, "gross_violation": false, "started_at": "2026-11-07T10:13:00Z" }

// ConversationDetail = ConversationSummary + …
{ "audio_url": "https://… | null",            // плеер показывается ТОЛЬКО если поле непустое
  "messages": [ { "id": "m1", "role": "client|operator|bot|system", "text": "…", "ts": "2026-11-07T10:13:05Z", "offset_sec": 12 /* опционально, для звонков */ } ],
  "evaluations": [ { "id": "EV-…", "checklist_version": 1, "status": "running|done|failed", "total_score": 87, "created_at": "…" } ] }
```

### Evaluations

| Endpoint | Назначение |
|---|---|
| `POST /conversations/{id}/evaluations` `{ "checklist_version": 2 }` | запуск оценки (Meta AI вызывает Backend). Ответ — `Evaluation` со `status: "running"`; UI опрашивает `GET /evaluations/{id}` каждые ~0.8 с (не дольше ~36 с). `409` если оценка этой версии уже есть, `503` — AI недоступен |
| `GET /evaluations` | `q`, `language`, `status=running\|done\|failed`, `score_min`, `score_max`, `checklist_version`, `gross=1`, `sort=created_at\|total_score\|conversation_id`, `order`, `page`, `page_size` → список `EvaluationSummary` |
| `GET /evaluations/{id}` | `Evaluation` |

```jsonc
// EvaluationSummary
{ "id": "EV-DEMO-1001", "conversation_id": "DEMO-1001", "checklist_version": 1, "status": "done",
  "total_score": 87, "final_total_score": 91 /* после апелляций, считает Backend */, "gross_violation": false,
  "created_at": "…", "language": "ru", "operator_id": "OP-17 | null", "error": { "message": "…" } | null }

// Evaluation = EvaluationSummary + criteria[] (пусто, пока status != "done")
{ "criteria": [ {
    "criterion_id": "c3", "name": "…", "description": "…", "weight": 20,
    "answer_type": "yes_no | scale | flag", "scale": { "min": 0, "max": 5 } | null,
    "answer": true | 4 | null,               // null при N/A
    "score": 20 | null,                      // баллы по критерию (null при N/A и для flag)
    "gross": false,                          // true для критерия-флага грубого нарушения
    "na": { "reason": "клиент не задавал вопросов о рисках" } | null,
    "explanation": "…",
    "evidence": [ { "message_id": "m4", "quote": "дословная цитата из реплики" } ],
    "revised": { "score": 20, "appeal_id": "APL-…" } | null   // итог апелляции; исходный score не меняется
} ] }
```

### Operators

| Endpoint | Ответ |
|---|---|
| `GET /operators?q=&sort=avg_score\|evaluated_count\|gross_violations_count\|appeals_count\|id&order=&page=&page_size=` | список `{ id, name?, languages[], conversations_count, evaluated_count, avg_score, gross_violations_count, appeals_count }` + `unassigned_count` (число диалогов без оператора) |
| `GET /operators/{id}` | то же + `trend[{date, avg_score, count}]`, `criteria_stats[{criterion_id, name, avg_pct, count}]` (по убыванию), `checklist_versions[]`, `mixed_versions`, `conversations[]` (последние 10, `ConversationSummary`), `appeals[]` (последние 10) |

В статистику входят только завершённые оценки. Если `mixed_versions: true`, UI предупреждает о смешении версий чек-листа. Ссылки на профиль строятся только по `operator_id` из Backend; если его нет, диалог привязывается вручную (`PUT /conversations/{id}/operator`).

### Appeals

| Endpoint | Назначение |
|---|---|
| `GET /appeals?q=&status=pending\|approved\|rejected&conversation_id=&operator_id=&sort=created_at\|status\|original_score&page=` | список |
| `GET /appeals/{id}` | `Appeal` + `evaluation`, `criterion`, `original_total_score`, `final_total_score` |
| `POST /appeals` `{ "evaluation_id", "criterion_id", "comment" /* ≥ 10 символов */ }` | создание; `409` если по критерию уже есть апелляция в статусе `pending`, `422` для N/A/короткого комментария |
| `POST /appeals/{id}/decision` `{ "decision": "approved\|rejected", "reason": "…", "new_score": 15 /* только approved, 0..max_score */ }` | решение проверяющего; `409` если уже решено |

```jsonc
{ "id": "APL-…", "conversation_id": "…", "evaluation_id": "…", "criterion_id": "c3", "criterion_name": "…",
  "original_score": 12, "max_score": 20, "status": "pending", "comment": "…", "created_at": "…", "operator_id": "OP-17 | null",
  "decision": { "reviewer": "…", "reason": "…", "new_score": 20 | null, "decided_at": "…" } | null }
```

Исходная оценка не изменяется: решение хранится отдельно и отражается в `criteria[].revised` и `final_total_score`. В Stage 1 статус одобренной апелляции назывался `accepted` — Frontend понимает оба варианта (`accepted` / `approved`).

### Checklists

| Endpoint | Назначение |
|---|---|
| `GET /checklists` | `{ "items": [ Checklist ] }` — все версии |
| `POST /checklists` | сохранение **новой** версии: `{ "base_version": 1, "note": "…", "criteria": [ ChecklistCriterion ] }`; Backend обязан проверить сумму весов (все, кроме `flag`, = 100 — иначе `422`) и не изменять прежние версии |

```jsonc
// Checklist
{ "id": "CL-1", "version": 1, "created_at": "…", "is_current": true, "note": "…", "criteria": [ {
    "id": "c1", "name": "… | {ru,kk,en}", "description": "…", "weight": 10, "answer_type": "yes_no|scale|flag",
    "scale": { "min": 0, "max": 5 } | null, "na_allowed": false, "na_condition": "…", "gross": false,
    "builtin": true      // базовый критерий: удалять нельзя; пользовательские (false) можно
} ] }
```

Чек-лист Freedom: 8 критериев, один из них — отдельный флаг грубого нарушения (`answer_type: "flag"`, `weight: 0`, не входит в сумму весов).

### Вопросы к Backend, требующие согласования

1. Формат списков (`items/total/page/page_size` vs cursor) и параметры сортировки/фильтров, названия статусов.
2. Асинхронный запуск оценки: `status: running` + опрос vs webhook/SSE; максимальное время оценки.
3. Формат `evidence` (`message_id` + `quote`) и стабильность `message_id` между повторными запросами.
4. Выдача `audio_url` (подписанные ссылки? CORS?) и `offset_sec` для звонков.
5. Модель оператора: приходит ли `operator_id`/`name` в диалоге; нужен ли `PUT /conversations/{id}/operator`.
6. Импорт диалогов (`POST /conversations/import`): нужен ли, формат, лимиты.
7. Хранение решений по апелляциям (`criteria[].revised`, `final_total_score`) и права проверяющих.
8. Версионирование чек-листов: `base_version`, неизменяемость, переоценка по выбранной версии.
9. Авторизация (токен/cookie), CORS, формат ошибок `{ "detail" }`.

## Тестирование

* `tests/smoke.html` — автоматические smoke-тесты (Mock Mode, Backend не нужен): слой данных, UI-компоненты, паритет i18n, обход всех
  разделов в RU/KK/EN на ширинах 1280 и 390 px, поведение (поиск/фильтры/evidence/апелляции/чек-лист/ошибка API-режима).
  Актуальный безопасный запуск: [изолированная тестовая статика](testing.md#изолированная-статика-для-браузерных-тестов); итог в `document.title` и `window.__SMOKE__`.
* Для проверки контракта с реальным Backend (Stage 4): переключить индикатор «Демо» → «API» и прогнать те же сценарии; тесты слоя данных можно направить на `api` режим, сменив `setMode('api')` и подготовив тестовые данные на Backend.


</details>
