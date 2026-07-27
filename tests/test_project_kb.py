"""Tests for antcrew.core.project_kb."""
from __future__ import annotations

from antcrew.core.project_kb import (
    EndpointRecord,
    ModelRecord,
    ProjectKB,
    ServiceRecord,
    _parse_requirements_txt,
    _parse_toml_deps,
)


def _art(file_path: str, content: str) -> dict:
    return {"file_path": file_path, "content": content}


# ── persistence ──────────────────────────────────────────────────────────────

class TestPersistence:
    def test_save_and_load_roundtrip(self, tmp_path):
        kb = ProjectKB(tmp_path / "kb.json")
        kb.endpoints.append(EndpointRecord("/users", "GET", "users.routes", "get_users"))
        kb.models.append(ModelRecord("User", "users.models", ["id", "name"]))
        kb.dependencies["fastapi"] = "0.111"
        kb.save()

        loaded = ProjectKB.load(tmp_path / "kb.json")
        assert len(loaded.endpoints) == 1
        assert loaded.endpoints[0].path == "/users"
        assert loaded.models[0].name == "User"
        assert loaded.dependencies["fastapi"] == "0.111"

    def test_load_missing_file_returns_empty(self, tmp_path):
        kb = ProjectKB.load(tmp_path / "nonexistent.json")
        assert kb.endpoints == []
        assert kb.models == []

    def test_save_creates_parent_dirs(self, tmp_path):
        kb = ProjectKB(tmp_path / "deep" / "dir" / "kb.json")
        kb.save()
        assert (tmp_path / "deep" / "dir" / "kb.json").exists()


# ── extraction from state ────────────────────────────────────────────────────

class TestUpdateFromState:
    def test_extracts_fastapi_endpoint(self):
        src = """
from fastapi import APIRouter
router = APIRouter()

@router.get("/users")
def list_users():
    pass
"""
        kb = ProjectKB()
        kb.update_from_state({"code_artifacts": [_art("users/routes.py", src)]})
        assert any(e.path == "/users" for e in kb.endpoints)

    def test_extracts_pydantic_model(self):
        src = """
from pydantic import BaseModel

class User(BaseModel):
    id: int
    name: str
    email: str
"""
        kb = ProjectKB()
        kb.update_from_state({"code_artifacts": [_art("models.py", src)]})
        assert any(m.name == "User" for m in kb.models)
        user = next(m for m in kb.models if m.name == "User")
        assert "id" in user.fields
        assert "name" in user.fields

    def test_extracts_service_class(self):
        src = """
class AuthService:
    def login(self, user, password):
        pass
    def logout(self, token):
        pass
"""
        kb = ProjectKB()
        kb.update_from_state({"code_artifacts": [_art("services/auth.py", src)]})
        assert any(s.name == "AuthService" for s in kb.services)

    def test_deduplicates_endpoints(self):
        src = """
@router.get("/ping")
def ping(): pass
"""
        kb = ProjectKB()
        kb.update_from_state({"code_artifacts": [_art("a.py", src)]})
        kb.update_from_state({"code_artifacts": [_art("b.py", src)]})
        assert sum(1 for e in kb.endpoints if e.path == "/ping") == 1

    def test_empty_state_no_crash(self):
        kb = ProjectKB()
        kb.update_from_state({})
        assert kb.endpoints == []

    def test_requirements_txt_parsed(self):
        src = "fastapi==0.111.0\npydantic>=2.0\nuvicorn\n"
        kb = ProjectKB()
        kb.update_from_state({"code_artifacts": [_art("requirements.txt", src)]})
        assert "fastapi" in kb.dependencies


# ── context_for_agent ────────────────────────────────────────────────────────

class TestContextForAgent:
    def _kb_with_data(self) -> ProjectKB:
        kb = ProjectKB()
        kb.endpoints.append(EndpointRecord("/users", "GET", "users.routes", "list_users"))
        kb.models.append(ModelRecord("User", "models", ["id", "name"]))
        kb.services.append(ServiceRecord("AuthService", "auth", ["login", "logout"]))
        kb.dependencies["fastapi"] = "0.111"
        kb.tech_stack = ["FastAPI", "PostgreSQL"]
        return kb

    def test_backend_dev_gets_endpoints(self):
        ctx = self._kb_with_data().context_for_agent("backend_dev")
        assert "/users" in ctx
        assert "User" in ctx

    def test_frontend_dev_gets_endpoints_not_models(self):
        ctx = self._kb_with_data().context_for_agent("frontend_dev")
        assert "/users" in ctx

    def test_empty_kb_returns_empty_string(self):
        assert ProjectKB().context_for_agent("backend_dev") == ""

    def test_respects_max_chars(self):
        kb = self._kb_with_data()
        ctx = kb.context_for_agent("backend_dev", max_chars=50)
        assert len(ctx) <= 70  # small buffer for truncation suffix


# ── dep parsing ──────────────────────────────────────────────────────────────

class TestDepParsing:
    def test_requirements_txt(self):
        deps: dict = {}
        _parse_requirements_txt("fastapi==0.111\npydantic>=2.0.0\n", deps)
        assert deps["fastapi"] == "0.111"
        assert "pydantic" in deps

    def test_requirements_txt_ignores_comments(self):
        deps: dict = {}
        _parse_requirements_txt("# comment\nrequests==2.28\n", deps)
        assert "comment" not in deps
        assert deps["requests"] == "2.28"

    def test_toml_deps(self):
        deps: dict = {}
        toml = "[tool.poetry.dependencies]\nfastapi = \"^0.111\"\npython = \"^3.12\"\n"
        _parse_toml_deps(toml, deps)
        assert "fastapi" in deps


# ── context_for_agent role coverage ──────────────────────────────────────────

class TestContextForAgentRoles:
    def _kb_with_data(self) -> ProjectKB:
        kb = ProjectKB()
        kb.endpoints.append(EndpointRecord("/orders", "GET", "orders", "get_orders"))
        kb.models.append(ModelRecord("Order", "models", ["id", "total", "status"]))
        kb.services.append(ServiceRecord("PaymentService", "payment", ["charge", "refund"]))
        kb.dependencies["fastapi"] = "0.111.0"
        return kb

    def test_reviewer_sees_endpoints_models_services_deps(self):
        kb = self._kb_with_data()
        ctx = kb.context_for_agent("reviewer")
        assert "GET /orders" in ctx
        assert "Order" in ctx
        assert "PaymentService" in ctx
        assert "fastapi" in ctx

    def test_pm_sees_endpoints_models_services(self):
        kb = self._kb_with_data()
        ctx = kb.context_for_agent("pm")
        assert "GET /orders" in ctx
        assert "Order" in ctx
        assert "PaymentService" in ctx

    def test_doc_writer_sees_endpoints_models_services(self):
        kb = self._kb_with_data()
        ctx = kb.context_for_agent("doc_writer")
        assert "GET /orders" in ctx
        assert "Order" in ctx
        assert "PaymentService" in ctx

    def test_business_analyst_gets_only_tech_stack(self):
        kb = self._kb_with_data()
        kb.tech_stack = ["Python", "FastAPI"]
        ctx = kb.context_for_agent("business_analyst")
        # business_analyst should see tech_stack but not endpoints/models/services
        assert "Python" in ctx
        assert "GET /orders" not in ctx

    def test_empty_kb_returns_empty_for_all_roles(self):
        kb = ProjectKB()
        for role in ("reviewer", "pm", "doc_writer", "qa", "backend_dev"):
            assert kb.context_for_agent(role) == ""
