#!/usr/bin/env python3
"""Resolve everything the build workflow needs to know about one version.

Emits the upstream checksum, the release channels the version currently
occupies and the list of image tags to publish. Any field passed on the command
line is trusted as-is, so the detection job's results are not re-fetched.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from factorio_api import (  # noqa: E402
    FactorioApiError,
    download_url,
    get_release_index,
    get_sha256,
    is_version,
)


def image_tags(version: str, channels: list[str]) -> list[str]:
    major, minor, _ = version.split(".")
    tags = [version, f"{major}.{minor}"]
    if "stable" in channels:
        # `latest` follows the stable channel, the way most server images do.
        tags += ["stable", "latest"]
    if "experimental" in channels:
        tags.append("experimental")
    # Preserve order, drop duplicates.
    return list(dict.fromkeys(tags))


def write_outputs(**outputs: str) -> None:
    path = os.environ.get("GITHUB_OUTPUT")
    if not path:
        return
    with open(path, "a", encoding="utf-8") as handle:
        for key, value in outputs.items():
            handle.write(f"{key}={value}\n")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", required=True)
    parser.add_argument("--sha256", default="", help="skip the checksum lookup")
    parser.add_argument(
        "--channels", default="", help="comma separated; skip the channel lookup"
    )
    parser.add_argument("--image", default="", help="image name, for the tag list")
    args = parser.parse_args()

    version = args.version.strip().lstrip("v")
    if not is_version(version):
        print(f"::error::'{args.version}' is not a x.y.z version", file=sys.stderr)
        return 1

    sha256 = args.sha256.strip()
    channels = [c for c in args.channels.split(",") if c]

    if not sha256 or not args.channels:
        try:
            if not args.channels:
                channels = get_release_index().channels_for(version)
            if not sha256:
                sha256 = get_sha256(version) or ""
        except FactorioApiError as error:
            print(f"::warning::{error}", file=sys.stderr)

    if not sha256:
        print(
            f"::warning::no published SHA-256 for {version}; "
            "the tarball will not be checksum-verified",
            file=sys.stderr,
        )

    tags = image_tags(version, channels)
    full_tags = [f"{args.image}:{tag}" for tag in tags] if args.image else tags

    result = {
        "version": version,
        "sha256": sha256,
        "channels": ",".join(channels),
        "tags": ",".join(tags),
        "full_tags": "\n".join(full_tags),
        "download_url": download_url(version),
        # An experimental-only release is flagged as a GitHub pre-release.
        "prerelease": "true"
        if channels == ["experimental"] or (not channels)
        else "false",
    }

    print(json.dumps({k: v for k, v in result.items() if k != "full_tags"}, indent=2))

    # full_tags is multi-line, so it needs GitHub's heredoc output syntax.
    write_outputs(**{k: v for k, v in result.items() if k != "full_tags"})
    path = os.environ.get("GITHUB_OUTPUT")
    if path:
        with open(path, "a", encoding="utf-8") as handle:
            handle.write("full_tags<<__EOF__\n")
            handle.write(result["full_tags"] + "\n")
            handle.write("__EOF__\n")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
