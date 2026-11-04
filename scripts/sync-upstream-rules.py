#!/usr/bin/env python3
"""Download Chromium's review rules to where /review-prs reads them.

The directory is `review.upstreamRules.dir` in the project profile and the
archive `review.upstreamRules.source` (Gitiles serves any tree as a tarball).
Nothing here calls a model: the rules are fetched, checked, and put in place.

The archive is untrusted until it has been through this script. Only rule files
(`*.md`) and COMMON_METADATA are kept, nothing is written outside a fresh
directory next to the destination, and the destination is replaced only when what
was downloaded parses into rules and capability groups. A download that fails or
does not parse leaves the rules already there alone.

Usage:
    python3 scripts/sync-upstream-rules.py              # download and install
    python3 scripts/sync-upstream-rules.py --archive F  # install from a tarball on disk
"""

import argparse
import os
import shutil
import sys
import tarfile
import tempfile
import urllib.request  # nosemgrep
from datetime import datetime, timezone
from urllib.parse import urlparse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from lib.load_config import bot_dir, load_config, load_profile, review_upstream_rules
from lib.upstream_rules import METADATA_FILE, SEVERITIES, UpstreamRules

MAX_ARCHIVE_BYTES = 50 * 1024 * 1024
MAX_FILE_BYTES = 1024 * 1024
TIMEOUT_SECONDS = 120
MARKER = "SYNCED"


def log(message):
    print(message, file=sys.stderr)


def download(url, to):
    """Fetch `url` to the file `to`, over https and no larger than MAX_ARCHIVE_BYTES."""
    if urlparse(url).scheme != "https":
        raise ValueError(f"{url} is not an https URL")
    request = urllib.request.Request(url, headers={"User-Agent": "brave-dev-loop"})
    size = 0
    with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as resp:  # nosemgrep
        with open(to, "wb") as out:
            while True:
                block = resp.read(64 * 1024)
                if not block:
                    break
                size += len(block)
                if size > MAX_ARCHIVE_BYTES:
                    raise ValueError(
                        f"archive is larger than {MAX_ARCHIVE_BYTES} bytes"
                    )
                out.write(block)


def _wanted(name):
    base = os.path.basename(name)
    return base == METADATA_FILE or base.endswith(".md")


def extract(archive, into):
    """Unpack the rule files and metadata of a tarball into the empty directory `into`.

    Links, devices, and anything with an absolute or `..` path are skipped, as is
    every file that is neither a rule nor metadata. Returns how many files it kept.
    """
    kept = 0
    root = os.path.realpath(into)
    with tarfile.open(archive, "r:*") as tar:
        for member in tar:
            if not member.isreg() or not _wanted(member.name):
                continue
            parts = member.name.replace("\\", "/").split("/")
            if (
                member.name.startswith("/")
                or ".." in parts
                or member.size > MAX_FILE_BYTES
            ):
                continue
            target = os.path.realpath(
                os.path.join(into, *[p for p in parts if p not in ("", ".")])
            )
            if os.path.commonpath([root, target]) != root:
                continue
            os.makedirs(os.path.dirname(target), exist_ok=True)
            src = tar.extractfile(member)
            if src is None:
                continue
            with src, open(target, "wb") as out:
                shutil.copyfileobj(src, out)
            kept += 1
    return kept


def install(archive, dest, source=""):
    """Replace `dest` with the rules in `archive`; return a summary dict, or raise ValueError."""
    dest = os.path.abspath(dest)
    parent = os.path.dirname(dest)
    os.makedirs(parent, exist_ok=True)
    staged = tempfile.mkdtemp(prefix=".upstream-rules-", dir=parent)
    try:
        kept = extract(archive, staged)
        checked = UpstreamRules(staged, "https://example.invalid/rules")
        if not checked.rules or not checked.groups:
            raise ValueError(
                f"{kept} files extracted, but they hold {len(checked.rules)} rules and "
                f"{len(checked.groups)} capability groups; not installing"
            )
        listed = {rule_id for g in checked.groups for rule_id in g["rules"]}
        by_severity = {
            s: sum(1 for r in checked.rules.values() if r["severity"] == s)
            for s in SEVERITIES
        }
        with open(os.path.join(staged, MARKER), "w") as f:
            f.write(
                f"source: {source or archive}\n"
                f"synced: {datetime.now(timezone.utc).isoformat(timespec='seconds')}\n"
            )
        old = dest + ".old"
        shutil.rmtree(old, ignore_errors=True)
        if os.path.isdir(dest):
            os.rename(dest, old)
        os.rename(staged, dest)
        shutil.rmtree(old, ignore_errors=True)
    except BaseException:
        shutil.rmtree(staged, ignore_errors=True)
        raise
    return {
        "rules": len(checked.rules),
        "groups": len(checked.groups),
        "unlisted": len(set(checked.rules) - listed),
        "severities": by_severity,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument(
        "--archive", help="install from this tarball instead of downloading"
    )
    args = parser.parse_args()

    config = load_config()
    settings = review_upstream_rules(load_profile(config, bot_dir()))
    if not settings:
        log("This project's profile sets no review.upstreamRules; nothing to sync.")
        return 0
    dest = settings["dir"]
    if not os.path.isabs(dest):
        dest = os.path.join(bot_dir(), dest)

    with tempfile.TemporaryDirectory() as scratch:
        archive = args.archive
        if not archive:
            if not settings.get("source"):
                log("review.upstreamRules has no `source` to download; use --archive.")
                return 1
            archive = os.path.join(scratch, "rules.tar.gz")
            try:
                download(settings["source"], archive)
            except (OSError, ValueError) as e:
                log(f"ERROR: could not download {settings['source']}: {e}")
                return 1
        try:
            summary = install(archive, dest, settings.get("source", ""))
        except (OSError, ValueError, tarfile.TarError) as e:
            log(f"ERROR: {e}; {dest} left as it was")
            return 1

    counts = ", ".join(f"{n} {s}" for s, n in summary["severities"].items())
    log(
        f"Installed {summary['rules']} rules ({counts}) in {summary['groups']} "
        f"capability groups at {dest}"
    )
    if summary["unlisted"]:
        log(f"{summary['unlisted']} rules are in no capability group and never apply.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
