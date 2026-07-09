from antcrew.teams.dev_team import DevTeam
from antcrew.teams.research_team import ResearchTeam
from antcrew.teams.content_team import ContentTeam
from antcrew.teams.fullstack_team import FullStackTeam
from antcrew.teams.async_teams import (
    AsyncDevTeam, AsyncFullStackTeam, AsyncResearchTeam, AsyncContentTeam,
    AsyncCustomTeam, AsyncFeatureTeam, AsyncRouter,
)
from antcrew.core.supervisor import Supervisor, ParallelGroup, parallel
from antcrew.core.tools import (
    BaseTool, ToolResult,
    WebSearchTool, CodeExecutorTool, ReadFileTool, WriteFileTool, ListDirTool,
)
from antcrew.agents.feature_agent import FeatureAgent, FeatureTeam
from antcrew.agents.direct_agent import DirectAgent
from antcrew.core.feedback import FeedbackRunner, FeedbackLoop, FeedbackResult
from antcrew.core.router import Router, RouteClassifier, LLMClassifier, RuleClassifier
from antcrew.core.pipeline import Pipeline
from antcrew.core.channel import BaseChannel
from antcrew.core.artifacts import (
    DevOpsArtifact, DocumentationArtifact,
    PRD, CodeArtifact, TestArtifact, CodeReview, ResearchDocument,
    ContentPiece, CodebaseAnalysis, Ticket,
    ArtifactContract, ContractError, ARTIFACT_REGISTRY, resolve_artifact_schema,
    coerce_model, coerce_list,
)
from antcrew.agents.coherence import CoherenceAgent
from antcrew.core.project_kb import ProjectKB
from antcrew.core.task_classifier import TaskType, classify_task, MinimalPipeline
from antcrew.agents.devops import DevOpsAgent
from antcrew.agents.doc_writer import DocWriterAgent
from antcrew.agents.codebase_scanner import CodebaseScannerAgent
from antcrew.agents.sprint_planner import SprintPlannerAgent
from antcrew.integrations.confluence import ConfluenceIntegration
from antcrew.models.simulated import SimulatedLLM
from antcrew.models.gemini_model import GeminiModel
from antcrew.models.fallback import FallbackLLM
try:
    from antcrew.models.openai_model import OpenAIModel
    from antcrew.models.azure_openai_model import AzureOpenAIModel
except ImportError:
    OpenAIModel = None       # type: ignore[assignment,misc]
    AzureOpenAIModel = None  # type: ignore[assignment,misc]
from antcrew.models.cache import LLMCache, FileLLMCache
from antcrew.sandbox import DockerRunner, LocalRunner, SandboxRunner
from antcrew.sandbox import RunResult as SandboxRunResult
from antcrew.core.run_result import RunResult
from antcrew.checkpointers import SqliteSaver
from antcrew.core.exceptions import CostLimitExceeded
from antcrew.core.events import bus, Event, EventBus, capture, new_run_id
from antcrew.core.gates import (
    BaseGate, GateResult, GateError,
    NonEmptyGate, PythonSyntaxGate, JsonGate, SchemaGate,
    AllGate, AnyGate, parse_gate,
)
from antcrew.trace import TraceLog
from antcrew.flow import load_flow, validate_flow, format_flow
from antcrew.project import Project
from antcrew.config import load_context, TeamContext, build_llm, build_runner
from antcrew.integrations.console import ConsoleChannel
from antcrew.integrations.jira import JiraIntegration
from antcrew.integrations.github import GitHubIntegration
try:
    from antcrew.integrations.telegram.integration import (
        TelegramChannel, TelegramIntegration, AgentBotConfig,
    )
except ImportError:
    TelegramChannel = None  # type: ignore[assignment,misc]
    TelegramIntegration = None  # type: ignore[assignment,misc]
    AgentBotConfig = None  # type: ignore[assignment,misc]
from antcrew.integrations.slack import SlackChannel
from antcrew.utils.persistence import save_state, load_state
from antcrew.memory.store import BaseMemory, InMemoryMemory, MemoryResult
from antcrew.memory.chroma import ChromaMemory
from antcrew.memory.repo_index import RepoIndex
from antcrew.eval import AgentScore, EvalCase, EvalReport, EvalRunner, JudgeResult
from antcrew.presets import AgentPreset, get_preset, CONCISE, STRICT, VERBOSE, CAREFUL
from antcrew.agents.template_agent import TemplateAgent, load_template_agent, register_transform
from antcrew.teams.custom_team import CustomTeam
from antcrew.core.operators import (
    BaseOperator, RenameOp, CopyOp, DropOp, SetOp, MapOp, MergeOp, build_operator,
)
from antcrew.core.validation import validate_agent_dag
from antcrew.testing import SequencedLLM
from antcrew.engine import (
    Artifact, ArtifactId, ArtifactKind, ArtifactDelta, EMPTY_DELTA,
    ArtifactStore, MemoryStore, FilesystemStore, MultiRepoStore,
    Condition, ConditionId, DesiredProjectState, Constraints, Goal,
    ProjectState,
    CapabilityDescriptor, CapabilityResult, Executor,
    ValidatorResult, Validator,
    CapabilityRegistry,
    EventLog,
    Operator, OperatorError,
    CapabilitySelector, CheapestFirst, FirstMatch, MostProductive, PrioritySelector,
    EventBusBridge,
)
from antcrew.capabilities import BugFixer, CodeRegenerator, DependencyInstaller, DocGenerator, HitlReviewer, ReviewFixer

__all__ = [
    # Teams
    "DevTeam",
    "FullStackTeam",
    "ResearchTeam",
    "ContentTeam",
    "CustomTeam",
    # Async teams
    "AsyncDevTeam",
    "AsyncFullStackTeam",
    "AsyncResearchTeam",
    "AsyncContentTeam",
    "AsyncCustomTeam",
    "AsyncFeatureTeam",
    "AsyncRouter",
    # Core
    "Supervisor",
    "ParallelGroup",
    "parallel",
    "Pipeline",
    # Tools
    "BaseTool",
    "ToolResult",
    "WebSearchTool",
    "CodeExecutorTool",
    "ReadFileTool",
    "WriteFileTool",
    "ListDirTool",
    "BaseChannel",
    "DevOpsArtifact",
    "DocumentationArtifact",
    # Agents (commonly used standalone)
    "DevOpsAgent",
    "DocWriterAgent",
    "CodebaseScannerAgent",
    "SprintPlannerAgent",
    "FeatureAgent",
    "FeatureTeam",
    "DirectAgent",
    # Router
    "Router",
    "RouteClassifier",
    "LLMClassifier",
    "RuleClassifier",
    # Feedback loop
    "FeedbackRunner",
    "FeedbackLoop",
    "FeedbackResult",
    # Artifact contracts
    "PRD",
    "CodeArtifact",
    "TestArtifact",
    "CodeReview",
    "ResearchDocument",
    "ContentPiece",
    "CodebaseAnalysis",
    "Ticket",
    "ArtifactContract",
    "ContractError",
    "ARTIFACT_REGISTRY",
    "resolve_artifact_schema",
    "coerce_model",
    "coerce_list",
    # Models
    "SimulatedLLM",
    "GeminiModel",
    "OpenAIModel",
    "AzureOpenAIModel",
    "FallbackLLM",
    "LLMCache",
    "FileLLMCache",
    # Project & config
    "Project",
    "load_context",
    "TeamContext",
    "build_llm",
    "build_runner",
    # Sandbox
    "SandboxRunner",
    "LocalRunner",
    "DockerRunner",
    "RunResult",
    "SandboxRunResult",
    "SqliteSaver",
    "CostLimitExceeded",
    # Gates
    "BaseGate",
    "GateResult",
    "GateError",
    "NonEmptyGate",
    "PythonSyntaxGate",
    "JsonGate",
    "SchemaGate",
    "AllGate",
    "AnyGate",
    "parse_gate",
    "TraceLog",
    # Flow
    "load_flow",
    "validate_flow",
    "format_flow",
    # Channels
    "ConsoleChannel",
    "SlackChannel",
    "TelegramChannel",
    "TelegramIntegration",
    "AgentBotConfig",
    # External integrations
    "JiraIntegration",
    "GitHubIntegration",
    "ConfluenceIntegration",
    # Persistence
    "save_state",
    "load_state",
    # Memory
    "BaseMemory",
    "ChromaMemory",
    "InMemoryMemory",
    "MemoryResult",
    "RepoIndex",
    # Eval
    "AgentScore",
    "EvalCase",
    "EvalReport",
    "EvalRunner",
    "JudgeResult",
    # Presets
    "AgentPreset",
    "get_preset",
    "CONCISE",
    "STRICT",
    "VERBOSE",
    "CAREFUL",
    # Template agents
    "TemplateAgent",
    "load_template_agent",
    "register_transform",
    # Operators
    "BaseOperator",
    "RenameOp",
    "CopyOp",
    "DropOp",
    "SetOp",
    "MapOp",
    "MergeOp",
    "build_operator",
    # Validation
    "validate_agent_dag",
    # Testing
    "SequencedLLM",
    # Engine
    "BugFixer",
    "CodeRegenerator",
    "DependencyInstaller",
    "DocGenerator",
    "HitlReviewer",
    "ReviewFixer",
    "Artifact", "ArtifactId", "ArtifactKind", "ArtifactDelta", "EMPTY_DELTA",
    "ArtifactStore", "MemoryStore", "FilesystemStore", "MultiRepoStore",
    "Condition", "ConditionId", "DesiredProjectState", "Constraints", "Goal",
    "ProjectState",
    "CapabilityDescriptor", "CapabilityResult", "Executor",
    "ValidatorResult", "Validator",
    "CapabilityRegistry",
    "EventLog",
    "Operator", "OperatorError",
    "CapabilitySelector", "CheapestFirst", "FirstMatch", "MostProductive", "PrioritySelector",
    "EventBusBridge",
    # Events
    "bus",
    "Event",
    "EventBus",
    "capture",
    "new_run_id",
    # Advanced / task-level
    "CoherenceAgent",
    "ProjectKB",
    "TaskType",
    "classify_task",
    "MinimalPipeline",
]
__version__ = "0.33.0"
