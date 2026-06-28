"""AntCrew CLI — run multi-agent pipelines from the terminal."""
from __future__ import annotations

# Re-export CLI singletons so external code can do `from antcrew.cli import app`.
from antcrew.cli._app import (  # noqa: F401
    app, _flow_app, _project_app, console, _TEAM_CHOICES, _MODEL_HELP,
)

# Re-export shared helpers used by tests and other consumers.
from antcrew.cli._shared import (  # noqa: F401
    _build_team, _print_state, _print_state_raw, _print_test_results, _print_usage,
)
from antcrew.cli._run_helpers import (  # noqa: F401
    _print_dry_run, _run_with_stream, _save_outputs_to_dir, _run_repl,
)
from antcrew.cli.history_cmd import _parse_since  # noqa: F401

# Import submodules — each registers its @app.command() / @_*_app.command()
# decorators on the shared Typer instances above.
from antcrew.cli import run_cmd        # noqa: F401
from antcrew.cli import init_cmd       # noqa: F401
from antcrew.cli import setup_cmds     # noqa: F401
from antcrew.cli import eval_cmds      # noqa: F401
from antcrew.cli import inspect_cmds   # noqa: F401
from antcrew.cli import project_cmds   # noqa: F401
from antcrew.cli import flow_cmds      # noqa: F401
from antcrew.cli import trace_cmd      # noqa: F401
from antcrew.cli import publish_cmd    # noqa: F401
from antcrew.cli import ops_cmds       # noqa: F401
from antcrew.cli import graph_cmd      # noqa: F401
from antcrew.cli import history_cmd    # noqa: F401
from antcrew.cli import validate_cmd   # noqa: F401
from antcrew.cli import writeback_cmd  # noqa: F401
from antcrew.cli import scan_cmd       # noqa: F401

if __name__ == "__main__":
    app()
