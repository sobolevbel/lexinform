from pathlib import Path

from tools.check_docs import check_site


def test_docs_checker_checks_raw_html_links_and_unicode_anchors(tmp_path: Path) -> None:
    docs = tmp_path / "docs"
    site = tmp_path / "site"
    docs.mkdir()
    site.mkdir()
    (docs / "index.md").write_text("# Начало")
    (docs / "map.html").write_text("<h1>Map</h1>")
    (site / "index.html").write_text('<h1 id="начало">Начало</h1><a href="map.html">Map</a>')
    (site / "map.html").write_text(
        '<a href="index.html#%D0%BD%D0%B0%D1%87%D0%B0%D0%BB%D0%BE">Back</a>'
    )
    assert check_site(site, docs) == []

    (site / "map.html").write_text('<a href="index.html#lost">Broken</a><img src="missing.png">')
    errors = check_site(site, docs)
    assert any("missing anchor: index.html#lost" in error for error in errors)
    assert any("missing target: missing.png" in error for error in errors)


def test_docs_checker_rejects_missing_pages_stale_output_and_state(tmp_path: Path) -> None:
    docs = tmp_path / "docs"
    site = tmp_path / "site"
    docs.mkdir()
    site.mkdir()
    (docs / "new.md").write_text("# New")
    (site / "old.html").write_text("<h1>Old</h1>")
    (site / ".env").write_text("EXAMPLE=not-a-secret")
    (site / "state.sql").write_text("SELECT 1;")
    errors = check_site(site, docs)
    assert any("new/index.html" in error and "absent" in error for error in errors)
    assert any("old.html" in error and "unexpected" in error for error in errors)
    assert any(".env" in error and "forbidden" in error for error in errors)
    assert any("state.sql" in error and "forbidden" in error for error in errors)


def test_docs_checker_limits_placeholder_exception_and_rejects_external_assets(
    tmp_path: Path,
) -> None:
    docs = tmp_path / "docs"
    site = tmp_path / "site"
    docs.mkdir()
    (site / "plans").mkdir(parents=True)
    (docs / "index.md").write_text("# Start")
    (site / "index.html").write_text(
        '<a href="#">Broken</a><a href="../outside">Escape</a>'
        '<script src="https://example.org/app.js"></script>'
    )
    (site / "plans/website-design-prototype.html").write_text('<a href="#">Mockup</a>')
    errors = check_site(site, docs)
    assert any("index.html:1: placeholder" in error for error in errors)
    assert not any("prototype.html:1: placeholder" in error for error in errors)
    assert any("link escapes site" in error for error in errors)
    assert any("external resource" in error for error in errors)
