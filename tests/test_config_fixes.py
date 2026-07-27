"""Tests for config.py bug fixes: flow:, channels: plural, team: fullstack."""
from __future__ import annotations

import textwrap
from pathlib import Path

# ---------------------------------------------------------------------------
# team: fullstack
# ---------------------------------------------------------------------------

def test_config_load_fullstack_team(tmp_path: Path):
    (tmp_path / "agentteam.yaml").write_text(
        "team: fullstack\nmodel: simulated\n", encoding="utf-8"
    )
    from antcrew.config import load
    from antcrew.teams.fullstack_team import FullStackTeam

    team = load(tmp_path / "agentteam.yaml")
    assert isinstance(team, FullStackTeam)


def test_config_fullstack_uses_simulated_llm(tmp_path: Path):
    (tmp_path / "agentteam.yaml").write_text(
        "team: fullstack\nmodel: simulated\n", encoding="utf-8"
    )
    from antcrew.config import load
    from antcrew.models.simulated import SimulatedLLM

    team = load(tmp_path / "agentteam.yaml")
    assert isinstance(team.llm, SimulatedLLM)


# ---------------------------------------------------------------------------
# flow: custom pipeline
# ---------------------------------------------------------------------------

def test_config_flow_key_creates_supervisor(tmp_path: Path):
    yaml = textwrap.dedent("""\
        team: dev
        model: simulated
        flow:
          - [business_analyst, pm]
          - [pm, backend_dev]
    """)
    (tmp_path / "agentteam.yaml").write_text(yaml, encoding="utf-8")

    from antcrew.config import load
    from antcrew.teams.dev_team import DevTeam

    team = load(tmp_path / "agentteam.yaml")
    assert isinstance(team, DevTeam)
    # Supervisor must have been created with the custom flow
    assert team._supervisor is not None


def test_config_flow_key_stores_correct_edges(tmp_path: Path):
    yaml = textwrap.dedent("""\
        team: dev
        model: simulated
        flow:
          - [business_analyst, pm]
          - [pm, backend_dev]
    """)
    (tmp_path / "agentteam.yaml").write_text(yaml, encoding="utf-8")

    from antcrew.config import load

    team = load(tmp_path / "agentteam.yaml")
    # _supervisor._flow stores normalised (src, dst, condition) tuples
    edges = team._supervisor._flow
    assert len(edges) == 2
    assert edges[0][:2] == ("business_analyst", "pm")
    assert edges[1][:2] == ("pm", "backend_dev")


def test_config_no_flow_key_uses_default_supervisor(tmp_path: Path):
    (tmp_path / "agentteam.yaml").write_text(
        "team: dev\nmodel: simulated\n", encoding="utf-8"
    )
    from antcrew.config import load

    team = load(tmp_path / "agentteam.yaml")
    # Default supervisor is always created by DevTeam
    assert team._supervisor is not None


# ---------------------------------------------------------------------------
# channels: (plural list)
# ---------------------------------------------------------------------------

def test_config_channels_plural_two_console(tmp_path: Path):
    yaml = textwrap.dedent("""\
        team: dev
        model: simulated
        channels:
          - type: console
          - type: console
    """)
    (tmp_path / "agentteam.yaml").write_text(yaml, encoding="utf-8")

    from antcrew.config import load

    team = load(tmp_path / "agentteam.yaml")
    assert len(team.integrations) == 2


def test_config_channels_singular_still_works(tmp_path: Path):
    yaml = textwrap.dedent("""\
        team: dev
        model: simulated
        channel:
          type: console
    """)
    (tmp_path / "agentteam.yaml").write_text(yaml, encoding="utf-8")

    from antcrew.config import load

    team = load(tmp_path / "agentteam.yaml")
    assert len(team.integrations) == 1


def test_config_no_channel_gives_empty_integrations(tmp_path: Path):
    (tmp_path / "agentteam.yaml").write_text(
        "team: dev\nmodel: simulated\n", encoding="utf-8"
    )
    from antcrew.config import load

    team = load(tmp_path / "agentteam.yaml")
    assert team.integrations == []


def test_config_channel_and_channels_combined(tmp_path: Path):
    """singular channel: and plural channels: can coexist."""
    yaml = textwrap.dedent("""\
        team: dev
        model: simulated
        channel:
          type: console
        channels:
          - type: console
    """)
    (tmp_path / "agentteam.yaml").write_text(yaml, encoding="utf-8")

    from antcrew.config import load

    team = load(tmp_path / "agentteam.yaml")
    assert len(team.integrations) == 2
