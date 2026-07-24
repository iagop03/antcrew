# shim — canonical implementation in antcrew_engine.engine.operator
from antcrew_engine.engine.operator import *

# antcrew-engine >= 0.3.0 renamed OperatorError → EngineLoopError; support both
try:
    from antcrew_engine.engine.operator import EngineLoopError  # noqa: F401
except ImportError:
    from antcrew_engine.engine.operator import (
        OperatorError as EngineLoopError,  # type: ignore[no-redef] # noqa: F401
    )

OperatorError = EngineLoopError  # noqa: F811
