"""Shared guild navigation for the server-rendered pages."""

GUILD_PAGES = [
    ("Home", "/g/{guild_id}"),
    ("Report", "/g/{guild_id}/report"),
    ("Calculator", "/g/{guild_id}/calc"),
    ("Planner", "/g/{guild_id}/platoons"),
    ("Assignments", "/g/{guild_id}/assignments"),
]


def guild_nav(active, guild_id=None):
    """Nav items for a guild page; `active` is the label of the current page."""
    if not guild_id:
        return []
    return [
        {"label": label, "href": href.format(guild_id=guild_id), "active": label == active}
        for label, href in GUILD_PAGES
    ]
