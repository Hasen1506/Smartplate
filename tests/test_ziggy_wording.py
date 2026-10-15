"""The app is called Ziggy to the people who use it (founder, Oct 2026).

No user-visible text says "SmartPlate" (any case): page titles and text, the web manifest,
the privacy / terms / credits pages as served, download filenames, and the strings the
front end renders. Internal names keep the old name on purpose and are allowed: the Python
package, localStorage keys (`smartplate.user` …), the `X-SmartPlate-Key` header, env vars,
notification tags, URLs and attribute values (the Swiggy redirect can't change yet).
"""
import json
import re
from html.parser import HTMLParser
from pathlib import Path

import pytest

STATIC = Path(__file__).resolve().parent.parent / "smartplate" / "static"
WORD = re.compile(r"smart\s*plate", re.I)


class _Text(HTMLParser):
    """Collects text nodes (not attribute values, scripts or styles)."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.text, self._skip = [], 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self._skip += 1
        # alt / title / aria-label / placeholder are read out or shown: check them too
        for k, v in attrs:
            if k in ("alt", "title", "aria-label", "placeholder") and v:
                self.text.append(v)

    def handle_endtag(self, tag):
        if tag in ("script", "style") and self._skip:
            self._skip -= 1

    def handle_data(self, data):
        if not self._skip:
            self.text.append(data)


def visible_text(html: str) -> str:
    p = _Text()
    p.feed(html)
    return " ".join(p.text)


@pytest.mark.parametrize("name", sorted(p.name for p in STATIC.glob("*.html")))
def test_static_pages_never_say_smartplate(name):
    html = (STATIC / name).read_text(encoding="utf-8")
    text = visible_text(html)
    assert not WORD.search(text), (name, WORD.findall(text))
    title = re.search(r"<title>(.*?)</title>", html, re.S)
    if title:
        assert "Ziggy" in title.group(1)


def test_manifest_is_ziggy():
    m = json.loads((STATIC / "manifest.webmanifest").read_text(encoding="utf-8"))
    for key in ("name", "short_name", "description"):
        assert not WORD.search(m.get(key, "")), key
    assert m["short_name"] == "Ziggy"
    for s in m.get("shortcuts", []):
        assert not WORD.search(s.get("name", ""))


# Internal identifiers that may keep the old name. Anything else in the JS is treated as text.
ALLOWED_JS = re.compile(
    r"smartplate\.[A-Za-z][\w.-]*"            # localStorage keys: smartplate.user, smartplate.keys …
    r"|X-SmartPlate-Key"                      # API header
    r"|smartplate/[\w./]+"                    # file paths in comments
    r"|[\"'`]smartplate[\"'`]"                # notification tag value
    r"|smartplate-\$\{"                       # notification tag template
    r"|SMARTPLATE_\w+", re.I)


def _strip_comments(js: str) -> str:
    js = re.sub(r"/\*.*?\*/", " ", js, flags=re.S)
    return re.sub(r"(?m)(^|[^:\\\"'`])//[^\n]*", r"\1", js)


@pytest.mark.parametrize("rel", ["app.js", "sw.js", "v3/v3.js"])
def test_front_end_strings_never_say_smartplate(rel):
    js = _strip_comments((STATIC / rel).read_text(encoding="utf-8"))
    left = ALLOWED_JS.sub(" ", js)
    hits = [left[max(0, m.start() - 40): m.end() + 40] for m in WORD.finditer(left)]
    assert not hits, (rel, hits[:5])


def test_served_pages_and_downloads_say_ziggy(seeded):
    from smartplate.app import create_app
    client = create_app().test_client()
    for path in ("/privacy", "/terms", "/credits"):
        r = client.get(path)
        assert r.status_code == 200, path
        assert not WORD.search(visible_text(r.get_data(as_text=True))), path
    # download filenames the server suggests
    src = (STATIC.parent / "app.py").read_text(encoding="utf-8")
    for name in re.findall(r"filename=([\w{}.-]+)", src):
        assert not WORD.search(name), name


def test_the_check_itself_catches_the_old_name():
    assert WORD.search(visible_text("<p>Welcome to SmartPlate</p>"))
    assert not WORD.search(visible_text('<a href="https://smartplate-xgxd.onrender.com">Ziggy</a>'))
    js = _strip_comments('store.get("smartplate.user"); h["X-SmartPlate-Key"]=k; toast(`Hi from Smartplate`)')
    assert len(list(WORD.finditer(ALLOWED_JS.sub(" ", js)))) == 1
