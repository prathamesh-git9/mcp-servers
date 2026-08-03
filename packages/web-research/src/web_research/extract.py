"""Small deterministic HTML search and main-text extractors."""

from html.parser import HTMLParser
from typing import ClassVar
from urllib.parse import parse_qs, unquote, urljoin, urlsplit

from web_research.models import SearchHit


class MainTextParser(HTMLParser):
    _ignored: ClassVar[set[str]] = {
        "script",
        "style",
        "noscript",
        "svg",
        "canvas",
        "form",
    }

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._ignored_depth = 0
        self._title_depth = 0
        self.title_parts: list[str] = []
        self.text_parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in self._ignored:
            self._ignored_depth += 1
        if tag == "title":
            self._title_depth += 1
        if tag in {"p", "li", "h1", "h2", "h3", "article", "main", "section"}:
            self.text_parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in self._ignored and self._ignored_depth:
            self._ignored_depth -= 1
        if tag == "title" and self._title_depth:
            self._title_depth -= 1

    def handle_data(self, data: str) -> None:
        if self._ignored_depth:
            return
        clean = " ".join(data.split())
        if not clean:
            return
        if self._title_depth:
            self.title_parts.append(clean)
        self.text_parts.append(clean)

    def result(self, *, max_chars: int) -> tuple[str | None, str]:
        title = " ".join(self.title_parts).strip() or None
        lines = [" ".join(line.split()) for line in " ".join(self.text_parts).split("\n")]
        text = "\n".join(line for line in lines if line)
        return title, text[:max_chars]


class DuckDuckGoParser(HTMLParser):
    def __init__(self, base_url: str) -> None:
        super().__init__(convert_charrefs=True)
        self.base_url = base_url
        self.hits: list[SearchHit] = []
        self._active_url: str | None = None
        self._active_title: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        classes = set((attributes.get("class") or "").split())
        if tag == "a" and "result__a" in classes and attributes.get("href"):
            self._active_url = _unwrap_url(urljoin(self.base_url, attributes["href"]))
            self._active_title = []

    def handle_data(self, data: str) -> None:
        if self._active_url:
            self._active_title.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "a" and self._active_url:
            title = " ".join("".join(self._active_title).split())
            if title and self._active_url.startswith(("http://", "https://")):
                self.hits.append(SearchHit(title=title, url=self._active_url))
            self._active_url = None
            self._active_title = []


def extract_main_text(html: str, *, max_chars: int = 30_000) -> tuple[str | None, str]:
    parser = MainTextParser()
    parser.feed(html)
    return parser.result(max_chars=max_chars)


def parse_search_results(html: str, base_url: str) -> list[SearchHit]:
    parser = DuckDuckGoParser(base_url)
    parser.feed(html)
    return parser.hits


def _unwrap_url(url: str) -> str:
    parts = urlsplit(url)
    target = parse_qs(parts.query).get("uddg", [None])[0]
    return unquote(target) if target else url
