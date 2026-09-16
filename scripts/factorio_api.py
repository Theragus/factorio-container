"""Helpers for talking to the public Factorio release endpoints.

Only the Linux *headless* build is of interest here: it is the sole release
that runs without a graphical stack and therefore the only one that can be
sensibly packaged as a container image.

Deliberately dependency-free (stdlib only) so the CI jobs need no pip install.
"""

from __future__ import annotations

import gzip
import json
import re
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Iterable
from dataclasses import dataclass
from html import unescape

AVAILABLE_VERSIONS_URL = "https://updater.factorio.com/get-available-versions"
LATEST_RELEASES_URL = "https://factorio.com/api/latest-releases"
SHA256SUMS_URL = "https://www.factorio.com/download/sha256sums/"
DOWNLOAD_URL_TEMPLATE = "https://www.factorio.com/get-download/{version}/headless/linux64"

RELEASES_FORUM_URL = "https://forums.factorio.com/viewforum.php?f=3"
FORUM_SEARCH_URL = "https://forums.factorio.com/search.php"
FORUM_TOPIC_URL = "https://forums.factorio.com/viewtopic.php?t={topic_id}"

# The headless tarball has been published under two names over the years.
TARBALL_NAMES = (
    "factorio-headless_linux_{version}.tar.xz",  # 2.0 and newer
    "factorio_headless_x64_{version}.tar.xz",  # 0.x / 1.x
)

USER_AGENT = "factorio-container release automation (+https://github.com/Theragus/factorio-container)"

_VERSION_RE = re.compile(r"^\d+\.\d+\.\d+$")


class FactorioApiError(RuntimeError):
    """Raised when an upstream endpoint cannot be used."""


def version_key(version: str) -> tuple[int, ...]:
    """Sort key for a dotted Factorio version."""
    return tuple(int(part) for part in version.split("."))


def is_version(value: str) -> bool:
    return bool(_VERSION_RE.match(value))


def _fetch(url: str, *, retries: int = 4, timeout: int = 60) -> bytes:
    last_error: Exception | None = None
    request = urllib.request.Request(
        url, headers={"User-Agent": USER_AGENT, "Accept-Encoding": "gzip"}
    )
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                payload = response.read()
                if response.headers.get("Content-Encoding") == "gzip":
                    payload = gzip.decompress(payload)
                return payload
        except (
            urllib.error.URLError,
            TimeoutError,
            OSError,
        ) as error:  # pragma: no cover
            last_error = error
            if attempt < retries - 1:
                import time

                time.sleep(2**attempt)
    raise FactorioApiError(f"failed to fetch {url}: {last_error}")


def _fetch_text(url: str, **kwargs) -> str:
    return _fetch(url, **kwargs).decode("utf-8", errors="replace")


@dataclass(frozen=True)
class ReleaseIndex:
    """Every headless version upstream knows about, plus the channel heads."""

    versions: tuple[str, ...]
    stable: str
    experimental: str

    @property
    def latest(self) -> str:
        return self.versions[-1]

    def newer_than(self, baseline: str | None) -> list[str]:
        if baseline is None:
            return list(self.versions)
        cutoff = version_key(baseline)
        return [v for v in self.versions if version_key(v) > cutoff]

    def channels_for(self, version: str) -> list[str]:
        channels = []
        if version == self.stable:
            channels.append("stable")
        if version == self.experimental:
            channels.append("experimental")
        return channels


def get_release_index() -> ReleaseIndex:
    """Fetch the full list of published headless versions.

    ``get-available-versions`` lists every from/to update pair for
    ``core-linux_headless64`` plus a trailing entry naming the current stable
    and experimental builds, which makes it a single source of truth. The
    ``latest-releases`` API is used as a fallback for the channel heads.
    """
    payload = json.loads(_fetch_text(AVAILABLE_VERSIONS_URL))
    entries = payload.get("core-linux_headless64")
    if not entries:
        raise FactorioApiError("get-available-versions returned no headless data")

    versions: set[str] = set()
    stable: str | None = None
    experimental: str | None = None

    for entry in entries:
        for key in ("from", "to"):
            value = entry.get(key)
            if value and is_version(value):
                versions.add(value)
        if entry.get("stable"):
            stable = entry["stable"]
        if entry.get("experimental"):
            experimental = entry["experimental"]

    if stable is None or experimental is None:
        latest = json.loads(_fetch_text(LATEST_RELEASES_URL))
        stable = stable or latest["stable"]["headless"]
        experimental = experimental or latest["experimental"]["headless"]

    # A channel head is a published release even if no update pair mentions it.
    versions.update({stable, experimental})

    return ReleaseIndex(
        versions=tuple(sorted(versions, key=version_key)),
        stable=stable,
        experimental=experimental,
    )


def download_url(version: str) -> str:
    return DOWNLOAD_URL_TEMPLATE.format(version=version)


def get_sha256(version: str) -> str | None:
    """Return the published SHA-256 of the headless tarball, if listed.

    The checksum page only covers recent releases; ``None`` means "not
    published", not "mismatch".
    """
    try:
        sums = _fetch_text(SHA256SUMS_URL)
    except FactorioApiError:
        return None

    wanted = {name.format(version=version) for name in TARBALL_NAMES}
    for line in sums.splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[1] in wanted:
            return parts[0]
    return None


def _topic_links(html: str) -> Iterable[tuple[str, str]]:
    """Yield ``(version, topic_url)`` for every "Version x.y.z" topic found."""
    pattern = re.compile(
        r'viewtopic\.php\?(?:[^"\']*?&(?:amp;)?)?t=(\d+)[^"\']*["\'][^>]*'
        r'class="topictitle"[^>]*>\s*Version\s+(\d+\.\d+\.\d+)\s*<',
        re.IGNORECASE,
    )
    for match in pattern.finditer(html):
        yield match.group(2), FORUM_TOPIC_URL.format(topic_id=match.group(1))


def get_forum_url(version: str) -> str:
    """Best-effort link to the forum announcement for ``version``.

    Falls back to a forum search and finally to the Releases forum index, so a
    release is never blocked on the forum being slow or restructured.
    """
    try:
        for found, url in _topic_links(unescape(_fetch_text(RELEASES_FORUM_URL))):
            if found == version:
                return url
    except FactorioApiError:
        pass

    search_url = f"{FORUM_SEARCH_URL}?" + urllib.parse.urlencode(
        {"keywords": f"Version {version}", "fid[]": 3, "sr": "topics"}
    )
    try:
        for found, url in _topic_links(unescape(_fetch_text(search_url))):
            if found == version:
                return url
    except FactorioApiError:
        pass

    return RELEASES_FORUM_URL


def extract_changelog(changelog: str, version: str) -> str:
    """Pull one version's section out of the bundled ``data/changelog.txt``.

    Sections are separated by a line of dashes and start with ``Version: x.y.z``.
    """
    separator = "-" * 99
    blocks = [block.strip("\n") for block in changelog.split(separator)]
    header = f"Version: {version}"
    for block in blocks:
        stripped = block.strip()
        if stripped.startswith(header) and stripped.splitlines()[0].strip() == header:
            return stripped
    return ""
