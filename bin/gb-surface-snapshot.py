#!/usr/bin/env python3
"""gb-surface-snapshot — freeze xAI's published Grok Bot surface into a hashed snapshot.

Oracle source 1 of 3 (see AGENTS.md §ORACLE): docs.x.ai is the vendor's own
statement of what Grok Bot can do. We do not control it, so it can falsify our
deployment. This script copies it verbatim and hashes it; gb-surface-gate.py
decides whether the copy is trustworthy and whether anything moved.

Fail-closed contract (the rule this file exists to enforce):
  an empty or short page set is an ERROR, never a pass. "Nothing new" and
  "the fetcher broke" MUST NOT look the same. Every page carries its HTTP
  status, byte count, sha256, and a required-marker flag so the gate can tell
  them apart mechanically.

Usage:
  gb-surface-snapshot.py [--out DIR] [--date YYYY-MM-DD] [--timeout S] [--json]
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import pathlib
import re
import sys
import urllib.error
import urllib.request

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from gbtypes import atomic_write_bytes, atomic_write_text  # noqa: E402

LLMS_URL = "https://docs.x.ai/llms.txt"
# Tier-1 producers AGENTS.md §5 promises to poll. They were named in the doc for days while this
# tool fetched only the grok-bot page set — a watch list nobody watched. Measured fetchable
# 2026-09-11; x.ai/news is deliberately absent because it returns 403 to a plain client and a
# promise we cannot keep does not belong in the loop.
#
# THE SECOND OFFICIAL TREE. Added 2026-09-11 after the awesome-grok-bot index made it plain that
# `cursor.com/docs/grok-bot/*` and `cursor.com/help/grok-bot/*` carry official pages with NO
# docs.x.ai equivalent — the TLS-proxy guide exists only there, and the routines page is the only
# official source for Slack-keyword and webhook triggers. `llms.txt` indexes docs.x.ai alone, so
# every canary and freshness claim this repo made covered one of the vendor's two doc trees while
# reading as if it covered the surface.
#
# THE PRACTITIONER INDEX. Also 2026-09-11: the mission is "state of the art every week", and for
# four ticks the loop watched only the vendor. awesome-grok-bot is a 799-entry curated index that
# moves daily; it is the one community source whose CHANGE is a usable signal, so it is fetched,
# hashed, and gated by g13 like everything else here. Community claims are leads, never evidence.
EXTRA_SOURCES = [
    ("release-notes", "https://docs.x.ai/developers/release-notes.md", 4000),
    ("sitemap", "https://docs.x.ai/sitemap.xml", 8000),
    ("status-feed", "https://status.x.ai/feed.xml", 2000),
    ("plans", "https://cursor.com/help/grok-bot/plans", 20000),
    ("cursor-docs-hub", "https://cursor.com/docs/grok-bot", 50000),
    ("cursor-docs-teams", "https://cursor.com/docs/grok-bot/teams", 50000),
    ("cursor-docs-security", "https://cursor.com/docs/grok-bot/security", 50000),
    (
        "cursor-docs-security-faq",
        "https://cursor.com/docs/grok-bot/security-faq",
        50000,
    ),
    ("cursor-docs-identity", "https://cursor.com/docs/grok-bot/identity", 50000),
    (
        "cursor-docs-private-networks",
        "https://cursor.com/docs/grok-bot/private-networks",
        50000,
    ),
    # No docs.x.ai equivalent exists for this one.
    ("cursor-docs-proxies", "https://cursor.com/docs/grok-bot/proxies", 50000),
    ("help-routines", "https://cursor.com/help/grok-bot/routines", 50000),
    ("help-secrets", "https://cursor.com/help/grok-bot/secrets", 50000),
    ("help-connect-plugins", "https://cursor.com/help/grok-bot/connect-plugins", 50000),
    (
        "help-computer-recovery",
        "https://cursor.com/help/grok-bot/computer-recovery",
        50000,
    ),
    (
        "practitioner-index",
        "https://raw.githubusercontent.com/RongleCat/awesome-grok-bot/main/README.md",
        100000,
    ),
]
UA = "grokbot-surface-snapshot/1 (+local weekly refresh; contact: Joshua Nowak)"
REQUIRED_MARKER = "Grok Bot"
MIN_PAGE_BYTES = 500
SECTION_RE = re.compile(r"^## (.+)$")
LINK_RE = re.compile(r"^- \[(?P<title>[^\]]+)\]\((?P<url>https://docs\.x\.ai/[^)]+)\)")


def fetch(url: str, timeout: float) -> tuple[int, bytes, str]:
    # `Accept: text/markdown` makes docs.x.ai 404 a `.md` URL (measured 2026-09-10);
    # `*/*` is the only value proven to return the page. Do not "improve" this.
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "*/*"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read(), ""
    except urllib.error.HTTPError as e:  # a 404 is a measurement, not a crash
        return e.code, e.read() if e.fp else b"", f"HTTPError {e.code}"
    except Exception as e:  # DNS, TLS, timeout — the "fetcher broke" family
        return 0, b"", f"{type(e).__name__}: {e}"


def slug(url: str) -> str:
    return url.removeprefix("https://docs.x.ai/").removesuffix(".md").replace("/", "__")


def parse_llms(text: str) -> tuple[list[dict], int]:
    """Return (grok-bot page rows, total page count across all sections)."""
    section = ""
    rows: list[dict] = []
    total = 0
    for line in text.splitlines():
        m = SECTION_RE.match(line)
        if m:
            section = m.group(1).strip()
            continue
        m = LINK_RE.match(line.strip())
        if not m:
            continue
        url = m.group("url")
        if not url.endswith(".md"):
            continue
        total += 1
        if "/grok-bot/" in url:
            rows.append(
                {
                    "section": section,
                    "title": m.group("title"),
                    "url": url,
                    "slug": slug(url),
                }
            )
    return rows, total


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--out", default=str(pathlib.Path(__file__).resolve().parents[1] / "surface")
    )
    ap.add_argument(
        "--date", default=dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d")
    )
    ap.add_argument("--timeout", type=float, default=30.0)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    outdir = pathlib.Path(args.out) / args.date
    (outdir / "pages").mkdir(parents=True, exist_ok=True)

    status, body, err = fetch(LLMS_URL, args.timeout)
    manifest: dict = {
        "captured_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "index_url": LLMS_URL,
        "index_status": status,
        "index_bytes": len(body),
        "index_sha256": hashlib.sha256(body).hexdigest() if body else "",
        "index_error": err,
        "llms_total_pages": 0,
        "grok_bot_pages_listed": 0,
        "pages": [],
    }
    if status != 200 or not body:
        atomic_write_text(
            (outdir / "manifest.json"), json.dumps(manifest, indent=1) + "\n"
        )
        print(
            f"ERROR index fetch failed: status={status} err={err or 'empty body'}",
            file=sys.stderr,
        )
        return 2

    text = body.decode("utf-8", "replace")
    atomic_write_text((outdir / "llms.txt"), text)
    rows, total = parse_llms(text)
    manifest["llms_total_pages"] = total
    manifest["grok_bot_pages_listed"] = len(rows)

    for row in rows:
        st, raw, e = fetch(row["url"], args.timeout)
        content = raw.decode("utf-8", "replace") if raw else ""
        if st == 200 and content:
            atomic_write_text((outdir / "pages" / f"{row['slug']}.md"), content)
        manifest["pages"].append(
            {
                **row,
                "status": st,
                "bytes": len(raw),
                "sha256": hashlib.sha256(raw).hexdigest() if raw else "",
                "marker_ok": REQUIRED_MARKER in content,
                "error": e,
            }
        )

    # The named Tier-1 sources, fetched and hashed alongside the page set.
    manifest["extras"] = []
    (outdir / "extras").mkdir(exist_ok=True)
    for label, url, floor in EXTRA_SOURCES:
        st, raw, e = fetch(url, args.timeout)
        if st == 200 and raw:
            atomic_write_bytes((outdir / "extras" / label), raw)
        manifest["extras"].append(
            {
                "label": label,
                "url": url,
                "status": st,
                "bytes": len(raw),
                "min_bytes": floor,
                "sha256": hashlib.sha256(raw).hexdigest() if raw else "",
                "error": e,
            }
        )

    atomic_write_text((outdir / "manifest.json"), json.dumps(manifest, indent=1) + "\n")
    ok = sum(
        1
        for p in manifest["pages"]
        if p["status"] == 200 and p["bytes"] >= MIN_PAGE_BYTES and p["marker_ok"]
    )
    if args.json:
        print(
            json.dumps(
                {
                    "snapshot": str(outdir),
                    "pages_ok": ok,
                    "pages_listed": len(rows),
                    "llms_total_pages": total,
                }
            )
        )
    else:
        ex_ok = sum(
            1
            for x in manifest["extras"]
            if x["status"] == 200 and x["bytes"] >= x["min_bytes"]
        )
        print(
            f"snapshot {outdir}: {ok}/{len(rows)} grok-bot pages OK, {total} pages listed in llms.txt, "
            f"{ex_ok}/{len(manifest['extras'])} extra sources OK"
        )
    return 0 if ok else 2


if __name__ == "__main__":
    sys.exit(main())
