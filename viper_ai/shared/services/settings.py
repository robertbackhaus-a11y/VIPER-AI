# Copyright (C) 2026 Robert Backhaus
# SPDX-License-Identifier: Apache-2.0

"""Settings of the web front end (JSON, no secrets).

The file is ``settings.json`` in the per-user settings folder of ``runtime_paths`` (Windows ``%LOCALAPPDATA%\\VIPER-AI``, Linux ``$XDG_CONFIG_HOME/viper-ai``).
As long as it does not exist, the file of the former settings folder (``%LOCALAPPDATA%\\TradingAgentGui``) is read once more, never changed; the first save
writes the new file.  A missing file means defaults; a broken file or an invalid value falls back to the default for that value and is reported as a warning
(loading never rewrites the file).  The default database is ``runtime_paths.default_db_path()``; a database saved here wins over it.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Any, Optional

from ... import runtime

PATH_FIELDS = ("trading_db_path",)
MIN_REFRESH_S, MAX_REFRESH_S = 30, 3600


def runtime_paths():
    return runtime.runtime_paths()


def trading_root() -> str:
    """The runtime root; a relative ``VIPER_TRADING_ROOT`` is ignored here (the default is used) and reported by :func:`runtime_warnings`."""
    paths = runtime_paths()
    try:
        return str(paths.trading_root())
    except ValueError:
        return str(paths.default_trading_root())


def default_db_path() -> str:
    paths = runtime_paths()
    try:
        return str(paths.default_db_path())
    except ValueError:  # a relative VIPER_TRADING_ROOT: the default root, reported by runtime_warnings
        return str(paths.default_trading_root() / "data" / "trading.db")


def app_dir() -> Path:
    """The settings folder (also the web log and the uploaded import files)."""
    return runtime_paths().settings_dir()


def settings_path() -> Path:
    return app_dir() / "settings.json"


def legacy_settings_path() -> Path:
    return runtime_paths().legacy_settings_dir() / "settings.json"


@dataclass
class Settings:
    ollama_base_url: str = "http://127.0.0.1:11434"
    ollama_model: str = "qwen38-uncensored:latest"
    trading_db_path: str = field(default_factory=default_db_path)
    think_enabled: bool = False
    refresh_interval_s: int = 120


@dataclass
class LoadedSettings:
    settings: Settings
    warnings: list[str]
    path: Path


def _valid(name: str, value: Any, default: Any) -> Optional[str]:
    """``None`` when ``value`` is acceptable for the field ``name``, else a short reason."""
    if isinstance(default, bool):
        return None if isinstance(value, bool) else "erwartet true/false"
    if isinstance(default, int):
        if isinstance(value, bool) or not isinstance(value, int):
            return "erwartet eine ganze Zahl"
        if name == "refresh_interval_s" and not MIN_REFRESH_S <= value <= MAX_REFRESH_S:
            return f"erwartet {MIN_REFRESH_S}..{MAX_REFRESH_S}"
        return None
    if not isinstance(value, str):
        return "erwartet Text"
    if name in PATH_FIELDS and not os.path.isabs(value):
        return "erwartet einen absoluten Pfad"
    if name == "ollama_base_url" and not value.lower().startswith(("http://", "https://")):
        return "erwartet eine http(s)-URL"
    if name in ("ollama_model",) and not value.strip():
        return "darf nicht leer sein"
    return None


def load_settings(path: Optional[Path] = None) -> LoadedSettings:
    """The settings of ``path`` (default: the settings file, else the former one); keys that are no setting any more are ignored."""
    if path is None:
        path = settings_path()
        if not path.exists() and legacy_settings_path().exists():
            path = legacy_settings_path()
    defaults = Settings()
    warnings: list[str] = []
    if not path.exists():
        return LoadedSettings(defaults, warnings, path)
    try:
        raw = json.loads(path.read_text(encoding="utf-8-sig"))
        if not isinstance(raw, dict):
            raise ValueError("Wurzel ist kein Objekt")
    except (OSError, ValueError) as exc:
        return LoadedSettings(defaults, [f"settings.json ist unlesbar ({type(exc).__name__}); Standardwerte werden verwendet"], path)
    values = asdict(defaults)
    for f in fields(Settings):
        if f.name not in raw:
            continue
        problem = _valid(f.name, raw[f.name], getattr(defaults, f.name))
        if problem:
            warnings.append(f"Einstellung {f.name} ungültig ({problem}); Standardwert wird verwendet")
        else:
            values[f.name] = raw[f.name]
    return LoadedSettings(Settings(**values), warnings, path)


def runtime_warnings(settings: Settings) -> list[str]:
    """Hints about the paths in use (never blocking, nothing is changed): a relative ``VIPER_TRADING_ROOT`` and a missing database."""
    out: list[str] = []
    paths = runtime_paths()
    configured = os.environ.get(paths.TRADING_ROOT_ENV, "").strip()
    if configured and not os.path.isabs(configured):
        out.append(f"{paths.TRADING_ROOT_ENV} ist kein absoluter Pfad ({configured}); {paths.default_trading_root()} wird verwendet")
    if not Path(settings.trading_db_path).is_file():
        out.append(f"Datenbank nicht gefunden: {settings.trading_db_path}")
    return out


def save_settings(settings: Settings, path: Optional[Path] = None) -> Path:
    """Atomic write (temporary file, then replace); always to the settings file of this installation, never to the former one."""
    path = path or settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(asdict(settings), indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)
    return path


def validate_edit(name: str, value: Any) -> Optional[str]:
    """Validation for values typed into the settings form (same rules as loading)."""
    return _valid(name, value, getattr(Settings(), name))
