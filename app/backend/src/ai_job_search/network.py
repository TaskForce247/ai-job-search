from __future__ import annotations

import http.client
import ipaddress
import json
import re
import socket
import ssl
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin, urlsplit

from .errors import AppError
from .policy import load_helper

MAX_BYTES = 3 * 1024 * 1024


def destination(url: str) -> tuple[str, str, int, str, list[str]]:
    try:
        parsed = urlsplit(url)
        if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username or parsed.password:
            raise ValueError("Unsupported destination")
        if parsed.fragment:
            raise ValueError("Remove the URL fragment before importing")
        hostname = parsed.hostname.encode("idna").decode("ascii")
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        if port not in (80, 443):
            raise ValueError("Only standard web ports are supported")
        addresses = list(dict.fromkeys(item[4][0] for item in socket.getaddrinfo(hostname, port, type=socket.SOCK_STREAM)))
        if not addresses or any(not ipaddress.ip_address(address).is_global for address in addresses):
            raise ValueError("Private, local, and reserved destinations are not allowed")
        path = parsed.path or "/"
        if parsed.query:
            path += "?" + parsed.query
        return parsed.scheme, hostname, port, path, addresses
    except (ValueError, UnicodeError, socket.gaierror) as exc:
        raise AppError("unsafe_url", "Use a public HTTP(S) posting URL without credentials, private addresses, or nonstandard ports.") from exc


@dataclass
class WebResponse:
    status: int
    url: str
    text: str
    content_type: str


class WebClient:
    def __init__(self, resources: Path):
        self.robots = load_helper(resources, "robots_check")

    def _request(self, url: str, *, browser: bool = False) -> tuple[int, dict, bytes]:
        scheme, hostname, port, path, addresses = destination(url)
        connection = http.client.HTTPConnection(hostname, port, timeout=15)
        try:
            # Connect to the validated address, not a second DNS lookup (DNS rebinding).
            raw = socket.create_connection((addresses[0], port), timeout=15)
            if scheme == "https":
                try:
                    raw = ssl.create_default_context().wrap_socket(raw, server_hostname=hostname)
                except BaseException:
                    raw.close()
                    raise
            connection.sock = raw
            agent = "Mozilla/5.0 (compatible; AIJobSearch/0.1; personal job research)" if browser else "Claude-User"
            connection.request("GET", path, headers={"Host": hostname, "User-Agent": agent,
                                                    "Accept": "text/html,text/plain,application/json"})
            response = connection.getresponse()
            data = response.read(MAX_BYTES + 1)
            if len(data) > MAX_BYTES:
                raise AppError("page_too_large", "The page is too large. Paste the relevant full posting instead.")
            return response.status, {key.lower(): value for key, value in response.getheaders()}, data
        except (OSError, http.client.HTTPException) as exc:
            raise AppError("posting_unreachable", "The website could not be reached. Paste the full posting instead; closure is not confirmed.", 502) from exc
        finally:
            connection.close()

    def fetch(self, url: str, *, robots: bool = True, browser: bool = False) -> WebResponse:
        original = url
        for _ in range(6):
            parsed = urlsplit(url)
            if robots:
                robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"
                status, headers, body = self._request(robots_url)
                if status == 404:
                    pass
                elif status == 200:
                    text = body.decode("utf-8", errors="replace")
                    path = parsed.path or "/"
                    if not self.robots.allowed(text, "Claude-User", path) or not self.robots.allowed(text, "*", path):
                        raise AppError("robots_denied", "This site disallows automated access. Open it yourself and paste the full posting.", 403)
                else:
                    raise AppError("robots_unresolved", "The site's access policy could not be verified. Paste the posting instead.", 403)
            status, headers, body = self._request(url, browser=browser)
            if status in (301, 302, 303, 307, 308):
                location = headers.get("location")
                if not location:
                    raise AppError("posting_unreachable", "The site returned an incomplete redirect. Paste the posting.", 502)
                url = urljoin(url, location)
                destination(url)
                continue
            if status == 403 and not browser:
                return self.fetch(url, robots=robots, browser=True)
            if status == 410:
                raise AppError("posting_closed", "The website explicitly reports that this posting is gone (HTTP 410).", 410)
            if status != 200:
                raise AppError("posting_unreachable", f"The website returned HTTP {status}. Paste the posting; this is not proof the employer closed it.", 502)
            content_type = headers.get("content-type", "text/html")
            if not any(kind in content_type for kind in ("text/", "application/json", "application/ld+json")):
                raise AppError("unsupported_page", "This is not a readable posting page. Paste its full text instead.")
            encoding = re.search(r"charset=([A-Za-z0-9_-]+)", content_type)
            charset = encoding.group(1) if encoding else "utf-8"
            try:
                text = body.decode(charset, errors="replace")
            except LookupError as exc:
                raise AppError("unsupported_encoding", "The website uses an unsupported text encoding. Paste its text.") from exc
            return WebResponse(status, url, text, content_type)
        raise AppError("too_many_redirects", f"The website redirected too many times ({urlsplit(original).hostname}). Paste the posting.", 502)


class PostingHTML(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.structured: list[str] = []
        self._skip = 0
        self._json = False
        self._json_parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in ("script", "style", "noscript", "nav", "footer", "header"):
            self._skip += 1
        if tag == "script" and dict(attrs).get("type") == "application/ld+json":
            self._json = True
            self._json_parts = []
        if tag in ("p", "div", "br", "li", "h1", "h2", "h3", "section"):
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag == "script" and self._json:
            self.structured.append("".join(self._json_parts))
            self._json = False
        if tag in ("script", "style", "noscript", "nav", "footer", "header"):
            self._skip = max(0, self._skip - 1)

    def handle_data(self, data: str) -> None:
        if self._json:
            self._json_parts.append(data)
        if not self._skip:
            self.parts.append(data)

    @property
    def text(self) -> str:
        return re.sub(r"\n[ \t]*\n+", "\n\n", "".join(self.parts)).strip()

    def posting(self) -> dict | None:
        def find(value: object) -> dict | None:
            if isinstance(value, dict):
                if value.get("@type") == "JobPosting" or (
                    isinstance(value.get("@type"), list) and "JobPosting" in value["@type"]
                ):
                    return value
                for child in value.values():
                    if isinstance(child, (dict, list)) and (found := find(child)):
                        return found
            elif isinstance(value, list):
                for child in value:
                    if found := find(child):
                        return found
            return None
        for raw in self.structured[:30]:
            try:
                if found := find(json.loads(raw)):
                    description = PostingHTML()
                    description.feed(str(found.get("description", "")))
                    employer = found.get("hiringOrganization") or {}
                    location = found.get("jobLocation") or {}
                    if isinstance(location, list):
                        location = location[0] if location else {}
                    address = location.get("address", {}) if isinstance(location, dict) else {}
                    return {
                        "title": str(found.get("title", "")),
                        "company": str(employer.get("name", "")) if isinstance(employer, dict) else "",
                        "location": ", ".join(str(address.get(key, "")) for key in (
                            "addressLocality", "addressRegion", "addressCountry",
                        ) if address.get(key)) if isinstance(address, dict) else "",
                        "description": description.text,
                        "deadline": str(found.get("validThrough", ""))[:10] or None,
                    }
            except (json.JSONDecodeError, RecursionError):
                continue
        return None
