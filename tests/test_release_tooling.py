#!/usr/bin/env python3
"""Offline tests for the release-detection and release-notes tooling."""

from __future__ import annotations

import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import factorio_api  # noqa: E402
from build_metadata import image_tags, parse_channels  # noqa: E402
from detect_versions import TAG_RE, select_versions  # noqa: E402
from factorio_api import (  # noqa: E402
    ReleaseIndex,
    extract_changelog,
    get_sha256,
    is_version,
    version_key,
)
from release_notes import changelog_to_markdown  # noqa: E402

SEPARATOR = "-" * 99

CHANGELOG = f"""{SEPARATOR}
Version: 2.0.77
Date: 21. 05. 2026
  Bugfixes:
    - Fixed a crash when loading a save.
    - Fixed a very long entry that wraps
      onto a second line.
  Scripting:
    - Added LuaThing::field read.
{SEPARATOR}
Version: 2.0.76
Date: 25. 02. 2026
  Bugfixes:
    - Fixed something else.
{SEPARATOR}
"""


def index(versions, stable, experimental):
    ordered = tuple(sorted(set(versions), key=version_key))
    return ReleaseIndex(versions=ordered, stable=stable, experimental=experimental)


class VersionHelpers(unittest.TestCase):
    def test_version_key_orders_numerically(self):
        versions = ["2.0.9", "2.0.10", "1.1.110", "2.1.2"]
        self.assertEqual(
            sorted(versions, key=version_key),
            ["1.1.110", "2.0.9", "2.0.10", "2.1.2"],
        )

    def test_is_version(self):
        self.assertTrue(is_version("2.0.77"))
        self.assertFalse(is_version("2.0"))
        self.assertFalse(is_version("v2.0.77"))
        self.assertFalse(is_version("2.0.77-rc1"))

    def test_tag_regex_accepts_both_spellings(self):
        self.assertEqual(TAG_RE.match("v2.0.77").group(1), "2.0.77")
        self.assertEqual(TAG_RE.match("2.0.77").group(1), "2.0.77")
        self.assertIsNone(TAG_RE.match("latest"))


class ReleaseIndexBehaviour(unittest.TestCase):
    def setUp(self):
        self.index = index(
            ["2.0.76", "2.0.77", "2.1.16", "2.1.17", "2.1.18"], "2.0.77", "2.1.18"
        )

    def test_newer_than(self):
        self.assertEqual(self.index.newer_than("2.1.16"), ["2.1.17", "2.1.18"])
        self.assertEqual(self.index.newer_than("2.1.18"), [])
        self.assertEqual(len(self.index.newer_than(None)), 5)

    def test_channels_for(self):
        self.assertEqual(self.index.channels_for("2.0.77"), ["stable"])
        self.assertEqual(self.index.channels_for("2.1.18"), ["experimental"])
        self.assertEqual(self.index.channels_for("2.1.17"), [])

    def test_a_single_version_can_hold_both_channels(self):
        both = index(["1.1.109", "1.1.110"], "1.1.110", "1.1.110")
        self.assertEqual(both.channels_for("1.1.110"), ["stable", "experimental"])


class VersionSelection(unittest.TestCase):
    def setUp(self):
        self.index = index(
            ["2.0.76", "2.0.77", "2.1.16", "2.1.17", "2.1.18"], "2.0.77", "2.1.18"
        )

    def test_fresh_repository_seeds_only_the_current_releases(self):
        self.assertEqual(select_versions(self.index, set(), 5), ["2.0.77", "2.1.18"])

    def test_nothing_to_do_when_everything_is_published(self):
        published = set(self.index.versions)
        self.assertEqual(select_versions(self.index, published, 5), [])

    def test_picks_up_releases_newer_than_the_newest_published_one(self):
        self.assertEqual(
            select_versions(self.index, {"2.0.77", "2.1.16"}, 5), ["2.1.17", "2.1.18"]
        )

    def test_never_backfills_below_the_baseline(self):
        # 2.0.76 is older than everything published and must stay unpackaged.
        selected = select_versions(self.index, {"2.1.17", "2.1.18", "2.0.77"}, 5)
        self.assertNotIn("2.0.76", selected)
        self.assertEqual(selected, [])

    def test_a_missing_channel_head_is_always_picked_up(self):
        # Stable moved backwards relative to experimental; stable is missing.
        selected = select_versions(self.index, {"2.1.18"}, 5)
        self.assertEqual(selected, ["2.0.77"])

    def test_max_builds_caps_a_run_oldest_first(self):
        selected = select_versions(self.index, {"2.0.77", "2.1.15"}, 2)
        self.assertEqual(selected, ["2.1.16", "2.1.17"])

    def test_max_builds_zero_means_unlimited(self):
        selected = select_versions(self.index, {"2.0.77", "2.1.15"}, 0)
        self.assertEqual(selected, ["2.1.16", "2.1.17", "2.1.18"])


class ImageTags(unittest.TestCase):
    def test_stable_owns_latest(self):
        self.assertEqual(
            image_tags("2.0.77", ["stable"]), ["2.0.77", "2.0", "stable", "latest"]
        )

    def test_experimental_does_not_own_latest(self):
        self.assertEqual(
            image_tags("2.1.18", ["experimental"]), ["2.1.18", "2.1", "experimental"]
        )

    def test_both_channels(self):
        self.assertEqual(
            image_tags("1.1.110", ["stable", "experimental"]),
            ["1.1.110", "1.1", "stable", "latest", "experimental"],
        )

    def test_superseded_version_only_gets_version_tags(self):
        self.assertEqual(image_tags("2.1.17", []), ["2.1.17", "2.1"])


class ChannelParsing(unittest.TestCase):
    def test_normal_lists(self):
        self.assertEqual(parse_channels("stable"), ["stable"])
        self.assertEqual(
            parse_channels("stable,experimental"), ["stable", "experimental"]
        )

    def test_surrounding_whitespace_is_stripped(self):
        self.assertEqual(
            parse_channels(" stable , experimental "), ["stable", "experimental"]
        )

    def test_blank_forms_all_yield_no_channels(self):
        # Each of these is truthy as a raw string, which is why the caller must
        # gate the channel lookup on the parsed list instead.
        for raw in ("", ",", " ", ",,", " , "):
            with self.subTest(raw=raw):
                self.assertEqual(parse_channels(raw), [])


class Sha256Lookup(unittest.TestCase):
    SUMS = (
        "aaaa  factorio-headless_linux_2.0.77.tar.xz\n"
        "bbbb  factorio_headless_x64_1.1.110.tar.xz\n"
        "cccc  factorio_linux_2.0.77.tar.xz\n"
        "garbage line with too many fields here\n"
    )

    def setUp(self):
        factorio_api._sha256sums.cache_clear()
        self.fetches = []
        self._real = factorio_api._fetch_text
        factorio_api._fetch_text = self._fake
        self.addCleanup(setattr, factorio_api, "_fetch_text", self._real)
        self.addCleanup(factorio_api._sha256sums.cache_clear)

    def _fake(self, url, **kwargs):
        self.fetches.append(url)
        return self.SUMS

    def test_finds_the_modern_tarball_name(self):
        self.assertEqual(get_sha256("2.0.77"), "aaaa")

    def test_finds_the_legacy_tarball_name(self):
        self.assertEqual(get_sha256("1.1.110"), "bbbb")

    def test_ignores_the_non_headless_build(self):
        # factorio_linux_2.0.77 is the full game, not the headless tarball.
        self.assertNotEqual(get_sha256("2.0.77"), "cccc")

    def test_unlisted_version_is_none(self):
        self.assertIsNone(get_sha256("9.9.9"))

    def test_page_is_fetched_once_for_many_versions(self):
        for version in ("2.0.77", "1.1.110", "9.9.9", "2.0.77"):
            get_sha256(version)
        self.assertEqual(len(self.fetches), 1)


class ChangelogExtraction(unittest.TestCase):
    def test_extracts_the_requested_section_only(self):
        section = extract_changelog(CHANGELOG, "2.0.77")
        self.assertTrue(section.startswith("Version: 2.0.77"))
        self.assertIn("Fixed a crash when loading a save.", section)
        self.assertNotIn("Fixed something else.", section)

    def test_unknown_version_yields_nothing(self):
        self.assertEqual(extract_changelog(CHANGELOG, "9.9.9"), "")

    def test_prefix_versions_do_not_match(self):
        # "2.0.7" must not match the "2.0.77" section.
        self.assertEqual(extract_changelog(CHANGELOG, "2.0.7"), "")


class ChangelogRendering(unittest.TestCase):
    def setUp(self):
        self.markdown = changelog_to_markdown(extract_changelog(CHANGELOG, "2.0.77"))

    def test_groups_become_headings(self):
        self.assertIn("#### Bugfixes", self.markdown)
        self.assertIn("#### Scripting", self.markdown)

    def test_entries_become_list_items(self):
        self.assertIn("- Fixed a crash when loading a save.", self.markdown)

    def test_wrapped_entries_are_joined(self):
        self.assertIn(
            "- Fixed a very long entry that wraps onto a second line.", self.markdown
        )

    def test_release_date_is_kept(self):
        self.assertIn("*Released 21. 05. 2026*", self.markdown)

    def test_version_header_is_dropped(self):
        self.assertNotIn("Version: 2.0.77", self.markdown)

    def test_empty_section_renders_empty(self):
        self.assertEqual(changelog_to_markdown(""), "")


if __name__ == "__main__":
    unittest.main()
