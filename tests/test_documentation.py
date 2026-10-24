"""Check image URLs that HACS cannot rewrite from HTML."""

from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlsplit


class ImageSources(HTMLParser):
    def __init__(self):
        super().__init__()
        self.sources = []

    def handle_starttag(self, tag, attrs):
        if tag == "img":
            self.sources.append(dict(attrs).get("src", ""))


def test_readme_html_images_are_absolute():
    readme = Path(__file__).resolve().parents[1] / "README.md"
    parser = ImageSources()
    parser.feed(readme.read_text())
    assert parser.sources
    for source in parser.sources:
        url = urlsplit(source)
        assert url.scheme == "https" and url.netloc
