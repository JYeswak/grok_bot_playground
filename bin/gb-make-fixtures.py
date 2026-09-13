#!/usr/bin/env python3
"""gb-make-fixtures — (re)generate the known-bad and known-good fixtures the gate is proven against.

One fixture per failure mode, each isolating exactly one check: a fixture that
trips two checks cannot prove which one fired. Fixtures are committed so they
are inspectable; this generator exists so they are also reproducible.

  gb-make-fixtures.py [--out fixtures]
"""

from __future__ import annotations

import argparse
import copy
import datetime as dt
import hashlib
import json
import pathlib
import shutil
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from gbtypes import atomic_write_text  # noqa: E402

PAGE_BODY = (
    "#### Work with Grok Bot\n\n# Fixture page {n}\n\n"
    "Synthetic stand-in for a docs.x.ai Grok Bot page. It exists so the gate's "
    "integrity check has a real file to hash. Grok Bot runs on a persistent cloud "
    "computer shared by every Bot on the account; approvals, routines, skills, and "
    "connectors are the surfaces this repo tracks week to week. "
    "This body is padded past the minimum byte floor so a truncated-page fixture is "
    "distinguishable from a healthy one by length alone, not by luck.\n\n"
    "* routines run on a schedule owned by one Bot\n"
    "* skills are reusable instructions shared across Bots\n"
    "* approvals stop consequential actions before they run\n"
    "* the shared computer is not a security boundary between Bots\n"
)


def djb2(s: str) -> str:
    h = 0
    for ch in s:
        h = ((h << 5) - h + ord(ch)) & 0xFFFFFFFF
    return str(h)


def with_ids(audit: dict) -> dict:
    """Keep enabled_ids consistent with enabled_names — the gate diffs ids, not names."""
    g = audit["gates"]
    g["enabled_ids"] = sorted(djb2(n) for n in g["enabled_names"])
    g["enabled"] = len(g["enabled_ids"])
    return audit


def page(n: int) -> str:
    body = PAGE_BODY.format(n=n)
    return body + ("filler line to clear the byte floor\n" * 4)


def market_doc(refs: dict, installed_ref: str = "abc123") -> dict:
    """A market census: the plugin catalog plus what this account has installed and pinned."""
    return {
        "schema": "gb-market/1",
        "captured_at": "2026-09-11T06:00:00+00:00",
        "plugins": {
            "total": len(refs),
            "rows": [
                {
                    "name": n,
                    "git_ref": r,
                    "status": "PLUGIN_STATUS_APPROVED",
                    "updated_at": "1789000000000",
                }
                for n, r in sorted(refs.items())
            ],
        },
        "bot_marketplace": {
            "total": 2,
            "categories": ["Sales"],
            "rows": [
                {
                    "slug": "a",
                    "name": "A",
                    "category": "Sales",
                    "creator": "x",
                    "updated_at_ms": "1789000000000",
                }
            ],
        },
        "my_templates": [],
        "installed": [
            {
                "name": "vercel",
                "enabled": True,
                "catalog_git_ref": installed_ref,
                "catalog_status": "PLUGIN_STATUS_APPROVED",
            }
        ],
    }


def mcp_doc(tools: int, ok: bool = True, state: str | None = None) -> dict:
    """`state` distinguishes the failure KINDS a reader has to act on differently: an unreachable
    server is an outage, a rejected credential is a stale secret, and 401 with no credential in
    hand is neither."""
    st = state or ("healthy" if ok else "unreachable")
    return {
        "schema": "gb-mcp/1",
        "captured_at": "2026-09-11T06:00:00+00:00",
        "servers": [
            {
                "name": "xai-docs",
                "ok": ok,
                "state": st,
                "credential": "credential-resolved"
                if state == "credential-rejected"
                else "no-credential-declared",
                "tool_count": tools,
                "tools": [f"t{i}" for i in range(tools)],
                "error": None
                if ok
                else (
                    "HTTP 401 WITH a resolved credential"
                    if st == "credential-rejected"
                    else "HTTP 0 URLError"
                ),
            }
        ],
        "probed": 1,
        "ok": 1 if ok else 0,
        "failed": [] if ok else ["xai-docs"],
    }


MCP_OK = mcp_doc(4)


def util_doc(idle_days: float | None, routines: int = 1, entries: int = 20) -> dict:
    """A fleet census. `idle_days` past the grace with no routine and no conversation is the
    shape g19 exists to refuse; inside the grace it must stay quiet."""
    idle = routines == 0 and entries <= 2
    return {
        "schema": "gb-utilization/1",
        "captured_at": "2026-09-11T06:00:00+00:00",
        "bots": [
            {
                "name": "Ops",
                "entries": entries,
                "last_active_days": idle_days,
                "routines": routines,
                "routines_with_runs": 0,
                "skills": 1,
                "idle_credentialed": idle,
            }
        ],
        "live_entries": entries,
        "orphaned_entries": 0,
        "orphaned_replicas": 0,
        "idle_credentialed": ["Ops"] if idle else [],
        "routines_total": routines,
        "usage_percent": 2.7,
        "memory_shards_with_content": 0,
        "bot_count": 1,
    }


UTIL_OK = util_doc(0.5)


def archive_doc(at_cap: int = 0) -> dict:
    """An archive manifest. `at_cap` replicas are the ones actively losing history to rotation."""
    return {
        "schema": "gb-context-archive/1",
        "captured_at": "2026-09-11T06:00:00+00:00",
        "archive_dir": "/fixture/archive",
        "client_cap": 200,
        "replicas": 2,
        "entries_total": 250,
        "orphaned_entries": 50,
        "at_cap": [f"uuid-{i}" for i in range(at_cap)],
        "rows": [
            {
                "uuid": "uuid-0",
                "entries": 200,
                "at_client_cap": at_cap > 0,
                "belonged_to": "Ops",
                "status": "live",
                "sha256": "a" * 64,
                "bytes": 10,
            }
        ],
    }


ARCHIVE_OK = archive_doc()


def grokbotdev_doc(n: int = 1, used_mcp: bool = False) -> dict:
    """Lean grokbot.dev artifact. `used_mcp` is the NE-25 RED shape; fixtures stay unused."""
    rows = [
        {
            "id": f"fixture-{i}",
            "type": "template",
            "slug": f"fixture-{i}",
            "headline": f"Fixture {i}",
            "added_at": "2026-09-11T00:00:00.000Z",
        }
        for i in range(n)
    ]
    return {
        "schema": "gb-grokbotdev/1",
        "epistemic": "community-claim",
        "captured_at": "2026-09-11T06:00:00Z",
        "count": n,
        "rows": rows,
        "transport": "json",
        "mcp": {
            "advertised": True,
            "used": used_mcp,
            "refused": "NE-25 mcp.grokbot.dev NXDOMAIN; JSON/RSS only",
        },
    }


GROKBOTDEV_OK = grokbotdev_doc()


def usecases_doc(bots: int = 2, integrations=(("Gmail", 2), ("Slack", 1))) -> dict:
    """A population corpus. Zero Bots is a FAILED FETCH, never a quiet ecosystem — g21 must
    refuse it rather than report calm."""
    return {
        "schema": "gb-usecases/1",
        "captured_at": "2026-09-11T06:00:00+00:00",
        "corpus_repo": "fixture/corpus",
        "bots": bots,
        "curated_links": 10,
        "categories": [["Ops", bots]],
        "integrations": [list(i) for i in integrations],
        "integration_gap": [
            {"integration": i[0], "bots": i[1], "installed": False}
            for i in integrations
        ],
        "approval_share": 0.3,
        "rows": [
            {"name": f"Bot {i}", "category": "Ops", "integrations": ["Gmail"]}
            for i in range(bots)
        ],
        "links": [],
    }


USECASES_OK = usecases_doc()
# A prior week to diff against: g21 must distinguish "moved unreviewed" from "first snapshot".
USECASES_PREV = usecases_doc()
GAPS_OK = {
    "schema": "gb-gaps/1",
    "catalog_plugins": 2,
    "corpus_bots": 2,
    "catalog_by_kind": {"connector": 2},
    "distinct_integrations": 2,
    "demand_gap": [{"integration": "Notion", "bots": 3}],
    "domain_gap": [
        {
            "domain": "math / statistics",
            "plugins": 0,
            "examples": [],
            "mirror_candidates": ["fast_cmaes"],
        }
    ],
    "shelf": [["Sales", 1]],
    "built": {"Ops": 2},
    "mirror_candidates": [],
}


DISCOVERY_OK = {
    "schema": "gb-discovery/1",
    "captured_at": "2026-09-11T06:00:00+00:00",
    "lookback_days": 21,
    "notable_stars": 50,
    "errors": [],
    "candidates_total": 2,
    "new": [{"repo": "noise/repo", "stars": 900, "description": "fixture noise"}],
    "notable": ["noise/repo"],
}


MARKET_PREV = market_doc({"vercel": "abc123", "supabase": "def456"})
MARKET_CUR = market_doc({"vercel": "abc123", "supabase": "def456"})


PRACTITIONER_SHA = hashlib.sha256(b"fixture-practitioner-index").hexdigest()
PRACTITIONER_REVIEWED = {
    "source": "awesome-grok-bot",
    "sha256": PRACTITIONER_SHA,
    "reviewed_at": "2026-09-11",
    "note": "fixture: reviewed",
}


def good_surface(
    date: str, n_pages: int = 16, total: int = 168
) -> tuple[dict, dict[str, str]]:
    files, pages = {}, []
    for i in range(1, n_pages + 1):
        slug = f"grok-bot__fixture-{i:02d}"
        content = page(i)
        files[slug] = content
        pages.append(
            {
                "section": "Grok Bot",
                "title": f"Fixture {i}",
                "url": f"https://docs.x.ai/grok-bot/fixture-{i:02d}.md",
                "slug": slug,
                "status": 200,
                "bytes": len(content.encode()),
                "sha256": hashlib.sha256(content.encode()).hexdigest(),
                "marker_ok": True,
                "error": "",
            }
        )
    manifest = {
        "captured_at": f"{date}T06:00:00+00:00",
        "index_url": "https://docs.x.ai/llms.txt",
        "index_status": 200,
        "index_bytes": 16540,
        "index_sha256": hashlib.sha256(b"fixture-index").hexdigest(),
        "index_error": "",
        "llms_total_pages": total,
        "grok_bot_pages_listed": n_pages,
        "pages": pages,
        # g13 reads this row: the practitioner index is fetched and hashed like every other source.
        "extras": [
            {
                "label": "practitioner-index",
                "url": "https://example.invalid/README.md",
                "status": 200,
                "bytes": 211120,
                "min_bytes": 100000,
                "sha256": PRACTITIONER_SHA,
                "error": "",
            }
        ],
    }
    return manifest, files


GOOD_AUDIT = {
    "captured_at": "2026-09-11T06:00:00+00:00",
    "host": "fixture.local",
    "support_dir": "/fixture/Application Support/Grok Bot",
    "support_present": True,
    "app": {
        "path": "/fixture/Grok Bot.app",
        "present": True,
        "version": "0.47.0",
        "bundle_id": "com.anysphere.sand",
        "signed_by": "Developer ID Application: Anysphere Incorporated",
    },
    "update_marker": {
        "version": 1,
        "phase": "applied",
        "mechanism": "squirrel",
        "targetVersion": "0.47.0",
        "fromVersion": "0.46.0",
        "stagedAtMs": 1789000000000,
    },
    "session_marker": {"version": 1, "appVersion": "0.47.0", "crashSeen": False},
    "secret_keys": ["cursor-accounts", "cursor-machine-id", "local-exec-file-key"],
    "bots": [
        {
            "id": "b1",
            "name": "Chief of Staff",
            "is_group": False,
            "description_chars": 1214,
            "idle_days": 0.4,
            "notifications_enabled": True,
            "unread_count": 0,
            "hidden": False,
            "member_ids": [],
        },
        {
            "id": "b2",
            "name": "CRM",
            "is_group": False,
            "description_chars": 1094,
            "idle_days": 1.1,
            "notifications_enabled": True,
            "unread_count": 0,
            "hidden": False,
            "member_ids": [],
        },
        {
            "id": "b3",
            "name": "Ops",
            "is_group": False,
            "description_chars": 960,
            "idle_days": 2.0,
            "notifications_enabled": True,
            "unread_count": 0,
            "hidden": False,
            "member_ids": [],
        },
    ],
    "gates": {
        "total": 2760,
        "enabled": 3,
        "decoded": 609,
        "enabled_names": [
            "sand_auto_review",
            "sand_multi_machine_local_exec",
            "sand_teach_by_demonstration",
        ],
        "undecoded_enabled": 0,
        "hash_used": "djb2",
    },
    "tunables": {
        "configs": 3,
        "layers": 0,
        "with_values": 3,
        "distinct_param_keys": 4,
        "named": 2,
        "names": {
            "h1": "grok_bot_conversation_size_limits",
            "h2": "grok_bot_loop_detection",
        },
        "digests": {"h1": "aaaaaaaaaaaa", "h2": "bbbbbbbbbbbb", "h3": "cccccccccccc"},
    },
    "account": {
        "app_version_seen_by_backend": "0.47.0",
        "membership": "ultra",
        "is_enterprise_user": False,
        "is_enterprise_trial_user": False,
    },
}


REGISTRY = {
    "host_of_record": "studio",
    "desktops": [
        {"label": "studio", "hostname": "fixture.local", "ssh": None},
        {"label": "brain", "hostname": "fixture-brain.local", "ssh": "fixture-brain"},
    ],
}


def desktop_audit(version: str) -> dict:
    d = copy.deepcopy(GOOD_AUDIT)
    d["host"] = "fixture-brain.local"
    d["app"]["version"] = version
    d["session_marker"]["appVersion"] = version
    d["account"]["app_version_seen_by_backend"] = version
    return d


USAGE_OK = {
    "period_start": "2026-09-05T18:22:04Z",
    "next_reset": "2026-09-12T02:17:01Z",
    "usage_percent": 2.7,
    "has_available_usage": True,
    "plan_label": "Grok Bot Plan",
    "on_demand_enabled": False,
    "on_demand_eligible": True,
    "dashboard_url": "https://x/",
}


def inventory(
    device: str,
    hostname: str,
    *,
    policy="never",
    rules=1,
    routines=("ok", "ok", "ok"),
    enabled=True,
    drop_policy=False,
    server_bots=None,
    # g25-routine-liveness asks a question no other check asks: is this routine PAST DUE and
    # still never run? It needs `next_run_at_ms`, which this builder never emitted — which is
    # exactly why disabling g25 still produced 54/54. A check no fixture can fail is not a check.
    next_run_ms=None,
    usage=...,
) -> dict:
    doc = {
        "schema": "gb-inventory/1",
        "device": device,
        "hostname": hostname,
        "recorded_at": "2026-09-11T07:00:00+00:00",
        "app_version": "0.47.0",
        "usage": (copy.deepcopy(USAGE_OK) if usage is ... else usage),
        "local_exec_policy": policy,
        "policy_recorded_at": "2026-09-05",
        "auto_review_rules": [
            {
                "kind": "require-approval",
                "rule": "require approval before sending any external email",
            }
        ]
        * rules,
        "plugins": [],
        "mcp_servers": [],
        "server_bots": server_bots
        if server_bots is not None
        else [
            {
                "name": "Chief of Staff",
                "numeric_id": "1001",
                "harness": "box",
                "description_chars": 1200,
            }
        ],
        "bots": [
            {
                "name": "Chief of Staff",
                "skills": ["weekly-digest"],
                "routines": [
                    {
                        "name": "morning digest",
                        "schedule": "weekdays 08:00",
                        "enabled": enabled,
                        "recent_runs": list(routines),
                        "provenance": "user",
                        "next_run_at_ms": next_run_ms,
                    }
                ],
            }
        ],
    }
    if drop_policy:
        doc["local_exec_policy"] = None
    return doc


ACK_FRESH = {
    "schema": "gb-pauses/1",
    "acknowledged": [
        {
            "bot": "Chief of Staff",
            "routine": "morning digest",
            "reason": "fixture: deliberate pause",
            "acked_by": "fixture",
            "acked_at": "2026-09-01",
            "review_by": "2026-12-01",
        }
    ],
}
ACK_EXPIRED = {
    "schema": "gb-pauses/1",
    "acknowledged": [
        {
            "bot": "Chief of Staff",
            "routine": "morning digest",
            "reason": "fixture: pause never re-decided",
            "acked_by": "fixture",
            "acked_at": "2026-05-01",
            "review_by": "2026-08-01",
        }
    ],
}


def _write_surface(
    root: pathlib.Path,
    snap_date: str,
    manifest: dict,
    files: dict[str, str],
    prev_surface: tuple | None,
) -> None:
    for date, man, fs in [(snap_date, manifest, files)] + (
        [("2026-09-04", *prev_surface)] if prev_surface else []
    ):
        (root / "surface" / date / "pages").mkdir(parents=True)
        atomic_write_text(
            (root / "surface" / date / "manifest.json"),
            json.dumps(man, indent=1) + "\n",
        )
        for slug, content in fs.items():
            atomic_write_text(
                (root / "surface" / date / "pages" / f"{slug}.md"), content
            )


def _write_deployment(
    root: pathlib.Path,
    cur: dict,
    prev: dict | None,
    desktops: dict[str, dict] | None,
    inventories: dict[str, dict] | None,
) -> None:
    (root / "deployment").mkdir(parents=True)
    if prev is not None:
        atomic_write_text(
            (root / "deployment" / "2026-09-04T0600.json"),
            json.dumps(prev, indent=1) + "\n",
        )
    atomic_write_text(
        (root / "deployment" / "2026-09-11T0600.json"), json.dumps(cur, indent=1) + "\n"
    )
    if desktops is None:
        desktops = {"2026-09-11T0605.brain": desktop_audit(cur["app"]["version"])}
    if desktops:
        (root / "deployment" / "desktops").mkdir(parents=True)
        for stem, payload in desktops.items():
            atomic_write_text(
                (root / "deployment" / "desktops" / f"{stem}.json"),
                json.dumps(payload, indent=1) + "\n",
            )
    if inventories is None:
        inventories = {
            "2026-09-11T0700.studio": inventory("studio", "fixture.local"),
            "2026-09-11T0700.brain": inventory("brain", "fixture-brain.local"),
        }
    if inventories:
        (root / "inventory").mkdir(parents=True)
        for stem, payload in inventories.items():
            atomic_write_text(
                (root / "inventory" / f"{stem}.json"),
                json.dumps(payload, indent=1) + "\n",
            )


def _write_schedule(root: pathlib.Path, agent, runs) -> None:
    """A registered agent plus a run log whose newest entry came from the scheduler."""
    sched = root / "schedule"
    sched.mkdir(parents=True)
    if agent is not False:
        atomic_write_text(
            (sched / "agent.json"),
            json.dumps(
                agent
                or {
                    "installed": True,
                    "label": "fixture.weekly",
                    "schedule": "Mon 09:15 local",
                    "installed_at": "2026-09-05",
                },
                indent=1,
            )
            + "\n",
        )
    rows_ = (
        runs
        if runs is not None
        else [
            {
                "at": "2026-09-08T09:15:00Z",
                "verdict": "GREEN",
                "exit": 0,
                "source": "launchd",
            },
            {
                "at": "2026-09-11T09:15:00Z",
                "verdict": "GREEN",
                "exit": 0,
                "source": "launchd",
            },
        ]
    )
    atomic_write_text(
        (sched / "runs.jsonl"), "".join(json.dumps(r) + "\n" for r in rows_)
    )


def typecheck_doc(errors: int = 0, ran: bool = True, missing: tuple = ()) -> dict:
    """A `gb-typecheck` artifact. `errors` drives g23's RED leg; `ran=False` drives its ERROR leg,
    because a checker that could not run leaves the repo UNMEASURED, never clean."""
    return {
        "schema": "gb-typecheck/1",
        "measured_at": "2026-09-11T06:00:00-06:00",
        "floor_size": 3,
        "checked": ["bin/gbargs.py", "bin/gbtypes.py", "bin/gb-typecheck.py"],
        "missing_from_disk": list(missing),
        "runs": [
            {
                "tool": "mypy",
                "version": "mypy 1.11.0",
                "ran": ran,
                "error_count": errors,
                "errors": [
                    'bin/gbtypes.py:120: error: Returning Any from function declared to return "bool"'
                ]
                * errors,
                "detail": "3 file(s) under --strict",
            },
            {
                "tool": "pyright",
                "version": "pyright 1.1.390",
                "ran": ran,
                "error_count": 0,
                "errors": [],
                "detail": "3 file(s)",
            },
        ],
        "clean": errors == 0 and ran and not missing,
    }


TYPECHECK_OK = typecheck_doc()


def _write_series(root: pathlib.Path, o: dict) -> None:
    """The dated per-tick artifact directories a gate diffs. Each entry is
    (dir, key, default_single, default_pair): a pair writes last-week + this-week so the gate has
    something to compare, a single writes only the current one. `False` omits the directory,
    which is what the known-bad fixtures for "never ran" need."""
    series = [
        ("archive", "archive", ARCHIVE_OK, None),
        ("usecases", "usecases", None, (USECASES_PREV, USECASES_OK)),
        ("gaps", "gaps", GAPS_OK, None),
        ("utilization", "utilization", UTIL_OK, None),
        ("discovery", "discovery", DISCOVERY_OK, None),
        ("mcp", "mcp", None, (MCP_OK, MCP_OK)),
        ("market", "market", None, (MARKET_PREV, MARKET_CUR)),
        ("typecheck", "typecheck", TYPECHECK_OK, None),
        ("grokbotdev", "grokbotdev", GROKBOTDEV_OK, None),
    ]
    for dirname, key, single, pair in series:
        val = o.get(key)
        if val is False:
            continue
        (root / dirname).mkdir(parents=True, exist_ok=True)
        if isinstance(val, tuple) and single is not None:  # (date, doc) override
            date, doc = val
            atomic_write_text(
                (root / dirname / f"{date}.json"), json.dumps(doc, indent=1) + "\n"
            )
        elif pair is not None:
            prev_d, cur_d = val if isinstance(val, tuple) else pair
            atomic_write_text(
                (root / dirname / "2026-09-04T0600.json"),
                json.dumps(prev_d, indent=1) + "\n",
            )
            atomic_write_text(
                (root / dirname / "2026-09-11T0600.json"),
                json.dumps(cur_d, indent=1) + "\n",
            )
        else:
            atomic_write_text(
                (root / dirname / "2026-09-11T0600.json"),
                json.dumps(val or single, indent=1) + "\n",
            )
    if o.get("discovery") is not False:
        seen = o.get("discovery_seen")
        if seen is not False:
            atomic_write_text(
                (root / "discovery-seen.json"),
                json.dumps(
                    seen
                    or {
                        "schema": "gb-discovery-seen/1",
                        "reviewed_at": "2026-09-11",
                        "dismissed": {"noise/repo": "fixture: irrelevant"},
                        "repos": [],
                    },
                    indent=1,
                )
                + "\n",
            )
        atomic_write_text(
            (root / "sources.json"),
            json.dumps(
                {
                    "schema": "gb-sources/1",
                    "repos": [
                        {
                            "repo": "xai-org/plugin-marketplace",
                            "tier": "official",
                            "means": "fixture",
                        }
                    ],
                },
                indent=1,
            )
            + "\n",
        )


def _write_sidecars(root: pathlib.Path, snap_date: str, o: dict) -> None:
    """The repo-root records the review gates read. Each is `False` to omit (which is what the
    known-bad fixtures for those gates need) or a dict to override the healthy default."""
    _write_series(root, o)
    defaults = {
        "surface-reviewed.json": (
            "surface_reviewed",
            {
                "snapshot": snap_date,
                "reviewed_at": snap_date,
                "note": "fixture: reviewed",
            },
        ),
        "market-reviewed.json": (
            "market_reviewed",
            {
                "snapshot": "2026-09-11T0600.json",
                "reviewed_at": "2026-09-11",
                "note": "fixture: reviewed",
            },
        ),
        "coverage-floor.json": (
            "floor",
            {
                "covered": 37,
                "of": 44,
                "recorded_at": "2026-09-11",
                "why": "fixture floor: what the fixture artifacts carry",
            },
        ),
        "practitioner-reviewed.json": ("practitioner", PRACTITIONER_REVIEWED),
        "usecases-reviewed.json": (
            "usecases_reviewed",
            {"snapshot": "2026-09-11T0600.json", "reviewed_at": "2026-09-11"},
        ),
        "dogfood-baseline.json": (
            "dogfood_baseline",
            {
                "schema": "gb-dogfood-baseline/2",
                "recorded_at": "2026-09-11T00:00:00+00:00",
                "gap_count": 7,
                "gaps": {
                    f"{kind}:gb-fixture-{kind}.py": {
                        "score": score,
                        "kind": kind,
                        "signals": {
                            "importers": 0,
                            "callers": 0,
                            "docs": 0,
                            "tick": 0,
                            "public": 0,
                        },
                        "detail": f"fixture: pinned {kind} exemplar",
                    }
                    for kind, score in (
                        ("absent", 120),
                        ("broken", 100),
                        ("unshipped", 80),
                        ("truncated", 60),
                        ("unreachable", 40),
                        ("tick-only", 20),
                        ("internal-only", 10),
                    )
                },
                "acceptances": [],
                "decisions": [
                    {
                        "gap": f"{kind}:gb-fixture-{kind}.py",
                        "disposition": "adopt",
                        "owner": "fixture",
                        "rationale": "fixture: pinned exemplar floor",
                        "evidence": "fixture: ordered by ORDER_ORACLE",
                        "falsifier": "fixture: would reopen on ladder change",
                        "review_by": "",
                        "review_predicate": "",
                        "ne": "",
                        "at": "2026-09-11T00:00:00+00:00",
                    }
                    for kind in (
                        "absent",
                        "broken",
                        "unshipped",
                        "truncated",
                        "unreachable",
                        "tick-only",
                        "internal-only",
                    )
                ],
            },
        ),
    }
    for fname, (key, default) in defaults.items():
        val = o.get(key)
        if val is False or (key == "market_reviewed" and o.get("market") is False):
            continue
        atomic_write_text((root / fname), json.dumps(val or default, indent=1) + "\n")
    if o.get("pauses") is not None:
        atomic_write_text(
            (root / "pauses-acknowledged.json"),
            json.dumps(o["pauses"], indent=1) + "\n",
        )


def write_root(
    out: pathlib.Path,
    name: str,
    snap_date: str,
    manifest: dict,
    files: dict[str, str],
    cur: dict,
    prev: dict | None,
    registry: dict | None = REGISTRY,
    findings: str | None = None,
    **o,
) -> None:
    """One fixture root. `o` carries the optional per-gate overrides (desktops, inventories,
    agent, runs, pauses, market, market_reviewed, surface_reviewed, practitioner, floor,
    prev_surface) — kwargs rather than twenty parameters, because every call site names them and
    the list grows by one every time a gate gains a record it reads."""
    root = out / name
    if root.exists():
        shutil.rmtree(root)
    _write_surface(root, snap_date, manifest, files, o.get("prev_surface"))
    _write_deployment(root, cur, prev, o.get("desktops"), o.get("inventories"))
    _write_schedule(root, o.get("agent"), o.get("runs"))
    _write_sidecars(root, snap_date, o)
    if registry is not None:
        atomic_write_text(
            (root / "desktops.json"), json.dumps(registry, indent=1) + "\n"
        )
    if findings is not None:
        (root / "findings").mkdir(parents=True)
        atomic_write_text((root / "findings" / f"{snap_date}.md"), findings)
    atomic_write_text(
        (root / "README.md"),
        f"# fixture: {name}\n\nGenerated by `bin/gb-make-fixtures.py`. Do not hand-edit.\n",
    )


def _cli_contract_fixtures(
    out: pathlib.Path, man: dict, files: dict, prev: dict
) -> None:
    """The known-bad root for g24: a CLI whose surface has decayed.

    Its own function rather than a lodger inside the typed-spine block — these fixtures
    answer a different question (does the operator surface still exist?) and grouping them
    by the check they feed is what keeps any one of these writers readable.
    """
    # g24 — a planted CLI whose command table and handler table disagree. This is the rot the
    # check exists for: adding a verb to COMMANDS without wiring HANDLERS is a KeyError the
    # first time an agent types it, and nothing else in the tree catches it.
    broke = out / "bad-cli-contract-unwired"
    write_root(broke.parent, broke.name, "2026-09-11", man, files, GOOD_AUDIT, prev)
    (broke / "bin").mkdir(parents=True, exist_ok=True)
    gb_stub = broke / "bin" / "gb"
    atomic_write_text(
        gb_stub,
        (
            "#!/usr/bin/env python3\n"
            '"""A CLI whose HANDLERS lost a verb. Parsed by g24, never executed."""\n'
            "EXIT_CODES = {0: 'ok', 1: 'findings', 2: 'usage', 3: 'env', 4: 'upstream', 5: 'refused'}\n"
            "COMMANDS = {'triage': 1, 'doctor': 1, 'health': 1, 'repair': 1, 'validate': 1,\n"
            "            'audit': 1, 'why': 1, 'work': 1, 'setup': 1, 'platform': 1,\n"
            "            'capabilities': 1, 'robot-docs': 1, 'quickstart': 1, 'examples': 1,\n"
            "            'info': 1, 'help': 1, 'completion': 1}\n"
            "HANDLERS = {'triage': 1, 'doctor': 1, 'health': 1, 'repair': 1, 'validate': 1,\n"
            "            'audit': 1, 'why': 1, 'work': 1, 'setup': 1, 'platform': 1,\n"
            "            'capabilities': 1, 'robot-docs': 1, 'quickstart': 1, 'examples': 1,\n"
            "            'info': 1, 'help': 1}\n"
        ),
    )
    gb_stub.chmod(0o755)
    # A compliant producer beside it, so this fixture fails g24 and ONLY g24. Without a .py in
    # bin/, g22 correctly reports "no python producers" and its ERROR dominates g24's RED —
    # which would make the fixture prove two things badly instead of one thing well.
    atomic_write_text(
        broke / "bin" / "gb-clean.py",
        (
            "#!/usr/bin/env python3\n"
            '"""A compliant producer: atomic write, deadlined child."""\n'
            "import pathlib\n"
            "import subprocess\n\n\n"
            "def emit(out: pathlib.Path, body: str) -> None:\n"
            "    atomic_write_text(out, body)  # noqa: F821 - fixture text, never imported\n\n\n"
            "def probe() -> str:\n"
            "    return subprocess.run(['true'], timeout=5, capture_output=True).stdout.decode()\n"
        ),
    )


_KIND_SCORES = {
    "absent": 120,
    "broken": 100,
    "unshipped": 80,
    "truncated": 60,
    "unreachable": 40,
    "tick-only": 20,
    "internal-only": 10,
}


def _dogfood_gap(key: str, score: int = 40) -> dict:
    kind = key.split(":")[0] if ":" in key else "unreachable"
    return {
        key: {
            "score": score,
            "kind": kind,
            "signals": {
                "importers": 0,
                "callers": 0,
                "docs": 0,
                "tick": 0,
                "public": 0,
            },
            "detail": f"fixture: {key}",
        }
    }


def _dogfood_decision(gap: str, disposition: str, **over: object) -> dict:
    base: dict = {
        "gap": gap,
        "disposition": disposition,
        "owner": "fixture",
        "rationale": f"fixture: {disposition} {gap}",
        "evidence": "",
        "falsifier": "",
        "review_by": "",
        "review_predicate": "",
        "ne": "",
        "at": "2026-09-11T00:00:00+00:00",
    }
    base.update(over)
    return base


def _dogfood_baseline(
    gaps: dict, decisions: list, acceptances: list | None = None
) -> dict:
    return {
        "schema": "gb-dogfood-baseline/2",
        "recorded_at": "2026-09-11T00:00:00+00:00",
        "gap_count": len(gaps),
        "gaps": gaps,
        "acceptances": acceptances if acceptances is not None else [],
        "decisions": decisions,
    }


def _dogfood_fixtures(out: pathlib.Path, man: dict, files: dict, prev: dict) -> None:
    """The known-bad roots for g29: one per RED arm, each failing nothing else."""
    gaps = _dogfood_gap("unreachable:gb-fixture.py")
    # A legacy blanket acceptance over gaps nobody disposed per gap.
    write_root(
        out,
        "bad-dogfood-blanket-accept",
        "2026-09-11",
        man,
        files,
        GOOD_AUDIT,
        prev,
        dogfood_baseline=_dogfood_baseline(
            {**gaps, **_dogfood_gap("unreachable:gb-second.py")},
            [],
            acceptances=[
                {
                    "at": "2026-09-11T00:00:00+00:00",
                    "by": "fixture",
                    "why": "fixture: free-form blanket",
                    "keys": ["unreachable:gb-fixture.py", "unreachable:gb-second.py"],
                }
            ],
        ),
    )
    # A defer past its review date (FIXTURE_NOW is 2026-09-11).
    write_root(
        out,
        "bad-dogfood-defer-expired",
        "2026-09-11",
        man,
        files,
        GOOD_AUDIT,
        prev,
        dogfood_baseline=_dogfood_baseline(
            gaps,
            [
                _dogfood_decision(
                    "unreachable:gb-fixture.py",
                    "defer",
                    review_by="2026-09-01",
                    review_predicate="fixture: tick re-reads",
                )
            ],
        ),
    )
    # A recorded reject whose gap is still open.
    write_root(
        out,
        "bad-dogfood-reject-open",
        "2026-09-11",
        man,
        files,
        GOOD_AUDIT,
        prev,
        dogfood_baseline=_dogfood_baseline(
            gaps,
            [_dogfood_decision("unreachable:gb-fixture.py", "reject")],
        ),
    )
    # An adopt missing its falsifier.
    write_root(
        out,
        "bad-dogfood-invalid-disposition",
        "2026-09-11",
        man,
        files,
        GOOD_AUDIT,
        prev,
        dogfood_baseline=_dogfood_baseline(
            gaps,
            [
                _dogfood_decision(
                    "unreachable:gb-fixture.py",
                    "adopt",
                    evidence="fixture: proof",
                )
            ],
        ),
    )
    # A recorded gap nobody decided at all.
    write_root(
        out,
        "bad-dogfood-undisposed",
        "2026-09-11",
        man,
        files,
        GOOD_AUDIT,
        prev,
        dogfood_baseline=_dogfood_baseline(gaps, []),
    )

    # g30 — two pinned exemplar scores swapped. Recomputation disagrees and the
    # recorded rank inverts the ladder: tampering with points cannot pass as
    # measurement. Dispositions stay valid so NOTHING else fires.
    tampered = {
        f"{kind}:gb-fixture-{kind}.py": {
            "score": {"truncated": 40, "unreachable": 60}.get(kind, _KIND_SCORES[kind]),
            "kind": kind,
            "signals": {
                "importers": 0,
                "callers": 0,
                "docs": 0,
                "tick": 0,
                "public": 0,
            },
            "detail": f"fixture: pinned {kind} exemplar",
        }
        for kind in _KIND_SCORES
    }
    write_root(
        out,
        "bad-dogfood-score-tampered",
        "2026-09-11",
        man,
        files,
        GOOD_AUDIT,
        prev,
        dogfood_baseline={
            "schema": "gb-dogfood-baseline/2",
            "recorded_at": "2026-09-11T00:00:00+00:00",
            "gap_count": 7,
            "gaps": tampered,
            "acceptances": [],
            "decisions": [
                {
                    "gap": f"{kind}:gb-fixture-{kind}.py",
                    "disposition": "adopt",
                    "owner": "fixture",
                    "rationale": "fixture: pinned exemplar floor",
                    "evidence": "fixture: ordered by ORDER_ORACLE",
                    "falsifier": "fixture: would reopen on ladder change",
                    "review_by": "",
                    "review_predicate": "",
                    "ne": "",
                    "at": "2026-09-11T00:00:00+00:00",
                }
                for kind in _KIND_SCORES
            ],
        },
    )


def _typed_spine_fixtures(
    out: pathlib.Path, man: dict, files: dict, prev: dict
) -> None:
    """The known-bad roots for the typed-spine checks (g22 durable IO, g23 type ratchet).

    Grouped out of `main` because a fixture catalogue grows one block per check forever, and a
    single 300-line function is where a mis-registered fixture hides. One family per function.
    """
    # g23 — the type floor regressed (a checker reported errors) and the ERROR twin where a
    # checker could not run at all: unmeasured is not clean, so that is ERROR, not RED.
    write_root(
        out,
        "bad-types-regressed",
        "2026-09-11",
        man,
        files,
        GOOD_AUDIT,
        prev,
        typecheck=typecheck_doc(errors=1),
    )
    write_root(
        out,
        "bad-types-unmeasured",
        "2026-09-11",
        man,
        files,
        GOOD_AUDIT,
        prev,
        typecheck=typecheck_doc(ran=False),
    )
    render_root = out / "bad-gate-error-envelope"
    write_root(
        render_root.parent,
        render_root.name,
        "2026-09-11",
        man,
        files,
        GOOD_AUDIT,
        prev,
    )
    atomic_write_text(
        render_root / "gate-error-envelope.json",
        json.dumps(
            {
                "verdict": "ERROR",
                "exit_code": 2,
                "checks": [],
                "advisories": [],
            },
            sort_keys=True,
        )
        + "\n",
    )

    # g22 — a planted producer that writes non-atomically and waits on a child with no deadline.
    # The check normally grades THIS repo's bin/; a fixture root carrying its own bin/ overrides
    # that, which is the only way to watch the check go RED without breaking the real tree.
    leaky = out / "bad-durable-io-raw-write"
    write_root(leaky.parent, leaky.name, "2026-09-11", man, files, GOOD_AUDIT, prev)
    (leaky / "bin").mkdir(parents=True, exist_ok=True)
    atomic_write_text(
        leaky / "bin" / "gb-leaky.py",
        (
            "#!/usr/bin/env python3\n"
            '"""A producer of the kind g22 exists to catch. Not imported, never run — parsed."""\n'
            "from __future__ import annotations\n\n"
            "import json\n"
            "import pathlib\n"
            "import subprocess\n\n\n"
            "def emit(out: pathlib.Path, doc: dict) -> None:\n"
            "    out.write_text(json.dumps(doc, indent=1) + '\\n')   # truncate-then-write: tearable\n\n\n"
            "def fetch() -> str:\n"
            "    proc = subprocess.Popen(['curl', '-s', 'https://x.ai'], stdout=subprocess.PIPE)\n"
            "    out, _ = proc.communicate()                          # no deadline: hangs the tick\n"
            "    return out.decode()\n"
        ),
    )

    # g26 — a done receipt with no Bot ask. Isolates the check: other checks see GOOD_AUDIT.
    bare = out / "bad-jobs-done-without-proof"
    write_root(bare.parent, bare.name, "2026-09-11", man, files, GOOD_AUDIT, prev)
    (bare / "jobs").mkdir(parents=True, exist_ok=True)
    atomic_write_text(
        bare / "jobs" / "walks.jsonl",
        json.dumps(
            {
                "id": "tpl:vendor-watch",
                "verdict": "done",
                "note": "fixture: name-match only",
                "paste_ok": True,
                "scripts_ok": 4,
                "scripts_need": 4,
                "proof_ok": False,
                "at": "2026-09-11T0700Z",
            },
            separators=(",", ":"),
        )
        + "\n",
    )


# ---------------------------------------------------------------------------------------------
# The planted-platform corpus — `fixtures/platform/`
#
# A DIFFERENT KIND OF FIXTURE, deliberately in its own subtree. Every root above is a working
# root the GATE grades. These are not: they are planted HOMES, planted `%APPDATA%`s and planted
# `$XDG_CONFIG_HOME`s that `gb platform --selftest` resolves through `gblib.platform_support`.
# They live under `fixtures/platform/` so the gate's fixture sweep (which iterates its own
# FIXTURE_EXPECT table, not the directory) never sees them and never has to learn about them.
#
# WHY THEY EXIST. Nobody in this repo owns a Windows machine or a Linux machine. Before this
# corpus the Windows and Linux branches of the resolver could only be asserted in prose, which
# is the same standard as not having written them. Making `platform_support` a pure function of
# (platform string, environment) makes those branches executable on a Mac: plant a foreign
# environment, resolve it, and check the answer against what the fixture says must happen.
#
# WHAT THEY DO NOT PROVE, stated here because the fixture is the place someone will look: that
# `%APPDATA%\\Grok Bot` is where the Windows client actually writes. That is an Electron
# inference, it is reported as UNVERIFIED everywhere, and only a real install closes it.
#
# ONE REASON PER CASE, the same discipline as the roots above.
# ---------------------------------------------------------------------------------------------
BOOTSTRAP = {
    "_why": "a synthetic stand-in for the desktop client's sand-statsig-bootstrap.json; its "
    "presence is what makes a planted directory look like a real support directory",
    "config": "{}",
}

WIN_LINUX_CAPS = {
    "credential_read": "NOT_IMPLEMENTED",
    "scheduler_install": "NOT_IMPLEMENTED",
    "deployment_audit": "PARTIAL",
    "local_exec_policy": "AVAILABLE",
}
REFUSED_CAPS = {
    "credential_read": "NOT_IMPLEMENTED",
    "scheduler_install": "NOT_IMPLEMENTED",
    "deployment_audit": "NOT_IMPLEMENTED",
    "local_exec_policy": "AVAILABLE",
}

PLATFORM_CASES: tuple[dict, ...] = (
    {
        "case": "macos-supported",
        "why": "the measured platform: the only one this tool calls SUPPORTED",
        "sys_platform": "darwin",
        "plant": ["home/Library/Application Support/Grok Bot"],
        "env": {"HOME": "home"},
        "expect": {
            "os": "macos",
            "status": "SUPPORTED",
            "support_dir": "home/Library/Application Support/Grok Bot",
            "support_dir_exists": True,
            "capabilities": {
                "credential_read": "AVAILABLE",
                "scheduler_install": "AVAILABLE",
                "deployment_audit": "AVAILABLE",
                "local_exec_policy": "AVAILABLE",
            },
        },
    },
    {
        "case": "windows-unverified",
        "why": "a Windows client installed where Electron's userData rule says it should be",
        "sys_platform": "win32",
        "plant": ["appdata/Grok Bot"],
        "env": {"HOME": "home", "APPDATA": "appdata"},
        "expect": {
            "os": "windows",
            "status": "UNVERIFIED",
            "support_dir": "appdata/Grok Bot",
            "support_dir_exists": True,
            "capabilities": WIN_LINUX_CAPS,
            "why_mentions": "INFERRED",
            # The refusal, driven through the same function `gb setup` calls. NOT_IMPLEMENTED
            # rather than ABSENT: there is no keychain item to be absent, because Windows does
            # not store the session in one.
            "keychain_probe": {"status": "NOT_IMPLEMENTED", "has_remediation": True},
        },
    },
    {
        "case": "linux-unverified",
        "why": "a Linux client installed under $XDG_CONFIG_HOME, same Electron rule",
        "sys_platform": "linux",
        "plant": ["config/Grok Bot"],
        "env": {"HOME": "home", "XDG_CONFIG_HOME": "config"},
        "expect": {
            "os": "linux",
            "status": "UNVERIFIED",
            "support_dir": "config/Grok Bot",
            "support_dir_exists": True,
            "capabilities": WIN_LINUX_CAPS,
            "why_mentions": "INFERRED",
            "keychain_probe": {"status": "NOT_IMPLEMENTED", "has_remediation": True},
        },
    },
    {
        "case": "override-moves-path-not-status",
        "why": "GB_SUPPORT_DIR wins over the inferred path — and the status stays UNVERIFIED",
        "sys_platform": "win32",
        # BOTH are planted. If only the override existed, the case would also pass for a
        # resolver that simply fell back to whatever exists, which is not what is being claimed.
        "plant": ["appdata/Grok Bot", "elsewhere/GrokBotData"],
        "env": {
            "HOME": "home",
            "APPDATA": "appdata",
            "GB_SUPPORT_DIR": "elsewhere/GrokBotData",
        },
        "expect": {
            "os": "windows",
            "status": "UNVERIFIED",
            "support_dir": "elsewhere/GrokBotData",
            "support_dir_exists": True,
            "capabilities": WIN_LINUX_CAPS,
            "path_source_mentions": "never the status",
            # The override path is NOT an inference and must not be described as one: an
            # operator who has already told the tool where the directory is must not be handed
            # a remediation asking him to do it again.
            "why_mentions": "the path is not a guess here",
        },
    },
    {
        "case": "linux-client-absent",
        "why": "the inferred Linux path does not exist — the remediation must name the override",
        "sys_platform": "linux",
        "plant": ["config"],
        "env": {"HOME": "home", "XDG_CONFIG_HOME": "config"},
        "expect": {
            "os": "linux",
            "status": "UNVERIFIED",
            "support_dir": "config/Grok Bot",
            "support_dir_exists": False,
            "capabilities": WIN_LINUX_CAPS,
            # The known-bad shape for an UNVERIFIED platform: absent is ambiguous between "not
            # installed" and "we guessed the wrong path", so the remediation must offer the
            # override rather than only telling him to install what he already installed.
            "remediation_mentions": "GB_SUPPORT_DIR",
        },
    },
    {
        "case": "android-credential-refused",
        "why": "UNSUPPORTED, and a producer asks for the credential read anyway: it must be "
        "refused with a remediation, never raised and never answered empty",
        # Termux reports sys.platform == "linux". Resolving it as Linux would hand a phone the
        # Linux desktop's userData path and report the client merely missing.
        "sys_platform": "linux",
        "plant": ["home"],
        "env": {"HOME": "home"},
        "env_literal": {"ANDROID_ROOT": "/system"},
        "expect": {
            "os": "android",
            "status": "UNSUPPORTED",
            "support_dir": None,
            "support_dir_exists": False,
            "capabilities": REFUSED_CAPS,
            "why_mentions": "no Grok Bot desktop client",
            "keychain_probe": {"status": "NOT_IMPLEMENTED", "has_remediation": True},
        },
    },
)


def _platform_fixtures(out: pathlib.Path) -> int:
    """Plant every foreign environment `gb platform --selftest` resolves. Returns the count."""
    root = out / "platform"
    if root.exists():
        shutil.rmtree(root)
    for spec in PLATFORM_CASES:
        case = root / str(spec["case"])
        for rel in spec.get("plant") or []:
            planted = case / str(rel)
            planted.mkdir(parents=True, exist_ok=True)
            # Only a directory that holds the client's own bootstrap file looks like a support
            # directory rather than an empty folder someone happened to create.
            is_support = str(rel).endswith(("Grok Bot", "GrokBotData"))
            if is_support:
                atomic_write_text(
                    planted / "sand-statsig-bootstrap.json",
                    json.dumps(BOOTSTRAP, indent=1) + "\n",
                )
            # EVERY planted directory gets a file, including the ones that stand for "this
            # exists but the client's folder inside it does not". Git does not track empty
            # directories and neither does the export, so a plant with no file in it would
            # silently stop existing in the published tree — and the fixture would keep
            # passing there for the wrong reason.
            atomic_write_text(
                planted / "README.md",
                f"# planted: `{rel}`\n\n"
                + (
                    "Stands in for the Grok Bot desktop client's userData directory.\n"
                    if is_support
                    else "Stands in for the directory the client's userData would live UNDER; the\n"
                    "client's own folder is deliberately absent here.\n"
                )
                + "\nGenerated by `bin/gb-make-fixtures.py`. Do not hand-edit.\n",
            )
        atomic_write_text(
            case / "expect.json",
            json.dumps({k: v for k, v in spec.items() if k != "case"}, indent=1) + "\n",
        )
        atomic_write_text(
            case / "README.md",
            f"# platform fixture: {spec['case']}\n\n{spec['why']}\n\n"
            f"Generated by `bin/gb-make-fixtures.py`. Do not hand-edit. Resolved by\n"
            f"`gb platform --selftest`, which reads `expect.json` and compares it with what\n"
            f"`gblib.platform_support` answers for this planted environment. Paths in\n"
            f"`expect.json` are relative to this directory.\n",
        )
    return len(PLATFORM_CASES)


def _phantom_schema(root: pathlib.Path) -> None:
    """The known-bad corpus for g27: two mined bundles, only the older one reviewed.

    GOOD surface, reviewed 0.47.0, unreviewed 0.48.0 whose method delta is one of
    each kind: +CreateBot added, -ListBots removed, ~GetBot changed by requiredness
    alone (same input/output/kind; field `verbose` flipped optional -> required).
    Nothing else in the root differs from known-good, so this fixture must trip
    ONLY g27 — the exclusivity the mutation run (`--disable g27-surface-drift`)
    proves. Method records carry the amended `fields` shape ({name, req}).
    """
    v47 = {
        "GrokBot": [
            {
                "name": "GetBot",
                "input": "GetBotRequest",
                "output": "GetBotResponse",
                "kind": "unary",
                "fields": [
                    {"name": "id", "req": True},
                    {"name": "verbose", "req": False},
                ],
            },
            {
                "name": "ListBots",
                "input": "ListBotsRequest",
                "output": "ListBotsResponse",
                "kind": "unary",
                "fields": [{"name": "page_size", "req": False}],
            },
        ]
    }
    v48 = {
        "GrokBot": [
            {
                "name": "GetBot",
                "input": "GetBotRequest",
                "output": "GetBotResponse",
                "kind": "unary",
                "fields": [
                    {"name": "id", "req": True},
                    {"name": "verbose", "req": True},
                ],
            },
            {
                "name": "CreateBot",
                "input": "CreateBotRequest",
                "output": "CreateBotResponse",
                "kind": "unary",
                "fields": [{"name": "name", "req": True}],
            },
        ]
    }
    messages = {
        "GetBotRequest": [
            {"no": 1, "name": "id", "kind": "string"},
            {"no": 2, "name": "verbose", "kind": "bool"},
        ],
        "GetBotResponse": [{"no": 1, "name": "bot", "kind": "message"}],
    }
    enums = {"BotKind": [{"name": "BOX", "no": 0}]}
    for ver, services in (("0.47.0", v47), ("0.48.0", v48)):
        bundle = root / "schema" / ver
        bundle.mkdir(parents=True)
        blob = json.dumps(services, sort_keys=True).encode()
        manifest = {
            "schema": "gb-schema/1",
            "app_version": ver,
            "bundle_sha256": hashlib.sha256(blob).hexdigest(),
            "mined_at": "2026-09-11T06:00:00+00:00",
            "counts": {
                "services": len(services),
                "methods": sum(len(recs) for recs in services.values()),
                "messages": len(messages),
                "enums": len(enums),
            },
        }
        atomic_write_text(
            (bundle / "manifest.json"), json.dumps(manifest, indent=1) + "\n"
        )
        atomic_write_text(
            (bundle / "services.json"), json.dumps(services, indent=1) + "\n"
        )
        atomic_write_text(
            (bundle / "messages.json"), json.dumps(messages, indent=1) + "\n"
        )
        atomic_write_text((bundle / "enums.json"), json.dumps(enums, indent=1) + "\n")
    registry = {
        "methods": {
            "GetBot": {
                "first_seen": "0.47.0",
                "last_seen": "0.48.0",
                "services": ["GrokBot"],
            },
            "ListBots": {
                "first_seen": "0.47.0",
                "last_seen": "0.47.0",
                "services": ["GrokBot"],
            },
            "CreateBot": {
                "first_seen": "0.48.0",
                "last_seen": "0.48.0",
                "services": ["GrokBot"],
            },
        },
        "messages": {
            "GetBotRequest": {"first_seen": "0.47.0", "last_seen": "0.48.0"},
            "GetBotResponse": {"first_seen": "0.47.0", "last_seen": "0.48.0"},
        },
    }
    atomic_write_text(
        (root / "schema" / "registry.json"), json.dumps(registry, indent=1) + "\n"
    )
    atomic_write_text(
        (root / "schema" / "reviewed.json"),
        json.dumps(
            {
                "version": "0.47.0",
                "reviewed_at": "2026-09-11",
                "note": "fixture: reviewed 0.47.0",
            },
            indent=1,
        )
        + "\n",
    )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--out", default=str(pathlib.Path(__file__).resolve().parents[1] / "fixtures")
    )
    out = pathlib.Path(ap.parse_args().out)
    out.mkdir(parents=True, exist_ok=True)

    with_ids(GOOD_AUDIT)
    man, files = good_surface("2026-09-11")
    prev = copy.deepcopy(GOOD_AUDIT)
    prev["captured_at"] = "2026-09-04T06:00:00+00:00"

    # GREEN
    write_root(out, "known-good", "2026-09-11", man, files, GOOD_AUDIT, prev)

    # ERROR: the index listed pages but the fetcher produced none. Isolated to g1 —
    # the listing count stays healthy so the canary (g2) cannot mask which check fired.
    empty = copy.deepcopy(man)
    empty["pages"] = []
    write_root(out, "bad-empty-surface", "2026-09-11", empty, {}, GOOD_AUDIT, prev)

    # ERROR: the index collapsed (doc restructure or a broken parse)
    small_man, small_files = good_surface("2026-09-11", n_pages=2, total=3)
    write_root(
        out,
        "bad-canary-collapse",
        "2026-09-11",
        small_man,
        small_files,
        GOOD_AUDIT,
        prev,
    )

    # RED: a page came back as a stub; hashing it would freeze garbage as "the surface"
    trunc_man, trunc_files = copy.deepcopy(man), dict(files)
    stub = "404\n"
    trunc_man["pages"][3].update(
        {
            "bytes": len(stub.encode()),
            "sha256": hashlib.sha256(stub.encode()).hexdigest(),
            "marker_ok": False,
        }
    )
    trunc_files[trunc_man["pages"][3]["slug"]] = stub
    write_root(
        out,
        "bad-truncated-page",
        "2026-09-11",
        trunc_man,
        trunc_files,
        GOOD_AUDIT,
        prev,
    )

    # RED: the weekly loop stopped running
    stale_man, stale_files = good_surface("2026-08-01")
    write_root(
        out,
        "bad-stale-snapshot",
        "2026-08-01",
        stale_man,
        stale_files,
        GOOD_AUDIT,
        prev,
    )

    # RED: the vendor shipped a client we never restarted into
    behind = copy.deepcopy(GOOD_AUDIT)
    behind["app"]["version"] = "0.24.0"
    behind["session_marker"]["appVersion"] = "0.24.0"
    behind["account"]["app_version_seen_by_backend"] = "0.24.0"
    behind["update_marker"].update(
        {"phase": "staged", "targetVersion": "0.47.0", "fromVersion": "0.24.0"}
    )
    write_root(out, "bad-update-staged", "2026-09-11", man, files, behind, prev)

    # RED: a Bot holds shared-computer access with no standing boundary
    # No server roster in this one on purpose: it is the pre-durable case, where the local audit
    # is the only list there is and g5 must still catch an uncharted Bot.
    naked = copy.deepcopy(GOOD_AUDIT)
    naked["bots"].append(
        {
            "id": "b4",
            "name": "CFS",
            "is_group": True,
            "description_chars": 0,
            "idle_days": 3.0,
            "notifications_enabled": True,
            "unread_count": 0,
            "hidden": False,
            "member_ids": ["b1", "b2", "b3"],
        }
    )
    write_root(
        out,
        "bad-empty-charter",
        "2026-09-11",
        man,
        files,
        naked,
        prev,
        inventories={
            "2026-09-11T0700.studio": inventory(
                "studio", "fixture.local", server_bots=[]
            ),
            "2026-09-11T0700.brain": inventory(
                "brain", "fixture-brain.local", server_bots=[]
            ),
        },
    )

    # RED: capabilities moved and nobody wrote it down
    moved = copy.deepcopy(GOOD_AUDIT)
    moved["gates"]["enabled_names"] = sorted(
        moved["gates"]["enabled_names"] + ["sand_memory_dreaming"]
    )
    with_ids(moved)
    write_root(
        out, "bad-capability-delta-unreviewed", "2026-09-11", man, files, moved, prev
    )

    # ERROR: the baseline audit predates enabled_ids, so no exact delta is computable. A
    # name-basis diff would invent movement that is really decode-coverage drift.
    legacy = copy.deepcopy(prev)
    legacy["gates"].pop("enabled_ids", None)
    write_root(
        out, "bad-baseline-without-ids", "2026-09-11", man, files, GOOD_AUDIT, legacy
    )

    # GREEN: the same delta, recorded. Proves the gate is not attack-only — a reviewed capability
    # change passes, so nobody is tempted to route around it.
    reviewed = (
        "# findings 2026-09-11\n\nreviewed-audit: 2026-09-11T0600.json\n\n"
        "- gained `sand_memory_dreaming` — evaluate on one low-risk Bot before fleet-wide use.\n"
    )
    write_root(
        out,
        "known-good-delta-reviewed",
        "2026-09-11",
        man,
        files,
        moved,
        prev,
        findings=reviewed,
    )

    # RED: the second Mac is running a different client, so its per-desktop approval policy and
    # Auto-review rules are governed by different code than the host of record.
    write_root(
        out,
        "bad-desktop-version-drift",
        "2026-09-11",
        man,
        files,
        GOOD_AUDIT,
        prev,
        desktops={"2026-09-11T0605.brain": desktop_audit("0.24.0")},
    )

    # RED: a registered desktop nobody ever measured. Absence must not read as parity.
    write_root(
        out,
        "bad-desktop-audit-missing",
        "2026-09-11",
        man,
        files,
        GOOD_AUDIT,
        prev,
        desktops={},
    )

    # RED: a desktop's hand record has gone stale — the settings it describes may no longer hold.
    write_root(
        out,
        "bad-inventory-stale",
        "2026-09-11",
        man,
        files,
        GOOD_AUDIT,
        prev,
        inventories={
            "2026-09-11T0700.studio": inventory("studio", "fixture.local"),
            "2026-07-01T0700.brain": inventory("brain", "fixture-brain.local"),
        },
    )

    # RED: recorded, but the local-execution policy was never actually looked at.
    write_root(
        out,
        "bad-no-local-exec-policy",
        "2026-09-11",
        man,
        files,
        GOOD_AUDIT,
        prev,
        inventories={
            "2026-09-11T0700.studio": inventory(
                "studio", "fixture.local", drop_policy=True
            ),
            "2026-09-11T0700.brain": inventory("brain", "fixture-brain.local"),
        },
    )

    # RED: an enabled routine whose every recorded run failed — a dead loop that still reads green.
    # Routines are account-level, so a failing one appears in BOTH device records — which is also
    # what makes the gate's dedupe correct. The fixture has to say the same thing twice.
    write_root(
        out,
        "bad-routine-failing",
        "2026-09-11",
        man,
        files,
        GOOD_AUDIT,
        prev,
        inventories={
            "2026-09-11T0700.studio": inventory(
                "studio", "fixture.local", routines=("failed", "failed", "failed")
            ),
            "2026-09-11T0700.brain": inventory(
                "brain", "fixture-brain.local", routines=("failed", "failed", "failed")
            ),
        },
    )

    # GREEN: the same paused routine, with a recorded and unexpired decision. Proves g9 is not
    # over-strict — a deliberate pause must not nag forever.
    write_root(
        out,
        "known-good-pause-acked",
        "2026-09-11",
        man,
        files,
        GOOD_AUDIT,
        prev,
        pauses=ACK_FRESH,
        inventories={
            "2026-09-11T0700.studio": inventory(
                "studio", "fixture.local", enabled=False
            ),
            "2026-09-11T0700.brain": inventory(
                "brain", "fixture-brain.local", enabled=False
            ),
        },
    )

    # RED: the decision expired and nobody re-made it. An exception that never expires is silence.
    write_root(
        out,
        "bad-pause-ack-expired",
        "2026-09-11",
        man,
        files,
        GOOD_AUDIT,
        prev,
        pauses=ACK_EXPIRED,
        inventories={
            "2026-09-11T0700.studio": inventory(
                "studio", "fixture.local", enabled=False
            ),
            "2026-09-11T0700.brain": inventory(
                "brain", "fixture-brain.local", enabled=False
            ),
        },
    )

    # RED: the SERVER roster carries an uncharted Bot while the local audit looks clean. Proves
    # g5 grades the authoritative list, not the desktop cache (the stray "New Bot" case).
    naked_server = [
        {
            "name": "Chief of Staff",
            "numeric_id": "1001",
            "harness": "box",
            "description_chars": 1200,
        },
        {
            "name": "New Bot",
            "numeric_id": "1002",
            "harness": "box",
            "description_chars": 0,
        },
    ]
    write_root(
        out,
        "bad-server-bot-uncharted",
        "2026-09-11",
        man,
        files,
        GOOD_AUDIT,
        prev,
        inventories={
            "2026-09-11T0700.studio": inventory(
                "studio", "fixture.local", server_bots=naked_server
            ),
            "2026-09-11T0700.brain": inventory(
                "brain", "fixture-brain.local", server_bots=naked_server
            ),
        },
    )

    # RED: the settings were recorded once and never re-confirmed. A fresh pull carries the old
    # answer forward, so the policy has to age on its own date or it silently becomes a claim.
    stale_pol = inventory("studio", "fixture.local")
    stale_pol["policy_recorded_at"] = "2026-06-01"
    write_root(
        out,
        "bad-policy-stale",
        "2026-09-11",
        man,
        files,
        GOOD_AUDIT,
        prev,
        inventories={
            "2026-09-11T0700.studio": stale_pol,
            "2026-09-11T0700.brain": inventory("brain", "fixture-brain.local"),
        },
    )

    # RED: a routine the Bot created for itself, enabled, never run, never reviewed.
    selfmade = {
        "schema": "gb-inventory/1",
        "device": "studio",
        "hostname": "fixture.local",
        "recorded_at": "2026-09-11T07:00:00+00:00",
        "app_version": "0.47.0",
        "local_exec_policy": "never",
        "policy_recorded_at": "2026-09-05",
        "usage": copy.deepcopy(USAGE_OK),
        "auto_review_rules": [],
        "plugins": [],
        "mcp_servers": [],
        "server_bots": [
            {
                "name": "Chief of Staff",
                "numeric_id": "1001",
                "harness": "box",
                "description_chars": 1200,
            }
        ],
        "bots": [
            {
                "name": "Chief of Staff",
                "skills": [],
                "routines": [
                    {
                        "name": "self-made digest",
                        "schedule": "daily 08:00",
                        "enabled": True,
                        "recent_runs": [],
                        "provenance": "untrusted",
                    }
                ],
            }
        ],
    }
    brain_copy = copy.deepcopy(selfmade)
    brain_copy["device"] = "brain"
    brain_copy["hostname"] = "fixture-brain.local"
    write_root(
        out,
        "bad-routine-self-created",
        "2026-09-11",
        man,
        files,
        GOOD_AUDIT,
        prev,
        inventories={
            "2026-09-11T0700.studio": selfmade,
            "2026-09-11T0700.brain": brain_copy,
        },
    )

    # GREEN: the same self-created routine, reviewed and accepted, unexpired. The anti-over-strict
    # half of the self-created check — a Bot writing its own routine must be reviewable, not banned.
    reviewed_ack = {
        "schema": "gb-pauses/1",
        "acknowledged": [],
        "self_created_reviewed": [
            {
                "bot": "Chief of Staff",
                "routine": "self-made digest",
                "reason": "fixture: reviewed and kept",
                "acked_by": "fixture",
                "acked_at": "2026-09-01",
                "review_by": "2026-12-01",
            }
        ],
    }
    sm2 = copy.deepcopy(selfmade)
    b2 = copy.deepcopy(sm2)
    b2["device"] = "brain"
    b2["hostname"] = "fixture-brain.local"
    write_root(
        out,
        "known-good-selfmade-reviewed",
        "2026-09-11",
        man,
        files,
        GOOD_AUDIT,
        prev,
        pauses=reviewed_ack,
        inventories={"2026-09-11T0700.studio": sm2, "2026-09-11T0700.brain": b2},
    )

    # RED: the loop runs, but only because a human types the command.
    write_root(
        out,
        "bad-schedule-manual-only",
        "2026-09-11",
        man,
        files,
        GOOD_AUDIT,
        prev,
        runs=[
            {
                "at": "2026-09-09T09:00:00Z",
                "verdict": "GREEN",
                "exit": 0,
                "source": "manual",
            },
            {
                "at": "2026-09-11T09:00:00Z",
                "verdict": "GREEN",
                "exit": 0,
                "source": "manual",
            },
        ],
    )

    # RED: a scheduler was never registered, whatever the run log says.
    write_root(
        out,
        "bad-schedule-unregistered",
        "2026-09-11",
        man,
        files,
        GOOD_AUDIT,
        prev,
        agent=False,
    )

    # RED: registered and firing once, then silence past the gap ceiling.
    write_root(
        out,
        "bad-schedule-stale",
        "2026-09-11",
        man,
        files,
        GOOD_AUDIT,
        prev,
        runs=[
            {
                "at": "2026-07-01T09:15:00Z",
                "verdict": "GREEN",
                "exit": 0,
                "source": "launchd",
            }
        ],
    )

    # ERROR: nothing recorded at all. Unmeasured, not healthy.
    write_root(
        out,
        "bad-no-inventory",
        "2026-09-11",
        man,
        files,
        GOOD_AUDIT,
        prev,
        inventories={},
    )

    # g11 — the parameter surface. RED when a value digest moves and nobody read the new value;
    # ERROR when the baseline predates digest capture, because unmeasured is not unchanged.
    retuned = copy.deepcopy(GOOD_AUDIT)
    retuned["tunables"]["digests"]["h1"] = "dddddddddddd"
    write_root(
        out, "bad-tunable-delta-unreviewed", "2026-09-11", man, files, retuned, prev
    )

    blind = copy.deepcopy(GOOD_AUDIT)
    blind_prev = copy.deepcopy(prev)
    blind_prev.pop("tunables", None)
    write_root(
        out, "bad-tunable-baseline-blind", "2026-09-11", man, files, blind, blind_prev
    )

    unmeasured = copy.deepcopy(GOOD_AUDIT)
    unmeasured.pop("tunables", None)
    write_root(out, "bad-tunables-missing", "2026-09-11", man, files, unmeasured, prev)

    # g12 — money. RED when on-demand billing is on and the period is on pace to
    # cross the included allowance; ERROR when no inventory carries a usage block,
    # because unmeasured spend is not bounded spend.
    # g12 REFRAMED 2026-09-11: the check no longer asks "is there a decision file",
    # it asks "is this period on pace to cross the included allowance while
    # on-demand is on". So the known-bad must plant a real overspend PACE, not a
    # missing file. 45% used at 40% of the window elapsed projects to 112.5% —
    # over the allowance, with on-demand enabled, which is the only state that
    # actually bills. A flat raw-45% reading would call this fine, which is
    # exactly the failure the projection exists to catch.
    # The window is PINNED to the fixture date (not wall-clock-relative): the
    # gate reads elapsed against its --now date, so a relative window drifts RED
    # into GREEN within hours of generation. 2026-09-09T00:00Z..2026-09-14T00:00Z
    # is 40% elapsed at the 2026-09-11 fixture date, deterministically, forever.
    spend_on = copy.deepcopy(USAGE_OK)
    spend_on["on_demand_enabled"] = True
    spend_on["usage_percent"] = 45.0
    spend_on["period_start"] = "2026-09-09T00:00:00Z"
    spend_on["next_reset"] = "2026-09-14T00:00:00Z"
    write_root(
        out,
        "bad-ondemand-unbounded",
        "2026-09-11",
        man,
        files,
        GOOD_AUDIT,
        prev,
        inventories={
            "2026-09-11T0700.studio": inventory(
                "studio", "fixture.local", usage=spend_on
            ),
            "2026-09-11T0700.brain": inventory(
                "brain", "fixture-brain.local", usage=spend_on
            ),
        },
    )
    # g25-routine-liveness: enabled, past its own next_run_at_ms, and ZERO runs ever.
    # 1788998400000 = 2026-09-10T00:00:00Z, computed not guessed — the first attempt used
    # 1789171200000 and called it "one day before", but it is 2026-09-12, two days AFTER the
    # fixture date, so the fixture came out GREEN and the check still had no proof. A FIXED
    # instant keeps this past-due forever instead of drifting the way a relative window would.
    # What makes this distinct from g9-routine-health: nothing here has FAILED. The runs list
    # is empty, not failing — the case g9 branches past, since it tests "runs is None" and
    # "every run failed" and an empty list is neither.
    dead_routine = dict(routines=(), next_run_ms=1788998400000)
    write_root(
        out,
        "bad-routine-never-fired",
        "2026-09-11",
        man,
        files,
        GOOD_AUDIT,
        prev,
        inventories={
            "2026-09-11T0700.studio": inventory(
                "studio", "fixture.local", **dead_routine
            ),
            "2026-09-11T0700.brain": inventory(
                "brain", "fixture-brain.local", **dead_routine
            ),
        },
    )
    write_root(
        out,
        "bad-usage-unmeasured",
        "2026-09-11",
        man,
        files,
        GOOD_AUDIT,
        prev,
        inventories={
            "2026-09-11T0700.studio": inventory("studio", "fixture.local", usage=None),
            "2026-09-11T0700.brain": inventory(
                "brain", "fixture-brain.local", usage=None
            ),
        },
    )

    # g13 — the practitioner half of the brief. RED when the curated index moved and nobody read
    # it, and when it was never reviewed at all. Attention is the thing being gated, not content.
    write_root(
        out,
        "bad-practitioner-index-moved",
        "2026-09-11",
        man,
        files,
        GOOD_AUDIT,
        prev,
        practitioner={
            "source": "awesome-grok-bot",
            "sha256": hashlib.sha256(b"older").hexdigest(),
            "reviewed_at": "2026-08-20",
            "note": "fixture: stale review",
        },
    )
    write_root(
        out,
        "bad-practitioner-never-reviewed",
        "2026-09-11",
        man,
        files,
        GOOD_AUDIT,
        prev,
        practitioner=False,
    )

    # g14 — the instrument watching itself. RED when measured coverage falls below the recorded
    # floor (a read that silently started returning null looks exactly like a clean board);
    # ERROR when no floor is recorded at all.
    write_root(
        out,
        "bad-coverage-regressed",
        "2026-09-11",
        man,
        files,
        GOOD_AUDIT,
        prev,
        floor={
            "covered": 41,
            "of": 46,
            "recorded_at": "2026-09-11",
            "why": "fixture: floor above what the artifacts carry. Bumped 38 -> 41 when the "
            "typed-spine facets landed: the fixture gained real coverage, so the old "
            "planted floor no longer sat above it and the RED leg stopped firing.",
        },
    )
    write_root(
        out,
        "bad-coverage-floor-missing",
        "2026-09-11",
        man,
        files,
        GOOD_AUDIT,
        prev,
        floor=False,
    )

    # g15 — the market. RED when an INSTALLED plugin's pinned ref moved and nobody read the diff
    # (new third-party code inside an already-approved Bot), and when the catalog moved unreviewed.
    write_root(
        out,
        "bad-installed-plugin-moved",
        "2026-09-11",
        man,
        files,
        GOOD_AUDIT,
        prev,
        market=(
            MARKET_PREV,
            market_doc({"vercel": "NEWREF", "supabase": "def456"}, "NEWREF"),
        ),
        market_reviewed={
            "snapshot": "2026-09-04T0600.json",
            "reviewed_at": "2026-09-04",
            "note": "fixture: stale review",
        },
    )
    write_root(
        out,
        "bad-market-unreviewed",
        "2026-09-11",
        man,
        files,
        GOOD_AUDIT,
        prev,
        market=(
            MARKET_PREV,
            market_doc({"vercel": "abc123", "supabase": "def456", "newplug": "z9"}),
        ),
        market_reviewed={
            "snapshot": "2026-09-04T0600.json",
            "reviewed_at": "2026-09-04",
            "note": "fixture: stale review",
        },
    )

    # g16 — a vendor doc page changed and nobody read it. The repo stored a sha256 per page for
    # five ticks and never once compared it to last week's.
    old_man, old_files = good_surface("2026-09-04")
    moved_man, moved_files = good_surface("2026-09-11")
    moved_files["grok-bot__fixture-01"] = (
        moved_files["grok-bot__fixture-01"] + "\n\nvendor edit\n"
    )
    moved_man["pages"][0]["sha256"] = hashlib.sha256(
        moved_files["grok-bot__fixture-01"].encode()
    ).hexdigest()
    moved_man["pages"][0]["bytes"] = len(moved_files["grok-bot__fixture-01"].encode())
    write_root(
        out,
        "bad-doc-changed-unreviewed",
        "2026-09-11",
        moved_man,
        moved_files,
        GOOD_AUDIT,
        prev,
        prev_surface=(old_man, old_files),
        surface_reviewed={
            "snapshot": "2026-09-04",
            "reviewed_at": "2026-09-04",
            "note": "fixture: stale review",
        },
    )

    # RED: the scheduler fired once, long ago, and a human has been running the tick by hand
    # since. Under the old "one of the last 3 runs" rule this PASSED as long as someone kept
    # running it manually — the loop looked alive because a person was holding it up.
    write_root(
        out,
        "bad-scheduler-gone-quiet",
        "2026-09-11",
        man,
        files,
        GOOD_AUDIT,
        prev,
        runs=[
            {
                "at": "2026-08-01T09:15:00Z",
                "verdict": "GREEN",
                "exit": 0,
                "source": "launchd",
            },
            {
                "at": "2026-09-10T11:00:00Z",
                "verdict": "GREEN",
                "exit": 0,
                "source": "manual",
            },
            {
                "at": "2026-09-11T11:02:00Z",
                "verdict": "GREEN",
                "exit": 0,
                "source": "manual",
            },
        ],
    )

    # g17 — discovery. RED when a notable NEW repo was found and never triaged; ERROR when a
    # query failed or every query came back empty, because a broken search reads like a quiet week.
    write_root(
        out,
        "bad-discovery-untriaged",
        "2026-09-11",
        man,
        files,
        GOOD_AUDIT,
        prev,
        discovery_seen={
            "schema": "gb-discovery-seen/1",
            "reviewed_at": "2026-09-04",
            "dismissed": {},
            "repos": [],
        },
    )
    broken = dict(DISCOVERY_OK, errors=[{"query": "topic", "error": "HTTP 422"}])
    write_root(
        out,
        "bad-discovery-query-failed",
        "2026-09-11",
        man,
        files,
        GOOD_AUDIT,
        prev,
        discovery=broken,
    )
    empty = dict(DISCOVERY_OK, candidates_total=0, new=[], notable=[])
    write_root(
        out,
        "bad-discovery-empty",
        "2026-09-11",
        man,
        files,
        GOOD_AUDIT,
        prev,
        discovery=empty,
    )

    # g18 — MCP. RED when a server stops answering, and RED when it answers with FEWER tools
    # than last week, which is the quieter failure nothing else would surface.
    write_root(
        out,
        "bad-mcp-unreachable",
        "2026-09-11",
        man,
        files,
        GOOD_AUDIT,
        prev,
        mcp=(MCP_OK, mcp_doc(0, ok=False)),
    )
    write_root(
        out,
        "bad-mcp-tools-shrank",
        "2026-09-11",
        man,
        files,
        GOOD_AUDIT,
        prev,
        mcp=(MCP_OK, mcp_doc(2)),
    )
    # RED: we HELD a credential and the server rejected it — a dead secret, not a locked door.
    # Measured on the real fleet the day this was written (Infisical's agent-mail token no longer
    # matched the running server), which is the whole reason the state exists.
    write_root(
        out,
        "bad-mcp-credential-rejected",
        "2026-09-11",
        man,
        files,
        GOOD_AUDIT,
        prev,
        mcp=(MCP_OK, mcp_doc(0, ok=False, state="credential-rejected")),
    )

    # g19 — a Bot that can act on your behalf and has no job. RED past the grace window, and
    # deliberately SILENT inside it: a check that fires the day you create a teammate teaches
    # you to create fewer teammates.
    write_root(
        out,
        "bad-idle-credentialed-bot",
        "2026-09-11",
        man,
        files,
        GOOD_AUDIT,
        prev,
        utilization=util_doc(45.0, routines=0, entries=1),
    )
    write_root(
        out,
        "known-good-new-bot-in-grace",
        "2026-09-11",
        man,
        files,
        GOOD_AUDIT,
        prev,
        utilization=util_doc(2.0, routines=0, entries=1),
    )

    # g20 — the archive. RED when it is stale, and RED HARDER when a replica is at the client
    # cap while the archive is stale: past the cap the archive is the only copy, so every new
    # turn destroys history permanently.
    write_root(
        out,
        "bad-archive-stale",
        "2026-09-11",
        man,
        files,
        GOOD_AUDIT,
        prev,
        archive=("2026-08-01T0600", archive_doc()),
    )
    write_root(
        out,
        "bad-archive-stale-at-cap",
        "2026-09-11",
        man,
        files,
        GOOD_AUDIT,
        prev,
        archive=("2026-08-01T0600", archive_doc(at_cap=3)),
    )

    # g21 — the population corpus. RED when it moved unreviewed; ERROR when the fetch came back
    # empty, because an empty ecosystem is a broken request, not news.
    write_root(
        out,
        "bad-usecases-unreviewed",
        "2026-09-11",
        man,
        files,
        GOOD_AUDIT,
        prev,
        usecases=(
            USECASES_PREV,
            usecases_doc(bots=5, integrations=(("Gmail", 3), ("Notion", 2))),
        ),
        usecases_reviewed={
            "snapshot": "2026-09-04T0600.json",
            "reviewed_at": "2026-09-04",
        },
    )
    write_root(
        out,
        "bad-usecases-empty",
        "2026-09-11",
        man,
        files,
        GOOD_AUDIT,
        prev,
        usecases=(USECASES_PREV, usecases_doc(bots=0, integrations=())),
    )

    # g27 — the shipped-client surface. GOOD in every old sense; RED only because the
    # newest mined bundle (0.48.0) is unreviewed and moves methods (+CreateBot,
    # -ListBots, ~GetBot by requiredness). The schema tree is planted after the root
    # is written, so no other fixture gains a bundle and no other verdict moves.
    write_root(out, "bad-surface-phantom", "2026-09-11", man, files, GOOD_AUDIT, prev)
    _phantom_schema(out / "bad-surface-phantom")

    # g28 — grokbot.dev JSON producer. ERROR when never collected; RED when stale.
    # Isolated: default series plants a fresh artifact, these two omit/age only that dir.
    write_root(
        out,
        "bad-grokbotdev-missing",
        "2026-09-11",
        man,
        files,
        GOOD_AUDIT,
        prev,
        grokbotdev=False,
    )
    write_root(
        out,
        "bad-grokbotdev-stale",
        "2026-09-11",
        man,
        files,
        GOOD_AUDIT,
        prev,
        grokbotdev=("2026-08-01T0600", GROKBOTDEV_OK),
    )

    _cli_contract_fixtures(out, man, files, prev)
    _dogfood_fixtures(out, man, files, prev)
    _typed_spine_fixtures(out, man, files, prev)
    planted = _platform_fixtures(out)

    # `platform/` is counted apart because it is not a gate root: the gate's selftest iterates
    # its own FIXTURE_EXPECT table, and folding these into the root count would make that table
    # look short by six for the rest of time.
    roots = [p.name for p in out.iterdir() if p.is_dir() and p.name != "platform"]
    print(
        f"fixtures written to {out}: {len(roots)} gate roots, "
        f"{planted} planted platforms under platform/"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
