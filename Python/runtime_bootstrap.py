"""
Runtime bootstrap helpers shared by EV charging scripts.

This module keeps CLI execution resilient by:
1) Re-executing in the project virtual environment when available.
2) Configuring writable local cache directories for matplotlib/fontconfig.
3) Printing clear dependency installation guidance when packages are missing.
"""

from __future__ import annotations

import importlib.util
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Iterable, Sequence

_REEXEC_GUARD_ENV = "EV_SIM_NO_VENV_REEXEC"


def _project_root(script_file: str) -> Path:
    return Path(script_file).resolve().parents[1]


def _venv_python_candidates(project_root: Path) -> list[Path]:
    venv_root = project_root / "venv"
    candidates = []
    active_venv = os.environ.get("VIRTUAL_ENV")
    if active_venv and Path(active_venv).resolve() == venv_root.resolve():
        active_root = Path(active_venv)
        candidates.extend([
            active_root / "bin" / "python3",
            active_root / "bin" / "python",
            active_root / "Scripts" / "python.exe",
        ])

    candidates.extend([
        venv_root / "bin" / "python3",
        venv_root / "bin" / "python",
        venv_root / "Scripts" / "python.exe",
    ])
    return candidates


def _current_python_is_venv() -> bool:
    base_prefix = getattr(sys, "base_prefix", sys.prefix)
    return sys.prefix != base_prefix


def _missing_modules(required_modules: Iterable[str]) -> list[str]:
    return [name for name in required_modules if importlib.util.find_spec(name) is None]


def _mac_process_arch() -> str | None:
    if sys.platform != "darwin":
        return None
    try:
        out = subprocess.check_output(["/usr/bin/arch"], text=True).strip()
        return out or None
    except (FileNotFoundError, subprocess.SubprocessError):
        return None


def _should_force_arm64_reexec() -> bool:
    if sys.platform != "darwin":
        return False
    try:
        arm64_capable = subprocess.check_output(
            ["/usr/sbin/sysctl", "-in", "hw.optional.arm64"],
            text=True,
        ).strip() == "1"
    except (FileNotFoundError, subprocess.SubprocessError):
        arm64_capable = False
    if not arm64_capable:
        return False
    return _mac_process_arch() == "i386"


def maybe_reexec_into_project_venv(
    script_file: str,
    argv: Sequence[str],
    is_main: bool,
) -> None:
    """If running as a script, prefer project venv over system Python."""
    if not is_main:
        return
    if os.environ.get(_REEXEC_GUARD_ENV) == "1":
        return
    if _current_python_is_venv():
        return

    project_root = _project_root(script_file)

    for candidate in _venv_python_candidates(project_root):
        if not candidate.exists() or not os.access(candidate, os.X_OK):
            continue

        os.environ[_REEXEC_GUARD_ENV] = "1"
        if _should_force_arm64_reexec():
            os.execv("/usr/bin/arch", ["/usr/bin/arch", "-arm64", str(candidate), *argv])
        os.execv(str(candidate), [str(candidate), *argv])


def configure_local_cache_dirs(script_file: str) -> None:
    """Set cache dirs to writable project-local paths for matplotlib/fontconfig."""
    project_root = _project_root(script_file)
    cache_root = project_root / ".cache"
    matplotlib_cache = cache_root / "matplotlib"
    config_root = cache_root / "config"
    fontconfig_cache = cache_root / "fontconfig"

    for path in (cache_root, matplotlib_cache, config_root, fontconfig_cache):
        path.mkdir(parents=True, exist_ok=True)

    os.environ.setdefault("XDG_CACHE_HOME", str(cache_root))
    os.environ.setdefault("XDG_CONFIG_HOME", str(config_root))
    os.environ.setdefault("MPLCONFIGDIR", str(matplotlib_cache))


def ensure_dependencies(required_modules: Iterable[str], script_file: str) -> None:
    """Exit with actionable guidance when required packages are missing."""
    missing = _missing_modules(required_modules)
    if not missing:
        return

    project_root = _project_root(script_file)
    req_file = project_root / "requirements.txt"
    preferred_python = next((p for p in _venv_python_candidates(project_root) if p.exists()), None)
    python_cmd = str(preferred_python) if preferred_python else sys.executable

    if req_file.exists():
        install_cmd = f'"{python_cmd}" -m pip install -r "{req_file}"'
    else:
        install_cmd = f'"{python_cmd}" -m pip install {" ".join(missing)}'

    message = (
        f"Missing required Python package(s): {', '.join(missing)}\n"
        f"Interpreter in use: {sys.executable}\n"
        f"Run this to fix:\n  {install_cmd}"
    )
    raise SystemExit(message)


def open_files_in_default_app(paths: Sequence[str]) -> bool:
    """Open output files in the OS default viewer when possible."""
    clean_paths = [p for p in paths if p]
    if not clean_paths:
        return False

    if sys.platform == "darwin":
        opener = shutil.which("open")
        if not opener:
            return False
        subprocess.run([opener, *clean_paths], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return True

    if os.name == "nt":
        opened_any = False
        if not hasattr(os, "startfile"):
            return False
        for path in clean_paths:
            try:
                getattr(os, "startfile")(path)  # type: ignore[attr-defined]
                opened_any = True
            except OSError:
                continue
        return opened_any

    opener = shutil.which("xdg-open")
    if not opener:
        return False

    subprocess.run([opener, clean_paths[0]], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return True


def bootstrap_runtime(
    script_file: str,
    argv: Sequence[str],
    required_modules: Iterable[str],
    is_main: bool,
) -> None:
    """Run standard bootstrap steps for simulator entrypoints and modules."""
    maybe_reexec_into_project_venv(
        script_file=script_file,
        argv=argv,
        is_main=is_main,
    )
    configure_local_cache_dirs(script_file=script_file)
    ensure_dependencies(required_modules=required_modules, script_file=script_file)
