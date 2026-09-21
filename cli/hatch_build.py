"""Bundle the isoforged binary into the wheel.

The CLI subprocess-manages a Go daemon, so a usable install must ship it. The binary
for the *current* platform is picked up from the repo root (`make build`) or from
dist/ (`make build-all`), and the resulting wheel is tagged for that platform.

Setting ISOFORGE_TARGET_PLATFORM selects a cross-built binary from dist/ instead,
which is how release wheels for other platforms are produced.
"""

from __future__ import annotations

import os
import platform
import shutil
import stat
from pathlib import Path

from hatchling.builders.hooks.plugin.interface import BuildHookInterface

# (GOOS, GOARCH) -> Python wheel platform tag
WHEEL_TAGS = {
    ("darwin", "arm64"): "macosx_11_0_arm64",
    ("darwin", "amd64"): "macosx_10_15_x86_64",
    ("linux", "arm64"): "manylinux2014_aarch64",
    ("linux", "amd64"): "manylinux2014_x86_64",
}


def current_target() -> tuple[str, str]:
    system = platform.system().lower()
    machine = platform.machine().lower()
    goos = {"darwin": "darwin", "linux": "linux"}.get(system, system)
    goarch = {"arm64": "arm64", "aarch64": "arm64",
              "x86_64": "amd64", "amd64": "amd64"}.get(machine, machine)
    return goos, goarch


class BundleDaemonHook(BuildHookInterface):
    PLUGIN_NAME = "bundle-daemon"

    def initialize(self, version: str, build_data: dict) -> None:
        root = Path(self.root)
        repo = root.parent

        target = os.environ.get("ISOFORGE_TARGET_PLATFORM")
        if target:
            goos, goarch = target.split("/", 1)
            source = repo / "dist" / f"isoforged-{goos}-{goarch}"
        else:
            goos, goarch = current_target()
            source = repo / "isoforged"
            if not source.is_file():
                source = repo / "dist" / f"isoforged-{goos}-{goarch}"

        if not source.is_file():
            raise FileNotFoundError(
                f"isoforged binary not found at {source}.\n"
                "Build it first:  make build   (or `make build-all` for cross targets)"
            )

        dest_dir = root / "isoforge" / "bin"
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / "isoforged"
        shutil.copy2(source, dest)
        dest.chmod(dest.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)

        build_data.setdefault("artifacts", []).append("isoforge/bin/isoforged")
        # A wheel containing a native binary is not pure Python and must say so, or
        # pip will happily install a macOS binary on Linux.
        build_data["pure_python"] = False
        build_data["tag"] = f"py3-none-{WHEEL_TAGS.get((goos, goarch), 'any')}"
