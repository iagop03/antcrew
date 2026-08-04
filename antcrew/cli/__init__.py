"""AntCrew CLI — run multi-agent pipelines from the terminal."""
from __future__ import annotations

# Import submodules — each registers its @app.command() / @_*_app.command()
# decorators on the shared Typer instances above.
from antcrew.cli import (
    configure_cmd,  # noqa: F401
    cost_cmd,  # noqa: F401
    dag_cmd,  # noqa: F401
    discover_cmd,  # noqa: F401
    doctor_cmd,  # noqa: F401
    engine_cmd,  # noqa: F401
    eval_cmds,  # noqa: F401
    flow_cmds,  # noqa: F401
    graph_cmd,  # noqa: F401
    history_cmd,  # noqa: F401
    init_cmd,  # noqa: F401
    inspect_cmds,  # noqa: F401
    ops_cmds,  # noqa: F401
    project_cmds,  # noqa: F401
    publish_cmd,  # noqa: F401
    review_cmd,  # noqa: F401
    run_cmd,  # noqa: F401
    scan_cmd,  # noqa: F401
    serve_cmd,  # noqa: F401
    setup_cmds,  # noqa: F401
    sprint_cmd,  # noqa: F401
    status_cmd,  # noqa: F401
    template_cmd,  # noqa: F401
    trace_cmd,  # noqa: F401
    validate_cmd,  # noqa: F401
    writeback_cmd,  # noqa: F401
)

# Re-export CLI singletons so external code can do `from antcrew.cli import app`.
from antcrew.cli._app import (  # noqa: F401
    _MODEL_HELP,
    _TEAM_CHOICES,
    _flow_app,
    _project_app,
    app,
    console,
)
from antcrew.cli._run_helpers import (  # noqa: F401
    _print_dry_run,
    _run_repl,
    _run_with_stream,
    _save_outputs_to_dir,
)

# Re-export shared helpers used by tests and other consumers.
from antcrew.cli._shared import (  # noqa: F401
    _build_team,
    _print_state,
    _print_state_raw,
    _print_test_results,
    _print_usage,
)

# Auto-apply .antcrew/config.yaml settings to env before any command runs.
from antcrew.cli.configure_cmd import apply_config_to_env as _apply_cfg
from antcrew.cli.history_cmd import _parse_since  # noqa: F401

_apply_cfg()

if __name__ == "__main__":
    app()
