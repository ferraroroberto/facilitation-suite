"""A synthetic deck in the exporter's ``export.json`` shape, with Mentimeter placeholder slides.

Each slide's ``texts`` are its text boxes as the COM exporter reads them,
the title box included, line breaks inside a box as ``\\n``.
"""

from __future__ import annotations

from typing import Any

SLIDES: list[dict[str, Any]] = [
    {"slide_id": 20, "title": "Opening", "texts": ["Opening"], "pictures": 0},
    {"slide_id": 21, "title": "Mentimeter – word cloud – One word for how you arrive today",
     "texts": ["Mentimeter – word cloud – One word for how you arrive today"]},
    {"slide_id": 22, "title": "Mentimeter",
     "texts": ["Mentimeter", "Multiple choice", "Where do you work most days?", "A) Office\nB) Home\nC) Both, it depends"]},
    {"slide_id": 23, "title": "mentimeter: how much energy do you have, 1-10?",
     "texts": ["mentimeter: how much energy do you have, 1-10?"]},
    {"slide_id": 24, "title": "Mentimeter slide", "texts": ["Mentimeter slide"]},
    {"slide_id": 25, "title": "Closing", "texts": ["Closing", "Thank you"]},
]


def export() -> dict[str, Any]:
    slides = []
    for i, s in enumerate(SLIDES, start=1):
        slides.append({"index": i, "notes": "", "pictures": 1, "background": "#f2f2f2", "hidden": False,
                       "file": f"slide-{s['slide_id']}.png", **s})
    return {"slide_width": 960, "slide_height": 540, "sections": [], "slides": slides}
