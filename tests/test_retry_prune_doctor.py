"""Tests for LLM retry wiring, TraceLog.prune(), and antcrew doctor."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from antcrew_engine.models.base import BaseLLM, Message, _is_retryable
from typer.testing import CliRunner

from antcrew.cli._app import app
from antcrew.trace import TraceLog

runner = CliRunner()


# ── _is_retryable helper ──────────────────────────────────────────────────────

class TestIsRetryable:
    def test_timeout_error(self):
        assert _is_retryable(TimeoutError("timeout"))

    def test_connection_error(self):
        assert _is_retryable(ConnectionError("refused"))

    def test_http_429(self):
        exc = Exception("rate limit")
        exc.response = MagicMock(status_code=429)
        assert _is_retryable(exc)

    def test_http_503(self):
        exc = Exception("server error")
        exc.response = MagicMock(status_code=503)
        assert _is_retryable(exc)

    def test_http_400_not_retryable(self):
        exc = Exception("bad request")
        exc.response = MagicMock(status_code=400)
        assert not _is_retryable(exc)

    def test_value_error_not_retryable(self):
        assert not _is_retryable(ValueError("bad input"))


# ── BaseLLM._with_retry ───────────────────────────────────────────────────────

class _MinimalLLM(BaseLLM):
    def complete(self, messages, *, max_tokens=16384, json_mode=False):
        return "ok"


class TestWithRetry:
    def setup_method(self):
        self.llm = _MinimalLLM()
        self.llm.max_retries = 3
        self.llm.retry_delay = 0.0
        self.llm.retry_jitter = 0.0

    def test_success_on_first_try(self):
        calls = []
        def fn():
            calls.append(1)
            return "result"
        assert self.llm._with_retry(fn) == "result"
        assert len(calls) == 1

    def test_retries_on_timeout_then_succeeds(self):
        attempts = []
        def fn():
            attempts.append(1)
            if len(attempts) < 3:
                raise TimeoutError("timeout")
            return "ok"
        result = self.llm._with_retry(fn)
        assert result == "ok"
        assert len(attempts) == 3

    def test_raises_after_max_retries(self):
        def fn():
            raise TimeoutError("always fails")
        with pytest.raises(TimeoutError):
            self.llm._with_retry(fn)

    def test_non_retryable_raises_immediately(self):
        attempts = []
        def fn():
            attempts.append(1)
            raise ValueError("non-retryable")
        with pytest.raises(ValueError):
            self.llm._with_retry(fn)
        assert len(attempts) == 1

    def test_retry_after_header_respected(self):
        exc = Exception("rate limited")
        resp = MagicMock(headers={"Retry-After": "0.0"})
        exc.response = resp
        exc.response.status_code = 429
        attempts = []
        def fn():
            attempts.append(1)
            if len(attempts) < 2:
                raise exc
            return "ok"
        result = self.llm._with_retry(fn)
        assert result == "ok"

    def test_passes_args_and_kwargs(self):
        def fn(a, b, *, c=0):
            return a + b + c
        assert self.llm._with_retry(fn, 1, 2, c=3) == 6


# ── Anthropic adapter wiring ──────────────────────────────────────────────────

class TestAnthropicRetryWiring:
    def test_blocking_complete_uses_with_retry(self):
        from antcrew.models.anthropic_model import AnthropicModel
        with patch("anthropic.Anthropic") as MockClient:
            mock_response = MagicMock()
            mock_response.usage = MagicMock(
                input_tokens=10, output_tokens=5,
                cache_creation_input_tokens=0, cache_read_input_tokens=0,
            )
            mock_response.stop_reason = "end_turn"
            mock_response.content = [MagicMock(text="hello")]
            MockClient.return_value.messages.create.return_value = mock_response

            llm = AnthropicModel.__new__(AnthropicModel)
            llm.model = "claude-sonnet-4-6"
            llm.prompt_caching = False
            llm._client = MockClient.return_value
            llm.max_retries = 2
            llm.retry_delay = 0.0
            llm.retry_jitter = 0.0
            llm.timeout = 30
            llm.on_token = None
            llm.current_agent = "test"

            # Fail once, then succeed
            call_count = []

            def flaky(**kwargs):
                call_count.append(1)
                if len(call_count) == 1:
                    raise TimeoutError("timeout")
                return mock_response

            MockClient.return_value.messages.create = flaky
            result = llm.complete([Message(role="user", content="hi")])
            assert result == "hello"
            assert len(call_count) == 2


# ── OpenAI adapter wiring ─────────────────────────────────────────────────────

class TestOpenAIRetryWiring:
    def _make_llm(self):
        from antcrew.models.openai_model import OpenAIModel
        llm = OpenAIModel.__new__(OpenAIModel)
        llm._model = "gpt-4o"
        llm._is_reasoning = False
        llm.max_retries = 2
        llm.retry_delay = 0.0
        llm.retry_jitter = 0.0
        llm.timeout = 30
        llm.on_token = None
        llm.current_agent = "test"
        mock_client = MagicMock()
        llm._client = mock_client
        return llm, mock_client

    def test_blocking_retries_on_connection_error(self):
        pytest.importorskip("openai")
        llm, mock_client = self._make_llm()
        mock_resp = MagicMock()
        mock_resp.usage = MagicMock(prompt_tokens=10, completion_tokens=5)
        mock_resp.choices = [MagicMock(message=MagicMock(content="answer"))]
        call_count = []
        def flaky(**kwargs):
            call_count.append(1)
            if len(call_count) == 1:
                raise ConnectionError("refused")
            return mock_resp
        mock_client.chat.completions.create = flaky
        result = llm._complete_blocking([{"role": "user", "content": "hi"}], 1024)
        assert result == "answer"
        assert len(call_count) == 2


# ── Groq adapter wiring ───────────────────────────────────────────────────────

class TestGroqRetryWiring:
    def _make_llm(self):
        from antcrew.models.groq_model import GroqModel
        llm = GroqModel.__new__(GroqModel)
        llm.model = "llama3-70b-8192"
        llm.max_retries = 2
        llm.retry_delay = 0.0
        llm.retry_jitter = 0.0
        llm.timeout = 30
        llm.on_token = None
        llm.current_agent = "test"
        mock_client = MagicMock()
        llm._client = mock_client
        return llm, mock_client

    def test_blocking_retries_on_429(self):
        llm, mock_client = self._make_llm()
        mock_resp = MagicMock()
        mock_resp.usage = MagicMock(prompt_tokens=5, completion_tokens=3)
        mock_resp.choices = [MagicMock(message=MagicMock(content="pong"))]
        exc = Exception("rate limited")
        exc.response = MagicMock(status_code=429)
        call_count = []
        def flaky(**kwargs):
            call_count.append(1)
            if len(call_count) == 1:
                raise exc
            return mock_resp
        mock_client.chat.completions.create = flaky
        result = llm._blocking_complete([{"role": "user", "content": "ping"}], 512)
        assert result == "pong"
        assert len(call_count) == 2


# ── Gemini adapter wiring ─────────────────────────────────────────────────────

class TestGeminiRetryWiring:
    def _make_llm(self):
        from antcrew.models.gemini_model import GeminiModel
        llm = GeminiModel.__new__(GeminiModel)
        llm._model = "gemini-1.5-flash"
        llm._api_key = "test-key"
        llm.max_retries = 2
        llm.retry_delay = 0.0
        llm.retry_jitter = 0.0
        llm.timeout = 30
        llm.on_token = None
        llm.current_agent = "test"
        return llm

    def test_blocking_retries_on_http_error(self):
        llm = self._make_llm()
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "candidates": [{"content": {"parts": [{"text": "hello"}]}}],
            "usageMetadata": {"promptTokenCount": 5, "candidatesTokenCount": 3},
        }
        mock_resp.raise_for_status.return_value = None
        call_count = []
        def flaky(*args, **kwargs):
            call_count.append(1)
            if len(call_count) == 1:
                raise TimeoutError("timeout")
            return mock_resp
        with patch("httpx.post", side_effect=flaky):
            body = llm._build_body([Message(role="user", content="hi")], 1024)
            result = llm._blocking_complete(body)
        assert result == "hello"
        assert len(call_count) == 2


# ── TraceLog.prune() ──────────────────────────────────────────────────────────

class TestTraceLogPrune:
    def _make_tl(self, tmp_path) -> TraceLog:
        return TraceLog(tmp_path / "trace.db")

    def _insert_run(self, tl: TraceLog, started_at: str) -> str:
        """Insert a run with an explicit started_at timestamp."""
        import uuid
        run_id = str(uuid.uuid4())
        tl._conn.execute(
            "INSERT INTO runs(id, thread_id, request, team, started_at, status) VALUES (?,?,?,?,?,?)",
            (run_id, "t1", "req", "dev", started_at, "done"),
        )
        tl._conn.commit()
        return run_id

    def test_prune_removes_old_runs(self, tmp_path):
        tl = self._make_tl(tmp_path)
        old_id = self._insert_run(tl, "2020-01-01T00:00:00+00:00")
        new_id = self._insert_run(tl, "2099-01-01T00:00:00+00:00")
        deleted = tl.prune(days=365)
        assert deleted >= 1
        assert tl.get_run(old_id) is None
        assert tl.get_run(new_id) is not None

    def test_prune_cascades_agent_calls(self, tmp_path):
        tl = self._make_tl(tmp_path)
        old_id = self._insert_run(tl, "2020-01-01T00:00:00+00:00")
        tl._conn.execute(
            "INSERT INTO agent_calls(run_id, agent_name, started_at, duration_ms) VALUES (?,?,?,?)",
            (old_id, "pm", "2020-01-01T00:00:01+00:00", 100.0),
        )
        tl._conn.commit()
        tl.prune(days=365)
        calls = tl.get_calls(old_id)
        assert calls == []

    def test_prune_zero_days_deletes_all(self, tmp_path):
        tl = self._make_tl(tmp_path)
        self._insert_run(tl, "2020-01-01T00:00:00+00:00")
        self._insert_run(tl, "2021-01-01T00:00:00+00:00")
        deleted = tl.prune(days=0)
        assert deleted == 2
        assert tl.list_runs() == []

    def test_prune_negative_days_raises(self, tmp_path):
        tl = self._make_tl(tmp_path)
        with pytest.raises(ValueError):
            tl.prune(days=-1)

    def test_prune_returns_count(self, tmp_path):
        tl = self._make_tl(tmp_path)
        self._insert_run(tl, "2020-01-01T00:00:00+00:00")
        self._insert_run(tl, "2020-06-01T00:00:00+00:00")
        self._insert_run(tl, "2099-01-01T00:00:00+00:00")
        assert tl.prune(days=365) == 2

    def test_prune_empty_db_returns_zero(self, tmp_path):
        tl = self._make_tl(tmp_path)
        assert tl.prune(days=30) == 0


# ── antcrew trace DB --prune CLI ─────────────────────────────────────────────

class TestTracePruneCLI:
    def _make_db(self, tmp_path) -> Path:
        tl = TraceLog(tmp_path / "t.db")
        import uuid
        run_id = str(uuid.uuid4())
        tl._conn.execute(
            "INSERT INTO runs(id, thread_id, request, team, started_at, status) VALUES (?,?,?,?,?,?)",
            (run_id, "t1", "req", "dev", "2020-01-01T00:00:00+00:00", "done"),
        )
        tl._conn.commit()
        tl.close()
        return tmp_path / "t.db"

    def test_prune_with_yes_flag(self, tmp_path):
        db = self._make_db(tmp_path)
        result = runner.invoke(app, ["trace", str(db), "--prune", "365", "--yes"])
        assert result.exit_code == 0
        assert "Deleted" in result.output

    def test_prune_missing_db(self, tmp_path):
        result = runner.invoke(app, ["trace", str(tmp_path / "nope.db"), "--prune", "30", "--yes"])
        assert result.exit_code != 0 or "not found" in result.output.lower()


# ── antcrew doctor ────────────────────────────────────────────────────────────

class TestDoctorCmd:
    def test_doctor_runs_without_error(self, monkeypatch):
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        monkeypatch.delenv("GROQ_API_KEY", raising=False)
        monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
        result = runner.invoke(app, ["doctor"])
        assert result.exit_code == 0
        assert "antcrew doctor" in result.output

    def test_doctor_shows_fail_for_missing_anthropic_key(self, monkeypatch):
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        result = runner.invoke(app, ["doctor"])
        assert "FAIL" in result.output

    def test_doctor_shows_ok_for_set_key(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test1234567")
        result = runner.invoke(app, ["doctor"])
        assert "OK" in result.output

    def test_doctor_shows_python_version(self):
        result = runner.invoke(app, ["doctor"])
        import sys
        v = sys.version_info
        assert f"{v.major}.{v.minor}" in result.output

    def test_doctor_warns_no_antcrew_api_key(self, monkeypatch):
        monkeypatch.delenv("ANTCREW_API_KEY", raising=False)
        result = runner.invoke(app, ["doctor"])
        assert "ANTCREW_API_KEY" in result.output

    def test_doctor_passes_all_checks_when_keys_set(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test123456789")
        monkeypatch.setenv("ANTCREW_API_KEY", "secret")
        result = runner.invoke(app, ["doctor"])
        assert result.exit_code == 0

    def test_doctor_no_ping_flag_skips_live_calls(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test123456789")
        with patch("anthropic.Anthropic") as mock_ant:
            runner.invoke(app, ["doctor"])
            mock_ant.assert_not_called()
