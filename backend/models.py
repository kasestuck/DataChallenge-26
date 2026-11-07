"""Public REST schemas. Decimal scores and costs are serialized as strings."""

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

Language = Literal["RU", "KZ", "EN"]
OutputLanguage = Literal["ru", "kk", "en"]
Channel = Annotated[str, StringConstraints(min_length=1, max_length=80, pattern=r"\S")]
Status = Literal["pending", "running", "completed", "needs_review", "failed"]


class RequestModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=False, strict=True)


class PublicModel(BaseModel):
    model_config = ConfigDict(extra="allow")


class ErrorDetail(BaseModel):
    code: str = Field(examples=["CONVERSATION_NOT_FOUND"])
    message: str = Field(examples=["Диалог не найден."])


class ErrorResponse(BaseModel):
    error: ErrorDetail


class HealthResponse(BaseModel):
    status: Literal["ok"] = "ok"
    database: Literal["ok"] = "ok"


class DatasetLoadRequest(RequestModel):
    group_id: str | None = Field(default=None, min_length=1, examples=["DEV"])


class DatasetLoadResponse(PublicModel):
    dataset_group_id: str
    total: int
    inserted: int
    updated: int
    unchanged: int
    warnings: list[dict[str, Any]] = Field(default_factory=list)


class TranscriptInput(RequestModel):
    timestamp: str | None = Field(default=None, description="Исходная строка без преобразования; отсутствующая/null метка хранится как пустая строка.", examples=["00:02"])
    speaker: Literal["client", "operator", "bot", "system"]
    text: str = Field(examples=["Здравствуйте, меня зовут Анна."])


class UploadRequest(RequestModel):
    conversation_id: str | None = Field(default=None, min_length=1, examples=["uploaded-001"])
    channel: Channel = "agent chat"
    language: Language = "RU"
    transcript: list[TranscriptInput] | str = Field(description="Массив реплик либо текст role: text, одна реплика на строку.", examples=["client: Какие тарифы?\noperator: Здравствуйте, меня зовут Анна."])
    audio_url: str | None = None


class TranscriptMessage(PublicModel):
    message_id: str = Field(examples=["m1"])
    timestamp: str
    speaker: Literal["client", "operator", "bot", "system"]
    text: str
    offset_sec: float | None = None
    audio_url: str | None = Field(default=None, description="Ссылка для проигрывателя звонка, повторяется из карточки диалога.")


class EvaluationRequest(RequestModel):
    conversation_id: str = Field(min_length=1, examples=["p1-ru-001"])
    checklist_version_id: str | None = Field(default=None, description="Если пропущен, используется активная версия.")
    output_language: OutputLanguage = "ru"


class BatchRequest(RequestModel):
    conversation_ids: list[str] | None = Field(default=None, min_length=1, examples=[["p1-ru-001", "p1-kz-001"]])
    all: bool = Field(default=False, description="Все диалоги, находящиеся в БД на момент запуска.")
    checklist_version_id: str | None = None
    output_language: OutputLanguage = "ru"
    force: bool = Field(default=False, description="Повторно оценить уже завершённые пары диалог/версия.")
    criterion_ids: list[str] | None = Field(default=None, description="Подмножество для переоценки. [] — пересчёт из прежних ответов, если изменились только веса.", examples=[["05"]])

    @model_validator(mode="after")
    def exclusive_selection(self):
        if self.all == (self.conversation_ids is not None):
            raise ValueError("Укажите либо conversation_ids, либо all=true.")
        for name in ("conversation_ids", "criterion_ids"):
            values = getattr(self, name)
            if values is not None and (any(not item.strip() for item in values) or len(values) != len(set(values))):
                raise ValueError(f"{name}: пустые значения и дубли недопустимы.")
        return self


class Evidence(PublicModel):
    message_id: str
    quote: str
    speaker: Literal["client", "operator", "bot", "system"] | None = None


class CriterionResult(PublicModel):
    criterion_id: str
    answer: str | None = None
    reasoning: str | None = None
    evidence: list[Evidence] = Field(default_factory=list)
    checked_range: dict[str, str] | None = None
    effective_weight: str | None = None
    points: str | None = None
    has_open_appeal: bool = False


class UsageResponse(PublicModel):
    calls: int = 0
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None
    cost: str | None = None
    price_status: str = Field(default="price_not_set", description="price_not_set означает «цена не задана».")


class EvaluationResponse(PublicModel):
    evaluation_id: str
    conversation_id: str
    checklist_version_id: str
    status: Status
    output_language: OutputLanguage = "ru"
    prompt_hash: str | None = Field(default=None, description="SHA-256 файла системного промпта этой оценки. null у старых записей; перенесённые ответы могут иметь другую версию в reevaluation.baseline_prompt_hash.")
    original: dict[str, Any] = Field(default_factory=dict, description="Неизменяемый результат исходной оценки.")
    final: dict[str, Any] = Field(default_factory=dict, description="Результат с учётом решений проверяющего.")
    decisions: list[dict[str, Any]] = Field(default_factory=list)
    criteria: list[CriterionResult] = Field(default_factory=list)
    sections: list[dict[str, Any]] = Field(default_factory=list)
    total: str | None = Field(default=None, examples=["92.31"])
    original_total: str | None = None
    final_total: str | None = None
    gross_violation: bool | None = None
    usage: UsageResponse = Field(default_factory=UsageResponse)


class ConversationListItem(PublicModel):
    conversation_id: str
    language: Language
    channel: Channel
    total: str | None = None
    gross_violation: bool | None = None
    status: Status | Literal["not_evaluated"]
    evaluation_id: str | None = None
    has_appeal: bool = False
    has_open_appeal: bool = False
    evaluation_stale: bool = False


class ConversationResponse(PublicModel):
    conversation_id: str
    channel: Channel
    language: Language
    audio_url: str | None = None
    transcript: list[TranscriptMessage]
    latest_result: EvaluationResponse | None = None


class UploadResponse(PublicModel):
    conversation_id: str
    import_result: DatasetLoadResponse
    conversation: ConversationResponse


class SectionConfig(RequestModel):
    id: str
    name: str


class CriterionConfig(RequestModel):
    criterion_id: str
    name: str
    description: str = ""
    section: str | None = None
    type: Literal["yes_no", "scale", "flag"]
    weight: int | float | str
    allow_na: bool = False
    na_condition: str | None = None
    yes_text: str | None = None
    no_text: str | None = None
    scale: dict[str, str] = Field(default_factory=dict)
    scored: bool | None = Field(default=None, description="По умолчанию true, кроме flag (false).")
    order: int | None = None
    active: bool = True


class ChecklistRequest(BaseModel):
    # Read-modify-write of an API version is supported; service reassigns metadata.
    model_config = ConfigDict(extra="ignore")
    name: str = Field(examples=["Freedom Broker: чек-лист качества чатов"])
    checklist_id: str = "freedom-broker"
    sections: list[SectionConfig]
    criteria: list[CriterionConfig] = Field(min_length=1)


class ChecklistResponse(PublicModel):
    checklist_version_id: str
    checklist_id: str
    version: int
    name: str
    sections: list[dict[str, Any]]
    criteria: list[dict[str, Any]]
    weight_sum: str = Field(description="Сумма весов активных scored-критериев до нормирования к 100.")
    created_at: str
    change_summary: dict[str, Any] | None = None


class JobResponse(PublicModel):
    job_id: str
    status: Literal["pending", "running", "completed", "failed", "interrupted"]
    total: int
    done: int
    errors: list[dict[str, Any]] = Field(default_factory=list)


class AppealRequest(RequestModel):
    evaluation_id: str
    criterion_id: str = Field(examples=["05"])
    comment: str = Field(min_length=1, max_length=20000, examples=["Прошу проверить реакцию оператора в m3."])


class SuggestRequest(RequestModel):
    output_language: OutputLanguage = "ru"


class DecisionRequest(RequestModel):
    decision: Literal["confirm", "change"]
    final_answer: str
    reason: str = Field(min_length=1, max_length=20000, examples=["Действие подтверждается репликой m3."])


class AppealResponse(PublicModel):
    appeal_id: str
    evaluation_id: str
    criterion_id: str
    comment: str
    status: Literal["open", "reviewed"]


class SuggestionContent(PublicModel):
    criterion_id: str
    original_answer: str
    decision: Literal["confirm", "change"]
    final_answer: str
    reason: str
    evidence: list[Evidence]


class SuggestionResponse(PublicModel):
    appeal_id: str
    is_suggestion: Literal[True] = True
    suggestion: SuggestionContent
    call_id: str | None = None
    usage: dict[str, Any] | None = None


class DecisionResponse(PublicModel):
    appeal_id: str
    evaluation_id: str
    decision: dict[str, Any]
    original_total: str | None = None
    before_total: str | None = None
    after_total: str | None = None
    final_total: str | None = None


class CostResponse(PublicModel):
    """Totals include failed attempts and appeal suggestions; unknown costs remain null."""
    evaluations: list[dict[str, Any]] = Field(default_factory=list)
