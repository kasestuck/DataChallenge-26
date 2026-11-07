"""CQA REST API. Run: uvicorn backend.main:app --port 8001."""

from __future__ import annotations

from contextlib import asynccontextmanager, closing
import json
from pathlib import Path
from typing import Literal

from fastapi import Body, FastAPI, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException

from backend import appeals, checklist, dataset, evaluator, jobs, stats
from backend.api_services import (
    APIError, conversation_detail, conversation_history, conversation_list, error_status,
    upload_conversation,
)
from backend.config import ConfigurationError, PROJECT_ROOT, load_data_settings, read_environment
from backend.db import connect_db, init_db
from backend.meta_client import ModelRequestError
from backend.models import (
    AppealRequest, AppealResponse, BatchRequest, Channel, ChecklistRequest, ChecklistResponse,
    ConversationListItem, ConversationResponse, CostResponse, DatasetLoadRequest,
    DatasetLoadResponse, DecisionRequest, DecisionResponse, ErrorResponse, EvaluationRequest,
    EvaluationResponse, HealthResponse, JobResponse, Language, Status, SuggestionResponse,
    SuggestRequest, UploadRequest, UploadResponse,
)


ERROR_RESPONSES = {
    status: {"model": ErrorResponse, "description": description}
    for status, description in {
        404: "Запись не найдена", 409: "Конфликт состояния", 413: "Вход превышает лимит; данные не обрезались",
        422: "Некорректный запрос", 429: "Лимит модели", 500: "Внутренняя ошибка",
        502: "Ошибка внешнего API/модели", 503: "Сервис не настроен", 504: "Таймаут внешнего API",
    }.items()
}


def _error(code: str, message: str, status: int, *, headers=None):
    return JSONResponse(status_code=status, content={"error": {"code": code, "message": message}}, headers=headers)


def _public_result(result: dict):
    if result.get("status") == "failed":
        failure = result.get("error") or {"code": "EVALUATION_FAILED", "message": "Оценка завершилась ошибкой."}
        return _error(failure["code"], failure["message"], error_status(failure["code"], 502),
                      headers={"X-Evaluation-ID": result["evaluation_id"]})
    return result


class UploadBodyLimit:
    """Bound the upload before JSON decoding, including requests without Content-Length."""

    def __init__(self, app, max_bytes: int):
        self.app, self.max_bytes = app, max_bytes

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["method"] != "POST" or scope["path"] != "/api/conversations/upload":
            return await self.app(scope, receive, send)

        async def reject():
            await _error("UPLOAD_TOO_LARGE", "Транскрипт превышает MAX_DATASET_BYTES; данные не обрезались.",
                         413)(scope, receive, send)

        headers = dict(scope.get("headers", []))
        try:
            declared_size = int(headers.get(b"content-length", b"0"))
        except (TypeError, ValueError):
            declared_size = 0
        if declared_size > self.max_bytes:
            return await reject()
        buffered, received_size = [], 0
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            received_size += len(message.get("body", b""))
            if received_size > self.max_bytes:
                return await reject()
            buffered.append(message)
            if not message.get("more_body", False):
                break
        pending = iter(buffered)

        async def bounded_receive():
            return next(pending, None) or await receive()

        await self.app(scope, bounded_receive, send)


def create_app(database_path: str | Path | None = None) -> FastAPI:
    """Create an isolated application; connections are opened inside the worker thread."""
    @asynccontextmanager
    async def lifespan(application):
        settings = load_data_settings()
        target = Path(database_path) if database_path is not None else settings.database_path
        env = read_environment()
        try:
            concurrency = int(env.get("MAX_CONCURRENCY") or 4)
            if concurrency <= 0:
                raise ValueError()
        except (TypeError, ValueError):
            raise ConfigurationError("MAX_CONCURRENCY должен быть положительным целым числом.") from None
        init_db(target)
        with closing(connect_db(target)) as conn:
            jobs.recover_interrupted(conn)
        application.state.database_path = target
        application.state.batch = jobs.BatchManager(target, max_concurrency=concurrency)
        try:
            yield
        finally:
            await application.state.batch.shutdown()

    application = FastAPI(
        title="CQA Conversation Quality Analytics", version="0.4.0", lifespan=lifespan,
        description="Оценка операторских диалогов по версиям чек-листа, доказательства, batch и апелляции. "
                    "Баллы и стоимость передаются десятичными строками; null не означает нулевую оценку.",
        responses=ERROR_RESPONSES,
    )
    env = read_environment()
    origins = [origin.strip() for origin in (env.get("CORS_ORIGINS") or "*").split(",") if origin.strip()]
    application.add_middleware(UploadBodyLimit, max_bytes=load_data_settings().max_dataset_bytes)
    application.add_middleware(
        CORSMiddleware, allow_origins=origins or ["*"], allow_credentials=False,
        allow_methods=["GET", "POST", "OPTIONS"], allow_headers=["*"],
        expose_headers=["X-Evaluation-ID"],
    )

    def connection():
        return closing(connect_db(application.state.database_path))

    @application.exception_handler(RequestValidationError)
    async def request_error_handler(request: Request, exc: RequestValidationError):
        locations = [".".join(str(part) for part in item["loc"]) for item in exc.errors()]
        return _error("VALIDATION_ERROR", "Некорректные поля запроса: " + ", ".join(locations), 422)

    @application.exception_handler(HTTPException)
    async def http_error_handler(request: Request, exc: HTTPException):
        messages = {404: "Маршрут не найден.", 405: "Метод не поддерживается."}
        return _error("HTTP_ERROR", messages.get(exc.status_code, "HTTP-запрос отклонён."), exc.status_code)

    @application.exception_handler(Exception)
    async def unexpected_error_handler(request: Request, exc: Exception):
        # Never serialize provider bodies, request payloads or keys into API errors.
        return _error("INTERNAL_ERROR", "Внутренняя ошибка сервера; результат не подменялся.", 500)

    async def safe_service_error(request: Request, exc: Exception):
        if isinstance(exc, ConfigurationError):
            code, status = "CONFIGURATION_ERROR", 503
        elif isinstance(exc, checklist.ChecklistNotFoundError):
            code, status = "CHECKLIST_NOT_FOUND", 404
        elif isinstance(exc, checklist.ChecklistValidationError):
            code, status = "CHECKLIST_INVALID", 422
        else:
            code = getattr(exc, "code", "INVALID_REQUEST")
            status = error_status(code, getattr(exc, "status_code", None) or 422)
        return _error(code, str(exc), status)

    for exception_type in (
        APIError, ConfigurationError, checklist.ChecklistNotFoundError, checklist.ChecklistValidationError,
        dataset.DatasetError, evaluator.EvaluationInputError, ModelRequestError, appeals.AppealError, jobs.JobError,
    ):
        application.add_exception_handler(exception_type, safe_service_error)

    @application.get("/api/health", response_model=HealthResponse, tags=["Service"], summary="Проверить API и БД")
    def health():
        with connection() as conn:
            conn.execute("SELECT 1").fetchone()
        return {"status": "ok", "database": "ok"}

    @application.post("/api/dataset/load", response_model=DatasetLoadResponse, tags=["Conversations"],
                      summary="Загрузить набор организаторов", description="GET к DATASET_API_URL через сервер; идемпотентный upsert.")
    def load_data(body: DatasetLoadRequest = Body(default_factory=DatasetLoadRequest)):
        with connection() as conn:
            return dataset.load_dataset(conn, load_data_settings(), group_id=body.group_id)

    @application.post("/api/conversations/upload", response_model=UploadResponse, status_code=201,
                      tags=["Conversations"], summary="Загрузить одиночный диалог",
                      description="JSON с transcript (массив или role: text) либо text/plain. Для text/plain метаданные передаются в query; "
                                  "время неизвестных реплик сохраняется пустой строкой. Существующий conversation_id обновляется идемпотентно.",
                      openapi_extra={"requestBody": {"content": {"text/plain": {"schema": {"type": "string"},
                          "example": "client: Какие тарифы?\noperator: Здравствуйте, меня зовут Анна."}}}})
    def upload(body: UploadRequest | str = Body(...), conversation_id: str | None = Query(default=None, min_length=1),
               language: Language = "RU", channel: Channel = "agent chat"):
        if isinstance(body, str):
            record = UploadRequest(conversation_id=conversation_id, language=language, channel=channel,
                                   transcript=body).model_dump()
        else:
            record = body.model_dump()
        limit = load_data_settings().max_dataset_bytes
        if len(json.dumps(record, ensure_ascii=False).encode("utf-8")) > limit:
            raise APIError("UPLOAD_TOO_LARGE", "Транскрипт превышает MAX_DATASET_BYTES; данные не обрезались.", 413)
        with connection() as conn:
            return upload_conversation(conn, record)

    @application.get("/api/conversations", response_model=list[ConversationListItem], tags=["Conversations"],
                     summary="Список диалогов", description="q ищет по ID и тексту реплик без учёта регистра; status относится к последней оценке.")
    def conversations(language: Language | None = None, channel: Channel | None = None,
                      status: Status | Literal["not_evaluated"] | None = None, q: str | None = None):
        with connection() as conn:
            return conversation_list(conn, language=language, channel=channel, status=status, q=q)

    @application.get("/api/conversations/{conversation_id}", response_model=ConversationResponse,
                     tags=["Conversations"], summary="Транскрипт и последний результат")
    def conversation(conversation_id: str):
        with connection() as conn:
            return conversation_detail(conn, conversation_id)

    @application.get("/api/conversations/{conversation_id}/evaluations", response_model=list[EvaluationResponse],
                     tags=["Evaluations"], summary="История оценок диалога по версиям")
    def history(conversation_id: str):
        with connection() as conn:
            return conversation_history(conn, conversation_id)

    @application.get("/api/checklists", response_model=list[ChecklistResponse], tags=["Checklists"], summary="Все неизменяемые версии")
    def checklists():
        with connection() as conn:
            return checklist.list_checklists(conn)

    @application.get("/api/checklists/active", response_model=ChecklistResponse, tags=["Checklists"], summary="Активная версия")
    def active_checklist():
        with connection() as conn:
            return checklist.get_checklist(conn)

    @application.post("/api/checklists", response_model=ChecklistResponse, status_code=201,
                      tags=["Checklists"], summary="Сохранить и активировать новую версию",
                      description="Возвращает diff с предыдущей активной версией: изменённые и добавленные критерии для модели; "
                                  "изменение веса требует только пересчёта. Существующие версии не изменяются.")
    def save_checklist(body: ChecklistRequest):
        config = body.model_dump(exclude_none=True)
        with connection() as conn:
            previous = checklist.get_checklist(conn)
            saved = checklist.save_checklist(conn, config, checklist_id=body.checklist_id)
            return {**saved, "change_summary": checklist.diff_checklists(previous, saved)}

    @application.post("/api/checklists/{version_id}/activate", response_model=ChecklistResponse,
                      tags=["Checklists"], summary="Выбрать активную версию")
    def activate(version_id: str):
        with connection() as conn:
            return checklist.activate_checklist(conn, version_id)

    @application.post("/api/evaluations", response_model=EvaluationResponse, status_code=201,
                      tags=["Evaluations"], summary="Оценить диалог реальной моделью",
                      description="Синхронное ожидание оценки; event loop не блокируется. При ошибке модели возвращает error и X-Evaluation-ID "
                                  "для чтения сохранённого отказа. needs_review сохраняет только проверенные ответы, total=null.")
    def evaluate(body: EvaluationRequest):
        with connection() as conn:
            return _public_result(evaluator.evaluate_conversation(conn, **body.model_dump()))

    @application.post("/api/evaluate", response_model=EvaluationResponse, status_code=201,
                      tags=["Evaluations"], deprecated=True, summary="Совместимый реальный alias /api/evaluations")
    def evaluate_alias(body: EvaluationRequest):
        return evaluate(body)

    @application.post("/api/evaluations/batch", response_model=JobResponse, status_code=202,
                      tags=["Evaluations"], summary="Запустить пакетную оценку",
                      description="Возвращает job_id сразу. Пары диалог/версия с готовой оценкой пропускаются, если force=false. "
                                  "criterion_ids включает только выбранные критерии в промпт; прочие ответы переносятся из предыдущей валидной версии.")
    async def batch(body: BatchRequest):
        return await application.state.batch.submit(body.model_dump())

    @application.get("/api/jobs/{job_id}", response_model=JobResponse, tags=["Evaluations"],
                     summary="Прогресс batch", description="done включает успешные, пропущенные и ошибочные элементы. errors содержит отдельные ошибки диалогов.")
    def job(job_id: str):
        with connection() as conn:
            return jobs.get_job(conn, job_id)

    @application.get("/api/evaluations/{evaluation_id}", response_model=EvaluationResponse,
                     tags=["Evaluations"], summary="Исходная оценка, решения и финальный результат")
    def evaluation(evaluation_id: str):
        with connection() as conn:
            return evaluator.get_evaluation(conn, evaluation_id)

    @application.get("/api/stats/cost", response_model=CostResponse, tags=["Service"],
                     summary="Токены и стоимость", description="Включает все физические вызовы, повторы и апелляции. При незаданных ценах стоимость null.")
    def cost():
        with connection() as conn:
            return stats.get_cost_stats(conn)

    @application.post("/api/appeals", response_model=AppealResponse, status_code=201,
                      tags=["Appeals"], summary="Создать апелляцию по критерию")
    def create_appeal(body: AppealRequest):
        with connection() as conn:
            return appeals.create_appeal(conn, **body.model_dump())

    @application.get("/api/appeals", response_model=list[AppealResponse], tags=["Appeals"], summary="Список апелляций")
    def appeal_list(status: Literal["open", "reviewed"] | None = None):
        with connection() as conn:
            return appeals.list_appeals(conn, status=status)

    @application.get("/api/appeals/{appeal_id}", response_model=AppealResponse,
                     tags=["Appeals"], summary="Спорный критерий и фрагмент диалога")
    def appeal(appeal_id: str):
        with connection() as conn:
            return appeals.get_appeal(conn, appeal_id)

    @application.post("/api/appeals/{appeal_id}/ai-suggest", response_model=SuggestionResponse,
                      tags=["Appeals"], summary="Получить AI-подсказку",
                      description="Подсказка валидируется и не изменяет оценку. Решение фиксируется только отдельным вызовом /decision.")
    def suggest(appeal_id: str, body: SuggestRequest = Body(default_factory=SuggestRequest)):
        with connection() as conn:
            return appeals.suggest_appeal(conn, appeal_id, output_language=body.output_language)

    @application.post("/api/appeals/{appeal_id}/decision", response_model=DecisionResponse,
                      tags=["Appeals"], summary="Зафиксировать решение и пересчитать итог",
                      description="Проверяющий обязан указать причину. Повторное решение запрещено; original сохраняется без изменений.")
    def decide(appeal_id: str, body: DecisionRequest):
        with connection() as conn:
            return appeals.decide_appeal(conn, appeal_id, **body.model_dump())

    frontend = PROJECT_ROOT / "frontend"
    if frontend.is_dir():
        application.mount("/", StaticFiles(directory=frontend, html=True), name="frontend")
    elif (PROJECT_ROOT / "index.html").is_file():
        # The FRONT branch stores public assets at the project root. Expose only
        # these directories, never the root containing .env, source code or data.
        for name in ("assets", "css", "js", "src"):
            directory = PROJECT_ROOT / name
            if directory.is_dir():
                application.mount(f"/{name}", StaticFiles(directory=directory), name=f"frontend-{name}")

        @application.get("/", include_in_schema=False)
        def frontend_index():
            return FileResponse(PROJECT_ROOT / "index.html")

    return application


app = create_app()
