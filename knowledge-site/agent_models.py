"""Strict, source-addressable context summaries; never executable instructions."""
from pydantic import BaseModel, ConfigDict, Field, model_validator
from typing import Annotated, Literal


class SummaryFact(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    text: str = Field(min_length=1, max_length=400)
    source_message_ids: list[int] = Field(min_length=1, max_length=80)


class ContextSummary(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    schema_version: int = Field(default=1, ge=1, le=1)
    goal: SummaryFact
    constraints: list[SummaryFact] = Field(default_factory=list, max_length=12)
    decisions: list[SummaryFact] = Field(default_factory=list, max_length=12)
    completed: list[SummaryFact] = Field(default_factory=list, max_length=12)
    open_questions: list[SummaryFact] = Field(default_factory=list, max_length=12)
    next_actions: list[SummaryFact] = Field(default_factory=list, max_length=12)

    def check_sources(self, allowed_ids):
        for fact in [self.goal, *self.constraints, *self.decisions, *self.completed,
                     *self.open_questions, *self.next_actions]:
            if not set(fact.source_message_ids) <= set(allowed_ids):
                raise ValueError('Summary references messages outside its input')
        return self


class ConstraintInput(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    expected_version: int = Field(ge=1)
    constraints: list[str] = Field(max_length=8)

    @model_validator(mode='after')
    def validate_constraints(self):
        self.constraints = [value.strip() for value in self.constraints]
        if any(not value or len(value) > 250 for value in self.constraints):
            raise ValueError('Each constraint must contain 1–250 characters')
        if sum(len(value) for value in self.constraints) > 1000:
            raise ValueError('Constraints exceed 1000 characters')
        if len(set(self.constraints)) != len(self.constraints):
            raise ValueError('Duplicate constraints')
        return self


class AgentInput(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)


class ProjectInput(AgentInput):
    title: str = Field(min_length=1, max_length=120)
    goal: str = Field(default='', max_length=2000)


class ProjectEdit(ProjectInput):
    expected_version: int = Field(ge=1)
    progress: str = Field(default='', max_length=2000)
    next_action: str = Field(default='', max_length=1000)
    status: Literal['active', 'archived'] = 'active'


class ResourceInput(AgentInput):
    resource_kind: Literal['note', 'session', 'draft']
    resource_id: str = Field(min_length=1, max_length=100)


class MemorySource(AgentInput):
    kind: Literal['manual', 'message', 'note'] = 'manual'
    id: str | None = Field(default=None, max_length=100)


class MemoryInput(AgentInput):
    scope: Literal['account', 'project'] = 'account'
    project_id: str | None = Field(default=None, max_length=64)
    kind: Literal['preference', 'project_fact', 'experience'] = 'preference'
    content: str = Field(min_length=1, max_length=1000)
    source: MemorySource = Field(default_factory=MemorySource)
    conflict_key: str = Field(default='', max_length=80)
    expires_at: int | None = Field(default=None, gt=0)


class MemoryEdit(AgentInput):
    expected_revision: int = Field(ge=1)
    content: str = Field(min_length=1, max_length=1000)
    status: Literal['active', 'disabled'] = 'active'
    conflict_key: str = Field(default='', max_length=80)
    expires_at: int | None = Field(default=None, gt=0)


class RevisionInput(AgentInput):
    expected_revision: int = Field(ge=1)


class RunInput(AgentInput):
    kind: Literal['writing', 'research', 'impact', 'knowledge', 'assessment']
    project_id: str | None = Field(default=None, max_length=64)
    topic: str = Field(min_length=1, max_length=1000)
    requirements: str = Field(default='', max_length=2000)
    draft_id: str | None = Field(default=None, max_length=64)
    note_id: int | None = Field(default=None, gt=0)
    web_search: bool = False
    memory_mode: Literal['off', 'account'] = 'account'
    max_tokens: int = Field(default=16000, ge=1000, le=64000)
    max_search_calls: int = Field(default=3, ge=1, le=3)
    idempotency_key: str = Field(min_length=8, max_length=100)
    scope: Literal['public', 'selected'] = 'public'
    note_ids: list[Annotated[int, Field(gt=0)]] = Field(default_factory=list, max_length=12)
    attempt_id: str | None = Field(default=None, max_length=64)

    @model_validator(mode='after')
    def validate_run_mode(self):
        if self.kind in {'writing', 'research', 'impact'} and (self.scope != 'public' or self.note_ids or self.attempt_id):
            raise ValueError('Legacy runs use public notes')
        if self.kind in {'knowledge', 'assessment'} and (self.web_search or self.draft_id):
            raise ValueError('Knowledge runs use local evidence and separate results')
        if self.kind == 'assessment' and (not self.attempt_id or self.requirements or self.project_id):
            raise ValueError('Assessment must reference a saved attempt')
        return self


class LocalSummary(AgentInput):
    schema_version: int = Field(default=1, ge=1, le=1)
    goal: str = Field(default='', max_length=1000)
    constraints: list[str] = Field(default_factory=list, max_length=12)
    decisions: list[str] = Field(default_factory=list, max_length=12)
    completed: list[str] = Field(default_factory=list, max_length=12)
    open_questions: list[str] = Field(default_factory=list, max_length=12)
    next_actions: list[str] = Field(default_factory=list, max_length=12)

    @model_validator(mode='after')
    def bounded(self):
        import json
        groups = (self.constraints,self.decisions,self.completed,self.open_questions,self.next_actions)
        if any(len(value)>1000 for group in groups for value in group):
            raise ValueError('Summary entry too long')
        if len(json.dumps(self.model_dump(),ensure_ascii=False).encode())>8000:
            raise ValueError('Summary too long')
        return self


class TransientInput(AgentInput):
    question: str = Field(min_length=1, max_length=1000)
    note_id: int | None = Field(default=None, gt=0)
    web_search: bool = False
    history: list[dict] = Field(default_factory=list, max_length=12)
    summary: dict = Field(default_factory=dict)
    memory_context: list[dict] = Field(default_factory=list, max_length=5)

    @model_validator(mode='after')
    def bounded_context(self):
        import json
        for message in self.history:
            if set(message) != {'role','content'} or message['role'] not in ('user','assistant') or not isinstance(message['content'],str) or len(message['content']) > 4000:
                raise ValueError('Invalid transient history')
        for item in self.memory_context:
            if set(item) - {'local_id','revision','content'} or not isinstance(item.get('content'),str) or not item['content'].strip() or len(item['content']) > 4000:
                raise ValueError('Invalid transient memory')
            if 'local_id' in item and (not isinstance(item['local_id'],str) or len(item['local_id'])>100):
                raise ValueError('Invalid local identifier')
            if 'revision' in item and (type(item['revision']) is not int or item['revision']<1):
                raise ValueError('Invalid local revision')
        if self.summary:
            self.summary = LocalSummary.model_validate(self.summary).model_dump()
        if len(json.dumps(self.summary,ensure_ascii=False).encode()) > 8000:
            raise ValueError('Transient summary exceeds budget')
        return self
