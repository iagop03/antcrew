from antcrew.agents.codebase_scanner import CodebaseScannerAgent
from antcrew.agents.coherence import CoherenceAgent
from antcrew.agents.conflict import ConflictAgent
from antcrew.agents.cost import CostAgent
from antcrew.agents.devops import DevOpsAgent
from antcrew.agents.direct_agent import DirectAgent
from antcrew.agents.discovery import DiscoveryAgent
from antcrew.agents.doc_writer import DocWriterAgent
from antcrew.agents.feature_agent import FeatureAgent, FeatureTeam
from antcrew.agents.retro import RetroAgent
from antcrew.agents.security import SecurityAgent
from antcrew.agents.sprint_planner import SprintPlannerAgent
from antcrew.core.artifacts import (
    ARTIFACT_REGISTRY,
    PRD,
    ArtifactContract,
    CodeArtifact,
    CodebaseAnalysis,
    CodeReview,
    ConflictItem,
    ConflictReport,
    ContentPiece,
    ContractError,
    DesignTokens,
    DevOpsArtifact,
    DiscoveryContext,
    DiscoveryQA,
    DocumentationArtifact,
    ResearchDocument,
    RetroReport,
    Screen,
    SecurityFinding,
    SecurityReport,
    TestArtifact,
    Ticket,
    UIDesignSpec,
    coerce_list,
    coerce_model,
    resolve_artifact_schema,
)
from antcrew.core.channel import BaseChannel
from antcrew.core.feedback import FeedbackLoop, FeedbackResult, FeedbackRunner
from antcrew.core.pipeline import Pipeline
from antcrew.core.project_kb import ProjectKB
from antcrew.core.router import LLMClassifier, RouteClassifier, Router, RuleClassifier
from antcrew.core.supervisor import FanOut, ParallelGroup, Supervisor, fan_out, parallel
from antcrew.core.task_classifier import MinimalPipeline, TaskType, classify_task
from antcrew.core.tools import (
    BaseTool,
    CodeExecutorTool,
    ListDirTool,
    ReadFileTool,
    ToolResult,
    WebSearchTool,
    WriteFileTool,
)
from antcrew.integrations.confluence import ConfluenceIntegration
from antcrew.models.comparison import ComparisonLLM
from antcrew.models.fallback import FallbackLLM
from antcrew.models.gemini_model import GeminiModel
from antcrew.models.simulated import SimulatedLLM
from antcrew.teams.async_teams import (
    AsyncContentTeam,
    AsyncCustomTeam,
    AsyncDevTeam,
    AsyncFeatureTeam,
    AsyncFullStackTeam,
    AsyncResearchTeam,
    AsyncRouter,
)
from antcrew.teams.content_team import ContentTeam
from antcrew.teams.dev_team import DevTeam
from antcrew.teams.fullstack_team import FullStackTeam
from antcrew.teams.research_team import ResearchTeam

try:
    from antcrew.models.azure_openai_model import AzureOpenAIModel
    from antcrew.models.openai_model import OpenAIModel
except ImportError:
    OpenAIModel = None  # type: ignore[assignment,misc]
    AzureOpenAIModel = None  # type: ignore[assignment,misc]
from antcrew.checkpointers import SqliteSaver
from antcrew.config import TeamContext, build_llm, build_runner, load_context
from antcrew.core.artifacts import ArtifactHistory, ArtifactVersion
from antcrew.core.events import Event, EventBus, bus, capture, new_run_id
from antcrew.core.exceptions import CostLimitExceeded
from antcrew.core.gates import (
    AllGate,
    AnyGate,
    BaseGate,
    GateError,
    GateResult,
    JsonGate,
    NonEmptyGate,
    PythonSyntaxGate,
    SchemaGate,
    parse_gate,
)
from antcrew.core.group_chat import GroupChat, GroupChatResult
from antcrew.core.run_result import RunResult
from antcrew.core.workflow_builder import Workflow, WorkflowBuilder
from antcrew.flow import format_flow, load_flow, validate_flow
from antcrew.integrations.console import ConsoleChannel
from antcrew.integrations.github import GitHubIntegration
from antcrew.integrations.google_cloud import BigQueryTool, GCSTool
from antcrew.integrations.jira import JiraIntegration
from antcrew.models.cache import FileLLMCache, LLMCache
from antcrew.project import Project
from antcrew.quickstart import QuickStart
from antcrew.sandbox import DockerRunner, LocalRunner, SandboxRunner
from antcrew.sandbox import RunResult as SandboxRunResult
from antcrew.trace import ReplayError, TraceLog

try:
    from antcrew.integrations.telegram.integration import (
        AgentBotConfig,
        TelegramChannel,
        TelegramIntegration,
    )
except ImportError:
    TelegramChannel = None  # type: ignore[assignment,misc]
    TelegramIntegration = None  # type: ignore[assignment,misc]
    AgentBotConfig = None  # type: ignore[assignment,misc]
from antcrew.agents.registry import instantiate_agent
from antcrew.agents.template_agent import TemplateAgent, load_template_agent, register_transform
from antcrew.capabilities import (
    Architect,
    BugFixer,
    CodeGenerator,
    CodeRegenerator,
    CodeReviewer,
    DependencyInstaller,
    DocGenerator,
    HitlReviewer,
    ReviewFixer,
    SpecExtractor,
    TaskPlanner,
    TeamExecutor,
    TestGenerator,
    TestRunner,
)
from antcrew.core.operators import (
    BaseOperator,
    CopyOp,
    DropOp,
    MapOp,
    MergeOp,
    RenameOp,
    SetOp,
    build_operator,
)
from antcrew.core.validation import validate_agent_dag
from antcrew.engine import (
    EMPTY_DELTA,
    Artifact,
    ArtifactDelta,
    ArtifactId,
    ArtifactKind,
    ArtifactStore,
    CapabilityDescriptor,
    CapabilityRegistry,
    CapabilityResult,
    CapabilitySelector,
    CheapestFirst,
    Condition,
    ConditionId,
    Constraints,
    DesiredProjectState,
    EngineLoop,
    EngineLoopError,
    EventBusBridge,
    EventLog,
    Executor,
    FilesystemStore,
    FirstMatch,
    Goal,
    MemoryStore,
    MostProductive,
    MultiRepoStore,
    PrioritySelector,
    ProjectState,
    Validator,
    ValidatorResult,
)
from antcrew.eval import AgentScore, EvalCase, EvalReport, EvalRunner, JudgeResult
from antcrew.integrations.slack import SlackChannel
from antcrew.memory.chroma import ChromaMemory
from antcrew.memory.repo_index import RepoIndex
from antcrew.memory.store import BaseMemory, InMemoryMemory, MemoryResult, RunSnapshot
from antcrew.presets import CAREFUL, CONCISE, STRICT, VERBOSE, AgentPreset, get_preset
from antcrew.teams.base import AGENT_ARTIFACT
from antcrew.teams.custom_team import CustomTeam
from antcrew.testing import SequencedLLM
from antcrew.utils.persistence import load_state, save_state

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
    "FanOut",
    "fan_out",
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
    "ConflictAgent",
    "RetroAgent",
    "CostAgent",
    "SecurityAgent",
    "DiscoveryAgent",
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
    "UIDesignSpec",
    "Screen",
    "DesignTokens",
    "ConflictItem",
    "ConflictReport",
    "RetroReport",
    "SecurityFinding",
    "SecurityReport",
    "DiscoveryQA",
    "DiscoveryContext",
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
    "ComparisonLLM",
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
    "ReplayError",
    # Quick-start
    "QuickStart",
    # Workflow builder
    "WorkflowBuilder",
    "Workflow",
    # Group chat
    "GroupChat",
    "GroupChatResult",
    # Artifact history
    "ArtifactHistory",
    "ArtifactVersion",
    # Google Cloud tools
    "BigQueryTool",
    "GCSTool",
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
    "RunSnapshot",
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
    # Registry
    "instantiate_agent",
    "AGENT_ARTIFACT",
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
    # Engine capabilities
    "Architect",
    "BugFixer",
    "CodeGenerator",
    "CodeRegenerator",
    "CodeReviewer",
    "DependencyInstaller",
    "DocGenerator",
    "HitlReviewer",
    "ReviewFixer",
    "SpecExtractor",
    "TaskPlanner",
    "TeamExecutor",
    "TestGenerator",
    "TestRunner",
    "Artifact",
    "ArtifactId",
    "ArtifactKind",
    "ArtifactDelta",
    "EMPTY_DELTA",
    "ArtifactStore",
    "MemoryStore",
    "FilesystemStore",
    "MultiRepoStore",
    "Condition",
    "ConditionId",
    "DesiredProjectState",
    "Constraints",
    "Goal",
    "ProjectState",
    "CapabilityDescriptor",
    "CapabilityResult",
    "Executor",
    "ValidatorResult",
    "Validator",
    "CapabilityRegistry",
    "EventLog",
    "EngineLoop",
    "EngineLoopError",
    "CapabilitySelector",
    "CheapestFirst",
    "FirstMatch",
    "MostProductive",
    "PrioritySelector",
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
try:
    from importlib.metadata import version as _v

    __version__: str = _v("antcrew")
except Exception:
    __version__ = "unknown"
