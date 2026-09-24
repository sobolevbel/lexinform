"""Check local links and the documentation-only build without network access."""

import argparse
import re
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urlsplit


class Page(HTMLParser):
    def __init__(self, content: str) -> None:
        super().__init__(convert_charrefs=True)
        self.anchors: set[str] = set()
        self.links: list[tuple[str, int, bool]] = []
        self.feed(content)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        for key in ("id", "name" if tag == "a" else "id"):
            if value := attributes.get(key):
                self.anchors.add(value)
        for key in ("href", "src", "poster"):
            if (value := attributes.get(key)) is not None:
                resource = (
                    key != "href"
                    or tag == "link"
                    and attributes.get("rel") in {"stylesheet", "preconnect", "preload", "icon"}
                )
                self.links.append((value, self.getpos()[0], resource))


def check_site(site: Path, docs: Path) -> list[str]:
    site = site.resolve()
    errors: list[str] = []
    pages = {path: Page(path.read_text()) for path in site.rglob("*.html")}
    if not pages:
        return [f"{site}: no HTML pages; run zensical build --strict first"]

    def check_link(source: Path, link: str, line: int, resource: bool) -> None:
        location = f"{source.relative_to(site)}:{line}"
        url = urlsplit(link)
        if url.scheme or url.netloc:
            if resource and url.scheme != "data":
                errors.append(f"{location}: external resource prevents offline use: {link}")
            return
        if link == "#" and source.relative_to(site).as_posix() == (
            "plans/website-design-prototype.html"
        ):
            return
        if link == "#":
            errors.append(f"{location}: placeholder link: #")
            return
        target = source
        if url.path:
            target = (
                site / unquote(url.path).lstrip("/")
                if url.path.startswith("/")
                else source.parent / unquote(url.path)
            ).resolve()
        if not target.is_relative_to(site):
            errors.append(f"{location}: link escapes site: {link}")
            return
        if target.is_dir():
            target /= "index.html"
        if not target.is_file():
            errors.append(f"{location}: missing target: {link}")
        elif (
            url.fragment and target in pages and unquote(url.fragment) not in pages[target].anchors
        ):
            errors.append(f"{location}: missing anchor: {link}")

    for path, page in pages.items():
        for link, line, resource in page.links:
            check_link(path, link, line, resource)
    for path in site.rglob("*.css"):
        for match in re.finditer(r"url\(\s*['\"]?([^)'\"]+)['\"]?\s*\)", path.read_text()):
            check_link(path, match[1].strip(), 1, True)

    expected: set[Path] = set()
    for source in docs.rglob("*"):
        if not source.is_file() or source.name == ".DS_Store":
            continue
        relative = source.relative_to(docs)
        if source.suffix == ".md":
            relative = (
                relative.parent / "index.html"
                if source.stem in {"index", "README"}
                else relative.with_suffix("") / "index.html"
            )
        expected.add(relative)
        if not (site / relative).is_file():
            errors.append(f"{relative}: source {source} is absent from build")
    for path in site.rglob("*"):
        if not path.is_file():
            continue
        relative = path.relative_to(site)
        if (
            relative not in expected
            and relative.parts[0] != "assets"
            and relative.as_posix()
            not in {"404.html", "search.json", "sitemap.xml", "sitemap.xml.gz", "objects.inv"}
        ):
            errors.append(f"{relative}: unexpected build file (stale page or non-documentation)")
        if path.name.startswith(".") or path.suffix in {".py", ".sql", ".db", ".sqlite", ".toml"}:
            errors.append(f"{relative}: forbidden source or state file")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--site", type=Path, default=Path("site"))
    parser.add_argument("--docs", type=Path, default=Path("docs"))
    args = parser.parse_args()
    errors = check_site(args.site, args.docs)
    if errors:
        print("\n".join(errors))
        print(f"Documentation check failed: {len(errors)} error(s)")
        return 1
    print("Documentation links, anchors, resources and build contents: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
