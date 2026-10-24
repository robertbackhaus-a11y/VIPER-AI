# Copyright (C) 2026 Robert Backhaus
# SPDX-License-Identifier: Apache-2.0

"""Build step of the packaging (``pyproject.toml``: ``[tool.setuptools.cmdclass]``); not used at runtime.

``pip install <repo-path>`` builds in the source tree, and setuptools copies the packages into ``build/lib`` without removing what is
already there: a module or package deleted from the repository (for example the former ``gui/``) would still end up in the wheel.  The
build therefore starts from an empty ``build/lib``.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from setuptools.command.build_py import build_py


class CleanBuildPy(build_py):
    def run(self) -> None:
        target = Path(self.build_lib)
        if target.is_dir():
            shutil.rmtree(target)
        super().run()
