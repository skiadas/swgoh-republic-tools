"""Playwright browser tests (run: uv run pytest -m browser).

Boots the real app against the local data dir and drives Chromium to verify
the server-rendered + htmx pages actually work in a browser (the pytest route
tests can't run JS). Requires `playwright install chromium`.

These tests are the interface review: every interactive control on every page
should round-trip through htmx and end in the expected state.
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


@pytest.fixture
def accept_dialogs(page):
    def _install():
        page.on("dialog", lambda d: d.accept())
    return _install


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


def seed_filled_planner(page, app_url):
    """Open the planner with a Coruscant day-1 plan and assign the first chip."""
    page.goto(f"{app_url}/g/{GUILD}/platoons")
    page.locator(".planet", has_text="Coruscant").wait_for()
    chip = page.locator(".planet", has_text="Coruscant").locator("button.chip").first
    chip.click()
    page.locator("#picker .modal").wait_for()
    page.locator("#picker .pick-row").first.click()
    page.locator("#picker .modal").wait_for(state="hidden", timeout=8000)
    return page.locator(".planet", has_text="Coruscant").locator("button.chip").first


# ---------------- admin / nav ----------------

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
def test_admin_guild_page(admin_page, app_url, errors):
    page = admin_page
    page.goto(f"{app_url}/admin")
    page.locator("h1", has_text="Admin").wait_for()
    page.click("a:has-text('manage')")
    page.wait_for_url("**/admin/g/**")
    assert page.locator("button", has_text="Refresh now (fetch from EA)").count() == 1
    assert page.locator("button", has_text="Regenerate pages (from cache)").count() == 1
    assert page.locator("button", has_text="Remove guild").count() == 1
    assert not errors, f"console errors: {errors}"


@pytest.mark.browser
def test_guild_home_and_nav(admin_page, app_url, errors):
    page = admin_page
    page.goto(f"{app_url}/g/{GUILD}")
    page.locator("nav.gnav").wait_for()
    links = page.locator("nav.gnav a")
    hrefs = [links.nth(i).get_attribute("href") for i in range(links.count())]
    assert hrefs == [f"/g/{GUILD}", f"/g/{GUILD}/report", f"/g/{GUILD}/calc", f"/g/{GUILD}/platoons", f"/g/{GUILD}/assignments"]
    active = [links.nth(i).text_content() for i in range(links.count()) if links.nth(i).get_attribute("class") == "active"]
    assert active == ["Home"], f"Home should be the active nav item, got {active}"
    assert not errors, f"console errors: {errors}"


# ---------------- calculator ----------------

@pytest.mark.browser
def test_calc_goal_change_rerenders(admin_page, app_url, errors):
    page = admin_page
    page.goto(f"{app_url}/g/{GUILD}/calc")
    page.locator("h1", has_text="Calculator").wait_for()
    page.locator('label.gopt', has=page.locator('input[name="d1-Mustafar"][value="2"]')).click()
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
def test_calc_deploy_slider(admin_page, app_url, errors):
    page = admin_page
    page.goto(f"{app_url}/g/{GUILD}/calc")
    page.locator('input[name="deploy"]').wait_for()
    page.locator('input[name="deploy"]').evaluate(
        "el => { el.value = 60; el.dispatchEvent(new Event('change', { bubbles: true })); }"
    )
    page.locator(".controls b", has_text="60%").first.wait_for(timeout=8000)
    assert page.locator('input[name="deploy"]').input_value() == "60", "deploy slider should persist"
    assert not errors, f"console errors: {errors}"


@pytest.mark.browser
def test_calc_cm_slider(admin_page, app_url, errors):
    page = admin_page
    page.goto(f"{app_url}/g/{GUILD}/calc")
    page.locator('input[name="d1-Coruscant-cm"]').wait_for()
    page.locator('input[name="d1-Coruscant-cm"]').evaluate(
        "el => { el.value = 70; el.dispatchEvent(new Event('change', { bubbles: true })); }"
    )
    page.locator(".cmval", has_text="70%").first.wait_for(timeout=8000)
    assert page.locator('input[name="d1-Coruscant-cm"]').input_value() == "70", "cm slider should persist"
    assert not errors, f"console errors: {errors}"


@pytest.mark.browser
def test_calc_compact_toggle(admin_page, app_url, errors):
    page = admin_page
    page.goto(f"{app_url}/g/{GUILD}/calc")
    page.locator('input[name="compact"]').wait_for()
    gp = page.locator(".controls .muted b").first.text_content()
    assert gp == "654,983,535", f"default should show full numbers, got {gp}"
    page.locator('input[name="compact"]').check()
    page.locator(".controls .muted b", has_text="655M").first.wait_for(timeout=8000)
    assert page.locator(".controls .muted b").first.text_content() == "655M", "compact should abbreviate to 1 decimal"
    assert page.locator('input[name="compact"]:checked').count() == 1, "compact checkbox should persist checked"
    page.locator(".summary", has_text="Total stars").wait_for(timeout=8000)
    assert not errors, f"console errors: {errors}"


@pytest.mark.browser
def test_calc_unlock_toggle(admin_page, app_url, errors):
    seed_plan(days={"1": {"Coruscant": {"goal": "1", "platoons": 6, "cmPct": 50}},
                    "2": {"Bracca": {"goal": "1", "platoons": 6, "cmPct": 50}}})
    page = admin_page
    page.goto(f"{app_url}/g/{GUILD}/calc")
    page.locator('input[name="unlock-zeffo"]').wait_for(timeout=8000)
    page.locator('input[name="unlock-zeffo"]').check()
    page.locator(".day", has_text="Day 3").locator(".pname", has_text="Zeffo").wait_for(timeout=8000)
    assert page.locator('input[name="unlock-zeffo"]:checked').count() == 1, "unlock checkbox should persist checked"
    page.locator(".summary", has_text="Total stars").wait_for(timeout=8000)
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
def test_calc_optimize_planet_mode(admin_page, app_url, errors):
    page = admin_page
    page.goto(f"{app_url}/g/{GUILD}/calc")
    page.click("button:has-text('Optimize')")
    page.locator("#opt .modal").wait_for()
    page.check('input[name="opt-mode"][value="planet"]')
    assert page.locator("#opt-planet").evaluate("el => el.style.display") != "none", "planet mode should be visible"
    assert page.locator("#opt-level").evaluate("el => el.style.display") == "none", "level mode should be hidden"
    page.locator('input[name="est-planet-Coruscant"]').evaluate(
        "el => { el.value = 25; el.dispatchEvent(new Event('input', { bubbles: true })); }"
    )
    page.click("button:has-text('Run')")
    page.locator("#opt-result .opt-line").wait_for(timeout=8000)
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


# ---------------- planner ----------------

@pytest.mark.browser
def test_planner_assign(admin_page, app_url, errors):
    seed_plan(days={"1": {"Coruscant": {"goal": "1", "platoons": 6, "cmPct": 50}}})
    page = admin_page
    page.goto(f"{app_url}/g/{GUILD}/platoons")
    page.locator(".planet", has_text="Coruscant").wait_for()
    chip = seed_filled_planner(page, app_url)
    assert chip.text_content() != "—", "chip should show the assigned member"
    assert not errors, f"console errors: {errors}"


@pytest.mark.browser
def test_planner_day_tab_content_and_highlight(admin_page, app_url, errors):
    seed_plan(days={"1": {"Coruscant": {"goal": "1", "platoons": 6, "cmPct": 50}},
                    "3": {"Zeffo": {"goal": "0", "platoons": 2, "cmPct": 10}}})
    page = admin_page
    page.goto(f"{app_url}/g/{GUILD}/platoons")
    page.locator(".planet", has_text="Coruscant").wait_for()
    day1 = page.locator(".tabs .tab", has_text="Day 1")
    day3 = page.locator(".tabs .tab", has_text="Day 3")
    assert "on" in day1.get_attribute("class")
    day3.click()
    page.locator(".planet", has_text="Zeffo").wait_for(timeout=8000)
    assert "on" in day3.get_attribute("class"), "day 3 tab highlight should move"
    assert "on" not in day1.get_attribute("class"), "day 1 tab should lose highlight"
    assert not errors, f"console errors: {errors}"


@pytest.mark.browser
def test_planner_picker_clear_fill(admin_page, app_url, errors):
    seed_plan(days={"1": {"Coruscant": {"goal": "1", "platoons": 6, "cmPct": 50}}})
    page = admin_page
    chip = seed_filled_planner(page, app_url)
    assert chip.text_content() != "—"
    chip.click()
    page.locator("#picker .modal").wait_for()
    clear = page.locator("#picker .pick-row.clear")
    assert clear.count() == 1, "clear option should appear once a fill is set"
    clear.click()
    page.locator("#picker .modal").wait_for(state="hidden", timeout=8000)
    assert page.locator(".planet .chip .lbl").first.text_content() == "—", "chip should be cleared"
    assert not errors, f"console errors: {errors}"


@pytest.mark.browser
def test_planner_publish_to_guild(admin_page, app_url, errors, accept_dialogs):
    from server.db import DB

    seed_plan(days={"1": {"Coruscant": {"goal": "1", "platoons": 6, "cmPct": 50}}})
    page = admin_page
    seed_filled_planner(page, app_url)
    db = DB("data/service.db")
    assert db.get_draft(GUILD) is not None, "editing should create a draft"
    accept_dialogs()
    page.click("button:has-text('Publish to guild')")
    deadline = time.time() + 8
    while time.time() < deadline:
        if db.get_draft(GUILD) is None:
            break
        time.sleep(0.1)
    assert db.get_draft(GUILD) is None, "publish should clear the draft"
    plans = db.list_plans(GUILD)
    assert any(p["is_current"] for p in plans), "publish should set a current plan"
    assert not errors, f"console errors: {errors}"


@pytest.mark.browser
def test_planner_clear_all(admin_page, app_url, errors, accept_dialogs):
    seed_plan(days={"1": {"Coruscant": {"goal": "1", "platoons": 6, "cmPct": 50}}})
    page = admin_page
    chip = seed_filled_planner(page, app_url)
    assert chip.text_content() != "—"
    accept_dialogs()
    page.click("button:has-text('Clear all')")
    page.locator(".notice", has_text="0 assignments").wait_for(timeout=8000)
    assert page.locator(".planet .chip .lbl").first.text_content() == "—"
    assert not errors, f"console errors: {errors}"


@pytest.mark.browser
def test_planner_generate_modal(admin_page, app_url, errors):
    seed_plan(days={"1": {"Coruscant": {"goal": "1", "platoons": 6, "cmPct": 50}}})
    page = admin_page
    page.goto(f"{app_url}/g/{GUILD}/platoons")
    page.locator(".planet", has_text="Coruscant").wait_for()
    page.click("button:has-text('Generate')")
    page.locator("#gen .modal").wait_for()
    page.locator("#gen button", has_text="Generate").click()
    page.locator("#gen .modal").wait_for(state="hidden", timeout=8000)
    page.locator(".notice", has_text="Day 1:").wait_for(timeout=8000)
    assert not errors, f"console errors: {errors}"


# ---------------- assignments ----------------

@pytest.mark.browser
def test_assignments_roster(admin_page, app_url, errors):
    seed_plan(fills={"Coruscant": {"1": {"0": "591764377"}}})
    page = admin_page
    page.goto(f"{app_url}/g/{GUILD}/assignments")
    page.locator("h1", has_text="Assignments").wait_for()
    page.locator("tr.mrow", has_text="Abo6").wait_for(timeout=8000)
    assert not errors, f"console errors: {errors}"


@pytest.mark.browser
def test_assignments_search_filters(admin_page, app_url, errors):
    seed_plan(fills={"Coruscant": {"1": {"0": "591764377"}}})
    page = admin_page
    page.goto(f"{app_url}/g/{GUILD}/assignments")
    page.locator("tr.mrow").first.wait_for(timeout=8000)
    search = page.locator('input[name="search"]')
    search.fill("Abo6")
    page.locator("tr.mrow", has_text="Abo6").wait_for(timeout=8000)
    search.fill("zzzz-no-such-member")
    page.locator("#roster .notice", has_text="No members match").wait_for(timeout=8000)
    search.fill("")
    page.locator("tr.mrow").first.wait_for(timeout=8000)
    assert not errors, f"console errors: {errors}"


@pytest.mark.browser
def test_assignments_member_detail_toggle(admin_page, app_url, errors):
    seed_plan(fills={"Coruscant": {"1": {"0": "591764377"}}})
    page = admin_page
    page.goto(f"{app_url}/g/{GUILD}/assignments")
    row = page.locator("tr.mrow", has_text="Abo6")
    row.wait_for(timeout=8000)
    ac = row.get_attribute("data-ac")
    det = page.locator(f"#mdet-{ac}")
    row.click()
    det.wait_for()
    assert det.evaluate("el => el.style.display") == "", "detail should expand"
    row.click()
    assert det.evaluate("el => el.style.display") == "none", "detail should collapse"
    assert not errors, f"console errors: {errors}"


@pytest.mark.browser
def test_assignments_copy_markdown(admin_page, app_url, errors):
    seed_plan(fills={"Coruscant": {"1": {"0": "591764377"}}})
    page = admin_page
    page.context.grant_permissions(["clipboard-read", "clipboard-write"], origin=app_url)
    page.route("**/assignments/member/*/markdown", lambda r: r.fulfill(
        status=200, content_type="text/plain; charset=utf-8", body="MARKDOWN-OK\n"
    ))
    page.goto(f"{app_url}/g/{GUILD}/assignments")
    page.locator(".copy-btn").first.wait_for(timeout=8000)
    page.locator(".copy-btn").first.click()
    text = ""
    for _ in range(80):
        text = page.evaluate("navigator.clipboard.readText()")
        if "MARKDOWN-OK" in text:
            break
        time.sleep(0.1)
    assert "MARKDOWN-OK" in text, f"clipboard should hold the fetched markdown, got {text!r}"
    assert not errors, f"console errors: {errors}"


# ---------------- squad report ----------------

@pytest.mark.browser
def test_report_tabs_and_players_detail(admin_page, app_url, errors):
    page = admin_page
    page.goto(f"{app_url}/g/{GUILD}/report")
    page.locator(".vtabs").wait_for()
    matrix_tab = page.locator(".vtabs button", has_text="Matrix")
    players_tab = page.locator(".vtabs button", has_text="Players")
    assert "active" in matrix_tab.get_attribute("class"), "matrix is the default tab"
    players_tab.click()
    page.locator(".player-card h3").wait_for(timeout=8000)
    assert "active" in players_tab.get_attribute("class"), "players tab highlight should move"
    assert "active" not in matrix_tab.get_attribute("class"), "matrix tab should lose highlight"
    assert page.locator("#view select option[selected]").count() >= 1, "players select should have a default selection"
    for view in ("squads", "needs", "matrix"):
        page.locator(".vtabs button", has_text=view).click()
        page.locator("#view table, #view .notice").first.wait_for(timeout=8000)
    assert not errors, f"console errors: {errors}"


@pytest.mark.browser
def test_report_players_select_switches_detail(admin_page, app_url, errors):
    page = admin_page
    page.goto(f"{app_url}/g/{GUILD}/report?view=players")
    page.locator(".player-card h3").wait_for(timeout=8000)
    sel = page.locator("#view select[name=player]")
    second_name = sel.locator("option").nth(1).text_content().split(" (")[0]
    sel.select_option(index=1)
    page.locator(".player-card h3", has_text=second_name).wait_for(timeout=8000)
    assert not errors, f"console errors: {errors}"


@pytest.mark.browser
def test_report_squads_select_switches_table(admin_page, app_url, errors):
    page = admin_page
    page.goto(f"{app_url}/g/{GUILD}/report?view=squads")
    page.locator(".squad-block h3").wait_for(timeout=8000)
    names = [s["squad"] for s in json.load(open(f"data/guilds/{GUILD}.squads.json"))["bySquad"]]
    page.locator("#view select[name=squad]").select_option(index=1)
    page.locator(".squad-block h3", has_text=names[1]).wait_for(timeout=8000)
    assert not errors, f"console errors: {errors}"


@pytest.mark.browser
def test_report_needs_search_filters(admin_page, app_url, errors):
    page = admin_page
    page.goto(f"{app_url}/g/{GUILD}/report?view=needs")
    page.locator(".need-table").wait_for(timeout=8000)
    search = page.locator(".need-controls input[name=search]")
    search.fill("zzzz-no-such-player")
    page.locator("#view .notice", has_text="No needs").wait_for(timeout=8000)
    search.fill("")
    page.locator(".need-table").wait_for(timeout=8000)
    assert not errors, f"console errors: {errors}"


@pytest.mark.browser
def test_report_matrix_sort_by_gp(admin_page, app_url, errors):
    page = admin_page
    page.goto(f"{app_url}/g/{GUILD}/report")
    page.locator(".m-controls select[name=sort]").wait_for()
    assert page.locator(".m-controls select[name=sort]").input_value() == "name"
    page.locator(".m-controls select[name=sort]").select_option("gp")
    page.locator("table tbody tr", has_text="Dakraa").first.wait_for(timeout=8000)
    assert page.locator(".m-controls select[name=sort]").input_value() == "gp", "sort selection should persist"
    first = page.locator("table tbody tr").first.locator("td").first.text_content()
    assert "Dakraa" in first, f"top-GP player should sort first, got {first}"
    assert not errors, f"console errors: {errors}"


@pytest.mark.browser
def test_report_matrix_hide_complete_roundtrip(admin_page, app_url, errors):
    page = admin_page
    page.goto(f"{app_url}/g/{GUILD}/report")
    page.locator(".m-controls input[name=hide]").wait_for()
    before = page.locator("table tbody tr").count()
    page.locator(".m-controls input[name=hide]").check()
    page.locator(".m-controls input[name=hide]:checked").wait_for(timeout=8000)
    after = page.locator("table tbody tr").count()
    assert after <= before, "hiding complete players must not grow the matrix"
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
