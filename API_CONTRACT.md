# CQA REST API — контракт для фронтенда

Версия backend 0.4.0. Примеры сняты 7 ноября 2026 через FastAPI TestClient,
на настоящем приложении, SQLite и `muse-spark-1.3`. Моков модели в этих проверках нет.
Исходные ответы модели в локальной БД не редактировались; решение проверяющего записано отдельным запросом.
В этой публикуемой документации структура, числовые результаты и идентификаторы
сохранены из фактических захватов. **Все тексты реплик, комментариев и содержательных
объяснений в примерах синтетические**, созданы только для описания контракта и не
являются ответами модели для указанных DEV ID. Полные транскрипты и отладочные
захваты организаторов остаются локально; официальный results.json содержит
сохранённые объяснения модели и проверяется отдельно от примеров документации.
Исторические примеры относятся к этапам 3–4, до уточнения промпта этапа 5.
Новая проверка с известным prompt_hash приведена отдельно в конце документа.
Полные исходные захваты доступны локально по ссылкам ниже в игнорируемом `data/reports/`;
после клонирования репозитория эта папка отсутствует.
Примеры загрузки одиночного диалога сняты с отдельной SQLite, чтобы не менять 65 DEV-записей.
Актуальные правила источников и связь UI с API описаны ниже; исторические захваты
сохранены. Проверки изоляции источников используют синтетические записи и временную
SQLite в `tests/test_upload_sources.py`, без обращения к модели.

## Подключение и типы

- Base URL: `http://127.0.0.1:8001`. Swagger `/docs`, схема `/openapi.json`.
- Запуск: `.\.venv\Scripts\python.exe -m uvicorn backend.main:app --host 127.0.0.1 --port 8001`.
- JSON: `Content-Type: application/json`; загрузка также принимает `text/plain`.
- CORS: `CORS_ORIGINS` через запятую, по умолчанию `*`, без credentials.
- API-ключ модели хранится только в `.env` бэкенда, фронтенду не передаётся.
- ID — строки. `language`: `RU|KZ|EN`; `output_language`: `ru|kk|en`, по умолчанию `ru`.
- `dataset_group_id`: строка источника (`DEV`, группа контроля, `UPLOAD`) либо null
  у старой записи без группы. Это отдельное поле списка и карточки диалога.
- `prompt_hash`: SHA-256 байтов файла evaluator_system.md, 64 шестнадцатеричных символа либо null у старых записей.
- `channel`: произвольная строка 1–80 символов, не состоящая только из пробелов;
  `speaker`: `client|operator|bot|system`.
- Баллы, проценты и стоимость — десятичные **строки**. Токены/счётчики — числа.
- `null` у итога означает отсутствие достоверного итога, у цены — неизвестную стоимость.
- Ответы критериев — строки `yes|no|na` или точный ключ шкалы, например `"10"`.
- Списки возвращаются JSON-массивами без обёртки и пагинации.
- Строки timestamp не меняются; входное поле можно пропустить или передать null,
  тогда в ответе timestamp пустой. `offset_sec` — число либо null.
- `audio_url` доступен на карточке и репликах звонка; для чата обычно null.
- Все эндпоинты используют один процесс приложения. Для этого прототипа не запускайте несколько workers.

## Маршруты

| Метод и путь | Запрос / параметры | Ответ |
| --- | --- | --- |
| GET `/api/health` | — | 200: status, database |
| POST `/api/dataset/load` | `{group_id?}` | 200: dataset_group_id,total,inserted,updated,unchanged,warnings |
| POST `/api/conversations/upload` | JSON: conversation_id?,channel?,language?,transcript,audio_url?; либо text/plain + query | 201: conversation_id,import_result,conversation |
| GET `/api/conversations` | language?,channel?,status?,q?,dataset_group_id? | 200: массив карточек списка |
| GET `/api/conversations/{id}` | — | 200: транскрипт,revision,audio_url,latest_result |
| GET `/api/conversations/{id}/evaluations` | — | 200: история, новые оценки первыми |
| GET `/api/checklists` | — | 200: массив полных версий |
| GET `/api/checklists/active` | — | 200: полная активная версия |
| POST `/api/checklists` | name,checklist_id?,sections,criteria | 201: новая активная версия + change_summary |
| POST `/api/checklists/{id}/activate` | — | 200: выбранная версия |
| POST `/api/evaluations` | conversation_id,checklist_version_id?,output_language? | 201: полная оценка; при сбое HTTP error |
| POST `/api/evaluate` | тот же запрос | Устаревший alias реального /api/evaluations |
| POST `/api/evaluations/batch` | conversation_ids ИЛИ all:true; dataset_group_id? только при all:true; checklist_version_id?,output_language?,force?,criterion_ids? | 202: job_id и начальное состояние |
| GET `/api/jobs/{id}` | — | 200: status,done,total,errors,error_count,skipped,items,request |
| GET `/api/evaluations/{id}` | — | 200: original,final,criteria,sections,total,gross_violation,usage,decisions |
| GET `/api/stats/cost` | — | 200: evaluations,total,average_per_evaluation,evaluation_count |
| POST `/api/appeals` | evaluation_id,criterion_id,comment | 201: апелляция с контекстом |
| GET `/api/appeals` | status=open\|reviewed (необязательно) | 200: массив апелляций |
| GET `/api/appeals/{id}` | — | 200: disputed_result,original_result,current_result,transcript_fragment,итоги,decision |
| POST `/api/appeals/{id}/ai-suggest` | output_language? | 200: is_suggestion:true,suggestion,usage,call_id |
| POST `/api/appeals/{id}/decision` | decision:confirm\|change,final_answer,reason | 200: decision,original_total,before_total,after_total,final_total |

`q` ищет подстроку в ID и тексте реплик без учёта регистра. `status` списка относится к
последней оценке: `not_evaluated|pending|running|completed|needs_review|failed`.
Карточка списка включает `total`, `gross_violation`, `has_appeal`, `has_open_appeal`,
`evaluation_stale`. Последнее означает, что транскрипт обновлён после оценки.
`dataset_group_id` фильтрует источник по точному совпадению; без фильтра список
включает все источники. Пустая/пробельная группа возвращает 422, неизвестная — пустой массив.

## Связь интерфейса с REST API

Обзор получает `GET /api/conversations`, `GET /api/appeals` и `GET /api/stats/cost`.
Из этих ответов UI вычисляет счётчики статусов, среднее/медиану, распределение баллов
и разрезы по языкам и каналам. Среднее, медиана и распределение используют только
последние `completed`-оценки с непустым итогом и `evaluation_stale=false`.
Это снимок всей БД, включая UPLOAD; расходы показываются по всем сохранённым
LLM-вызовам, включая повторы и апелляции. Отдельного `/api/stats/overview` нет.
Дневной тренд и сравнение периодов не строятся из `conversations.updated_at`:
это время изменения исходного диалога, а не время его оценки.

В проверенных 65 DEV-записях нет стабильного ID оператора: исходная запись содержит
только `conversation_id`, `channel`, `language`, `transcript`, `audio_url`.
Роль `speaker=operator` не является идентификатором сотрудника. Маршрутов операторов
нет; интерфейс показывает отсутствие данных, не создаёт профили из имён в репликах.

Файловый импорт UI принимает массив записей, `{records:[...]}` и прежнюю оболочку
фронтенда `{conversations:[...]}`. Одиночную запись оберните в массив;
из официального ответа с `data.records` передайте `{records:[...]}`.
Это форматы **адаптера интерфейса**; REST `/api/conversations/upload` по-прежнему
принимает один диалог. Адаптер отправляет записи последовательно, показывает
успех/ошибку каждой и продолжает после ошибки отдельной записи. Успешные записи
уже сохранены, общей транзакции на весь файл нет. Импорт через UI всегда создаёт
источник UPLOAD; поле `dataset_group_id=DEV` в файле не даёт права перезаписать DEV.
Официальный набор загружается через `/api/dataset/load` или `scripts/load_dataset.py`.

## Примеры по фактической структуре: health и загрузка

`GET /api/health`

HTTP 200. Структура ответа (тексты синтетические):
```json
{
  "status": "ok",
  "database": "ok"
}
```

Полная запись: [запрос и ответ](data/reports/stage34-20261107/health.json).

`DATASET_API_URL` не задан: внешнего вызова не было. Получен реальный отказ конфигурации, а не вымышленная загрузка:

`POST /api/dataset/load`

```json
{
  "group_id": "DEV"
}
```

HTTP 503. Структура ответа (тексты синтетические):
```json
{
  "error": {
    "code": "DATASET_NOT_CONFIGURED",
    "message": "DATASET_API_URL не задан в .env."
  }
}
```

Полная запись: [запрос и ответ](data/reports/stage34-20261107/dataset_unconfigured.json).

`POST /api/conversations/upload`

```json
{
  "conversation_id": "api-upload-example",
  "language": "RU",
  "channel": "agent chat",
  "transcript": "client: Какие тарифы?\noperator: Здравствуйте, меня зовут Анна."
}
```

HTTP 201. Фрагмент структуры ответа: числа фактические, тексты синтетические; остальные поля опущены:
```json
{
  "conversation_id": "api-upload-example",
  "import_result": {
    "dataset_group_id": "UPLOAD",
    "total": 1,
    "inserted": 1,
    "updated": 0,
    "unchanged": 0,
    "warnings": []
  },
  "conversation": {
    "conversation_id": "api-upload-example",
    "channel": "agent chat",
    "language": "RU",
    "transcript": [
      {
        "message_id": "m1",
        "timestamp": "",
        "speaker": "client",
        "text": "Какие тарифы?",
        "offset_sec": null,
        "audio_url": null
      },
      {
        "message_id": "m2",
        "timestamp": "",
        "speaker": "operator",
        "text": "Здравствуйте, меня зовут Анна.",
        "offset_sec": null,
        "audio_url": null
      }
    ],
    "latest_result": null
  }
}
```

Полная запись: [запрос и ответ](data/reports/stage34-20261107/upload_json.json).

Повтор идентичной загрузки вернул:

```json
{
  "dataset_group_id": "UPLOAD",
  "total": 1,
  "inserted": 0,
  "updated": 0,
  "unchanged": 1,
  "warnings": []
}
```

Также разрешён `text/plain`: `POST /api/conversations/upload?conversation_id=...&language=RU&channel=agent%20chat`.
Тело — по одной строке `role: text`; допустимы четыре указанные роли. ID генерируется,
если не передан. Загрузка изменённой записи повышает revision; старые оценки сохраняют
свой snapshot. Превышение `MAX_DATASET_BYTES` даёт 413 до разбора JSON, без обрезки.

Источник пользовательской загрузки всегда `UPLOAD`. Повторный ID можно обновить
только внутри UPLOAD. Если ID уже принадлежит DEV, контролю либо записи без группы,
запрос возвращает HTTP 409; исходные реплики, группа, revision и оценки сохраняются.
Проверка конфликта и запись выполняются в одной транзакции.

Форма ответа, проверенная на синтетической записи с конфликтующим ID:

```json
{
  "error": {
    "code": "UPLOAD_ID_CONFLICT",
    "message": "Этот conversation_id уже принадлежит другому набору. Исходный диалог не изменён; используйте другой ID для загрузки."
  }
}
```

Фильтры примеров: `GET /api/conversations?dataset_group_id=DEV` и
`GET /api/conversations?dataset_group_id=UPLOAD`. Официальный экспорт выбирает
именно группу набора; UPLOAD не попадает в DEV/PROD `results.json`.

## Оценка и её отображение

Показывайте верхние `criteria`, `total`, `sections`, `gross_violation`, `status`, `usage`.
`original` содержит неизменяемый результат, `final` — слой с решениями проверяющего.
Доказательства имеют `message_id`, `quote`, `speaker`. `checked_range.from/to` ссылаются
на существующие реплики. У критерия есть `valid`, `validation_errors`, `points`,
`effective_weight`, `has_open_appeal`, `decisions` и источник ответа.

Каждая новая оценка сохраняет `prompt_hash` в момент чтения файла системного промпта:
одни и те же байты используются для хэша и текста запроса. Поле видно в карточке,
а также в original/final новых результатов, включая failed и пересчёт без вызова модели.
У прежних записей верхний `prompt_hash=null`; старые original/final не переписываются.
В частичной оценке `reevaluation.baseline_prompt_hash` указывает хэш базовой оценки
(может быть null), а `source_evaluation_id` у перенесённых критериев связывает их
с источником. Текущий prompt_hash не означает, что все перенесённые ответы были
получены этим промптом. Язык объяснений хранится отдельно в output_language.

Исторический пример ниже создан до введения хэшей. Поле prompt_hash показано как null
по актуальной схеме чтения такой записи; старым оценкам новый хэш не приписывается.

В `needs_review` валидные критерии доступны, но итог и нормированные баллы null.
`raw_responses`, `validation_attempts`, `validation_errors`, `llm_calls` и snapshot
сохраняются для аудита. Это не дополнительные решения: расчёт определяется валидными ответами.

`POST /api/evaluations`

```json
{
  "conversation_id": "p1-ru-003",
  "checklist_version_id": "eada7a09-09af-4bc9-9527-b66248ac738b",
  "output_language": "ru"
}
```

HTTP 201. Фрагмент структуры ответа: числа фактические, тексты синтетические; остальные поля опущены:
```json
{
  "evaluation_id": "836d2a6c-731c-4f5b-94b4-12ba680d7d4e",
  "conversation_id": "p1-ru-003",
  "checklist_version_id": "eada7a09-09af-4bc9-9527-b66248ac738b",
  "status": "completed",
  "total": "70.00",
  "gross_violation": false,
  "usage": {
    "calls": 1,
    "prompt_tokens": 2883,
    "completion_tokens": 4325,
    "total_tokens": 7208,
    "cost": null,
    "price_status": "price_not_set",
    "successful_calls": 1,
    "failed_calls": 0,
    "usage_complete": true,
    "known_cost": null,
    "cost_complete": false
  },
  "criteria": [
    {
      "criterion_id": "03",
      "answer": "na",
      "reasoning": "Синтетическое объяснение: применимость критерия проверена по учебному диалогу; это пример структуры, а не оценка DEV-записи.",
      "evidence": [
        {
          "message_id": "m1",
          "quote": "Где находится раздел справки в учебном приложении?",
          "speaker": "client"
        }
      ],
      "checked_range": null,
      "effective_weight": "0.00",
      "points": "0.00",
      "has_open_appeal": false,
      "name": "Идентификация",
      "type": "yes_no",
      "section": "s2",
      "weight": "20",
      "valid": true,
      "validation_errors": [],
      "earned": null,
      "source": "model",
      "decisions": []
    },
    {
      "criterion_id": "06",
      "answer": "4",
      "reasoning": "Синтетическое объяснение: применимость критерия проверена по учебному диалогу; это пример структуры, а не оценка DEV-записи.",
      "evidence": [
        {
          "message_id": "m15",
          "quote": "Нажмите ту кнопку и вернитесь туда.",
          "speaker": "operator"
        }
      ],
      "checked_range": {
        "from": "m11",
        "to": "m15"
      },
      "effective_weight": "18.75",
      "points": "7.50",
      "has_open_appeal": false,
      "name": "Управление диалогом, скорость и грамотность",
      "type": "scale",
      "section": "s3",
      "weight": "15",
      "valid": true,
      "validation_errors": [],
      "earned": "6.00",
      "source": "model",
      "decisions": []
    }
  ],
  "prompt_hash": null
}
```

Полная запись: [запрос и ответ](data/reports/stage34-20261107/single_evaluation.json).

В примере критерий 03 — N/A, поэтому его вес исключён. Полная карточка содержит все восемь критериев.

Пример ответа фильтрованного списка:

`GET /api/conversations?language=EN&channel=agent%20chat&status=completed&q=p1-en-001`

HTTP 200. Структура ответа (тексты синтетические):
```json
[
  {
    "conversation_id": "p1-en-001",
    "language": "EN",
    "channel": "agent chat",
    "total": "92.31",
    "gross_violation": false,
    "status": "completed",
    "evaluation_id": "d9976c03-37bc-4fe5-a519-8c6e2690c8dd",
    "has_appeal": false,
    "has_open_appeal": false,
    "evaluation_stale": false,
    "dataset_group_id": "DEV",
    "revision": 1,
    "updated_at": "2026-11-07T09:06:33.938698+00:00",
    "conversation_revision": 1
  }
]
```

Полная запись: [запрос и ответ](data/reports/stage34-20261107/conversations_filtered.json).

## Batch, прогресс и перезапуск

Выбор фиксируется при создании задачи:

- `{"all":true}` — все находящиеся в БД диалоги, кроме источника UPLOAD.
- `{"all":true,"dataset_group_id":"DEV"}` — только DEV; группа выбирается точно.
- `{"all":true,"dataset_group_id":"UPLOAD"}` — явная оценка пользовательских загрузок.
- `{"conversation_ids":["synthetic-user-id"]}` — явный список ID; UPLOAD разрешён.

`dataset_group_id` допускается только с `all:true`: сочетание с `conversation_ids`
возвращает 422, чтобы фильтр не был молча проигнорирован. Неизвестная группа создаёт
пустую задачу с total=0, которая завершится без вызова модели. Параметр `force`
не меняет выбранный источник. В старых примерах ниже список ID остаётся явным.

`force` по умолчанию false. Готовая оценка пары (conversation_id, checklist_version_id)
для текущей revision пропускается. `done` включает completed, skipped, failed.
`errors` — массив `{conversation_id,code,message}`; `items` содержит статус и evaluation_id
каждого элемента. Job `completed` означает завершённую обработку и может иметь errors.
`needs_review` отдельного результата отражается в errors задачи, сама оценка доступна.

Опрос: GET job до `completed|failed|interrupted`. Начальные статусы pending/running.
Один сбой не останавливает остальные диалоги. SDK работает вне event loop;
MAX_CONCURRENCY ограничивает все batch одного процесса. При старте зависшие jobs
получают interrupted; автоматической повторной отправки запросов после сбоя нет.

`POST /api/evaluations/batch`

```json
{
  "conversation_ids": [
    "p1-ru-003",
    "p1-ru-001",
    "p1-ru-002",
    "p1-kz-001",
    "p1-kz-002",
    "p1-en-001",
    "p1-en-002",
    "p4-ru-001"
  ],
  "checklist_version_id": "eada7a09-09af-4bc9-9527-b66248ac738b",
  "force": true
}
```

HTTP 202. Фрагмент структуры ответа: числа фактические, тексты синтетические; остальные поля опущены:
```json
{
  "job_id": "b5f28687-6731-4e09-bd23-f3f923dc0ca6",
  "status": "pending",
  "done": 0,
  "total": 8,
  "errors": []
}
```

Полная запись: [запрос и ответ](data/reports/stage34-20261107/full_submit.json).

Фактический прогресс (время от отправки запроса, секунды):

```json
[
  {
    "elapsed_seconds": 0.031,
    "status": "running",
    "done": 0,
    "total": 8,
    "errors": []
  },
  {
    "elapsed_seconds": 20.328,
    "status": "running",
    "done": 1,
    "total": 8,
    "errors": []
  },
  {
    "elapsed_seconds": 22.359,
    "status": "running",
    "done": 2,
    "total": 8,
    "errors": []
  },
  {
    "elapsed_seconds": 23.391,
    "status": "running",
    "done": 3,
    "total": 8,
    "errors": []
  },
  {
    "elapsed_seconds": 24.922,
    "status": "running",
    "done": 4,
    "total": 8,
    "errors": []
  },
  {
    "elapsed_seconds": 46.859,
    "status": "running",
    "done": 5,
    "total": 8,
    "errors": []
  },
  {
    "elapsed_seconds": 48.906,
    "status": "running",
    "done": 6,
    "total": 8,
    "errors": []
  },
  {
    "elapsed_seconds": 53.484,
    "status": "running",
    "done": 7,
    "total": 8,
    "errors": []
  },
  {
    "elapsed_seconds": 57.031,
    "status": "completed",
    "done": 8,
    "total": 8,
    "errors": []
  }
]
```

`GET /api/jobs/b5f28687-6731-4e09-bd23-f3f923dc0ca6`

HTTP 200. Фрагмент структуры ответа: числа фактические, тексты синтетические; остальные поля опущены:
```json
{
  "job_id": "b5f28687-6731-4e09-bd23-f3f923dc0ca6",
  "status": "completed",
  "done": 8,
  "total": 8,
  "errors": [],
  "error_count": 0,
  "skipped": 0
}
```

Полная запись: [запрос и ответ](data/reports/stage34-20261107/full_job.json).

## Апелляция и отдельное решение

По одному критерию допускается одна открытая апелляция. Контекст включает две соседние
реплики вокруг доказательств и весь checked_range. `original_total` — из исходной
оценки; `disputed_total` — при открытии; `final_total` — текущий итог.
AI-подсказка ничего не утверждает автоматически. Её токены входят в верхний usage оценки.
Решение confirm обязано оставить ответ, change — изменить на допустимое значение.
Причина обязательна. Для ручного изменения исходные цитаты не выдаются за новые:
`source=reviewer`, original доступен отдельно.

`POST /api/appeals`

```json
{
  "evaluation_id": "836d2a6c-731c-4f5b-94b4-12ba680d7d4e",
  "criterion_id": "06",
  "comment": "Синтетический комментарий: прошу повторно проверить последовательность инструкции в учебном примере."
}
```

HTTP 201. Фрагмент структуры ответа: числа фактические, тексты синтетические; остальные поля опущены:
```json
{
  "appeal_id": "8a653cc3-3887-44f3-8e00-548d145e0585",
  "evaluation_id": "836d2a6c-731c-4f5b-94b4-12ba680d7d4e",
  "criterion_id": "06",
  "status": "open",
  "original_total": "70.00",
  "disputed_total": "70.00"
}
```

Полная запись: [запрос и ответ](data/reports/stage34-20261107/appeal_open.json).

`POST /api/appeals/8a653cc3-3887-44f3-8e00-548d145e0585/ai-suggest`

```json
{
  "output_language": "ru"
}
```

HTTP 200. Структура ответа (тексты синтетические):
```json
{
  "appeal_id": "8a653cc3-3887-44f3-8e00-548d145e0585",
  "is_suggestion": true,
  "suggestion": {
    "criterion_id": "06",
    "original_answer": "4",
    "decision": "change",
    "final_answer": "7",
    "reason": "Синтетическое обоснование решения для демонстрации пересчёта; оно не является экспертной разметкой DEV-записи.",
    "evidence": [
      {
        "message_id": "m14",
        "quote": "Первый шаг понятен. Как перейти к следующему?",
        "speaker": "client"
      },
      {
        "message_id": "m15",
        "quote": "Нажмите ту кнопку и вернитесь туда.",
        "speaker": "operator"
      }
    ]
  },
  "call_id": "750148d8-ff75-4354-be32-be15abe6d587",
  "usage": {
    "prompt_tokens": 1580,
    "completion_tokens": 3374,
    "total_tokens": 4954
  },
  "evaluation_usage": {
    "calls": 2,
    "successful_calls": 2,
    "failed_calls": 0,
    "prompt_tokens": 4463,
    "completion_tokens": 7699,
    "total_tokens": 12162,
    "usage_complete": true,
    "cost": null,
    "known_cost": null,
    "cost_complete": false,
    "price_status": "price_not_set"
  }
}
```

Полная запись: [запрос и ответ](data/reports/stage34-20261107/appeal_suggestion.json).

Для проверки самостоятельного решения проверяющего выбран ответ 10, отличный от AI-подсказки 7. Это отдельное аудируемое решение; исходный ответ модели 4 не изменён.

`POST /api/appeals/8a653cc3-3887-44f3-8e00-548d145e0585/decision`

```json
{
  "decision": "change",
  "final_answer": "10",
  "reason": "Синтетическое обоснование решения для демонстрации пересчёта; оно не является экспертной разметкой DEV-записи."
}
```

HTTP 200. Структура ответа (тексты синтетические):
```json
{
  "appeal_id": "8a653cc3-3887-44f3-8e00-548d145e0585",
  "evaluation_id": "836d2a6c-731c-4f5b-94b4-12ba680d7d4e",
  "decision": {
    "decision_id": "8b41dc86-a08e-4366-9190-44aad4944787",
    "appeal_id": "8a653cc3-3887-44f3-8e00-548d145e0585",
    "decision": "change",
    "original_answer": "4",
    "final_answer": "10",
    "reason": "Синтетическое обоснование решения для демонстрации пересчёта; оно не является экспертной разметкой DEV-записи.",
    "decided_by": "reviewer",
    "created_at": "2026-11-07T10:49:56.000780+00:00",
    "before_total": "70.00",
    "after_total": "81.25",
    "evidence": []
  },
  "original_total": "70.00",
  "before_total": "70.00",
  "after_total": "81.25",
  "final_total": "81.25"
}
```

Полная запись: [запрос и ответ](data/reports/stage34-20261107/appeal_decision.json).

После решения карточка оценки содержит:

```json
{
  "evaluation_id": "836d2a6c-731c-4f5b-94b4-12ba680d7d4e",
  "original_total": "70.00",
  "final_total": "81.25",
  "original_answer_06": "4",
  "final_answer_06": "10"
}
```

Названия original_answer_06/final_answer_06 в предыдущем блоке — поясняющая проекция `original.criteria` и `final.criteria`, не отдельные поля API.

`POST /api/appeals/8a653cc3-3887-44f3-8e00-548d145e0585/decision`

```json
{
  "decision": "change",
  "final_answer": "10",
  "reason": "Синтетическое обоснование решения для демонстрации пересчёта; оно не является экспертной разметкой DEV-записи."
}
```

HTTP 409. Структура ответа (тексты синтетические):
```json
{
  "error": {
    "code": "APPEAL_ALREADY_REVIEWED",
    "message": "Решение по апелляции уже принято."
  }
}
```

Полная запись: [запрос и ответ](data/reports/stage34-20261107/decision_conflict.json).

## Новая версия и частичная переоценка

GET active -> измените копию config -> POST checklists. Версии не изменяются и не
удаляются; сохранение сразу активирует новую. Требуются name,sections,criteria.
Критерий: criterion_id,name,description?,section,type,weight,allow_na,na_condition,
yes_text,no_text,scale,scored,order,active. Flag: weight=0,scored=false,NA запрещён.
Scale: минимум две различные числовые неотрицательные ступени с положительным максимумом.
allow_na=true требует na_condition. ID уникальны, section должен существовать.
weight_sum — сумма активных scored весов, итог нормируется к 100.

`change_summary.evaluation_required` включает добавленные/активированные критерии
и изменения name/description/type/allow_na/na_condition/yes_text/no_text/scale.
Вес, scored, секция и порядок попадают в recalculation_only, удаления — removed.
Для другого checklist_id пригодность базы проверяется по семейству чек-листа.

Без criterion_ids выполняется полная оценка. `criterion_ids:["05"]` запускает
выбранный критерий; семантические изменения вне списка автоматически добавляются.
`criterion_ids:[]` использует diff, а для одних весов делает пересчёт без модели.
Перенос идёт из последней completed-оценки предыдущей версии того же семейства и
той же revision, включая её решения проверяющего. Если базы нет — полная оценка.
`reevaluation` явно показывает фактический набор, базу и режим.
Перенесённые объяснения сохраняют прежний язык (`baseline_output_language`).

В реальном запросе изменено только описание критерия 05:

```json
{
  "criterion_id": "05",
  "name": "Эмпатия и клиентоориентированность",
  "description": "Если клиент явно описал неудобство или недовольство, оператор должен отреагировать именно на эту проблему. Одна формальная вежливость без такой реакции недостаточна; при отсутствии неудобства достаточно вежливого общения.",
  "section": "s3",
  "type": "yes_no",
  "weight": 15,
  "allow_na": false,
  "na_condition": null,
  "yes_text": "Оператор вежлив и, если клиент выразил недовольство или неудобство, отреагировал на конкретную проблему.",
  "no_text": "Оператор невежлив либо не отреагировал на конкретную проблему клиента.",
  "scale": {},
  "scored": true,
  "order": 5,
  "active": true
}
```

Полное тело POST: [checklist_new.json](data/reports/stage34-20261107/checklist_new.json). HTTP 201, фрагмент ответа:

```json
{
  "checklist_version_id": "5b8cf1c2-2522-4e8b-a0ad-5aedc9e9edb7",
  "version": 2,
  "weight_sum": "100",
  "change_summary": {
    "previous_version_id": "eada7a09-09af-4bc9-9527-b66248ac738b",
    "version_id": "5b8cf1c2-2522-4e8b-a0ad-5aedc9e9edb7",
    "evaluation_required": [
      "05"
    ],
    "added": [],
    "changed": [
      "05"
    ],
    "recalculation_only": [],
    "removed": [],
    "unchanged": [
      "01",
      "02",
      "03",
      "04",
      "06",
      "07",
      "08"
    ]
  }
}
```

`POST /api/evaluations/batch`

```json
{
  "conversation_ids": [
    "p1-ru-003",
    "p1-ru-001",
    "p1-ru-002",
    "p1-kz-001",
    "p1-kz-002",
    "p1-en-001",
    "p1-en-002",
    "p4-ru-001"
  ],
  "checklist_version_id": "5b8cf1c2-2522-4e8b-a0ad-5aedc9e9edb7",
  "criterion_ids": [
    "05"
  ],
  "force": true
}
```

HTTP 202. Фрагмент структуры ответа: числа фактические, тексты синтетические; остальные поля опущены:
```json
{
  "job_id": "e291eead-4a66-4508-9c86-675b795e0d51",
  "status": "pending",
  "done": 0,
  "total": 8,
  "errors": []
}
```

Полная запись: [запрос и ответ](data/reports/stage34-20261107/partial_submit.json).

Метаданные одной реальной частичной оценки:

```json
{
  "mode": "partial",
  "requested_criterion_ids": [
    "05"
  ],
  "evaluated_criterion_ids": [
    "05"
  ],
  "reused_criterion_ids": [
    "01",
    "02",
    "03",
    "04",
    "06",
    "07",
    "08"
  ],
  "baseline_evaluation_id": "d5baaea8-f565-4011-ac22-792d6f8a01bf",
  "baseline_version_id": "eada7a09-09af-4bc9-9527-b66248ac738b",
  "baseline_output_language": "ru",
  "change_summary": {
    "previous_version_id": "eada7a09-09af-4bc9-9527-b66248ac738b",
    "version_id": "5b8cf1c2-2522-4e8b-a0ad-5aedc9e9edb7",
    "evaluation_required": [
      "05"
    ],
    "added": [],
    "changed": [
      "05"
    ],
    "recalculation_only": [],
    "removed": [],
    "unchanged": [
      "01",
      "02",
      "03",
      "04",
      "06",
      "07",
      "08"
    ]
  },
  "baseline_prompt_hash": null
}
```

Для p1-ru-003 базой здесь служит более поздняя полная batch-оценка, а не отдельная ранняя оценка, на которую подана демонстрационная апелляция.

| Проверка | Время, сек | Вход | Выход | Всего | Вызовы | Повторы |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Полная, 8 диалогов | 57.156 | 24941 | 32263 | 57204 | 8 | 0 |
| Только 05, те же 8 | 22.016 | 17397 | 13239 | 30636 | 8 | 0 |
| Повтор без force, 8 пропусков | 0.75 | 0 | 0 | 0 | 0 | 0 |

Экономия токенов — **46.44%**. Полный batch сравнивается с уже выполненной полной оценкой по базовой версии; отдельный полный прогон новой версии не выполнялся.

| Диалог | Полная оценка | После частичной | Токены полной | Токены частичной |
| --- | ---: | ---: | ---: | ---: |
| p1-ru-003 | 70.00 | 70.00 | 6540 | 3751 |
| p1-ru-001 | 92.31 | 92.31 | 6116 | 4292 |
| p1-ru-002 | 100.00 | 100.00 | 6916 | 3106 |
| p1-kz-001 | 92.31 | 92.31 | 6648 | 3274 |
| p1-kz-002 | 92.31 | 92.31 | 7806 | 4028 |
| p1-en-001 | 92.31 | 92.31 | 6684 | 3711 |
| p1-en-002 | 100.00 | 100.00 | 6787 | 3142 |
| p4-ru-001 | 85.00 | 60.00 | 9707 | 5332 |

## Стоимость

В статистику включены все физические вызовы, транспортные/валидационные повторы
и апелляции, включая известные токены ошибочных ответов. `usage_complete=false`
означает, что это известная часть расхода; неизвестный расход не заменяется нулём.
Тарифы берутся на момент вызова из PRICE_INPUT_PER_1M и PRICE_OUTPUT_PER_1M.
Без тарифов cost=null, price_status=price_not_set, price_message="цена не задана".
Среднее считается по всем сохранённым оценкам, включая failed и пересчёты без LLM.
Чистый пересчёт имеет известные нули токенов/стоимости; денежное среднее не округляется
до копеек, чтобы не скрывать малые расходы. Денежную единицу задаёт тариф провайдера.

`GET /api/stats/cost`

HTTP 200. Фрагмент структуры ответа: числа фактические, тексты синтетические; остальные поля опущены:
```json
{
  "evaluation_count": 23,
  "total": {
    "calls": 25,
    "successful_calls": 24,
    "failed_calls": 1,
    "retry_calls": 1,
    "prompt_tokens": 65839,
    "completion_tokens": 78683,
    "total_tokens": 144522,
    "usage_complete": false,
    "cost": null,
    "known_cost": null,
    "cost_complete": false,
    "price_status": "price_not_set",
    "price_message": "цена не задана"
  },
  "average_per_evaluation": {
    "prompt_tokens": "2862.57",
    "completion_tokens": "3421.00",
    "total_tokens": "6283.57",
    "usage_complete": false,
    "cost_complete": false,
    "cost": null
  },
  "average_basis": "Все сохранённые оценки, включая failed; токены — известная сумма, включая апелляции и повторы."
}
```

Полная запись: [запрос и ответ](data/reports/stage34-20261107/cost_stats.json).

Статистика включает также прежние этапы, в том числе ранний сбой TLS без usage. Поэтому общий usage_complete=false, хотя новые 18 вызовов имеют полный usage.

## Ошибки и ограничения

Форма всегда `{"error":{"code":"...","message":"..."}}`.
Ошибочный POST evaluations дополнительно возвращает `X-Evaluation-ID`, если оценка
успела сохраниться; прочитайте её GET-запросом. HTTP 200 на GET не означает успех модели:
проверяйте status/error. HTTP 401/403/404 провайдера преобразуются в 502, поскольку
это сбой внешней модели, а не аутентификация пользователя фронтенда.

| HTTP | Коды | Значение |
| --- | --- | --- |
| 404 | CONVERSATION_NOT_FOUND,EVALUATION_NOT_FOUND,CHECKLIST_NOT_FOUND,JOB_NOT_FOUND,APPEAL_NOT_FOUND,CRITERION_NOT_FOUND | Нет записи |
| 409 | APPEAL_ALREADY_OPEN,APPEAL_ALREADY_REVIEWED,CRITERION_NOT_REVIEWABLE | Конфликт состояния |
| 409 | UPLOAD_ID_CONFLICT | ID пользовательской загрузки занят другим источником; запись не изменена |
| 413 | UPLOAD_TOO_LARGE,DATASET_TOO_LARGE,MODEL_INPUT_TOO_LARGE,APPEAL_INPUT_TOO_LARGE | Превышен лимит; обрезки нет |
| 422 | VALIDATION_ERROR,CHECKLIST_INVALID,INVALID_TRANSCRIPT_TEXT,INVALID_FINAL_ANSWER,INVALID_DECISION,INVALID_CRITERION_IDS,NO_OPERATOR_MESSAGES | Недопустимые поля/значение либо недостаточно данных об операторе |
| 429 | MODEL_RATE_LIMITED | Лимит провайдера после ограниченных повторов |
| 502 | MODEL_ACCESS_DENIED | Провайдер 401/403: проверьте ключ/доступ |
| 502 | MODEL_NOT_FOUND | Провайдер 404: проверьте META_MODEL, list_models.py |
| 502 | MODEL_UNAVAILABLE,MODEL_CONNECTION_ERROR,MODEL_SDK_ERROR,MODEL_HTTP_ERROR | Внешний сбой, результат не имитируется |
| 502 | MODEL_OUTPUT_TRUNCATED,MODEL_INCOMPLETE_RESPONSE,MODEL_INVALID_RESPONSE,AI_SUGGESTION_INVALID | Ответ модели не принят |
| 502 | DATASET_HTTP_ERROR,DATASET_CONNECTION_ERROR,DATASET_API_ERROR,DATASET_INVALID_JSON | Сбой источника данных |
| 503 | DATASET_NOT_CONFIGURED,CONFIGURATION_ERROR,SERVER_STOPPING | Конфигурация/готовность сервера |
| 504 | MODEL_TIMEOUT,DATASET_TIMEOUT | Истёк таймаут |
| 500 | INTERNAL_ERROR | Внутренняя ошибка, без раскрытия исключения |

Для неизвестного маршрута/метода используется HTTP_ERROR с HTTP 404/405.
needs_review — статус сохранённого результата, а не вымышленный успешный ответ.
При перезапуске у прерванных оценок JOB_INTERRUPTED. Полный перечень проверяемых
структур доступен в OpenAPI; сообщения могут уточняться, обрабатывайте code/status.

`GET /api/conversations/acceptance-nonexistent`

HTTP 404. Структура ответа (тексты синтетические):
```json
{
  "error": {
    "code": "CONVERSATION_NOT_FOUND",
    "message": "Диалог не найден."
  }
}
```

Полная запись: [запрос и ответ](data/reports/stage34-20261107/not_found.json).

`POST /api/evaluations`

```json
{}
```

HTTP 422. Структура ответа (тексты синтетические):
```json
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "Некорректные поля запроса: body.conversation_id"
  }
}
```

Полная запись: [запрос и ответ](data/reports/stage34-20261107/invalid_request.json).

На реальной проверке не проверена загрузка организаторов по сети: URL не задан.
Цены не заданы, денежный расход неизвестен. Техническая валидация не является
замером точности Ground Truth: обнаружен спорный двойной штраф, использованный
для демонстрации апелляции. В прототипе нет аутентификации/ролей, пагинации,
распределённой очереди и автоматического возобновления прерванных задач.

## Свежая проверка этапа 5 — 7 ноября 2026

Ниже новый захват `POST /api/evaluations` из изолированной демонстрационной SQLite,
HTTP 201. **Структура, идентификаторы, ответы критериев и числа фактические;
все свободные тексты reasoning/quote синтетические и не являются результатом модели
для указанного DEV ID.** Показаны только два критерия из восьми, остальные поля
полной карточки опущены. Исходный захват не редактировался:
[evaluation.json](data/reports/stage5-20261107/demo/evaluation.json).

```json
{
  "evaluation_id": "96415fc2-1df3-4f6d-a08a-36ed2b1ff29d",
  "conversation_id": "p1-en-009",
  "checklist_version_id": "5b8cf1c2-2522-4e8b-a0ad-5aedc9e9edb7",
  "status": "completed",
  "output_language": "ru",
  "prompt_hash": "1152fc7a3704fc46aa70a2fad397fe2d03eb2ccd57fa670f453d5777376ff7b9",
  "total": "100.00",
  "original_total": "100.00",
  "final_total": "100.00",
  "gross_violation": false,
  "usage": {
    "calls": 1,
    "prompt_tokens": 3022,
    "completion_tokens": 3521,
    "total_tokens": 6543,
    "cost": null,
    "price_status": "price_not_set",
    "successful_calls": 1,
    "failed_calls": 0,
    "usage_complete": true,
    "known_cost": null,
    "cost_complete": false
  },
  "validation_errors": [],
  "criteria": [
    {
      "criterion_id": "01",
      "answer": "yes",
      "valid": true,
      "source": "model",
      "effective_weight": "16.67",
      "points": "16.67",
      "checked_range": null,
      "reasoning": "Синтетическое пояснение структуры API; не оценка указанного DEV-диалога.",
      "evidence": [
        {
          "message_id": "m10",
          "quote": "Синтетическая реплика для описания поля evidence.",
          "speaker": "operator"
        }
      ]
    },
    {
      "criterion_id": "03",
      "answer": "na",
      "valid": true,
      "source": "model",
      "effective_weight": "0.00",
      "points": "0.00",
      "checked_range": null,
      "reasoning": "Синтетическое пояснение структуры API; не оценка указанного DEV-диалога.",
      "evidence": [
        {
          "message_id": "m1",
          "quote": "Синтетическая реплика для описания поля evidence.",
          "speaker": "client"
        },
        {
          "message_id": "m10",
          "quote": "Синтетическая реплика для описания поля evidence.",
          "speaker": "operator"
        }
      ]
    }
  ]
}
```

У новой оценки известен `prompt_hash`; у исторических примеров выше он null.
В одном штатном вызове израсходовано 6543 токена. Позже отдельный демонстрационный
запрос решения изменил yes → no и итог с 100.00 на 83.33, сохранив `original_total=100.00`.
Предшествующая AI-подсказка рекомендовала `confirm`; итог изменила только отдельная
операция решения проверяющего.
Решение находится в отдельном слое final и не является Ground Truth.
Весь демонстрационный процесс: 4 реальных вызова, 14895 токенов, 37.172 с;
[demo_report.json](data/reports/stage5-20261107/demo/demo_report.json).
Основная БД этим сценарием не изменялась.

Техническая проверка DEV завершила 65/65 выбранных записей: 57 новых и 8 пропущенных
исторических, 0 needs_review/failed. В новых вызовах 428208 токенов, два валидационных
повтора, 416.531 с. Исторические результаты имеют неизвестный хэш промпта и не
приписываются новой версии. Повторяемость 5 диалогов × 3: все три ответа совпали
для 35/40 пар «диалог–критерий» (87.50%), попарно — 109/120 (90.83%). Это не точность
относительно экспертных ответов. Полные таблицы, новые типы критерия и крайние случаи:
[README_BACKEND.md](README_BACKEND.md#реальная-проверка-этапа-5--7-ноября-2026).

В историческом прогоне без оператора сохранялся completed с итогом 0.00. Это
поведение исправлено: новая оценка возвращает HTTP 422 `NO_OPERATOR_MESSAGES`
до вызова модели; итог не вычисляется. При экспорте старой такой записи статус
error, criteria пустой массив, score=null. Сами исторические оценки не переписаны.
Ручная выборка из 80 критериев ещё не размечена. DATASET_API_URL и цены пусты:
внешняя загрузка и настоящий контрольный набор не проверены, cost=null.
Локальный `--from-db` явно возвращает `network_load_verified=false`.

## Файл для сдачи организаторам

REST-карточки выше сохраняют свой контракт. Для сдачи предусмотрен отдельный
адаптер `scripts/export_results.py`, который формирует JSON 1.0 из сохранённых
оценок без новых вызовов модели. Он сохраняет team_name, фактическую модель,
исходные conversation_id, восемь официальных кодов, исходные значения шкал,
объяснения и номера доказательств. Инструкция и таблица преобразований:
[README_BACKEND.md](README_BACKEND.md#сдача-результатов-json-10).

Пути сдачи: `results/dev/results.json` и `results/prod/results.json`.
Контрольный файл появляется только после получения всех 10 настоящих чатов.
Текущие ошибки не заменяются нулями: `status="error"`, `criteria=[]`, `score=null`,
причина в `error`. Значение N/A преобразуется в JSON null только у разрешённых
критериев. `critical_flag` совпадает с critical и не обнуляет final_score.
Нумерация evidence_turns начинается с 1 по всему исходному транскрипту.
Публичные результаты содержат объяснения модели; полные транскрипты, цитаты
evidence и отладочные захваты остаются в локальной БД и `data/reports/`.

## Изменения для интеграции после аудита требований

Повторный GET /openapi.json на изолированном приложении вернул HTTP 200.
Сопоставлены все 21 маршрут с таблицей этого контракта: пропущенных или лишних нет.
Сравнение с `origin/FRONT:docs/backend-openapi.json` (снимок ветки 05a573b)
выявило следующие изменения; названия маршрутов не менялись:

- EvaluationResponse: добавлен `prompt_hash`, string/null.
- UploadRequest, ConversationResponse, ConversationListItem и query channel:
  удалён enum из двух каналов, добавлены minLength=1, maxLength=80, pattern=`\S`.
- TranscriptInput.timestamp: string/null, поле необязательное; неизвестная метка
  возвращается как пустая строка. Строковый формат на выходе не менялся.
- POST /api/evaluations и совместимый alias: при отсутствии operator-реплик
  HTTP 422 с `error.code=NO_OPERATOR_MESSAGES`, без нулевой оценки и LLM-вызова.

Все изменения расширяют допустимые каналы/метки или явно отклоняют недостаточные
данные. Фронтенду следует обрабатывать неизвестный channel как строку, а не считать
его ошибкой. `channel_context` служит только входом модели, не полем REST-ответа.
Консенсус `--runs 3` работает в CLI обработки контроля; поле runs в REST BatchRequest
не добавлено. REST хранит отдельные исходные прогоны, агрегат находится в отчёте CLI
и официальной выгрузке прежнего формата JSON 1.0.

Синтетический запрос email, не связанный с данными организаторов:

```json
{
  "conversation_id": "synthetic-email-contract",
  "channel": "email",
  "language": "RU",
  "transcript": [
    {"speaker": "client", "timestamp": null, "text": "Как открыть учебную инструкцию?"},
    {"speaker": "operator", "timestamp": "Sat, 07 Nov 2026 10:00:00 +0500", "text": "Здравствуйте, я учебный помощник. Инструкция доступна в разделе помощи. До свидания."}
  ]
}
```

Реальные проверки отдельных синтетических email и whatsapp прошли с HTTP 201,
completed и исходными строками времени; захват находится в игнорируемом
[omnichannel.json](data/reports/compliance-20261107/omnichannel.json).
Тексты примера выше синтетические и не подменяют содержимое этого захвата.

Репетиция CLI-консенсуса на десяти DEV-чатах завершилась с 10 успешными записями
в прежнем формате сдачи: независимый валидатор PASS. 31 реальный вызов, 219031 токен,
250.203 с; повторный запуск пропустил все 10 и не вызвал модель. Это локальная
репетиция, не настоящий PROD; публичный контрольный файл ею не создавался.
REST-протокол от режима --runs 3 не изменяется. Полные захваты и ограничения:
[README_BACKEND.md](README_BACKEND.md#особенности-подхода).
