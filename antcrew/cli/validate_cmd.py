"""Validate command for antcrew CLI."""
from __future__ import annotations

import json
from pathlib import Path

import typer

from antcrew.cli._app import app, console


@app.command(name="validate")
def validate_cmd(
    config: Path = typer.Argument(..., help="Path to agentteam.yaml or agentteam.json"),
    strict: bool = typer.Option(
        False, "--strict",
        help="Exit 1 even on warnings (not just errors).",
    ),
) -> None:
    """Validate a team YAML/JSON config without running it.

    \b
    Checks:
      • YAML / JSON syntax
      • Required fields (name, system_prompt) for every agent step
      • Agent instantiation using SimulatedLLM (no API calls)
      • Step references (output_key used in {interpolation} later in the pipeline)

    \b
    Examples:
        antcrew validate team.yaml
        antcrew validate team.yaml --strict
    """
    import yaml as _yaml

    from antcrew.models.simulated import SimulatedLLM as _Sim

    errors: list[str] = []
    warnings: list[str] = []

    # ── 1. Parse file ─────────────────────────────────────────────────────────
    if not config.exists():
        console.print(f"[red]✗[/] File not found: [cyan]{config}[/]")
        raise typer.Exit(1)

    try:
        text = config.read_text(encoding="utf-8")
        if config.suffix.lower() == ".json":
            cfg = json.loads(text)
        else:
            cfg = _yaml.safe_load(text)
        if not isinstance(cfg, dict):
            raise ValueError("Top-level value must be a YAML mapping / JSON object.")
    except Exception as exc:
        console.print(f"[red]✗[/] Parse error: {exc}")
        raise typer.Exit(1)

    console.print(f"[green]✓[/] File parsed: [cyan]{config}[/]")

    # ── 2. Detect team type ────────────────────────────────────────────────────
    team_type = cfg.get("team", "custom")
    model_name = cfg.get("model", "(not set)")
    console.print(f"[green]✓[/] Team type: [cyan]{team_type}[/]  model: [cyan]{model_name}[/]")

    if team_type != "custom":
        # For built-in teams, just try load_context with SimulatedLLM substitution.
        # We can't fully validate without the model, but we can check the YAML loads.
        console.print(
            f"[dim]  (deep validation for '{team_type}' teams is not supported yet; "
            "YAML structure looks OK)[/dim]"
        )
        console.print("\n[bold green]✓ Config looks valid[/]\n")
        return

    # ── 3. CustomTeam step-by-step validation ─────────────────────────────────
    raw_steps = cfg.get("steps")
    if not raw_steps:
        console.print("[red]✗[/] 'steps:' key is missing or empty.")
        raise typer.Exit(1)

    from rich.table import Table

    tbl = Table(show_header=True, header_style="bold", box=None, show_edge=False)
    tbl.add_column("#", style="dim", width=3)
    tbl.add_column("Name", style="cyan")
    tbl.add_column("Type", style="dim")
    tbl.add_column("output_key", style="green")
    tbl.add_column("Flags", style="yellow")

    step_idx = 0
    team_vars: dict = cfg.get("vars") or {}
    if team_vars:
        console.print(f"  [dim]vars:[/dim] {list(team_vars.keys())}")
    produced_keys: set[str] = {"request"} | set(team_vars.keys())

    import re as _re
    _INTERP_RE = _re.compile(r"\{([a-zA-Z_][a-zA-Z0-9_]*)\}")

    def _validate_one(raw: dict, label: str, idx_str: str) -> None:
        name = raw.get("name", "")
        if not name:
            errors.append(f"Step {idx_str}: missing required field 'name'.")
        has_prompt = bool(raw.get("system_prompt", "").strip())
        has_prompt_file = bool(raw.get("system_prompt_file"))
        if has_prompt and has_prompt_file:
            errors.append(
                f"Step {idx_str} '{name}': use 'system_prompt' or "
                "'system_prompt_file', not both."
            )
        elif not has_prompt and not has_prompt_file:
            errors.append(
                f"Step {idx_str} '{name}': missing 'system_prompt' or 'system_prompt_file'."
            )
        elif has_prompt_file:
            spf = Path(raw["system_prompt_file"])
            if not spf.is_absolute():
                spf = config.parent / spf
            if not spf.exists():
                warnings.append(
                    f"Step {idx_str} '{name}': system_prompt_file not found: {spf}"
                )

        out_key = raw.get("output_key") or (f"{name}_output" if name else "")
        flags = []
        if raw.get("output_json"):
            flags.append("json")
        if raw.get("interpolate") is False:
            flags.append("no-interp")
        if raw.get("max_retries"):
            flags.append(f"retry×{raw['max_retries']}")
        if raw.get("condition"):
            cond = raw["condition"]
            cond_keys = [cond] if isinstance(cond, str) else cond
            for ck in cond_keys:
                if ck not in produced_keys:
                    warnings.append(
                        f"Step {idx_str} '{name}': condition key '{ck}' is not "
                        "produced by any earlier step."
                    )
            flags.append(f"if:{','.join(cond_keys)}")

        # Validate on_error
        on_error = raw.get("on_error", "raise")
        if on_error not in ("raise", "skip"):
            errors.append(
                f"Step {idx_str} '{name}': 'on_error' must be 'raise' or 'skip'; "
                f"got {on_error!r}."
            )
        elif on_error == "skip":
            dv = f"={raw['default']!r}" if raw.get("default") is not None else ""
            flags.append(f"skip{dv}")

        # Validate timeout
        timeout_raw = raw.get("timeout")
        if timeout_raw is not None:
            try:
                t_val = float(timeout_raw)
                if t_val <= 0:
                    errors.append(
                        f"Step {idx_str} '{name}': 'timeout' must be a positive number."
                    )
                else:
                    flags.append(f"timeout:{t_val:.0f}s")
            except (TypeError, ValueError):
                errors.append(
                    f"Step {idx_str} '{name}': 'timeout' must be a number, got {timeout_raw!r}."
                )

        # Mutual exclusivity: user_template and input_key
        if raw.get("user_template") and raw.get("input_key"):
            errors.append(
                f"Step {idx_str} '{name}': use 'input_key' or 'user_template', not both."
            )
        if raw.get("user_template"):
            flags.append("user_tmpl")

        # Validate post_process transform names
        raw_pp = raw.get("post_process")
        if raw_pp is not None:
            from antcrew.agents.template_agent import POST_PROCESS_TRANSFORMS
            pp_list = [raw_pp] if isinstance(raw_pp, str) else list(raw_pp)
            unknown_pp = [t for t in pp_list if t not in POST_PROCESS_TRANSFORMS]
            if unknown_pp:
                errors.append(
                    f"Step {idx_str} '{name}': unknown post_process transform(s): "
                    f"{unknown_pp}. Available: {sorted(POST_PROCESS_TRANSFORMS)}"
                )
            else:
                flags.append(f"pp:({','.join(pp_list)})")

        # Check {placeholder} references in system_prompt and user_template
        for field_label, text in [
            ("system_prompt", raw.get("system_prompt", "")),
            ("user_template", raw.get("user_template", "")),
        ]:
            for m in _INTERP_RE.finditer(text):
                key = m.group(1)
                if key not in produced_keys:
                    warnings.append(
                        f"Step {idx_str} '{name}' ({field_label}): "
                        f"interpolation {{'{key}'}} references a key not yet "
                        "produced by earlier steps."
                    )

        tbl.add_row(idx_str, name, label, out_key, " ".join(flags))
        if out_key:
            produced_keys.add(out_key)

    def _validate_team_file_step(raw: dict, idx_str: str) -> None:
        """Validate a team_file: step (nested CustomTeam)."""
        tf = raw.get("team_file", "")
        tf_path = Path(tf)
        if not tf_path.is_absolute():
            tf_path = config.parent / tf_path

        name = raw.get("name") or Path(tf).stem
        flags = []

        if not tf_path.exists():
            warnings.append(f"Step {idx_str}: team_file not found: {tf_path}")
        else:
            # Best-effort: extract output keys from nested team to populate dataflow
            try:
                import yaml as _y
                nested_cfg = _y.safe_load(tf_path.read_text(encoding="utf-8")) or {}
                for ns in nested_cfg.get("steps") or []:
                    if isinstance(ns, dict):
                        nk = ns.get("output_key")
                        if nk:
                            produced_keys.add(nk)
            except Exception:
                pass
            flags.append(f"→ {tf_path.name}")

        on_error = raw.get("on_error", "raise")
        if on_error not in ("raise", "skip"):
            errors.append(
                f"Step {idx_str} '{name}': 'on_error' must be 'raise' or 'skip'; "
                f"got {on_error!r}."
            )
        elif on_error == "skip":
            flags.append("skip")

        timeout_raw = raw.get("timeout")
        if timeout_raw is not None:
            try:
                flags.append(f"timeout:{float(timeout_raw):.0f}s")
            except (TypeError, ValueError):
                errors.append(
                    f"Step {idx_str}: 'timeout' must be a number, got {timeout_raw!r}."
                )

        if raw.get("condition"):
            cond = raw["condition"]
            cond_keys = [cond] if isinstance(cond, str) else cond
            flags.append(f"if:{','.join(cond_keys)}")

        tbl.add_row(idx_str, name, "nested", f"{name}/* (merged)", " ".join(flags))

    for item in raw_steps:
        step_idx += 1
        if isinstance(item, dict) and "parallel" in item:
            parallel_cfgs = item["parallel"]
            if not parallel_cfgs:
                errors.append(f"Step {step_idx}: empty 'parallel:' group.")
                continue
            for j, pcfg in enumerate(parallel_cfgs):
                sub_label = "parallel" if j == 0 else ""
                idx_str = f"{step_idx}.{j + 1}"
                if isinstance(pcfg, dict):
                    _validate_one(pcfg, sub_label, idx_str)
        elif isinstance(item, dict) and "team_file" in item:
            _validate_team_file_step(item, str(step_idx))
        else:
            if isinstance(item, dict):
                _validate_one(item, "seq", str(step_idx))
            else:
                errors.append(
                    f"Step {step_idx}: expected a dict, got {type(item).__name__}."
                )

    console.print()
    console.print(tbl)
    console.print()

    # ── 4. Instantiation check ────────────────────────────────────────────────
    if not errors:
        try:
            from antcrew.teams.custom_team import CustomTeam
            CustomTeam(list(raw_steps), _Sim(), base_dir=config.parent)
            console.print("[green]✓[/] All agents instantiated successfully (SimulatedLLM)")
        except FileNotFoundError as exc:
            # Missing system_prompt_file: already a warning above; skip instantiation.
            warnings.append(f"Instantiation skipped (file not found): {exc}")
        except Exception as exc:
            errors.append(f"Instantiation failed: {exc}")

    # ── 5. Report ─────────────────────────────────────────────────────────────
    for w in warnings:
        console.print(f"[yellow]⚠[/] {w}")
    for e in errors:
        console.print(f"[red]✗[/] {e}")

    if errors or (strict and warnings):
        total = len(errors) + (len(warnings) if strict else 0)
        console.print(
            f"\n[bold red]✗ Validation failed[/] ({total} issue{'s' if total != 1 else ''})\n"
        )
        raise typer.Exit(1)

    if warnings:
        console.print(
            f"\n[bold yellow]⚠ Config valid with {len(warnings)} warning(s)[/]\n"
        )
    else:
        console.print("\n[bold green]✓ Config is valid[/]\n")
