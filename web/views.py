# Copyright (C) 2026 Robert Backhaus
# SPDX-License-Identifier: Apache-2.0

"""JSON form of the view models of ``gui/viewmodels`` (the tables and texts the desktop GUI shows), so the browser shows the same wording.

A ``Presented`` text becomes ``{"t": text, "tip": plain-text tooltip}`` (plain ``text`` when it has no tooltip); a dataclass becomes a dict of its
fields plus the read-only properties named in ``PROPERTIES``.  Nothing is computed here.
"""

from __future__ import annotations

import dataclasses
import html
import re
from typing import Any

from .deps import reason_codes

# properties of view-model classes the pages need besides the fields (class name -> property names)
PROPERTIES: dict[str, tuple[str, ...]] = {
    "PositionVM": ("lookthrough", "table_label", "label", "origin_text", "tip"),
    "SectorVM": ("title",),
    "CompanyVM": ("tile_tip", "label", "stand_text", "overlap", "contributors_text", "tip", "hints"),
}

_TAG = re.compile(r"<[^>]+>")


def plain(tip: str) -> str:
    """A rich-text tooltip as plain text (paragraphs and line breaks become new lines)."""
    text = re.sub(r"</p>\s*<p>|<br\s*/?>", "\n", tip or "")
    return html.unescape(_TAG.sub("", text)).strip()


def jsonable(obj: Any) -> Any:
    if isinstance(obj, reason_codes.Presented):
        return {"t": str(obj), "tip": plain(obj.tip)} if obj.tip else str(obj)
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        out = {f.name: jsonable(getattr(obj, f.name)) for f in dataclasses.fields(obj)}
        for name in PROPERTIES.get(type(obj).__name__, ()):
            out[name] = jsonable(getattr(obj, name))
        return out
    if isinstance(obj, (list, tuple)):
        return [jsonable(x) for x in obj]
    if isinstance(obj, dict):
        return {str(k): jsonable(v) for k, v in obj.items()}
    return obj
