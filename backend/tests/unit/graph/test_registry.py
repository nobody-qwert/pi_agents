from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from pydantic import ValidationError

from orchestrator.graph import (
    RegistryValidationError,
    load_agent_registry,
    project_registry,
)

CONFIG_ROOT = Path(__file__).resolve().parents[4] / "config"
EXPECTED_REGISTRY_HASH = (
    "a5f417b73885de9481396061775fb5c2f81242be048f6ff6f25442f90e747842"
)
EXPECTED_INTAKE_CONFIG_HASH = (
    "30ef0b2a0126653e5687175c7867f60a45dcb2ab26fb8fd97ec157d5b41cadae"
)
EXPECTED_INTAKE_PROMPT_HASH = (
    "a46fde5abb5570aab9d3cacde78c9dfd3e69774f91e80dfcdd166f32dfd46ce9"
)


def copy_registry(tmp_path: Path) -> Path:
    target = tmp_path / "config"
    shutil.copytree(CONFIG_ROOT, target)
    return target


def replace_in(path: Path, old: str, new: str) -> None:
    value = path.read_text(encoding="utf-8")
    assert old in value
    path.write_text(value.replace(old, new, 1), encoding="utf-8")


def test_registry_load_is_deterministic_and_hashes_content() -> None:
    first = load_agent_registry(CONFIG_ROOT)
    second = load_agent_registry(CONFIG_ROOT)

    assert tuple(first.definitions) == tuple(second.definitions)
    assert first.registry_hash == second.registry_hash == EXPECTED_REGISTRY_HASH
    assert first["intake"].config_hash == EXPECTED_INTAKE_CONFIG_HASH
    assert first["intake"].prompt_hash == EXPECTED_INTAKE_PROMPT_HASH


def test_registry_and_configs_are_immutable() -> None:
    registry = load_agent_registry(CONFIG_ROOT)

    with pytest.raises(TypeError):
        registry.definitions["replacement"] = registry["intake"]  # type: ignore[index]
    with pytest.raises(ValidationError, match="frozen"):
        registry["intake"].config.display_name = "Changed"  # type: ignore[misc]


def test_projection_contains_only_explicit_operator_fields() -> None:
    registry = load_agent_registry(CONFIG_ROOT)
    projection = project_registry(registry)
    payload = projection.model_dump(mode="json")

    assert projection.registry_hash == registry.registry_hash
    assert [agent.agent_id for agent in projection.agents] == sorted(
        registry.definitions
    )
    assert set(payload["agents"][0]) == {
        "schema_version",
        "agent_id",
        "display_name",
        "description",
        "prompt_title",
        "prompt_excerpt",
        "prompt",
        "provider",
        "model",
        "temperature",
        "max_output_tokens",
        "timeout_seconds",
        "max_attempts",
        "allow_parallel",
        "tools",
        "input_schema",
        "output_schema",
        "input_schema_json",
        "output_schema_json",
        "authority_badges",
        "config_hash",
        "prompt_hash",
    }
    serialized = projection.model_dump_json().lower()
    assert "prompt_ref" not in serialized
    assert "expose_prompt_to_operator" not in serialized


def test_hidden_prompt_is_redacted_from_projection(tmp_path: Path) -> None:
    root = copy_registry(tmp_path)
    config = root / "agents" / "intake.yaml"
    replace_in(
        config, "expose_prompt_to_operator: true", "expose_prompt_to_operator: false"
    )
    (root / "prompts" / "intake.md").write_text(
        "# Hidden\n\nclient_secret = hidden-value\n", encoding="utf-8"
    )

    projection = project_registry(load_agent_registry(root))
    intake = next(agent for agent in projection.agents if agent.agent_id == "intake")

    assert intake.prompt is None
    assert intake.prompt_excerpt is None
    assert intake.prompt_title == "Intake Analyst prompt"
    assert "Hidden" not in projection.model_dump_json()
    assert "hidden-value" not in projection.model_dump_json()


@pytest.mark.parametrize(
    ("filename", "old", "new", "diagnostic"),
    (
        ("intake.yaml", "tools: []", "tools: [shell]", "unknown tools: shell"),
        (
            "intake.yaml",
            "output_schema: WorkReport",
            "output_schema: InventedSchema",
            "unknown output_schema: InventedSchema",
        ),
        (
            "intake.yaml",
            "  can_propose_charter: true",
            "  can_propose_charter: true\n  can_complete_run: true",
            "authority exceeds role boundary: can_complete_run",
        ),
        (
            "intake.yaml",
            "prompt_ref: prompts/intake.md",
            "prompt_ref: ../outside.md",
            "prompt_ref must resolve inside config/prompts",
        ),
        (
            "intake.yaml",
            "visibility:",
            "edges: []\nvisibility:",
            "Extra inputs are not permitted",
        ),
        (
            "intake.yaml",
            "  can_propose_charter: true",
            "  can_propose_charter: true\n  can_control_topology: true",
            "Extra inputs are not permitted",
        ),
    ),
)
def test_invalid_config_has_focused_diagnostic(
    tmp_path: Path, filename: str, old: str, new: str, diagnostic: str
) -> None:
    root = copy_registry(tmp_path)
    replace_in(root / "agents" / filename, old, new)

    with pytest.raises(RegistryValidationError, match=diagnostic):
        load_agent_registry(root)


def test_duplicate_agent_ids_are_rejected(tmp_path: Path) -> None:
    root = copy_registry(tmp_path)
    shutil.copyfile(
        root / "agents" / "intake.yaml",
        root / "agents" / "second-intake.yaml",
    )

    with pytest.raises(RegistryValidationError, match="duplicate agent_id: intake"):
        load_agent_registry(root)


def test_unknown_authority_field_is_rejected(tmp_path: Path) -> None:
    root = copy_registry(tmp_path)
    config = root / "agents" / "intake.yaml"
    replace_in(
        config,
        "  can_propose_charter: true",
        "  can_propose_charter: true\n  can_override_policy: true",
    )

    with pytest.raises(RegistryValidationError, match="can_override_policy"):
        load_agent_registry(root)


def test_duplicate_yaml_keys_are_rejected(tmp_path: Path) -> None:
    root = copy_registry(tmp_path)
    config = root / "agents" / "intake.yaml"
    replace_in(config, "agent_id: intake", "agent_id: intake\nagent_id: intake")

    with pytest.raises(RegistryValidationError, match="duplicate YAML key: 'agent_id'"):
        load_agent_registry(root)


def test_secret_bearing_visible_prompt_is_rejected(tmp_path: Path) -> None:
    root = copy_registry(tmp_path)
    (root / "prompts" / "intake.md").write_text(
        "# Unsafe\n\napi_key: leaked-value\n", encoding="utf-8"
    )

    with pytest.raises(RegistryValidationError, match="appears to contain a secret"):
        load_agent_registry(root)


def test_secret_bearing_projected_config_is_rejected(tmp_path: Path) -> None:
    root = copy_registry(tmp_path)
    config = root / "agents" / "intake.yaml"
    replace_in(
        config,
        "description: Drafts",
        "description: password=leaked Drafts",
    )

    with pytest.raises(RegistryValidationError, match="projection appears"):
        load_agent_registry(root)


@pytest.mark.parametrize(
    ("target", "old", "secret"),
    (
        (
            "agents/intake.yaml",
            "description: Drafts",
            "description: Authorization: Bearer sk-live-projection-leak",
        ),
        (
            "prompts/intake.md",
            "# Intake Analyst",
            "# Intake Analyst\n\n-----BEGIN PRIVATE KEY-----",
        ),
        (
            "agents/intake.yaml",
            "display_name: Intake Analyst",
            "display_name: AWS key AKIAIOSFODNN7EXAMPLE",
        ),
    ),
)
def test_common_secret_formats_are_rejected_from_operator_projection(
    tmp_path: Path, target: str, old: str, secret: str
) -> None:
    root = copy_registry(tmp_path)
    replace_in(root / target, old, secret)

    with pytest.raises(RegistryValidationError, match="projection appears"):
        load_agent_registry(root)
