#!/usr/bin/env python3
"""Decide which Factorio headless releases still need a container image.

The repository has no backfill ambition: on a fresh repo this seeds only the
current release heads, and from then on it packages every new release that
shows up upstream. Published state is read from the repository's own GitHub
releases, so there is no state file to keep in sync.

Writes ``matrix`` / ``has_new`` / ``summary`` to ``$GITHUB_OUTPUT`` when running
inside GitHub Actions, and always prints the matrix to stdout.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from factorio_api import (  # noqa: E402
    FactorioApiError,
    ReleaseIndex,
    get_release_index,
    get_sha256,
    is_version,
    version_key,
)

TAG_RE = re.compile(r"^v?(\d+\.\d+\.\d+)$")


def published_versions(repository: str, token: str | None) -> set[str]:
    """Versions that already have a GitHub release in this repository."""
    versions: set[str] = set()
    url = f"https://api.github.com/repos/{repository}/releases?per_page=100"
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "factorio-container release automation",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"

    while url:
        request = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                releases = json.loads(response.read())
                link = response.headers.get("Link", "")
        except urllib.error.HTTPError as error:
            if error.code == 404:
                return versions
            raise

        for release in releases:
            if release.get("draft"):
                continue
            match = TAG_RE.match(release.get("tag_name", ""))
            if match:
                versions.add(match.group(1))

        url = ""
        for part in link.split(","):
            if 'rel="next"' in part:
                url = part.split(";")[0].strip().strip("<>")
                break

    return versions


def select_versions(
    index: ReleaseIndex, published: set[str], max_builds: int
) -> list[str]:
    heads = sorted({index.stable, index.experimental}, key=version_key)

    if not published:
        # Fresh repository: start at the newest releases, never backfill.
        return heads

    baseline = max(published, key=version_key)
    candidates = set(index.newer_than(baseline))
    # A head can be missing even when something newer exists (for example an
    # older stable while experimental has already moved on).
    candidates.update(head for head in heads if head not in published)
    candidates -= published

    ordered = sorted(candidates, key=version_key)
    if max_builds > 0 and len(ordered) > max_builds:
        # Oldest first, so a long gap is worked through over several runs
        # without ever losing a release.
        ordered = ordered[:max_builds]
    return ordered


def build_matrix(index: ReleaseIndex, versions: list[str]) -> list[dict]:
    matrix = []
    for version in versions:
        channels = index.channels_for(version)
        matrix.append(
            {
                "version": version,
                "sha256": get_sha256(version) or "",
                "channels": ",".join(channels),
                "stable": "stable" in channels,
                "experimental": "experimental" in channels,
            }
        )
    return matrix


def write_outputs(**outputs: str) -> None:
    path = os.environ.get("GITHUB_OUTPUT")
    if not path:
        return
    with open(path, "a", encoding="utf-8") as handle:
        for key, value in outputs.items():
            handle.write(f"{key}={value}\n")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--repository",
        default=os.environ.get("GITHUB_REPOSITORY", ""),
        help="owner/repo whose releases mark what is already published",
    )
    parser.add_argument(
        "--version",
        default="",
        help="build this exact version instead of auto-detecting",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="include versions that already have a release",
    )
    parser.add_argument(
        "--max-builds",
        type=int,
        default=5,
        help="cap the number of versions per run (0 = unlimited)",
    )
    args = parser.parse_args()

    try:
        index = get_release_index()
    except FactorioApiError as error:
        print(f"::error::{error}", file=sys.stderr)
        return 1

    print(
        f"Upstream: stable={index.stable} experimental={index.experimental} "
        f"(latest={index.latest})",
        file=sys.stderr,
    )

    if args.version:
        if not is_version(args.version):
            print(f"::error::'{args.version}' is not a x.y.z version", file=sys.stderr)
            return 1
        if args.version not in index.versions:
            print(
                f"::warning::{args.version} is not listed upstream; "
                "building it anyway because it was requested explicitly",
                file=sys.stderr,
            )
        versions = [args.version]
    else:
        published = set()
        if not args.force:
            if not args.repository:
                print(
                    "::error::--repository is required (or set GITHUB_REPOSITORY)",
                    file=sys.stderr,
                )
                return 1
            published = published_versions(
                args.repository, os.environ.get("GITHUB_TOKEN")
            )
            print(
                f"Already published: {len(published)} release(s)"
                + (
                    f", newest {max(published, key=version_key)}"
                    if published
                    else " (fresh repository, seeding from the current releases)"
                ),
                file=sys.stderr,
            )
        versions = select_versions(index, published, args.max_builds)

    matrix = build_matrix(index, versions)
    print(json.dumps(matrix, indent=2))

    if matrix:
        summary = ", ".join(
            f"{item['version']}" + (f" ({item['channels']})" if item["channels"] else "")
            for item in matrix
        )
    else:
        summary = "nothing to do - every upstream release is already packaged"
    print(summary, file=sys.stderr)

    write_outputs(
        matrix=json.dumps(matrix),
        has_new="true" if matrix else "false",
        summary=summary,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
