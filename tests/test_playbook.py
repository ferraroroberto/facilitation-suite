"""A session's playbook.md: rendered safely, created from the template, never overwritten, carried by a duplicate."""

from __future__ import annotations

from pathlib import Path

from src.sessions.playbook import PLAYBOOK_FILE, TEMPLATE, render_markdown

SAMPLE = """\
# Title

Some **bold**, some *italic* and `code <b>`, plus a [link](https://example.com/a?x=1&y=2).

## List

- one
- two
  - nested
1. first
2. second

| Name | Role |
|------|:----:|
| Ann  | host |

> quoted text

```
raw <script>alert(1)</script>
```
"""


def _new_session(client) -> tuple[str, Path]:  # noqa: ANN001 — the conftest TestClient
    sid = client.post("/api/sessions", json={"title": "Demo", "workshop": "w", "folder": "demo"}).json()["id"]
    folder = Path(client.app.state.store.folder(sid))
    return sid, folder


def test_markdown_renders_the_constructs_the_card_promises() -> None:
    html = render_markdown(SAMPLE)
    assert "<h4>Title</h4>" in html and "<h5>List</h5>" in html
    assert "<strong>bold</strong>" in html and "<em>italic</em>" in html
    assert "<code>code &lt;b&gt;</code>" in html
    assert '<a href="https://example.com/a?x=1&amp;y=2" target="_blank" rel="noopener noreferrer">link</a>' in html
    assert "<ul><li>one</li><li>two<ul><li>nested</li></ul></li></ul>" in html
    assert "<ol><li>first</li><li>second</li></ol>" in html
    assert '<th>Name</th><th style="text-align:center">Role</th>' in html
    assert '<td>Ann</td><td style="text-align:center">host</td>' in html
    assert "<blockquote><p>quoted text</p></blockquote>" in html
    assert "<pre><code>raw &lt;script&gt;alert(1)&lt;/script&gt;</code></pre>" in html


def test_raw_html_is_escaped_never_passed_through() -> None:
    html = render_markdown('<script>alert(1)</script>\n\n<img src=x onerror=alert(1)>\n\n**<b>x</b>**')
    assert "<script" not in html and "<img" not in html and "<b>" not in html
    assert "&lt;script&gt;" in html and "&lt;img src=x onerror=alert(1)&gt;" in html


def test_a_link_to_a_script_keeps_its_label_only() -> None:
    for target in ("javascript:alert(1)", "JaVaScRiPt:alert(1)", "data:text/html,x", "java&#115;cript:alert(1)"):
        html = render_markdown(f"[click]({target})")
        assert "href" not in html and "click" in html, target
    assert 'href="docs/a.md"' in render_markdown("[a](docs/a.md)")
    assert 'href="mailto:a@b.co"' in render_markdown("[a](mailto:a@b.co)")


def test_a_trailing_hash_in_a_heading_text_is_kept() -> None:
    assert render_markdown("# Learn C#") == "<h4>Learn C#</h4>"
    assert render_markdown("## Closed ##") == "<h5>Closed</h5>"


def test_a_loose_list_stays_one_list_and_a_backslash_escapes() -> None:
    assert render_markdown("- a\n\n- b").count("<ul>") == 1
    assert render_markdown(r"\*not italic\*") == "<p>*not italic*</p>"


def test_the_template_renders_and_has_the_headings_the_skill_reads() -> None:
    html = render_markdown(TEMPLATE)
    for heading in ("Purpose", "What to analyse", "Output format", "Rules", "People to leave out"):
        assert f"<h5>{heading}</h5>" in html


def test_get_without_a_playbook_says_so(client) -> None:  # noqa: ANN001
    sid, _folder = _new_session(client)
    r = client.get(f"/api/sessions/{sid}/playbook")
    assert r.status_code == 200
    assert r.json() == {"exists": False, "html": "", "modified_ms": 0}
    assert client.get(f"/api/sessions/{sid}").json()["folder"]["playbook"] is False


def test_get_renders_the_file_and_escapes_its_html(client) -> None:  # noqa: ANN001
    sid, folder = _new_session(client)
    (folder / PLAYBOOK_FILE).write_text("# Rules\n\n<script>alert(1)</script> and **bold**\n", encoding="utf-8")
    body = client.get(f"/api/sessions/{sid}/playbook").json()
    assert body["exists"] is True and body["modified_ms"] > 0
    assert "<h4>Rules</h4>" in body["html"] and "<strong>bold</strong>" in body["html"]
    assert "<script" not in body["html"] and "&lt;script&gt;" in body["html"]
    assert client.get(f"/api/sessions/{sid}").json()["folder"]["playbook"] is True


def test_post_creates_the_template_then_refuses_to_overwrite(client) -> None:  # noqa: ANN001
    sid, folder = _new_session(client)
    first = client.post(f"/api/sessions/{sid}/playbook")
    assert first.status_code == 201 and first.json()["exists"] is True
    assert (folder / PLAYBOOK_FILE).read_text(encoding="utf-8") == TEMPLATE
    (folder / PLAYBOOK_FILE).write_text("# My own\n", encoding="utf-8")
    again = client.post(f"/api/sessions/{sid}/playbook")
    assert again.status_code == 409 and again.json()["error"]["code"] == "playbook_exists"
    assert (folder / PLAYBOOK_FILE).read_text(encoding="utf-8") == "# My own\n"


def test_edit_in_the_editor_needs_a_playbook(client) -> None:  # noqa: ANN001
    sid, _folder = _new_session(client)
    r = client.post(f"/api/sessions/{sid}/open", json={"what": "playbook"})
    assert r.status_code == 404 and r.json()["error"]["code"] == "playbook_missing"


def test_duplicate_carries_the_playbook(client) -> None:  # noqa: ANN001
    sid, folder = _new_session(client)
    (folder / PLAYBOOK_FILE).write_text("# Mine\n", encoding="utf-8")
    dup = client.post(f"/api/sessions/{sid}/duplicate", json={"title": "Demo 2", "folder": "demo-2"})
    assert dup.status_code == 201
    copy = client.get(f"/api/sessions/{dup.json()['id']}/playbook").json()
    assert copy["exists"] is True and "<h4>Mine</h4>" in copy["html"]
