"""Story 10: OBS profiles — OBS follows each item's profile; a profile's scene is
changed in Settings. Runs its own instance wired to a stand-in OBS."""

from __future__ import annotations

import tempfile
from pathlib import Path

from playwright.sync_api import Page, expect

from tests.e2e.conftest import boot_instance, shot, stop_instance
from tests.fixtures.demo import build_demo_session
from tests.fixtures.fake_obs import FakeObs


def test_obs_follows_the_profile(page: Page, shots) -> None:
    fake = FakeObs(["Slides + camera", "Camera PiP", "Screen only", "Camera big"])
    with tempfile.TemporaryDirectory(prefix="fs-e2e-obs-", ignore_cleanup_errors=True) as tmp:
        proc, inst = boot_instance(
            Path(tmp),
            obs={"enabled": True, "host": "127.0.0.1", "port": fake.port, "password": ""},
            profiles={"camera_strip": {"scene": "Slides + camera"}, "camera_pip": {"scene": "Camera PiP"}},
        )
        try:
            sid, _ = build_demo_session(inst.root / "sessions" / "demo" / "obs-story", inst.root / "sessions.local.yaml")
            page.set_viewport_size({"width": 1440, "height": 900})
            page.goto(f"{inst.base_url}/presenter?session={sid}")
            expect(page.locator(".p-chips")).to_contain_text("OBS · profile Camera strip", timeout=15000)
            page.keyboard.press("ArrowRight")  # the map: camera PiP
            expect(page.locator(".p-chips")).to_contain_text("OBS · profile Camera PiP")
            assert fake.switched[-1] == "Camera PiP"

            # Settings: give Camera PiP another scene
            page.goto(f"{inst.base_url}/")
            page.locator("[data-open-settings]").first.click()
            expect(page.locator(".settings-card .chip", has_text="OBS")).to_contain_text("OBS connected")
            page.locator("[data-profile=camera_pip]").click()
            page.locator("dialog select[name=scene]").select_option("Camera big")
            page.locator("dialog .detail-save-btn").click()
            expect(page.locator("[data-profile=camera_pip]")).to_contain_text("Camera big")
            shot(page, shots / "story-10-obs-1-settings.png")
        finally:
            stop_instance(proc)
            fake.close()
