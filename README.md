# CQA Insight — Frontend

Интерфейс оценки качества клиентских диалогов (Freedom Broker CQA Hackathon). Чистый HTML/CSS/ES-modules, без сборки.
Backend (FastAPI + Meta AI) разрабатывается отдельно — ветка `BACK`; этот репозиторий (`FRONT`) его не изменяет.

## Запуск

```bash
python -m http.server 5180
```

Открыть <http://localhost:5180/index.html> → «Начать работу» → вход (Demo Auth: любой непустой e-mail и пароль, пароль не сохраняется; **это не защита**).

* **Режим «Демо»** (по умолчанию): вымышленные данные (`js/workspace/mock/`), в UI везде помечены «Демо». Backend не нужен.
* **Режим «API»**: переключатель в шапке Workspace → реальный Backend (`http://localhost:8001/api`, `js/config.js`). Ошибки не маскируются моками.

## Структура

| Путь | Назначение |
|---|---|
| `index.html`, `css/`, `js/main.js`, `js/landing.js` | Landing, Login, роутер по хэшу (`#/welcome`, `#/login`, `#/dashboard/...`) |
| `src/i18n/` | RU/KK/EN; строки Workspace — `ws.<lang>.js` |
| `js/config.js` | Базовый URL, таймауты (без секретов) |
| `js/http.js` | HTTP-клиент: таймаут, отмена, единая `ApiError` |
| `js/api.js` | Фасад: выбор адаптера по режиму |
| `js/mockAdapter.js` + `js/workspace/mock/` | Демо-данные |
| `js/realApiAdapter.js` | Перевод контракта Backend ⇄ view-model UI |
| `js/workspace/` | Оболочка, страницы (`pages/`), компоненты (`ui.js`, `overlay.js`, `listPage.js`, `appealForm.js`) |
| `tests/` | `smoke.html`, `adapters.html`, `e2e.html` |
| `docs/` | `api-contract.md`, `backend-integration.md`, `frontend-status.md`, `testing.md`, `backend-openapi.json` |

## Документация

* [Интеграция с Backend](docs/backend-integration.md) — запуск связки, что проверено, чего не хватает.
* [Контракт API](docs/api-contract.md) — используемые endpoints, расхождения, вопросы к Backend.
* [Статус](docs/frontend-status.md) — что готово в Mock/API.
* [Тестирование](docs/testing.md) — как запускать, результаты.

## Принципы

Ключ Meta AI — только на Backend. Итоговые баллы приходят с Backend и не пересчитываются. Evidence не генерируется на Frontend.
Мок-данные помечены «Демо» и не выдаются за результат Backend.
