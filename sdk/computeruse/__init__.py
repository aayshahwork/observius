"""
ComputerUse SDK - One API to automate any web workflow

Example:
    from computeruse import ComputerUse

    cu = ComputerUse()
    result = cu.run_task(
        url="https://example.com",
        task="Extract the page title",
        output_schema={"title": "str"}
    )

    print(result.result["title"])
"""

from computeruse.client import ComputerUse
from computeruse.exceptions import (
    APIError,
    AuthenticationError,
    BrowserError,
    ComputerUseError,
    ComputerUseSDKError,
    NetworkError,
    RateLimitError,
    RetryExhaustedError,
    ServiceUnavailableError,
    SessionError,
    TaskExecutionError,
    TaskTimeoutError,
    TimeoutError,
    ValidationError,
)
from computeruse.models import ActionType, StepData, TaskConfig, TaskResult

from computeruse.cost import COST_PER_M_INPUT, COST_PER_M_OUTPUT, calculate_cost_cents, calculate_cost_from_steps
from computeruse.error_classifier import ClassifiedError, ErrorCategory, classify_error, classify_error_message
from computeruse.replay_generator import ReplayGenerator
from computeruse.retry_policy import MAX_DELAY_SECONDS, RETRIABLE_CATEGORIES, RetryDecision, should_retry_task
from computeruse.stuck_detector import StuckDetector, StuckSignal
from computeruse.track import TrackConfig, TrackedPage, track
from computeruse.tracker import PokantTracker, TrackerConfig, create_tracker
from computeruse.wrap import WrappedAgent, WrapConfig, wrap
from computeruse.models import CompiledStep, CompiledWorkflow

# Optional dependencies — these require extras that may not be installed
# (e.g. stagehand, pyautogui/mss/pillow, or modules not yet committed).
try:
    from computeruse.stagehand import StagehandConfig, TrackedStagehand, observe_stagehand
except ImportError:
    StagehandConfig = None  # type: ignore[assignment,misc]
    TrackedStagehand = None  # type: ignore[assignment,misc]
    observe_stagehand = None  # type: ignore[assignment]

try:
    from computeruse.alerts import AlertConfig, AlertEmitter
except ImportError:
    AlertConfig = None  # type: ignore[assignment,misc]
    AlertEmitter = None  # type: ignore[assignment,misc]

try:
    from computeruse.analyzer import AnalysisConfig, AnalysisFinding, HistoryAnalyzer, LLMAnalyzer, RuleAnalyzer, RunAnalysis, RunAnalyzer
except ImportError:
    AnalysisConfig = None  # type: ignore[assignment,misc]
    AnalysisFinding = None  # type: ignore[assignment,misc]
    HistoryAnalyzer = None  # type: ignore[assignment,misc]
    LLMAnalyzer = None  # type: ignore[assignment,misc]
    RuleAnalyzer = None  # type: ignore[assignment,misc]
    RunAnalysis = None  # type: ignore[assignment,misc]
    RunAnalyzer = None  # type: ignore[assignment,misc]

try:
    from computeruse.desktop import mss_screenshot_fn, pillow_screenshot_fn, pyautogui_screenshot_fn
except ImportError:
    mss_screenshot_fn = None  # type: ignore[assignment]
    pillow_screenshot_fn = None  # type: ignore[assignment]
    pyautogui_screenshot_fn = None  # type: ignore[assignment]

# Person B modules — lazy imports so the package works even if these
# files haven't been committed yet (fresh clone without B's branch).
try:
    from computeruse.budget import BudgetExceededError, BudgetMonitor
except ImportError:
    BudgetMonitor = None  # type: ignore[assignment,misc]
    BudgetExceededError = None  # type: ignore[assignment,misc]

try:
    from computeruse.action_verifier import ActionVerifier, VerificationResult
except ImportError:
    ActionVerifier = None  # type: ignore[assignment,misc]
    VerificationResult = None  # type: ignore[assignment,misc]

try:
    from computeruse.step_enrichment import extract_selectors, infer_intent_from_step
except ImportError:
    extract_selectors = None  # type: ignore[assignment]
    infer_intent_from_step = None  # type: ignore[assignment]

try:
    from computeruse.compiler import CompilationError, WorkflowCompiler
except ImportError:
    WorkflowCompiler = None  # type: ignore[assignment,misc]
    CompilationError = None  # type: ignore[assignment,misc]

try:
    from computeruse.replay_executor import ReplayConfig, ReplayExecutor, ReplayResult, ReplayStepError
except ImportError:
    ReplayExecutor = None  # type: ignore[assignment,misc]
    ReplayConfig = None  # type: ignore[assignment,misc]
    ReplayResult = None  # type: ignore[assignment,misc]
    ReplayStepError = None  # type: ignore[assignment,misc]

try:
    from computeruse.failure_analyzer import FailureAnalyzer, FailureCategory, FailureDiagnosis
except ImportError:
    FailureAnalyzer = None  # type: ignore[assignment,misc]
    FailureCategory = None  # type: ignore[assignment,misc]
    FailureDiagnosis = None  # type: ignore[assignment,misc]

try:
    from computeruse.recovery_router import RecoveryPlan, RecoveryRouter
except ImportError:
    RecoveryRouter = None  # type: ignore[assignment,misc]
    RecoveryPlan = None  # type: ignore[assignment,misc]

try:
    from computeruse.retry_memory import AttemptRecord, RetryMemory
except ImportError:
    RetryMemory = None  # type: ignore[assignment,misc]
    AttemptRecord = None  # type: ignore[assignment,misc]

__version__ = "0.2.0"

__all__ = [
    # Client
    "ComputerUse",
    # Models
    "TaskConfig",
    "TaskResult",
    # Exceptions (primary names)
    "ComputerUseSDKError",
    "TaskExecutionError",
    "BrowserError",
    "ValidationError",
    "AuthenticationError",
    "TaskTimeoutError",
    "RateLimitError",
    "NetworkError",
    "ServiceUnavailableError",
    "RetryExhaustedError",
    "SessionError",
    "APIError",
    # Backward-compatible aliases
    "ComputerUseError",
    "TimeoutError",
    # Reliability features
    "ActionType",
    "StepData",
    "ErrorCategory",
    "ClassifiedError",
    "classify_error",
    "classify_error_message",
    "RetryDecision",
    "should_retry_task",
    "RETRIABLE_CATEGORIES",
    "MAX_DELAY_SECONDS",
    "StuckDetector",
    "StuckSignal",
    "ReplayGenerator",
    "calculate_cost_cents",
    "calculate_cost_from_steps",
    "COST_PER_M_INPUT",
    "COST_PER_M_OUTPUT",
    # Tracking
    "track",
    "TrackedPage",
    "TrackConfig",
    # Generic tracker
    "PokantTracker",
    "TrackerConfig",
    "create_tracker",
    # Wrapper
    "wrap",
    "WrappedAgent",
    "WrapConfig",
    # Stagehand
    "observe_stagehand",
    "TrackedStagehand",
    "StagehandConfig",
    # Alerts
    "AlertConfig",
    "AlertEmitter",
    # Analysis
    "AnalysisFinding",
    "RunAnalysis",
    "AnalysisConfig",
    "RuleAnalyzer",
    "HistoryAnalyzer",
    "LLMAnalyzer",
    "RunAnalyzer",
    # Desktop helpers
    "pyautogui_screenshot_fn",
    "pillow_screenshot_fn",
    "mss_screenshot_fn",
    # Budget
    "BudgetMonitor",
    "BudgetExceededError",
    # Action verification
    "ActionVerifier",
    "VerificationResult",
    # Step enrichment
    "extract_selectors",
    "infer_intent_from_step",
    # Compiled workflows
    "CompiledStep",
    "CompiledWorkflow",
    "WorkflowCompiler",
    "CompilationError",
    # Replay executor
    "ReplayExecutor",
    "ReplayConfig",
    "ReplayResult",
    "ReplayStepError",
    # Adaptive retry (AR3)
    "FailureAnalyzer",
    "FailureCategory",
    "FailureDiagnosis",
    "RecoveryRouter",
    "RecoveryPlan",
    "RetryMemory",
    "AttemptRecord",
    # Metadata
    "__version__",
]
