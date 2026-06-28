"""Tests for antcrew sprint command."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from antcrew.cli._app import app

runner = CliRunner()


def _make_backlog(tmp_path: Path, tickets: list) -> Path:
    p = tmp_path / "backlog.json"
    p.write_text(json.dumps(tickets))
    return p


class TestSprintCmdBasics:
    def test_exits_zero_with_file(self, tmp_path):
        bl = _make_backlog(tmp_path, ["Add auth", "Billing", "CI"])
        result = runner.invoke(app, ["sprint", str(bl)])
        assert result.exit_code == 0

    def test_shows_sprint_header(self, tmp_path):
        bl = _make_backlog(tmp_path, ["A", "B", "C"])
        result = runner.invoke(app, ["sprint", str(bl)])
        assert "Sprint" in result.output

    def test_three_tickets_one_sprint(self, tmp_path):
        bl = _make_backlog(tmp_path, ["A", "B", "C"])
        result = runner.invoke(app, ["sprint", str(bl)])
        assert "1 sprint" in result.output or "Sprint 1" in result.output

    def test_five_tickets_default_size_two_sprints(self, tmp_path):
        tickets = ["T1", "T2", "T3", "T4", "T5"]
        bl = _make_backlog(tmp_path, tickets)
        result = runner.invoke(app, ["sprint", str(bl)])
        assert "Sprint 2" in result.output  # 5 tickets / 4 per sprint = 2 sprints

    def test_custom_size(self, tmp_path):
        tickets = ["T1", "T2", "T3", "T4", "T5", "T6"]
        bl = _make_backlog(tmp_path, tickets)
        result = runner.invoke(app, ["sprint", str(bl), "--size", "2"])
        assert "Sprint 3" in result.output  # 6 / 2 = 3 sprints

    def test_missing_file_exits_1(self, tmp_path):
        result = runner.invoke(app, ["sprint", str(tmp_path / "nope.json")])
        assert result.exit_code == 1
        assert "not found" in result.output.lower()

    def test_empty_array_exits_0(self, tmp_path):
        bl = _make_backlog(tmp_path, [])
        result = runner.invoke(app, ["sprint", str(bl)])
        assert result.exit_code == 0
        assert "No tickets" in result.output

    def test_invalid_json_exits_1(self, tmp_path):
        p = tmp_path / "bad.json"; p.write_text("{not json}")
        result = runner.invoke(app, ["sprint", str(p)])
        assert result.exit_code == 1

    def test_object_array_with_title(self, tmp_path):
        tickets = [{"title": "Auth"}, {"title": "Billing"}]
        bl = _make_backlog(tmp_path, tickets)
        result = runner.invoke(app, ["sprint", str(bl)])
        assert "Auth" in result.output
        assert "Billing" in result.output

    def test_dict_with_tickets_key(self, tmp_path):
        data = {"tickets": ["A", "B", "C"]}
        p = tmp_path / "run.json"; p.write_text(json.dumps(data))
        result = runner.invoke(app, ["sprint", str(p)])
        assert result.exit_code == 0
        assert "Sprint" in result.output


class TestSprintCmdJson:
    def test_json_flag_outputs_valid_json(self, tmp_path):
        bl = _make_backlog(tmp_path, ["A", "B", "C", "D", "E"])
        result = runner.invoke(app, ["sprint", str(bl), "--json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["total_tickets"] == 5
        assert data["sprint_size"] == 4
        assert data["total_sprints"] == 2

    def test_json_sprints_array(self, tmp_path):
        bl = _make_backlog(tmp_path, ["A", "B"])
        result = runner.invoke(app, ["sprint", str(bl), "--json"])
        data = json.loads(result.output)
        assert len(data["sprints"]) == 1
        assert data["sprints"][0]["tickets"] == ["A", "B"]

    def test_json_custom_size(self, tmp_path):
        bl = _make_backlog(tmp_path, ["A", "B", "C"])
        result = runner.invoke(app, ["sprint", str(bl), "--json", "--size", "2"])
        data = json.loads(result.output)
        assert data["total_sprints"] == 2
        assert data["sprint_size"] == 2

    def test_output_file(self, tmp_path):
        bl = _make_backlog(tmp_path, ["A", "B"])
        out = tmp_path / "result.json"
        result = runner.invoke(app, ["sprint", str(bl), "--output", str(out)])
        assert result.exit_code == 0
        data = json.loads(out.read_text())
        assert "sprints" in data

    def test_output_file_and_json_flag(self, tmp_path):
        bl = _make_backlog(tmp_path, ["A", "B"])
        out = tmp_path / "result.json"
        result = runner.invoke(app, ["sprint", str(bl), "--json", "--output", str(out)])
        assert result.exit_code == 0
        # Both file written and stdout emitted
        assert out.exists()
        json.loads(result.output)  # stdout is also valid JSON
