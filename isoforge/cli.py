"""isoforge command-line interface.

One process. The CLI calls the agent and renderer as libraries; there is no server,
no daemon, and nothing to supervise.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from . import __version__
from .agent.orchestrator import Conversation, Orchestrator, TurnError
from .agent.providers import ProviderError, build_provider, detect_available
from .diff import diff_scenes
from .isodsl import canonical_json
from .isodsl.errors import IsoValidationError
from .preview import palette_swatches, print_scene, scene_summary
from .render.raster import RasterError, build_icon_bundle, render_png
from .render.svg import render_svg
from .store import Project, default_data_dir, slugify

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


def fail(message: str, *, hint: str = "") -> None:
    err_console.print(f"[bold red]error[/bold red] {message}")
    if hint:
        err_console.print(f"[dim]{hint}[/dim]")
    raise typer.Exit(1)


def show_validation_errors(exc: IsoValidationError) -> None:
    err_console.print("[bold red]error[/bold red] the design failed validation")
    for detail in exc.result.errors[:5]:
        err_console.print(f"  [yellow]{detail.code}[/yellow] {detail.message}")
        if detail.remedy:
            err_console.print(f"    [dim]{detail.remedy}[/dim]")


def resolve_project(name: str | None, *, must_exist: bool = True) -> Project:
    """Find a project by name or slug, else fall back to the most recent one."""
    if name:
        if found := Project.find(name):
            return found
        known = ", ".join(p.slug for p in Project.list_all()[:6]) or "(none yet)"
        fail(f"no design named '{name}'", hint=f"available: {known}")
    if project := Project.most_recent():
        return project
    if must_exist:
        fail("no designs yet", hint="start designing with:  isoforge chat")
    return Project.create()


def preview(project: Project, *, version: int | None = None, width: int | None = None) -> None:
    """Render the design into the terminal."""
    target = project.get(version) if version else project.latest
    if target is None:
        return
    try:
        png = render_png(target.scene, width=480, height=480)
    except RasterError as exc:
        console.print(f"[dim]preview unavailable: {exc}[/dim]")
        console.print(f"[dim]{scene_summary(target.scene)}[/dim]")
        return
    print_scene(console, png, target.scene, max_width=width)


SLASH_HELP = """\
[bold]Commands[/bold]
  [cyan]/preview[/cyan]          redraw the current design
  [cyan]/history[/cyan]          list versions
  [cyan]/revert <n>[/cyan]       restore version n (appends a new version)
  [cyan]/diff [a] [b][/cyan]     show what changed between versions
  [cyan]/export <fmt>[/cyan]     svg | png | icons | json
  [cyan]/json[/cyan]             print the current scene document
  [cyan]/themes[/cyan]           list built-in palettes
  [cyan]/help[/cyan]             show this list
  [cyan]/quit[/cyan]             end the session
"""


@app.command()
def chat(
    prompt: str | None = typer.Argument(
        None, help="Opening message; omit for an interactive prompt."
    ),
    name: str | None = typer.Option(None, "--name", "-n", help="Name for a new design."),
    project: str | None = typer.Option(
        None, "--project", "-P", help="Continue this design (implies --resume)."
    ),
    resume: bool = typer.Option(False, "--resume", "-r", help="Continue your most recent design."),
    continue_from: Path | None = typer.Option(
        None, "--continue", "-c", help="Resume from an exported .isoforge.json file."
    ),
    model: str | None = typer.Option(None, "--model", "-m", help="Model id override."),
    provider: str | None = typer.Option(
        None, "--provider", "-p", help="openrouter, anthropic or ollama."
    ),
) -> None:
    """Start a conversational design session."""
    seed_scene = None
    if continue_from:
        if not continue_from.is_file():
            fail(f"{continue_from} does not exist")
        try:
            document = json.loads(continue_from.read_text())
        except ValueError as exc:
            fail(f"{continue_from} is not valid JSON: {exc}")
        # Accept both a bare scene and a full export envelope.
        seed_scene = document.get("scene", document)

    # Naming a design to continue implies continuing it.
    if project or resume:
        target = resolve_project(project, must_exist=False)
        if target.latest is None:
            console.print("[dim]nothing to resume; starting a new design[/dim]")
        if name:
            target.update_meta(name=name)
    else:
        # Every `chat` starts its own design, so a second run never appends to the
        # first. Naming is optional; unnamed designs get a timestamped slug.
        target = Project.create(name or (continue_from.stem if continue_from else None))

    try:
        llm = build_provider(provider, model)
    except ValueError as exc:
        fail(str(exc))

    if not llm.available():
        fail(
            f"the {llm.name} provider is not configured",
            hint="run `isoforge doctor` to see what is available",
        )

    conversation = Conversation(model_provider=llm.name, model_name=llm.model)

    if seed_scene is not None:
        try:
            target.save(seed_scene, "Imported scene")
        except IsoValidationError as exc:
            show_validation_errors(exc)
            raise typer.Exit(1)
    conversation.scene = target.scene

    console.print(
        Panel(
            f"[bold]IsoForge[/bold]  [dim]describe a logo and watch it build[/dim]\n"
            f"[dim]design:[/dim] {target.slug}\n"
            f"[dim]{llm.name} · {llm.model}[/dim]\n"
            f"[dim]type /help for commands, /quit to exit[/dim]",
            border_style="bright_blue",
        )
    )

    if conversation.scene is not None:
        console.print(f"[dim]continuing from v{target.latest.number}[/dim]")
        preview(target)

    async def session() -> None:
        try:
            await _repl(target, conversation, Orchestrator(llm), opening=prompt)
        finally:
            await llm.aclose()

    # One loop for the whole session: the provider pools HTTP connections between
    # turns, and those sockets belong to the loop that opened them.
    asyncio.run(session())


async def _repl(
    project: Project,
    conversation: Conversation,
    orchestrator: Orchestrator,
    *,
    opening: str | None,
) -> None:
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
            if _handle_slash(project, conversation, message):
                return
            continue

        try:
            with console.status("[dim]designing…[/dim]", spinner="dots"):
                result = await orchestrator.run_turn(conversation, message)
        except TurnError as exc:
            err_console.print(f"[bold red]error[/bold red] {exc}")
            for detail in exc.errors[:3]:
                err_console.print(
                    f"  [yellow]{detail.get('code')}[/yellow] {detail.get('message')}"
                )
            continue
        except ProviderError as exc:
            err_console.print(f"[bold red]error[/bold red] {exc}")
            continue

        console.print(f"\n[green]{result.reply}[/green]")

        if result.changed and result.scene is not None:
            try:
                version = project.save(result.scene, result.summary)
            except IsoValidationError as exc:
                show_validation_errors(exc)
                continue

            kind = "rewrote" if result.is_full_scene else "patched"
            repairs = result.repair_attempts or 0
            note = (
                f" [yellow](after {repairs} correction{'s' if repairs > 1 else ''})[/yellow]"
                if repairs
                else ""
            )
            console.print(f"[dim]v{version.number} · {kind}[/dim]{note}\n")
            preview(project)

        if result.theme_saved:
            _save_theme(result.theme_saved["name"], result.theme_saved["colors"])
            console.print(f"[dim]saved theme '{result.theme_saved['name']}'[/dim]")


def _handle_slash(project: Project, conversation: Conversation, raw: str) -> bool:
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
        preview(project)
    elif command == "history":
        _print_history(project)
    elif command == "revert":
        if not args:
            err_console.print("[yellow]usage: /revert <version>[/yellow]")
        else:
            _do_revert(project, conversation, args[0])
    elif command == "diff":
        _print_diff(project, args)
    elif command == "export":
        _do_export(project, args[0] if args else "png")
    elif command == "json":
        _print_json(project)
    elif command == "themes":
        _print_themes()
    else:
        err_console.print(f"[yellow]unknown command /{command}[/yellow]  try /help")
    return False


def _print_history(project: Project, limit: int = 20) -> None:
    versions = project.versions()
    if not versions:
        console.print("[dim]no versions yet[/dim]")
        return

    latest = versions[-1].number
    table = Table(box=None, pad_edge=False, show_header=True, header_style="dim")
    table.add_column("", width=1)
    table.add_column("ver", justify="right", style="cyan")
    table.add_column("shapes", justify="right", style="dim")
    table.add_column("change")
    table.add_column("when", style="dim")

    for version in reversed(versions[-limit:]):
        table.add_row(
            "●" if version.number == latest else "",
            f"v{version.number}",
            str(version.shape_count),
            version.summary or "[dim]—[/dim]",
            version.created_at[11:19],
        )
    console.print(table)


def _do_revert(project: Project, conversation: Conversation, raw_version: str) -> None:
    try:
        target = int(raw_version.lstrip("v"))
    except ValueError:
        err_console.print("[yellow]version must be a number[/yellow]")
        return
    try:
        version = project.revert(target)
    except KeyError:
        err_console.print(f"[yellow]v{target} does not exist[/yellow]")
        return
    except IsoValidationError as exc:
        show_validation_errors(exc)
        return

    # Keep the agent in step, or its next patch would apply to the pre-revert design
    # and silently undo the revert.
    conversation.scene = version.scene
    console.print(f"[green]reverted to v{target}[/green] [dim](saved as v{version.number})[/dim]")
    preview(project)


def _print_diff(project: Project, args: list[str]) -> None:
    versions = project.versions()
    if len(versions) < 2:
        console.print("[dim]need at least two versions to compare[/dim]")
        return

    def parse(raw: str) -> int | None:
        try:
            return int(raw.lstrip("v"))
        except ValueError:
            return None

    to_number = parse(args[1]) if len(args) > 1 else versions[-1].number
    from_number = parse(args[0]) if args else versions[-2].number
    if from_number is None or to_number is None:
        err_console.print("[yellow]usage: /diff [from] [to][/yellow]")
        return

    before, after = project.get(from_number), project.get(to_number)
    if before is None or after is None:
        err_console.print("[yellow]no such version[/yellow]")
        return

    result = diff_scenes(before.scene, after.scene)
    header = f"v{from_number} → v{to_number}"
    if not result.changed:
        console.print(f"[dim]{header}: identical[/dim]")
        return

    console.print(
        f"[bold]{header}[/bold]  "
        f"[green]+{result.added}[/green] "
        f"[red]-{result.removed}[/red] "
        f"[yellow]~{result.replaced}[/yellow]"
    )
    for op in result.ops[:20]:
        color = {"add": "green", "remove": "red", "replace": "yellow"}.get(op.op, "white")
        rendered = ""
        if op.value is not None and not isinstance(op.value, (dict, list)):
            rendered = f" → {op.value}"
        elif isinstance(op.value, dict) and "id" in op.value:
            rendered = f" → shape '{op.value['id']}'"
        console.print(f"  [{color}]{op.op:8}[/{color}] {op.path}{rendered}")
    if len(result.ops) > 20:
        console.print(f"  [dim]… and {len(result.ops) - 20} more[/dim]")


def _do_export(project: Project, fmt: str, output: Path | None = None, size: int = 0) -> None:
    fmt = {"icons": "icon-bundle", "icon": "icon-bundle", "ico": "icon-bundle"}.get(fmt, fmt)
    latest = project.latest
    if latest is None:
        err_console.print("[yellow]nothing to export yet[/yellow]")
        return

    try:
        if fmt == "svg":
            data, suffix = render_svg(latest.scene).encode(), ".svg"
        elif fmt == "png":
            side = size or 512
            data, suffix = render_png(latest.scene, width=side, height=side), ".png"
        elif fmt == "icon-bundle":
            data, suffix = build_icon_bundle(latest.scene).to_zip(), ".zip"
        elif fmt == "json":
            data, suffix = (canonical_json(latest.scene) + "\n").encode(), ".isoforge.json"
        else:
            err_console.print(
                f"[yellow]unknown format '{fmt}'[/yellow]  use svg, png, icons or json"
            )
            return
    except RasterError as exc:
        err_console.print(f"[bold red]error[/bold red] {exc}")
        return

    # Prefer the display name for the filename; the slug is an internal handle and a
    # timestamped one makes for an unhelpful `logo-20260921-170112.png`.
    stem = slugify(project.name) if project.name != project.slug else project.slug
    target = output or Path.cwd() / f"{stem}{suffix}"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
    console.print(f"[green]exported[/green] {target}  [dim]{len(data):,} bytes[/dim]")


def _print_json(project: Project) -> None:
    latest = project.latest
    if latest is None:
        console.print("[dim]no design yet[/dim]")
        return
    from rich.syntax import Syntax

    console.print(Syntax(canonical_json(latest.scene), "json", theme="ansi_dark", word_wrap=False))


# --- themes -----------------------------------------------------------------


def themes_file() -> Path:
    return default_data_dir() / "themes.json"


BUILTIN_THEMES: dict[str, dict[str, str]] = {
    "violet-dev": {"base": "#7C5CFF", "accent": "#22D3EE", "ink": "#0B0E14"},
    "ember": {"hot": "#FF6B35", "warm": "#F7B801", "cool": "#2EC4B6", "ink": "#1A1423"},
    "forest": {"leaf": "#34D399", "moss": "#059669", "bark": "#78350F", "ink": "#022C22"},
    "nord": {"polar": "#2E3440", "frost": "#88C0D0", "aurora": "#A3BE8C", "ink": "#242933"},
    "mono": {"light": "#E2E8F0", "mid": "#94A3B8", "dark": "#475569", "ink": "#0F172A"},
}


def _load_user_themes() -> dict[str, dict[str, str]]:
    try:
        return json.loads(themes_file().read_text())
    except (OSError, ValueError):
        return {}


def _save_theme(name: str, colors: dict[str, str]) -> None:
    if name in BUILTIN_THEMES:
        name = f"{name}-custom"
    themes = _load_user_themes()
    themes[name] = colors
    path = themes_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(themes, indent=2) + "\n")


def _print_themes() -> None:
    table = Table(box=None, show_header=True, header_style="dim", pad_edge=False)
    table.add_column("name", style="cyan")
    table.add_column("kind", style="dim")
    table.add_column("colors")

    for source, kind in ((BUILTIN_THEMES, "builtin"), (_load_user_themes(), "custom")):
        for name, colors in source.items():
            swatches = Text()
            for value in colors.values():
                if isinstance(value, str) and value.startswith("#"):
                    swatches.append("  ", style=f"on {value[:7]}")
            table.add_row(name, kind, swatches)
    console.print(table)


# --- standalone commands ----------------------------------------------------


@app.command()
def history(
    project: str | None = typer.Option(None, "--project", "-P"),
    limit: int = typer.Option(20, "--limit", "-n"),
) -> None:
    """Show version history."""
    _print_history(resolve_project(project), limit)


@app.command()
def diff(
    from_version: str | None = typer.Argument(None, metavar="[FROM]"),
    to_version: str | None = typer.Argument(None, metavar="[TO]"),
    project: str | None = typer.Option(None, "--project", "-P"),
) -> None:
    """Compare two versions. Defaults to the latest change."""
    _print_diff(resolve_project(project), [a for a in (from_version, to_version) if a])


@app.command()
def revert(
    version: str = typer.Argument(..., help="Version to restore, e.g. 3 or v3."),
    project: str | None = typer.Option(None, "--project", "-P"),
) -> None:
    """Restore an earlier version by appending it as a new one."""
    target = resolve_project(project)
    _do_revert(target, Conversation(), version)


@app.command()
def export(
    fmt: str = typer.Argument("png", help="svg, png, icons or json."),
    output: Path | None = typer.Option(None, "--out", "-o", help="Destination path."),
    size: int = typer.Option(0, "--size", "-s", help="Pixel size for png."),
    project: str | None = typer.Option(None, "--project", "-P"),
) -> None:
    """Export the current design."""
    _do_export(resolve_project(project), fmt, output, size)


@app.command("show")
def show(
    project: str | None = typer.Option(None, "--project", "-P"),
    version: int = typer.Option(0, "--version", "-v"),
) -> None:
    """Render a design in the terminal."""
    target = resolve_project(project)
    preview(target, version=version or None)


@app.command("list")
def list_projects(
    tag: str | None = typer.Option(None, "--tag", "-t", help="Only show designs with this tag."),
) -> None:
    """List your designs."""
    projects = Project.list_all()
    if tag:
        projects = [p for p in projects if tag in p.tags]
    if not projects:
        if tag:
            console.print(f"[dim]no designs tagged '{tag}'[/dim]")
        else:
            console.print("[dim]no designs yet[/dim]  start with:  isoforge chat")
        return

    # no_wrap keeps one design per row; the terminal truncates rather than reflowing,
    # which keeps the list scannable on narrow windows.
    table = Table(box=None, show_header=True, header_style="dim", pad_edge=False)
    table.add_column("design", style="cyan", no_wrap=True)
    table.add_column("name", no_wrap=True, max_width=22)
    table.add_column("v", justify="right", style="dim")
    table.add_column("palette", no_wrap=True)
    table.add_column("tags", style="dim", no_wrap=True, max_width=18)
    table.add_column("latest change", no_wrap=True, max_width=34)

    for project in projects:
        latest = project.latest
        display = project.name if project.name != project.slug else "[dim]—[/dim]"
        table.add_row(
            project.slug,
            display,
            str(len(project.versions())),
            palette_swatches(latest.scene) if latest else Text(),
            " ".join(project.tags),
            latest.summary if latest else "",
        )
    console.print(table)
    console.print(
        f"\n[dim]{len(projects)} design{'s' if len(projects) != 1 else ''} · "
        f"open one with:  isoforge chat --resume -P <design>[/dim]"
    )


@app.command()
def info(
    project: str | None = typer.Option(None, "--project", "-P"),
) -> None:
    """Show details for one design."""
    target = resolve_project(project)
    latest = target.latest

    table = Table(box=None, show_header=False, pad_edge=False)
    table.add_column("field", style="dim", width=12)
    table.add_column("value")
    table.add_row("design", f"[cyan]{target.slug}[/cyan]")
    table.add_row("name", target.name)
    if target.description:
        table.add_row("description", target.description)
    if target.tags:
        table.add_row("tags", " ".join(target.tags))
    table.add_row("versions", str(len(target.versions())))
    if latest:
        table.add_row("latest", f"v{latest.number} · {latest.summary}")
        table.add_row("shapes", str(latest.shape_count))
        table.add_row("palette", palette_swatches(latest.scene))
    table.add_row("path", str(target.path))
    console.print(table)


@app.command()
def edit(
    project: str | None = typer.Option(None, "--project", "-P"),
    name: str | None = typer.Option(None, "--name", "-n", help="Set the display name."),
    description: str | None = typer.Option(None, "--description", "-d"),
    tags: str | None = typer.Option(None, "--tags", help="Comma-separated, or '' to clear."),
    rename: str | None = typer.Option(
        None, "--rename", help="Rename the design handle (moves its directory)."
    ),
) -> None:
    """Edit a design's metadata."""
    target = resolve_project(project)

    if not any(v is not None for v in (name, description, tags, rename)):
        fail(
            "nothing to change",
            hint="try:  isoforge edit --name 'My Logo' --tags work,client",
        )

    parsed_tags = None
    if tags is not None:
        parsed_tags = [t.strip() for t in tags.split(",") if t.strip()]

    target.update_meta(name=name, description=description, tags=parsed_tags)

    if rename:
        try:
            target = target.rename_slug(rename)
        except FileExistsError as exc:
            fail(str(exc))

    console.print(f"[green]updated[/green] {target.slug}")
    info(project=target.slug)


@app.command()
def delete(
    project: str | None = typer.Option(None, "--project", "-P"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip the confirmation prompt."),
) -> None:
    """Delete a design and all of its versions."""
    target = resolve_project(project)
    count = len(target.versions())

    if not yes:
        console.print(
            f"about to delete [cyan]{target.slug}[/cyan] "
            f"and its {count} version{'s' if count != 1 else ''}"
        )
        if not typer.confirm("are you sure?"):
            console.print("[dim]cancelled[/dim]")
            return

    target.delete()
    console.print(f"[green]deleted[/green] {target.slug}")


@theme_app.command("list")
def theme_list() -> None:
    """List available palettes."""
    _print_themes()


@theme_app.command("save")
def theme_save(
    name: str = typer.Argument(..., help="Name for the saved palette."),
    project: str | None = typer.Option(None, "--project", "-P"),
) -> None:
    """Save the current design's palette as a reusable theme."""
    target = resolve_project(project)
    scene = target.scene
    if scene is None:
        fail("no design yet")
    colors = (scene.get("palette") or {}).get("colors") or {}
    if not colors:
        fail("the current design has no palette to save")
    _save_theme(name, colors)
    console.print(f"[green]saved theme[/green] {name}  [dim]{len(colors)} colors[/dim]")


@app.command()
def doctor() -> None:
    """Diagnose the local setup."""
    console.print("[bold]IsoForge environment[/bold]\n")

    table = Table(box=None, show_header=False, pad_edge=False)
    table.add_column("check", style="dim", width=18)
    table.add_column("result")

    try:
        import resvg_py  # noqa: F401

        table.add_row("raster backend", "[green]resvg[/green]")
    except ImportError:
        try:
            import cairosvg  # noqa: F401

            table.add_row("raster backend", "[yellow]cairosvg[/yellow]")
        except ImportError:
            table.add_row("raster backend", "[red]missing[/red] — PNG export unavailable")

    available = detect_available()
    table.add_row(
        "llm providers",
        f"[green]{', '.join(available)}[/green]"
        if available
        else "[red]none configured[/red] — set OPENROUTER_API_KEY",
    )
    table.add_row("data directory", str(default_data_dir()))
    table.add_row("designs", str(len(Project.list_all())))
    console.print(table)

    if not available:
        console.print("\n[dim]Set a key to get started:[/dim]")
        console.print("  export OPENROUTER_API_KEY=sk-or-...")


@app.command()
def version() -> None:
    """Print the version."""
    console.print(f"isoforge {__version__}")


def run() -> None:
    try:
        app()
    except KeyboardInterrupt:
        err_console.print("\n[dim]interrupted[/dim]")
        sys.exit(130)


if __name__ == "__main__":
    run()
