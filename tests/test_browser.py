"""Playwright browser tests (run: uv run pytest -m browser).

Boots the real app against the local data dir and drives Chromium to verify
the server-rendered + htmx pages actually work in a browser (the pytest route
tests can't run JS). Requires `playwright install chromium`.
"""

import json
import os
import socket
import threading
import time
import urllib.request

import pytest

from server import auth

GUILD = "NW4t0-dBRcG8n-PVhykpKg"


@pytest.fixture(scope="session")
def app_url():
    os.environ.setdefault("SWGOH_ADMIN_TOKEN", "dev")
    os.environ.setdefault("SWGOH_APP_SECRET", "dev")
    import uvicorn

    from server.app import create_app

    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    app = create_app(outdir="data")
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{port}"
    for _ in range(100):
        try:
            urllib.request.urlopen(url + "/healthz", timeout=1)
            break
        except Exception:
            time.sleep(0.1)
    yield url
    server.should_exit = True
    thread.join(timeout=5)


@pytest.fixture
def admin_page(page, app_url):
    page.context.add_cookies([{"name": auth.ADMIN_COOKIE, "value": auth.sign_admin(), "url": app_url}])
    return page


@pytest.fixture
def errors(page):
    out = []
    page.on("pageerror", lambda e: out.append(f"pageerror: {e}"))
    page.on("console", lambda m: out.append(f"console.{m.type}: {m.text}") if m.type == "error" else None)
    return out


@pytest.fixture(autouse=True)
def clean_state():
    from server.db import DB

    db = DB("data/service.db")
    yield
    db.clear_draft(GUILD)
    for p in db.list_plans(GUILD):
        db.delete_plan(p["id"], GUILD)


def seed_plan(days=None, fills=None):
    from server.db import DB

    db = DB("data/service.db")
    db.clear_draft(GUILD)
    payload = {
        "deployPct": 100,
        "unlockZeffo": False,
        "unlockMandalore": False,
        "days": days or {},
        "fills": fills or {},
    }
    db.create_plan(GUILD, "Browser", json.dumps(payload))


@pytest.mark.browser
def test_admin_login_flow(page, app_url, errors):
    page.goto(f"{app_url}/admin")
    page.wait_for_url("**/admin/login")
    page.fill('input[name="token"]', "dev")
    page.click("button:has-text('Sign in')")
    page.wait_for_url("**/admin")
    assert page.locator("h1").text_content() == "Admin"
    assert not errors, f"console errors: {errors}"


@pytest.mark.browser
def test_calc_goal_change_rerenders(admin_page, app_url, errors):
    page = admin_page
    page.goto(f"{app_url}/g/{GUILD}/calc")
    page.locator("h1", has_text="Calculator").wait_for()
    # click the 2★ goal for Mustafar on day 1
    page.locator('label.gopt', has=page.locator('input[name="d1-Mustafar"][value="2"]')).click()
    # after the htmx POST + server re-render, the 2★ label becomes active
    page.locator('label.gopt.on', has=page.locator('input[name="d1-Mustafar"][value="2"]')).wait_for(timeout=8000)
    assert not errors, f"console errors: {errors}"


@pytest.mark.browser
def test_calc_platoons_and_cm(admin_page, app_url, errors):
    page = admin_page
    page.goto(f"{app_url}/g/{GUILD}/calc")
    page.locator('label.gopt', has=page.locator('input[name="d1-Coruscant-plats"][value="3"]')).click()
    page.locator('label.gopt.on', has=page.locator('input[name="d1-Coruscant-plats"][value="3"]')).wait_for(timeout=8000)
    assert not errors, f"console errors: {errors}"


@pytest.mark.browser
def test_planner_assign(admin_page, app_url, errors):
    seed_plan(days={"1": {"Coruscant": {"goal": "1", "platoons": 6, "cmPct": 50}}})
    page = admin_page
    page.goto(f"{app_url}/g/{GUILD}/platoons")
    page.locator(".planet", has_text="Coruscant").wait_for()
    chip = page.locator(".planet", has_text="Coruscant").locator("button.chip").first
    chip.click()
    page.locator("#picker .modal").wait_for()
    member = page.locator("#picker .pick-row").first
    member.click()
    # after assign, the picker closes and the chip shows the member's name
    page.locator("#picker .modal").wait_for(state="hidden", timeout=8000)
    assert not errors, f"console errors: {errors}"


@pytest.mark.browser
def test_assignments_roster(admin_page, app_url, errors):
    seed_plan(fills={"Coruscant": {"1": {"0": "591764377"}}})
    page = admin_page
    page.goto(f"{app_url}/g/{GUILD}/assignments")
    page.locator("h1", has_text="Assignments").wait_for()
    page.locator("tr.mrow", has_text="Abo6").wait_for(timeout=8000)
    assert not errors, f"console errors: {errors}"


@pytest.mark.browser
def test_calc_optimize_run_apply(admin_page, app_url, errors):
    page = admin_page
    page.goto(f"{app_url}/g/{GUILD}/calc")
    page.locator("h1", has_text="Calculator").wait_for()
    page.click("button:has-text('Optimize')")
    page.locator("#opt .modal").wait_for()
    assert page.locator("#opt").evaluate("el => getComputedStyle(el).position") == "fixed", "optimizer must open as an overlay"
    assert page.locator("#opt").evaluate("el => getComputedStyle(el).display") == "flex", "optimizer overlay must be visible"
    page.click("button:has-text('Run')")
    page.locator("#opt-result .opt-line").wait_for(timeout=8000)
    page.click("button:has-text('Apply plan')")
    page.locator("#opt .modal").wait_for(state="hidden", timeout=8000)
    page.locator(".summary", has_text="Total stars").wait_for(timeout=8000)
    assert not errors, f"console errors: {errors}"


@pytest.mark.browser
def test_calc_optimizer_values_persist(admin_page, app_url, errors):
    page = admin_page
    page.goto(f"{app_url}/g/{GUILD}/calc")
    page.click("button:has-text('Optimize')")
    page.locator("#opt .modal").wait_for()
    page.locator('input[name="est-level-1"]').evaluate(
        "el => { el.value = 60; el.dispatchEvent(new Event('input', { bubbles: true })); }"
    )
    page.click("button:has-text('Cancel')")
    page.locator("#opt .modal").wait_for(state="hidden")
    page.click("button:has-text('Optimize')")
    page.locator("#opt .modal").wait_for()
    assert page.locator('input[name="est-level-1"]').input_value() == "60", "estimate value should persist across reopen"
    page.click("button:has-text('Reset estimates to defaults')")
    assert page.locator('input[name="est-level-1"]').input_value() != "60", "reset restores defaults"
    assert not errors, f"console errors: {errors}"


@pytest.mark.browser
def test_report_views(admin_page, app_url, errors):
    page = admin_page
    page.goto(f"{app_url}/g/{GUILD}/report")
    page.locator(".vtabs").wait_for()
    for view in ("squads", "players", "needs", "matrix"):
        page.locator(f".vtabs button", has_text=view).click()
        page.locator("#view table, #view .notice").first.wait_for(timeout=8000)
    assert not errors, f"console errors: {errors}"


@pytest.mark.browser
def test_report_matrix_filter(admin_page, app_url, errors):
    page = admin_page
    page.goto(f"{app_url}/g/{GUILD}/report")
    page.locator(".m-controls input[name=search]").wait_for()
    search = page.locator(".m-controls input[name=search]")
    search.fill("zzzz-no-such-player")
    page.locator("table tbody tr").first.wait_for(state="hidden", timeout=8000)
    search.fill("")
    page.locator("table tbody tr").first.wait_for(timeout=8000)
    assert not errors, f"console errors: {errors}"
