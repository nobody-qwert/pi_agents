"""Validated loading and safe projection of versioned agent configuration."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Annotated, Any, Final, Literal, Self

from pydantic import Field, ValidationError, field_validator, model_validator

from orchestrator.domain import (
    CharterRecord,
    DesignProposal,
    DesignRevision,
    IssueReport,
    OutcomeEvidence,
    ProposedWorkPlan,
    StrictDomainModel,
    VerificationReport,
    WorkNodeRecord,
    WorkReport,
)
from orchestrator.domain.primitives import NonEmptyStr, Sha256Digest, ShortStr

AgentId = Annotated[
    str,
    Field(pattern=r"^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$", min_length=1, max_length=64),
]
ProviderId = Literal["lm-studio"]


class RegistryValidationError(ValueError):
    """A focused startup failure caused by unsafe or inconsistent registry data."""


_YAML_KEY = re.compile(r"^[A-Za-z_][A-Za-z0-9_-]*$")
_YAML_INTEGER = re.compile(r"^-?(?:0|[1-9][0-9]*)$")
_YAML_FLOAT = re.compile(r"^-?(?:0|[1-9][0-9]*)\.[0-9]+(?:[eE][+-]?[0-9]+)?$")


class ModelConfig(StrictDomainModel):
    provider: ProviderId
    model: Literal["qwen3.6-27b"]
    temperature: Annotated[float, Field(ge=0.0, le=2.0)]
    max_output_tokens: Annotated[int, Field(ge=1, le=100_000)]


class ExecutionConfig(StrictDomainModel):
    timeout_seconds: Annotated[int, Field(ge=1, le=3600)]
    max_attempts: Annotated[int, Field(ge=1, le=10)]
    allow_parallel: bool


class AgentAuthority(StrictDomainModel):
    can_propose_charter: bool = False
    can_investigate_current_state: bool = False
    can_propose_design: bool = False
    can_recommend_design_acceptance: bool = False
    can_accept_design: bool = False
    can_propose_work_plan: bool = False
    can_mutate_artifacts: bool = False
    can_verify_local: bool = False
    can_integrate: bool = False
    can_verify_outcome: bool = False
    can_triage: bool = False
    can_complete_run: bool = False

    def granted_capabilities(self) -> frozenset[str]:
        return frozenset(
            name
            for name, value in self.model_dump(exclude={"schema_version"}).items()
            if value
        )


class VisibilityConfig(StrictDomainModel):
    expose_prompt_to_operator: bool


class AgentConfig(StrictDomainModel):
    agent_id: AgentId
    display_name: ShortStr
    description: NonEmptyStr
    prompt_ref: NonEmptyStr
    model: ModelConfig
    execution: ExecutionConfig
    tools: tuple[ShortStr, ...]
    input_schema: ShortStr
    output_schema: ShortStr
    authority: AgentAuthority
    visibility: VisibilityConfig

    @field_validator("tools", mode="before")
    @classmethod
    def freeze_tools(cls, value: object) -> object:
        return tuple(value) if isinstance(value, list) else value

    @model_validator(mode="after")
    def collections_are_unique(self) -> Self:
        if len(set(self.tools)) != len(self.tools):
            raise ValueError("tools must not contain duplicate names")
        return self


SCHEMA_REGISTRY: Final[Mapping[str, type[StrictDomainModel]]] = MappingProxyType(
    {
        model.__name__: model
        for model in (
            CharterRecord,
            DesignProposal,
            DesignRevision,
            IssueReport,
            OutcomeEvidence,
            ProposedWorkPlan,
            VerificationReport,
            WorkReport,
            WorkNodeRecord,
        )
    }
)
TOOL_REGISTRY: Final[frozenset[str]] = frozenset()

_ROLE_AUTHORITY: Final[Mapping[str, frozenset[str]]] = MappingProxyType(
    {
        "intake": frozenset({"can_propose_charter"}),
        "investigator": frozenset({"can_investigate_current_state"}),
        "design-authority": frozenset({"can_propose_design"}),
        "design-critic": frozenset({"can_recommend_design_acceptance"}),
        "work-planner": frozenset({"can_propose_work_plan"}),
        "executor": frozenset({"can_mutate_artifacts"}),
        "local-verifier": frozenset({"can_verify_local"}),
        "integrator": frozenset({"can_integrate", "can_mutate_artifacts"}),
        "outcome-verifier": frozenset({"can_verify_outcome"}),
        "issue-triager": frozenset({"can_triage"}),
    }
)

_SECRET_PATTERNS: Final[tuple[re.Pattern[str], ...]] = (
    re.compile(
        r"(?im)\b(?:api[_ -]?(?:key|token)|auth[_ -]?token|access[_ -]?token|"
        r"refresh[_ -]?token|id[_ -]?token|password|passwd|client[_ -]?secret|"
        r"consumer[_ -]?secret|signing[_ -]?secret|private[_ -]?key|secret[_ -]?key|"
        r"secret[_ -]?access[_ -]?key|account[_ -]?key|connection[_ -]?string)"
        r"[\"']?\s*[:=]\s*[^\s<>{}\[\]]+"
    ),
    re.compile(r"(?i)\bauthorization\s*[:=]\s*(?:bearer|basic)\s+\S+"),
    re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{8,}"),
    re.compile(
        r"-----BEGIN (?:RSA |EC |DSA |OPENSSH )?PRIVATE KEY-----|"
        r"-----BEGIN PGP PRIVATE KEY BLOCK-----"
    ),
    re.compile(r"\b(?:AKIA|ASIA|AIDA|AROA|AIPA|ANPA|ANVA|ASCA)[A-Z0-9]{16}\b"),
    re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b"),
    re.compile(
        r"\b(?:github_pat_[0-9A-Za-z_]{20,}|gh[pousr]_[0-9A-Za-z]{36,}|"
        r"glpat-[0-9A-Za-z_-]{20,}|xox[baprs]-[0-9A-Za-z-]{10,}|"
        r"(?:sk|rk)_live_[0-9A-Za-z]{16,}|sk-(?:proj-)?[0-9A-Za-z_-]{20,})\b"
    ),
)


@dataclass(frozen=True, slots=True)
class AgentDefinition:
    config: AgentConfig
    prompt: str
    config_hash: str
    prompt_hash: str


@dataclass(frozen=True, slots=True)
class AgentRegistry:
    definitions: Mapping[str, AgentDefinition]
    registry_hash: str

    def __post_init__(self) -> None:
        immutable = MappingProxyType(dict(self.definitions))
        object.__setattr__(self, "definitions", immutable)

    def __getitem__(self, agent_id: str) -> AgentDefinition:
        return self.definitions[agent_id]


class AgentProjection(StrictDomainModel):
    agent_id: AgentId
    display_name: ShortStr
    description: NonEmptyStr
    prompt_title: ShortStr
    prompt_excerpt: NonEmptyStr | None
    prompt: NonEmptyStr | None
    provider: ProviderId
    model: ShortStr
    temperature: float
    max_output_tokens: int
    timeout_seconds: int
    max_attempts: int
    allow_parallel: bool
    tools: tuple[ShortStr, ...]
    input_schema: ShortStr
    output_schema: ShortStr
    input_schema_json: Mapping[str, Any]
    output_schema_json: Mapping[str, Any]
    authority_badges: tuple[ShortStr, ...]
    config_hash: Sha256Digest
    prompt_hash: Sha256Digest


class RegistryProjection(StrictDomainModel):
    registry_hash: Sha256Digest
    agents: tuple[AgentProjection, ...]


def load_agent_registry(config_root: Path) -> AgentRegistry:
    """Load a complete registry from ``agents/*.yaml`` and referenced prompts."""

    root = config_root.resolve()
    agents_dir = root / "agents"
    paths = sorted(agents_dir.glob("*.yaml"), key=lambda path: path.name)
    if not paths:
        raise RegistryValidationError(f"no agent definitions found in {agents_dir}")

    definitions: dict[str, AgentDefinition] = {}
    for path in paths:
        config = _load_agent_config(path)
        if config.agent_id in definitions:
            raise RegistryValidationError(f"duplicate agent_id: {config.agent_id}")
        _validate_agent_policy(config, path)
        prompt_path = _resolve_prompt(root, config.prompt_ref, path)
        prompt_bytes = prompt_path.read_bytes()
        try:
            prompt = prompt_bytes.decode("utf-8")
        except UnicodeDecodeError as error:
            raise RegistryValidationError(
                f"{path.name}: prompt must be valid UTF-8: {config.prompt_ref}"
            ) from error
        if not prompt.strip():
            raise RegistryValidationError(f"{path.name}: prompt must not be empty")
        config_payload = _canonical_json(config.model_dump(mode="json"))
        definitions[config.agent_id] = AgentDefinition(
            config=config,
            prompt=prompt,
            config_hash=_sha256(config_payload),
            prompt_hash=_sha256(prompt_bytes),
        )

    missing = set(_ROLE_AUTHORITY).difference(definitions)
    extra = set(definitions).difference(_ROLE_AUTHORITY)
    if missing or extra:
        details = []
        if missing:
            details.append(f"missing required agent IDs: {', '.join(sorted(missing))}")
        if extra:
            details.append(f"unknown agent IDs: {', '.join(sorted(extra))}")
        raise RegistryValidationError("; ".join(details))

    registry_payload = _canonical_json(
        [
            {
                "agent_id": agent_id,
                "config_hash": definition.config_hash,
                "prompt_hash": definition.prompt_hash,
            }
            for agent_id, definition in sorted(definitions.items())
        ]
    )
    registry = AgentRegistry(
        definitions=definitions,
        registry_hash=_sha256(registry_payload),
    )
    project_registry(registry)
    return registry


def project_registry(registry: AgentRegistry) -> RegistryProjection:
    """Create the only operator-safe registry representation."""

    agents = []
    for agent_id in sorted(registry.definitions):
        agent = _project_agent(registry.definitions[agent_id])
        if _contains_secret(agent.model_dump(mode="json")):
            raise RegistryValidationError(
                f"{agent_id}: operator projection appears to contain a secret"
            )
        agents.append(agent)
    return RegistryProjection(
        registry_hash=registry.registry_hash, agents=tuple(agents)
    )


def _load_agent_config(path: Path) -> AgentConfig:
    try:
        raw = _parse_agent_yaml(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError) as error:
        raise RegistryValidationError(f"{path.name}: invalid YAML: {error}") from error
    try:
        return AgentConfig.model_validate(raw, strict=True)
    except ValidationError as error:
        raise RegistryValidationError(
            f"{path.name}: invalid agent config: {error}"
        ) from error


def _validate_agent_policy(config: AgentConfig, path: Path) -> None:
    allowed = _ROLE_AUTHORITY.get(config.agent_id)
    if allowed is None:
        raise RegistryValidationError(
            f"{path.name}: unknown agent_id: {config.agent_id}"
        )
    excess = config.authority.granted_capabilities().difference(allowed)
    if excess:
        raise RegistryValidationError(
            f"{path.name}: authority exceeds role boundary: {', '.join(sorted(excess))}"
        )
    unknown_tools = set(config.tools).difference(TOOL_REGISTRY)
    if unknown_tools:
        raise RegistryValidationError(
            f"{path.name}: unknown tools: {', '.join(sorted(unknown_tools))}"
        )
    for field_name in ("input_schema", "output_schema"):
        schema_name = getattr(config, field_name)
        if schema_name not in SCHEMA_REGISTRY:
            raise RegistryValidationError(
                f"{path.name}: unknown {field_name}: {schema_name}"
            )


def _resolve_prompt(root: Path, prompt_ref: str, config_path: Path) -> Path:
    prompt_path = (root / prompt_ref).resolve()
    prompts_root = (root / "prompts").resolve()
    if not prompt_path.is_relative_to(prompts_root):
        raise RegistryValidationError(
            f"{config_path.name}: prompt_ref must resolve inside config/prompts"
        )
    if prompt_path.suffix != ".md" or not prompt_path.is_file():
        raise RegistryValidationError(
            f"{config_path.name}: bad prompt reference: {prompt_ref}"
        )
    return prompt_path


def _project_agent(definition: AgentDefinition) -> AgentProjection:
    config = definition.config
    visible = config.visibility.expose_prompt_to_operator
    title, excerpt = _prompt_summary(definition.prompt)
    prompt = definition.prompt.strip() if visible else None
    return AgentProjection(
        agent_id=config.agent_id,
        display_name=config.display_name,
        description=config.description,
        prompt_title=title if visible else f"{config.display_name} prompt",
        prompt_excerpt=excerpt if visible else None,
        prompt=prompt,
        provider=config.model.provider,
        model=config.model.model,
        temperature=config.model.temperature,
        max_output_tokens=config.model.max_output_tokens,
        timeout_seconds=config.execution.timeout_seconds,
        max_attempts=config.execution.max_attempts,
        allow_parallel=config.execution.allow_parallel,
        tools=config.tools,
        input_schema=config.input_schema,
        output_schema=config.output_schema,
        input_schema_json=SCHEMA_REGISTRY[config.input_schema].model_json_schema(),
        output_schema_json=SCHEMA_REGISTRY[config.output_schema].model_json_schema(),
        authority_badges=tuple(sorted(config.authority.granted_capabilities())),
        config_hash=definition.config_hash,
        prompt_hash=definition.prompt_hash,
    )


def _prompt_summary(prompt: str) -> tuple[str, str]:
    lines = [line.strip() for line in prompt.splitlines() if line.strip()]
    heading = lines[0].lstrip("# ").strip()
    title = heading[:256] or "Agent prompt"
    body = [line for line in lines[1:] if not line.startswith("#")]
    excerpt = " ".join(body[:3])[:4096] or title
    return title, excerpt


def _contains_secret(value: object) -> bool:
    if isinstance(value, str):
        return any(pattern.search(value) is not None for pattern in _SECRET_PATTERNS)
    if isinstance(value, Mapping):
        return any(_contains_secret(item) for item in value.values())
    if isinstance(value, (list, tuple)):
        return any(_contains_secret(item) for item in value)
    return False


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _parse_agent_yaml(value: str) -> dict[str, object]:
    """Parse the closed mapping-only YAML subset accepted for agent configs.

    The registry intentionally supports only nested mappings, scalar values, and
    the empty sequence used for the currently closed tool registry. YAML tags,
    aliases, block scalars, and implicit nulls are rejected instead of gaining
    executable or ambiguous parser behavior.
    """

    root: dict[str, object] = {}
    stack: list[tuple[int, dict[str, object]]] = [(0, root)]
    saw_value = False
    for line_number, raw_line in enumerate(value.splitlines(), start=1):
        if not raw_line.strip() or raw_line.lstrip().startswith("#"):
            continue
        if "\t" in raw_line:
            raise ValueError(f"line {line_number}: tabs are not permitted")
        indent = len(raw_line) - len(raw_line.lstrip(" "))
        if indent % 2:
            raise ValueError(f"line {line_number}: indentation must use two spaces")
        while stack and indent < stack[-1][0]:
            stack.pop()
        if not stack or indent != stack[-1][0]:
            raise ValueError(f"line {line_number}: unexpected indentation")

        content = raw_line[indent:]
        key, separator, raw_scalar = content.partition(":")
        if not separator or not _YAML_KEY.fullmatch(key):
            raise ValueError(f"line {line_number}: expected a simple mapping key")
        target = stack[-1][1]
        if key in target:
            raise ValueError(f"line {line_number}: duplicate YAML key: {key!r}")
        raw_scalar = raw_scalar.strip()
        if not raw_scalar:
            nested: dict[str, object] = {}
            target[key] = nested
            stack.append((indent + 2, nested))
            continue
        target[key] = _parse_yaml_scalar(raw_scalar, line_number)
        saw_value = True

    if not saw_value:
        raise ValueError("agent definition must be a non-empty mapping")
    return root


def _parse_yaml_scalar(value: str, line_number: int) -> object:
    if value == "[]":
        return []
    if value.startswith("["):
        if not value.endswith("]"):
            raise ValueError(f"line {line_number}: invalid inline sequence")
        items = value[1:-1].split(",")
        return [
            _parse_yaml_text_item(item.strip(), line_number)
            for item in items
            if item.strip()
        ]
    if value in {"true", "false"}:
        return value == "true"
    if _YAML_INTEGER.fullmatch(value):
        return int(value)
    if _YAML_FLOAT.fullmatch(value):
        return float(value)
    if value.startswith('"'):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError as error:
            raise ValueError(f"line {line_number}: invalid quoted scalar") from error
        if not isinstance(parsed, str):
            raise ValueError(f"line {line_number}: quoted scalar must be text")
        return parsed
    if value.startswith("'"):
        if len(value) < 2 or not value.endswith("'"):
            raise ValueError(f"line {line_number}: invalid quoted scalar")
        return value[1:-1].replace("''", "'")
    if value[0] in "{&*!|>" or value in {"null", "Null", "NULL", "~"}:
        raise ValueError(f"line {line_number}: unsupported YAML construct")
    return value


def _parse_yaml_text_item(value: str, line_number: int) -> str:
    parsed = _parse_yaml_scalar(value, line_number)
    if not isinstance(parsed, str):
        raise ValueError(f"line {line_number}: sequence items must be text")
    return parsed
