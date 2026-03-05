"""Tests for agent base class and prompt loading."""

import json
import pytest
from pathlib import Path


def test_prompts_file_exists():
    prompts_path = Path(__file__).parent.parent / "agent_system_prompts.json"
    assert prompts_path.exists(), "agent_system_prompts.json must exist"


def test_prompts_file_valid_json():
    prompts_path = Path(__file__).parent.parent / "agent_system_prompts.json"
    with open(prompts_path) as f:
        data = json.load(f)
    assert isinstance(data, dict)


def test_prompts_has_required_agents():
    prompts_path = Path(__file__).parent.parent / "agent_system_prompts.json"
    with open(prompts_path) as f:
        data = json.load(f)

    required = ["editor", "stylist", "judge", "chapter_worker", "audience_reviewer", "micro_book"]
    for agent in required:
        assert agent in data, f"Missing agent prompt: {agent}"


def test_prompts_agents_have_system_prompt():
    prompts_path = Path(__file__).parent.parent / "agent_system_prompts.json"
    with open(prompts_path) as f:
        data = json.load(f)

    for key in ["editor", "stylist", "judge", "chapter_worker", "micro_book"]:
        assert "system_prompt" in data[key], f"{key} missing system_prompt"
        assert len(data[key]["system_prompt"]) > 50, f"{key} system_prompt too short"


def test_audience_reviewer_has_template():
    prompts_path = Path(__file__).parent.parent / "agent_system_prompts.json"
    with open(prompts_path) as f:
        data = json.load(f)

    assert "system_prompt_template" in data["audience_reviewer"]
    template = data["audience_reviewer"]["system_prompt_template"]
    assert "{persona_name}" in template
    assert "{persona_description}" in template


def test_audience_personas_exist():
    prompts_path = Path(__file__).parent.parent / "agent_system_prompts.json"
    with open(prompts_path) as f:
        data = json.load(f)

    personas = data.get("audience_personas", [])
    assert len(personas) == 3, "Need exactly 3 audience personas"
    for p in personas:
        assert "name" in p
        assert "description" in p
