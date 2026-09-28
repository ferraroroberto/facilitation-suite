"""The shell: pages, build identity, error envelope, config fallbacks."""

from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import sys
import time
import uuid
from pathlib import Path

import pytest

from src import config as config_mod
from src import logger as logger_mod
from src.no_window import NO_WINDOW


def test_version_reports_build_identity(client) -> None:
    body = client.get("/api/version").json()
    assert body["app"] == "facilitation-suite"
    assert body["git_sha"]
    assert body["schema_version"] == 1


def test_healthz(client) -> None:
    assert client.get("/healthz").json() == {"status": "ok"}


@pytest.mark.parametrize("path", ["/", "/presenter", "/stage"])
def test_pages_are_served_no_cache(client, path: str) -> None:
    res = client.get(path)
    assert res.status_code == 200
    assert res.headers["cache-control"] == "no-cache"
    assert "<!--SPRITE-->" not in res.text


def test_index_inlines_the_sprite(client) -> None:
    html = client.get("/").text
    assert 'id="i-presentation"' in html


def test_unknown_route_uses_the_error_envelope(client) -> None:
    res = client.get("/api/nope")
    assert res.status_code == 404
    assert res.json()["error"]["code"] == "not_found"


def test_static_is_revalidated(client) -> None:
    res = client.get("/static/css/tokens.css")
    assert res.status_code == 200
    assert res.headers["cache-control"] == "no-cache"


def test_config_defaults_on_missing_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FS_CONFIG_PATH", str(tmp_path / "absent.json"))
    cfg = config_mod.load_config()
    assert cfg.port == 8449
    assert cfg.source == "defaults"


def test_config_wrong_type_falls_back(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "c.json"
    path.write_text(json.dumps({"port": "not-a-port", "obs": {"port": 4460, "enabled": "yes"}}), encoding="utf-8")
    monkeypatch.setenv("FS_CONFIG_PATH", str(path))
    cfg = config_mod.load_config()
    assert cfg.port == 8449
    assert cfg.obs.port == 4460
    assert cfg.obs.enabled is True


# ---- config.host / config.port are the one bind setting (#39) ----------------------

def _loopback_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> config_mod.AppConfig:
    path = tmp_path / "c.json"
    path.write_text(json.dumps({"host": "127.0.0.1", "port": 8601}), encoding="utf-8")
    monkeypatch.setenv("FS_CONFIG_PATH", str(path))
    return config_mod.load_config()


def test_tray_manager_binds_what_the_config_says(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.webapp import manager as manager_mod

    monkeypatch.setattr(manager_mod, "uvicorn_ssl_args", lambda: [])
    cmd = manager_mod.WebappManager(manager_mod.WebappManagerConfig.from_app_config(
        _loopback_config(tmp_path, monkeypatch)))._build_command()
    assert cmd[cmd.index("--host") + 1] == "127.0.0.1"
    assert cmd[cmd.index("--port") + 1] == "8601"
    # The defaults are the config's defaults: every interface, :8449 (the phone remote needs it).
    default = manager_mod.WebappManagerConfig.from_app_config(config_mod.AppConfig())
    assert (default.host, default.port) == ("0.0.0.0", config_mod.DEFAULT_PORT)


def test_loopback_bind_opens_the_loopback_url(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.webapp import manager as manager_mod

    monkeypatch.setattr(manager_mod, "cert_hostname", lambda: "pc.example.ts.net")
    monkeypatch.setattr(manager_mod, "cert_paths", lambda: ("cert.pem", "key.pem"))
    shared = manager_mod.WebappManager(manager_mod.WebappManagerConfig(host="0.0.0.0", port=8601))
    local = manager_mod.WebappManager(manager_mod.WebappManagerConfig(host="127.0.0.1", port=8601))
    assert shared.public_url == "https://pc.example.ts.net:8601"
    assert local.public_url == "https://127.0.0.1:8601"


def test_launcher_webapp_binds_what_the_config_says(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import uvicorn

    import launcher
    from src import certs

    _loopback_config(tmp_path, monkeypatch)
    seen: dict = {}
    monkeypatch.setattr(uvicorn, "run", lambda app, **kwargs: seen.update(kwargs))
    monkeypatch.setattr(certs, "ensure_cert_fresh", lambda *args, **kwargs: None)
    monkeypatch.setattr(certs, "uvicorn_ssl_kwargs", lambda: {})
    monkeypatch.setattr(logger_mod, "configure_logging", lambda *args, **kwargs: None)
    assert launcher.main(["launcher.py", "webapp"]) == 0
    assert (seen["host"], seen["port"]) == ("127.0.0.1", 8601)


def test_log_follows_fs_data_dir(tmp_path: Path) -> None:
    """A second instance (a test run, a scratch server) must not write into the tray's log (#26)."""
    marker = f"log-probe-{uuid.uuid4().hex}"
    data = tmp_path / "data"
    script = ("import logging; from src.logger import configure_logging; "
              f"configure_logging(); logging.getLogger('probe').info({marker!r})")
    env = dict(os.environ, FS_DATA_DIR=str(data), PYTHONUTF8="1")
    subprocess.run([sys.executable, "-c", script], cwd=config_mod.PROJECT_ROOT, env=env, check=True,
                   capture_output=True, timeout=60, creationflags=NO_WINDOW)
    assert marker in (data / "logs" / "facilitation-suite.log").read_text(encoding="utf-8")
    repo_log = config_mod.PROJECT_ROOT / "data" / "logs" / "facilitation-suite.log"
    assert not repo_log.is_file() or marker not in repo_log.read_text(encoding="utf-8", errors="replace")


def test_log_defaults_to_the_repo_data_dir(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("FS_DATA_DIR", raising=False)
    assert logger_mod.default_log_file() == config_mod.PROJECT_ROOT / "data" / "logs" / "facilitation-suite.log"


# ---- one writer per log file (#37) -------------------------------------------------

_ROLE = r'''
import logging, sys, time
from pathlib import Path

role, go, n = sys.argv[1], Path(sys.argv[2]), int(sys.argv[3])

def fill(*_args, **_kwargs):
    log = logging.getLogger(role)
    log.info("%s ready", role)  # every process now holds its log open
    go.with_name(role + ".ready").touch()
    while not go.exists():
        time.sleep(0.05)
    for i in range(n):
        log.info("%s-%05d %s", role, i, "x" * 400)
    return 1

if role == "tray":
    import app.tray.tray as tray
    tray.run_tray = lambda config: fill() and 0
    import launcher
    sys.exit(launcher.main(["launcher.py", "tray"]))
elif role == "webapp":
    import uvicorn
    import src.certs as certs
    uvicorn.run = fill
    certs.ensure_cert_fresh = lambda *args, **kwargs: None
    import launcher
    sys.exit(launcher.main(["launcher.py", "webapp"]))
else:  # the PDF helper the webapp spawns
    import src.results.pdf as pdf
    pdf.print_pdf = lambda html, out: fill()
    sys.exit(pdf.main(["pdf", "in.html", "out.pdf"]))
'''


def test_tray_webapp_and_helper_each_rotate_without_dropping_lines(tmp_path: Path, isolated_env: Path) -> None:
    """The tray, the webapp and a helper all logging past maxBytes at once: every line survives (#37).

    On Windows a file another process holds open cannot be renamed, so two processes rotating one
    file each fail with WinError 32 and drop every later line. Each file must have one writer.
    """
    n = 3000  # ~1.4 MB per process: every writer passes maxBytes once
    go = tmp_path / "go"
    env = dict(os.environ, PYTHONUTF8="1")
    procs: dict[str, subprocess.Popen] = {}
    for role in ("tray", "webapp", "pdf"):
        err = open(tmp_path / f"{role}.err", "wb")  # noqa: SIM115 — closed after the process ends
        procs[role] = subprocess.Popen([sys.executable, "-c", _ROLE, role, str(go), str(n)],
                                       cwd=config_mod.PROJECT_ROOT, env=env, stdout=subprocess.DEVNULL,
                                       stderr=err, creationflags=NO_WINDOW)
        procs[role].err = err  # type: ignore[attr-defined]
    try:
        for role, proc in procs.items():
            for _ in range(600):
                if (tmp_path / f"{role}.ready").exists() or proc.poll() is not None:
                    break
                time.sleep(0.05)
            assert (tmp_path / f"{role}.ready").exists(), (tmp_path / f"{role}.err").read_text(encoding="utf-8")
        go.touch()
        for proc in procs.values():
            proc.wait(timeout=120)
    finally:
        for proc in procs.values():
            if proc.poll() is None:
                proc.kill()
            proc.err.close()  # type: ignore[attr-defined]

    errors = {role: (tmp_path / f"{role}.err").read_text(encoding="utf-8", errors="replace") for role in procs}
    for role, text in errors.items():
        assert procs[role].returncode == 0, text[-2000:]
        at = max(text.find("Logging error"), text.find("WinError 32"))
        assert at < 0, f"{role}: {text[at:at + 1500]}"
    logs = isolated_env / "data" / "logs"
    kept = "".join(p.read_text(encoding="utf-8", errors="replace") for p in logs.glob("*.log*"))
    for role in ("tray", "webapp"):
        assert len(set(re.findall(rf"{role}-\d{{5}}", kept))) == n, f"{role} lost lines"
    assert len({p.name for p in logs.glob("*.log.1")}) >= 2, "each long-lived process rotated its own file"
    assert "pdf-00000" not in kept, "a helper must not hold a log file open (its parent relays its stderr)"
    assert len(set(re.findall(r"pdf-\d{5}", errors["pdf"]))) == n


def test_a_helpers_stderr_is_relayed_at_its_own_level(isolated_env: Path, caplog: pytest.LogCaptureFixture) -> None:
    """A ``to_file=False`` helper opens no log file; its parent re-logs its stderr, levels kept (#37)."""
    script = ("import logging; from src.logger import configure_logging; configure_logging(to_file=False); "
              "logging.getLogger('freeze').error('❌ boom'); logging.getLogger('freeze').info('✅ frozen')")
    proc = subprocess.run([sys.executable, "-c", script], cwd=config_mod.PROJECT_ROOT, capture_output=True,
                          text=True, encoding="utf-8", timeout=60, creationflags=NO_WINDOW)
    assert not (isolated_env / "data" / "logs").exists()
    with caplog.at_level(logging.INFO, logger="parent"):
        logger_mod.relay([*proc.stderr.splitlines(), "", "Traceback (most recent call last):"],
                         logging.getLogger("parent"))
    got = [(r.levelno, r.getMessage()) for r in caplog.records if r.name == "parent"]
    assert got == [(logging.ERROR, "freeze: ❌ boom"), (logging.INFO, "freeze: ✅ frozen"),
                   (logging.INFO, "Traceback (most recent call last):")]
