"""Harbium's brand, as the app uses it (GUI_PLAN.md, section 9).

Ink and bone with warm greys, Cormorant Garamond for titles, Inter for text,
DM Mono for frame names and paths. Behind photographs the surround is neutral
grey, never bone: tinted surroundings change how colour is judged.
"""

from __future__ import annotations

DARK = {
    "bg": "#0a0a0a", "panel": "#111111", "line": "#1f1f1f", "text": "#e8e4db", "dim": "#a8a39a",
    "surround": "#3a3a3a", "primary_bg": "#e8e4db", "primary_text": "#0a0a0a",
    "measured": (132, 127, 117), "curve": (232, 228, 219),
}
LIGHT = {
    "bg": "#e8e4db", "panel": "#ded9ce", "line": "#c7c1b5", "text": "#0a0a0a", "dim": "#5e5a52",
    "surround": "#8c8c8c", "primary_bg": "#0a0a0a", "primary_text": "#e8e4db",
    "measured": (132, 127, 117), "curve": (10, 10, 10),
}
FONTS = {"Inter": "fonts/Inter.ttf", "Cormorant": "fonts/CormorantGaramond.ttf", "DM Mono": "fonts/DMMono-Regular.ttf"}
DISPLAY, BODY, MONO = "Cormorant", "Inter", "DM Mono"
