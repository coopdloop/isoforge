"""isoforge command-line interface."""

from __future__ import annotations

import json
import os
import sys
import webbrowser
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from .client import GatewayClient, GatewayError
from .preview import print_scene, scene_summary
from .supervisor import StartupError, Supervisor, probe_running_gateway

app = typer.Typer(
    name="isoforge",
    help="Design isometric cube-art logos by chatting with an LLM.",
    add_completion=False,
    no_args_is_help=True,
)
theme_app = typer.Typer(help="Manage reusable color palettes.", no_args_is_help=True)
app.add_typer(theme_app, name="theme")

console = Console()
err_console = Console(stderr=True)

DEFAULT_PORT = 4747


def data_dir() -> Path:
    """Where the local database, scenes and exports live."""
    if override := os.environ.get("ISOFORGE_DATA_DIR"):
        return Path(override).expanduser()
    return Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")) / "isoforge"


def state_file() -> Path:
    return data_dir() / "last-session.json"


def save_state(session_id: str, project_id: str, port: int) -> None:
    """Remember the active session so other commands can attach to it."""
    path = state_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(
        {"session_id": session_id, "project_id": project_id, "port": port}, indent=2
    ))


def load_state() -> dict:
    try:
        return json.loads(state_file().read_text())
    except (OSError, ValueError):
        return {}


def resolve_project(workspace: "Workspace") -> str:
    """Find the project a standalone command should operate on.

    The remembered project may be an empty one from an abandoned `chat` run, so fall
    back to the most recently updated project that actually has a design.
    """
    if workspace.project_id:
        try:
            workspace.client.get_scene(workspace.project_id)
            return workspace.project_id
        except GatewayError:
            pass

    try:
        projects = workspace.client.list_projects()
    except GatewayError:
        return ""
    for project in projects:
        if project.get("version_count"):
            workspace.project_id = project["id"]
            return project["id"]
    return ""


def fail(message: str, *, hint: str = "") -> None:
    err_console.print(f"[bold red]error[/bold red] {message}")
    if hint:
        err_console.print(f"[dim]{hint}[/dim]")
    raise typer.Exit(1)


def show_gateway_error(exc: GatewayError) -> None:
    err_console.print(f"[bold red]error[/bold red] {exc}")
    for detail in exc.validation_errors[:5]:
        err_console.print(f"  [yellow]{detail.get('code')}[/yellow] {detail.get('message')}")
        if remedy := detail.get("remedy"):
            err_console.print(f"    [dim]{remedy}[/dim]")
    if exc.hint:
        err_console.print(f"[dim]{exc.hint}[/dim]")


class Workspace:
    """A connected CLI session: either attached to a running gateway, or self-hosted."""

    def __init__(self, client: GatewayClient, supervisor: Supervisor | None,
                 session_id: str, project_id: str, port: int):
        self.client = client
        self.supervisor = supervisor
        self.session_id = session_id
        self.project_id = project_id
        self.port = port

    def close(self) -> None:
        self.client.close()
        if self.supervisor:
            self.supervisor.stop()


def attach_or_start(
    *, port: int | None = None, start_if_needed: bool = True, quiet: bool = True,
    serve_web: bool = True,
) -> Workspace:
    """Reuse a running gateway when possible, otherwise start one."""
    state = load_state()
    candidate_port = port or state.get("port") or DEFAULT_PORT

    if existing := probe_running_gateway(candidate_port):
        client = GatewayClient(existing)
        return Workspace(client, None, state.get("session_id", ""),
                         state.get("project_id", ""), candidate_port)

    if not start_if_needed:
        fail("no isoforge session is running",
             hint="start one with:  isoforge chat")

    supervisor = Supervisor(data_dir(), quiet=quiet)
    try:
        with console.status("[dim]starting local services…[/dim]", spinner="dots"):
            url = supervisor.start(gateway_port=port, serve_web=serve_web)
    except StartupError as exc:
        supervisor.stop()
        fail(str(exc))

    client = GatewayClient(url)
    return Workspace(client, supervisor, "", state.get("project_id", ""),
                     supervisor.gateway_port)


def render_preview(workspace: Workspace, *, version: int = 0, width: int | None = None) -> None:
    """Fetch and display the current design in the terminal."""
    try:
        artifact = workspace.client.export(
            fmt="png",
            session_id=workspace.session_id,
            project_id=workspace.project_id,
            version=version,
            options={"width": 480, "height": 480},
        )
        png = workspace.client.download(artifact["download_url"])
        scene_record = (
            workspace.client.get_version(workspace.project_id, version)
            if version else workspace.client.get_scene(workspace.project_id)
        )
        print_scene(console, png, scene_record["scene"], max_width=width)
    except GatewayError as exc:
        err_console.print(f"[dim]preview unavailable: {exc}[/dim]")


SLASH_HELP = """\
[bold]Commands[/bold]
  [cyan]/preview[/cyan]          redraw the current design
  [cyan]/history[/cyan]          list versions
  [cyan]/revert <n>[/cyan]       restore version n (appends a new version)
  [cyan]/diff [a] [b][/cyan]     show what changed between versions
  [cyan]/export <fmt>[/cyan]     svg | png | icons | json
  [cyan]/json[/cyan]             print the current scene document
  [cyan]/themes[/cyan]           list available palettes
  [cyan]/open[/cyan]             open the web preview in a browser
  [cyan]/help[/cyan]             show this list
  [cyan]/quit[/cyan]             end the session
"""


@app.command()
def chat(
    prompt: Optional[str] = typer.Argument(None, help="Opening message; omit for an interactive prompt."),
    continue_from: Optional[Path] = typer.Option(
        None, "--continue", "-c", help="Resume from an exported .isoforge.json file."),
    model: Optional[str] = typer.Option(None, "--model", "-m", help="Model id override."),
    provider: Optional[str] = typer.Option(
        None, "--provider", "-p", help="openrouter, anthropic or ollama."),
    port: Optional[int] = typer.Option(None, "--port", help="Gateway port."),
    no_browser: bool = typer.Option(False, "--no-browser", help="Do not open the web preview."),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Show service logs."),
) -> None:
    """Start a conversational design session."""
    seed_scene = None
    if continue_from:
        if not continue_from.is_file():
            fail(f"{continue_from} does not exist")
        try:
            seed_scene = json.loads(continue_from.read_text())
        except ValueError as exc:
            fail(f"{continue_from} is not valid JSON: {exc}")

    workspace = attach_or_start(port=port, quiet=not verbose)

    try:
        session = workspace.client.create_session(
            name=continue_from.stem if continue_from else "", scene=seed_scene
        )
    except GatewayError as exc:
        show_gateway_error(exc)
        workspace.close()
        raise typer.Exit(1)

    workspace.session_id = session["id"]
    workspace.project_id = session["project_id"]
    save_state(workspace.session_id, workspace.project_id, workspace.port)

    url = f"{workspace.client.base_url}/sessions/{workspace.session_id}"
    console.print(Panel(
        f"[bold]IsoForge[/bold]  [dim]describe a logo and watch it build[/dim]\n"
        f"[dim]preview:[/dim] {url}\n"
        f"[dim]type /help for commands, /quit to exit[/dim]",
        border_style="bright_blue",
    ))

    if not no_browser:
        try:
            webbrowser.open(url)
        except Exception:  # noqa: BLE001 - a missing browser must not break the CLI
            pass

    if seed_scene:
        console.print("[dim]resumed from file[/dim]")
        render_preview(workspace)

    try:
        _repl(workspace, opening=prompt, model=model, provider=provider)
    finally:
        try:
            if workspace.session_id:
                workspace.client.end_session(workspace.session_id)
        except GatewayError:
            pass
        workspace.close()


def _repl(workspace: Workspace, *, opening: str | None,
          model: str | None, provider: str | None) -> None:
    pending = opening

    while True:
        if pending is None:
            try:
                message = console.input("\n[bold bright_blue]›[/bold bright_blue] ").strip()
            except (EOFError, KeyboardInterrupt):
                console.print("\n[dim]session ended[/dim]")
                return
        else:
            message, pending = pending, None
            console.print(f"\n[bold bright_blue]›[/bold bright_blue] {message}")

        if not message:
            continue

        if message.startswith("/"):
            if _handle_slash(workspace, message):
                return
            continue

        try:
            with console.status("[dim]designing…[/dim]", spinner="dots"):
                result = workspace.client.send_message(
                    workspace.session_id, message,
                    model_override=model, provider_override=provider,
                )
        except GatewayError as exc:
            show_gateway_error(exc)
            continue

        console.print(f"\n[green]{result.get('reply', '')}[/green]")

        if result.get("changed"):
            version = result.get("version")
            kind = "rewrote" if result.get("is_full_scene") else "patched"
            repairs = result.get("repair_attempts") or 0
            note = f" [yellow](after {repairs} correction{'s' if repairs > 1 else ''})[/yellow]" if repairs else ""
            console.print(f"[dim]v{version} · {kind}[/dim]{note}\n")
            render_preview(workspace)
        if saved := result.get("theme_saved"):
            console.print(f"[dim]saved theme '{saved}'[/dim]")


def _handle_slash(workspace: Workspace, raw: str) -> bool:
    """Execute a slash command. Returns True when the session should end."""
    parts = raw[1:].split()
    command = parts[0].lower() if parts else ""
    args = parts[1:]

    if command in ("quit", "exit", "q"):
        console.print("[dim]session ended[/dim]")
        return True

    if command in ("help", "h", "?"):
        console.print(SLASH_HELP)
    elif command in ("preview", "p"):
        render_preview(workspace)
    elif command == "history":
        _print_history(workspace)
    elif command == "revert":
        if not args:
            err_console.print("[yellow]usage: /revert <version>[/yellow]")
        else:
            _do_revert(workspace, args[0])
    elif command == "diff":
        _print_diff(workspace, args)
    elif command == "export":
        _do_export(workspace, args[0] if args else "png")
    elif command == "json":
        _print_json(workspace)
    elif command == "themes":
        _print_themes(workspace)
    elif command == "open":
        webbrowser.open(f"{workspace.client.base_url}/sessions/{workspace.session_id}")
    else:
        err_console.print(f"[yellow]unknown command /{command}[/yellow]  try /help")
    return False


def _print_history(workspace: Workspace, limit: int = 20) -> None:
    try:
        entries = workspace.client.history(workspace.project_id, limit)
    except GatewayError as exc:
        show_gateway_error(exc)
        return
    if not entries:
        console.print("[dim]no versions yet[/dim]")
        return

    table = Table(box=None, pad_edge=False, show_header=True, header_style="dim")
    table.add_column("", width=1)
    table.add_column("ver", justify="right", style="cyan")
    table.add_column("shapes", justify="right", style="dim")
    table.add_column("change")
    table.add_column("when", style="dim")

    for entry in entries:
        table.add_row(
            "●" if entry["is_current"] else "",
            f"v{entry['version_number']}",
            str(entry["shape_count"]),
            entry.get("change_summary") or "[dim]—[/dim]",
            entry["created_at"][11:19],
        )
    console.print(table)


def _do_revert(workspace: Workspace, raw_version: str) -> None:
    try:
        target = int(raw_version.lstrip("v"))
    except ValueError:
        err_console.print("[yellow]version must be a number[/yellow]")
        return
    try:
        version = workspace.client.revert(workspace.project_id, target, workspace.session_id)
    except GatewayError as exc:
        show_gateway_error(exc)
        return
    console.print(f"[green]reverted to v{target}[/green] "
                  f"[dim](saved as v{version['version_number']})[/dim]")
    render_preview(workspace)


def _print_diff(workspace: Workspace, args: list[str]) -> None:
    def parse(raw: str) -> int:
        try:
            return int(raw.lstrip("v"))
        except ValueError:
            return 0

    a = parse(args[0]) if args else 0
    b = parse(args[1]) if len(args) > 1 else 0
    try:
        diff = workspace.client.diff(workspace.project_id, a, b)
    except GatewayError as exc:
        show_gateway_error(exc)
        return

    header = f"v{diff['from_version']} → v{diff['to_version']}"
    if not diff["changed"]:
        console.print(f"[dim]{header}: identical[/dim]")
        return

    summary = diff["summary"]
    console.print(
        f"[bold]{header}[/bold]  "
        f"[green]+{summary['added']}[/green] "
        f"[red]-{summary['removed']}[/red] "
        f"[yellow]~{summary['replaced']}[/yellow]"
    )
    for op in json.loads(diff["patch"]) if isinstance(diff["patch"], str) else diff["patch"]:
        color = {"add": "green", "remove": "red", "replace": "yellow"}.get(op["op"], "white")
        value = op.get("value")
        rendered = ""
        if value is not None and not isinstance(value, (dict, list)):
            rendered = f" → {value}"
        elif isinstance(value, dict) and "id" in value:
            rendered = f" → shape '{value['id']}'"
        console.print(f"  [{color}]{op['op']:8}[/{color}] {op['path']}{rendered}")


def _do_export(workspace: Workspace, fmt: str) -> None:
    fmt = {"icons": "icon-bundle", "icon": "icon-bundle", "ico": "icon-bundle"}.get(fmt, fmt)
    if fmt not in ("svg", "png", "icon-bundle", "json"):
        err_console.print(f"[yellow]unknown format '{fmt}'[/yellow]  use svg, png, icons or json")
        return
    try:
        artifact = workspace.client.export(
            fmt=fmt, session_id=workspace.session_id, project_id=workspace.project_id
        )
        data = workspace.client.download(artifact["download_url"])
    except GatewayError as exc:
        show_gateway_error(exc)
        return

    target = Path.cwd() / artifact.get("filename", f"logo.{fmt}")
    target.write_bytes(data)
    console.print(f"[green]exported[/green] {target}  [dim]{len(data):,} bytes[/dim]")


def _print_json(workspace: Workspace) -> None:
    try:
        record = workspace.client.get_scene(workspace.project_id)
    except GatewayError as exc:
        show_gateway_error(exc)
        return
    from rich.syntax import Syntax

    console.print(Syntax(json.dumps(record["scene"], indent=2), "json",
                         theme="ansi_dark", word_wrap=False))


def _print_themes(workspace: Workspace) -> None:
    try:
        themes = workspace.client.list_themes()
    except GatewayError as exc:
        show_gateway_error(exc)
        return

    table = Table(box=None, show_header=True, header_style="dim", pad_edge=False)
    table.add_column("name", style="cyan")
    table.add_column("kind", style="dim")
    table.add_column("colors")

    for theme in themes:
        doc = theme["theme"]
        if isinstance(doc, str):
            doc = json.loads(doc)
        swatches = Text()
        for value in (doc.get("colors") or {}).values():
            if isinstance(value, str) and value.startswith("#"):
                swatches.append("  ", style=f"on {value[:7]}")
        table.add_row(theme["name"], "builtin" if theme["is_builtin"] else "custom", swatches)
    console.print(table)


# --- non-interactive commands ---------------------------------------------


@app.command()
def history(
    limit: int = typer.Option(20, "--limit", "-n"),
    port: Optional[int] = typer.Option(None, "--port"),
) -> None:
    """Show version history for the most recent project."""
    workspace = attach_or_start(port=port, start_if_needed=True, serve_web=False)
    try:
        if not resolve_project(workspace):
            fail("no designs yet", hint="start designing with:  isoforge chat")
        _print_history(workspace, limit)
    finally:
        workspace.close()


@app.command()
def diff(
    from_version: Optional[str] = typer.Argument(None, metavar="[FROM]"),
    to_version: Optional[str] = typer.Argument(None, metavar="[TO]"),
    port: Optional[int] = typer.Option(None, "--port"),
) -> None:
    """Compare two versions. Defaults to the latest change."""
    workspace = attach_or_start(port=port, serve_web=False)
    try:
        if not resolve_project(workspace):
            fail("no designs yet", hint="start designing with:  isoforge chat")
        args = [a for a in (from_version, to_version) if a]
        _print_diff(workspace, args)
    finally:
        workspace.close()


@app.command()
def revert(
    version: str = typer.Argument(..., help="Version to restore, e.g. 3 or v3."),
    port: Optional[int] = typer.Option(None, "--port"),
) -> None:
    """Restore an earlier version by appending it as a new one."""
    workspace = attach_or_start(port=port, serve_web=False)
    try:
        if not resolve_project(workspace):
            fail("no designs yet", hint="start designing with:  isoforge chat")
        _do_revert(workspace, version)
    finally:
        workspace.close()


@app.command()
def export(
    fmt: str = typer.Argument("png", help="svg, png, icons or json."),
    output: Optional[Path] = typer.Option(None, "--out", "-o", help="Destination path."),
    size: Optional[int] = typer.Option(None, "--size", "-s", help="Pixel size for png."),
    version: int = typer.Option(0, "--version", help="Export a specific version."),
    port: Optional[int] = typer.Option(None, "--port"),
) -> None:
    """Export the current design."""
    workspace = attach_or_start(port=port, serve_web=False)
    try:
        if not resolve_project(workspace):
            fail("no designs yet", hint="start designing with:  isoforge chat")

        normalized = {"icons": "icon-bundle", "icon": "icon-bundle"}.get(fmt, fmt)
        options: dict = {}
        if size and normalized == "png":
            options = {"width": size, "height": size}

        artifact = workspace.client.export(
            fmt=normalized, project_id=workspace.project_id,
            version=version, options=options,
        )
        data = workspace.client.download(artifact["download_url"])
        target = output or Path.cwd() / artifact.get("filename", f"logo.{fmt}")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        console.print(f"[green]exported[/green] {target}  [dim]{len(data):,} bytes[/dim]")
    except GatewayError as exc:
        show_gateway_error(exc)
        raise typer.Exit(1)
    finally:
        workspace.close()


@theme_app.command("list")
def theme_list(port: Optional[int] = typer.Option(None, "--port")) -> None:
    """List available palettes."""
    workspace = attach_or_start(port=port, serve_web=False)
    try:
        _print_themes(workspace)
    finally:
        workspace.close()


@theme_app.command("save")
def theme_save(
    name: str = typer.Argument(..., help="Name for the saved palette."),
    port: Optional[int] = typer.Option(None, "--port"),
) -> None:
    """Save the current design's palette as a reusable theme."""
    workspace = attach_or_start(port=port, serve_web=False)
    try:
        if not resolve_project(workspace):
            fail("no designs yet", hint="start designing with:  isoforge chat")
        record = workspace.client.get_scene(workspace.project_id)
        colors = (record["scene"].get("palette") or {}).get("colors") or {}
        if not colors:
            fail("the current design has no palette to save")
        workspace.client.save_theme(name, colors, project_id=workspace.project_id)
        console.print(f"[green]saved theme[/green] {name}  [dim]{len(colors)} colors[/dim]")
    except GatewayError as exc:
        show_gateway_error(exc)
        raise typer.Exit(1)
    finally:
        workspace.close()


@theme_app.command("import")
def theme_import(
    path: Path = typer.Argument(..., help="Theme JSON file."),
    port: Optional[int] = typer.Option(None, "--port"),
) -> None:
    """Import a shared palette file."""
    if not path.is_file():
        fail(f"{path} does not exist")
    workspace = attach_or_start(port=port, serve_web=False)
    try:
        theme = workspace.client.import_theme(path.read_bytes())
        console.print(f"[green]imported[/green] {theme['name']}")
    except GatewayError as exc:
        show_gateway_error(exc)
        raise typer.Exit(1)
    finally:
        workspace.close()


@app.command()
def doctor() -> None:
    """Diagnose the local setup."""
    console.print("[bold]IsoForge environment[/bold]\n")

    table = Table(box=None, show_header=False, pad_edge=False)
    table.add_column("check", style="dim", width=22)
    table.add_column("result")

    from .supervisor import find_binary

    binary = find_binary("isoforged")
    table.add_row("isoforged binary",
                  f"[green]{binary}[/green]" if binary else
                  "[red]missing[/red] — run `make build`")

    try:
        import resvg_py  # noqa: F401
        table.add_row("raster backend", "[green]resvg[/green]")
    except ImportError:
        try:
            import cairosvg  # noqa: F401
            table.add_row("raster backend", "[yellow]cairosvg[/yellow]")
        except ImportError:
            table.add_row("raster backend", "[red]missing[/red] — PNG export unavailable")

    providers = []
    if os.environ.get("OPENROUTER_API_KEY"):
        providers.append("openrouter")
    if os.environ.get("ANTHROPIC_API_KEY"):
        providers.append("anthropic")
    try:
        import httpx as _httpx

        host = os.environ.get("OLLAMA_HOST", "http://localhost:11434")
        if _httpx.get(f"{host}/api/tags", timeout=1.0).status_code == 200:
            providers.append("ollama")
    except Exception:  # noqa: BLE001
        pass

    table.add_row("llm providers",
                  f"[green]{', '.join(providers)}[/green]" if providers else
                  "[red]none configured[/red] — set OPENROUTER_API_KEY")

    table.add_row("data directory", str(data_dir()))

    state = load_state()
    if state and probe_running_gateway(state.get("port", DEFAULT_PORT)):
        table.add_row("running session", f"[green]port {state['port']}[/green]")
    else:
        table.add_row("running session", "[dim]none[/dim]")

    console.print(table)

    if not providers:
        console.print("\n[dim]Set a key to get started:[/dim]")
        console.print("  export OPENROUTER_API_KEY=sk-or-...")


@app.command()
def version() -> None:
    """Print the version."""
    from . import __version__

    console.print(f"isoforge {__version__}")


def run() -> None:
    try:
        app()
    except KeyboardInterrupt:
        err_console.print("\n[dim]interrupted[/dim]")
        sys.exit(130)


if __name__ == "__main__":
    run()
