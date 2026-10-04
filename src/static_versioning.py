"""Static-asset versioning: one fleet hash stamped onto every ``.js``/``.css`` URL (#209).

The webapp is an ES-module graph, so a per-file hash would go stale on a transitive edit (``a.js``
changes, ``b.js``'s own bytes do not, yet ``b.js`` now pulls in something different). One **fleet
hash** — a digest over every hashable file's own digest — moves every ``?v=`` stamp on any edit to any
asset, so the whole graph is re-fetched together and a deploy can never leave a device on old code.

Shape follows ``home-automation/src/static_versioning.py`` (the fleet reference, project-scaffolding
``docs/app-onboarding.md`` §4a), with two differences this app needs:

- assets live under several URL prefixes (``/static``, ``/themes``, ``/activities``), so the hash map is
  keyed by the **absolute URL path** (``/static/js/ui.js``), and
- modules import each other by absolute path (``from '/static/js/ui.js'``) and by ``import()`` as well
  as by relative path (the vendored ``../icons/icons.js``) — all three are stamped.

The hashes are computed once when :class:`AssetVersions` is built at startup (the tray restarts on every
code change, so there is no watcher and no per-request hashing). A partial deploy degrades to unstamped
URLs, which the server still revalidates, rather than a crashed page.
"""

from __future__ import annotations

import hashlib
import logging
import posixpath
import re
from collections.abc import Iterable, Mapping
from pathlib import Path

logger = logging.getLogger(__name__)

HASH_LEN = 8
HASHED_SUFFIXES = (".js", ".css")

# ``from '<spec>'`` · ``import '<spec>'`` · ``import('<spec>')`` with a literal specifier — absolute (under
# a served prefix) or relative. An existing ``?v=…`` is captured too, so re-stamping is idempotent.
_JS_IMPORT = r"""(\bfrom\s*|\bimport\s*\(?\s*)(['"])((?:/|\.{1,2}/)[\w\-./]+\.js)(\?v=[^'"]*)?\2"""
# ``href`` / ``src`` pointing at a served ``.css``/``.js`` in a page.
_HTML_ASSET = r"""\b(href|src)=(['"])(/[\w\-./]+\.(?:css|js))(\?v=[^'"]*)?\2"""


def _short_hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()[:HASH_LEN]


class AssetVersions:
    """The fleet hash over a set of served directories, and the rewriters that stamp it.

    ``roots`` maps a URL prefix to the directory it serves (``{"/static": STATIC_DIR, ...}``).
    """

    def __init__(self, roots: Mapping[str, Path]) -> None:
        self.roots = {prefix.rstrip("/"): Path(directory) for prefix, directory in roots.items()}
        per_file = {url: _short_hash(path.read_bytes()) for url, path in self._files()}
        self.fleet_hash = _short_hash("\n".join(f"{url}:{per_file[url]}" for url in sorted(per_file)).encode("utf-8")) if per_file else ""
        self.hashes = dict.fromkeys(per_file, self.fleet_hash)
        if not per_file:
            logger.warning("⚠️ static versioning: no .js/.css found under %s — URLs go out unstamped", list(self.roots.values()))

    def _files(self) -> Iterable[tuple[str, Path]]:
        for prefix, directory in self.roots.items():
            if not directory.is_dir():
                continue
            for path in sorted(directory.rglob("*")):
                if path.is_file() and path.suffix.lower() in HASHED_SUFFIXES:
                    yield f"{prefix}/{path.relative_to(directory).as_posix()}", path

    def is_current(self, query_string: str | bytes) -> bool:
        """True when the request's ``?v=`` is this process's fleet hash (so the URL names these exact bytes)."""
        if isinstance(query_string, bytes):
            query_string = query_string.decode("latin-1")
        return bool(self.fleet_hash) and f"v={self.fleet_hash}" in query_string.split("&")

    def read(self, url_path: str) -> str:
        """The source of a served asset, by its URL path (a ``KeyError`` for one that is not served)."""
        for prefix, directory in self.roots.items():
            if url_path.startswith(prefix + "/"):
                return (directory / url_path[len(prefix) + 1 :]).read_text(encoding="utf-8")
        raise KeyError(url_path)

    def _stamp(self, url_path: str) -> str | None:
        stamp = self.hashes.get(posixpath.normpath(url_path))
        return f"?v={stamp}" if stamp else None

    def stamp_html(self, html: str) -> str:
        """Stamp ``?v=<hash>`` onto every ``href``/``src`` that names a served ``.css``/``.js``."""

        def sub(m: re.Match[str]) -> str:
            attr, quote, path = m.group(1, 2, 3)
            stamp = self._stamp(path)
            return f"{attr}={quote}{path}{stamp}{quote}" if stamp else m.group(0)

        return re.sub(_HTML_ASSET, sub, html)

    def stamp_js(self, source: str, url_path: str) -> str:
        """Stamp ``?v=<hash>`` onto every literal module specifier in ``source``.

        ``url_path`` is the URL the source is served at: it resolves the relative specifiers
        (``./x.js``, ``../icons/icons.js``) to the key the hash map is held under. A specifier naming
        nothing served is left alone.
        """
        base = posixpath.dirname(url_path)

        def sub(m: re.Match[str]) -> str:
            lead, quote, spec = m.group(1, 2, 3)
            stamp = self._stamp(spec if spec.startswith("/") else posixpath.join(base, spec))
            return f"{lead}{quote}{spec}{stamp}{quote}" if stamp else m.group(0)

        return re.sub(_JS_IMPORT, sub, source)
