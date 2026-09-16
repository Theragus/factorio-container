#!/usr/bin/env python3
"""Render the GitHub release notes for one packaged Factorio version.

The upstream notes come from ``data/changelog.txt`` inside the release tarball
(the authoritative text Wube ships with the build); the link to the matching
announcement thread is resolved against the Releases forum.
"""

from __future__ import annotations

import argparse
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from factorio_api import download_url, extract_changelog, get_forum_url  # noqa: E402

# "  Bugfixes:" / "  Scripting:" style group headings.
GROUP_RE = re.compile(r"^  (\S[^:]*):\s*$")
# "    - some entry"
ENTRY_RE = re.compile(r"^    - (.*)$")


def changelog_to_markdown(section: str) -> str:
    """Turn Factorio's indented changelog block into markdown."""
    lines = section.splitlines()
    out: list[str] = []
    date = ""

    for line in lines:
        if line.startswith("Version:"):
            continue
        if line.startswith("Date:"):
            date = line.split(":", 1)[1].strip()
            continue

        group = GROUP_RE.match(line)
        if group:
            out.append("")
            out.append(f"#### {group.group(1)}")
            continue

        entry = ENTRY_RE.match(line)
        if entry:
            out.append(f"- {entry.group(1).strip()}")
            continue

        stripped = line.strip()
        if stripped:
            # Continuation of the previous entry.
            if out and out[-1].startswith("- "):
                out[-1] = f"{out[-1]} {stripped}"
            else:
                out.append(stripped)

    body = "\n".join(out).strip()
    if date:
        body = f"*Released {date}*\n\n{body}" if body else f"*Released {date}*"
    return body


def tag_list(image: str, tags: list[str]) -> str:
    return "\n".join(f"- `{image}:{tag}`" for tag in tags)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", required=True)
    parser.add_argument(
        "--changelog",
        required=True,
        help="path to data/changelog.txt taken from the built image",
    )
    parser.add_argument("--image", required=True, help="fully qualified image name")
    parser.add_argument("--tags", default="", help="comma separated list of image tags")
    parser.add_argument(
        "--channels", default="", help="comma separated: stable, experimental"
    )
    parser.add_argument("--sha256", default="", help="sha256 of the upstream tarball")
    parser.add_argument("--forum-url", default="", help="override the forum link")
    parser.add_argument("--output", default="-")
    args = parser.parse_args()

    with open(args.changelog, encoding="utf-8", errors="replace") as handle:
        changelog = handle.read()

    section = extract_changelog(changelog, args.version)
    notes = changelog_to_markdown(section) if section else ""

    forum_url = args.forum_url or get_forum_url(args.version)
    tags = [tag for tag in args.tags.split(",") if tag]
    channels = [channel for channel in args.channels.split(",") if channel]

    channel_line = (
        " / ".join(f"**{channel}**" for channel in channels) if channels else "archived"
    )

    url = download_url(args.version)
    parts = [
        f"Factorio **{args.version}** headless (dedicated server), packaged as a "
        f"container image.",
        "",
        f"- **Channel:** {channel_line}",
        f"- **Announcement:** "
        f"[Version {args.version} on the Factorio forums]({forum_url})",
        f"- **Upstream download:** [{url}]({url})",
    ]
    if args.sha256:
        parts.append(f"- **Tarball SHA-256:** `{args.sha256}`")
    parts += [
        "",
        "## Run it",
        "",
        "```bash",
        "docker run -d --name factorio \\",
        "  -p 34197:34197/udp -p 27015:27015/tcp \\",
        "  -v factorio-data:/factorio \\",
        f"  {args.image}:{args.version}",
        "```",
        "",
        "## Image tags",
        "",
        tag_list(args.image, tags) if tags else f"- `{args.image}:{args.version}`",
        "",
        f"## Factorio {args.version} release notes",
        "",
    ]

    if notes:
        parts.append(notes)
    else:
        parts.append(
            f"No changelog entry for {args.version} was found in the release "
            f"tarball. See the [forum announcement]({forum_url})."
        )

    parts += [
        "",
        "---",
        "",
        "Release notes above are Wube Software's, taken from `data/changelog.txt` "
        "in the official release. This image only repackages the unmodified "
        "Linux headless build.",
    ]

    rendered = "\n".join(parts).rstrip() + "\n"

    if args.output == "-":
        sys.stdout.write(rendered)
    else:
        with open(args.output, "w", encoding="utf-8") as handle:
            handle.write(rendered)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
