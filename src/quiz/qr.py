"""The join QR code (#52): an SVG players scan to open ``/play?pin=…`` on the public URL.

Drawn from ``qrcode``'s module matrix as one ``<path>`` on a white square, so
it scales to any size on the stage (no millimetre size, no Pillow).
"""

from __future__ import annotations

from html import escape

import qrcode
from qrcode.constants import ERROR_CORRECT_M

BORDER = 2  # modules of quiet zone around the code (inside the white square)


def qr_svg(text: str) -> str:
    """``text`` as a QR code: black modules on white, ``viewBox`` in modules."""
    qr = qrcode.QRCode(error_correction=ERROR_CORRECT_M, border=BORDER)
    qr.add_data(text)
    qr.make(fit=True)
    matrix = qr.get_matrix()  # includes the border
    size = len(matrix)
    runs = []
    for y, row in enumerate(matrix):
        x = 0
        while x < size:
            if row[x]:
                start = x
                while x < size and row[x]:
                    x += 1
                runs.append(f"M{start} {y}h{x - start}v1h{start - x}z")
            else:
                x += 1
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {size} {size}" shape-rendering="crispEdges" '
            f'role="img" aria-label="{escape(text, quote=True)}">'
            f'<rect width="{size}" height="{size}" fill="#fff"/><path fill="#000" d="{"".join(runs)}"/></svg>')


def not_configured_svg() -> str:
    """The placeholder while ``quiz.public_url`` is empty: a QR-sized square that says so."""
    return ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 200 200" role="img" '
            'aria-label="Public URL not configured">'
            '<rect x="1" y="1" width="198" height="198" rx="12" fill="#fff" stroke="#818b98" stroke-width="2" '
            'stroke-dasharray="8 6"/>'
            '<text x="100" y="92" text-anchor="middle" font-family="system-ui, sans-serif" font-size="17" '
            'font-weight="700" fill="#1f2328">Public URL</text>'
            '<text x="100" y="116" text-anchor="middle" font-family="system-ui, sans-serif" font-size="17" '
            'font-weight="700" fill="#1f2328">not configured</text></svg>')
