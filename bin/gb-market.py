#!/usr/bin/env python3
"""gb-market — official listings ∪ public corpus ∪ curated URLs.

Scan, not a 101 and not official-listings-only. A clone refreshes both
stamps: `gb market refresh` (market/) and `gb market refresh --corpus`
(usecases/, the public botdirectory tarball). Dedup by casefold name.
Pass share_id through only when the official row already has one.
Does not read templates/. Does not invent share ids.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import subprocess
import sys
import tempfile
from collections import Counter
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple
from urllib.parse import urlparse

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from gblib import dated_children  # noqa: E402
from gbtypes import main as gbmain  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[1]
BIN = ROOT / "bin"
SCHEMA = "gb-market-bots/1"
JOBS_SCHEMA = "gb-market-jobs/3"
PREVIEW_ROWS = 20
EXIT_OK, EXIT_USAGE, EXIT_ENVIRONMENT = 0, 2, 3
SOURCES = ("official", "corpus", "both")


def emit(text: str) -> None:
    sys.stdout.write(text if text.endswith("\n") else text + "\n")


def warn(text: str) -> None:
    sys.stderr.write(text if text.endswith("\n") else text + "\n")


def dumps(payload: dict) -> str:
    return json.dumps(payload, indent=1, sort_keys=True)


def load_doc(path: Optional[pathlib.Path]) -> Optional[dict]:
    if path is None or not path.is_file():
        return None
    try:
        doc = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    return doc if isinstance(doc, dict) else None


def stamp_pair(root: pathlib.Path, folder: str) -> Tuple[
    Optional[pathlib.Path], Optional[dict], Optional[pathlib.Path], Optional[dict]
]:
    rows = dated_children(root / folder, ".json")
    if not rows:
        return None, None, None, None
    newest = rows[-1]
    prev = rows[-2] if len(rows) > 1 else None
    return newest, load_doc(newest), prev, load_doc(prev) if prev else None


def name_key(value: Any) -> str:
    return " ".join(str(value or "").casefold().split())


def official_rows(doc: Optional[dict]) -> List[dict]:
    if not isinstance(doc, dict):
        return []
    shelf = doc.get("bot_marketplace") if isinstance(doc.get("bot_marketplace"), dict) else {}
    rows = shelf.get("rows") or []
    return [r for r in rows if isinstance(r, dict) and name_key(r.get("name"))]


def corpus_rows(doc: Optional[dict]) -> List[dict]:
    if not isinstance(doc, dict):
        return []
    rows = doc.get("rows") or []
    return [r for r in rows if isinstance(r, dict) and name_key(r.get("name"))]


def corpus_links(doc: Optional[dict]) -> List[dict]:
    if not isinstance(doc, dict):
        return []
    rows = doc.get("links") or []
    return [r for r in rows if isinstance(r, dict)]


def _market_db():
    import importlib.util

    path = pathlib.Path(__file__).resolve().parent / "gb-market-db.py"
    spec = importlib.util.spec_from_file_location("gb_market_db", path)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


def _market_bandit():
    import importlib.util

    path = pathlib.Path(__file__).resolve().parent / "gb-market-bandit.py"
    spec = importlib.util.spec_from_file_location("gb_market_bandit", path)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


def cache_corpus(root: pathlib.Path) -> Tuple[Optional[List[dict]], Optional[List[dict]]]:
    """Valid sqlite cache wins over the JSON stamp. Refuse is not a silent fallback."""
    path = root / "usecases" / "market.sqlite"
    if not path.is_file():
        return None, None
    mdb = _market_db()
    err = mdb.refuse_path(path)
    if err:
        return None, None
    return mdb.load_bots(path), mdb.load_links(path)


def official_share_id(row: Optional[dict]) -> Optional[str]:
    if not row:
        return None
    sid = row.get("share_id") or row.get("shareId")
    if sid in (None, ""):
        return None
    return str(sid)


def host_of(url: str) -> str:
    host = (urlparse(url).netloc or "").casefold()
    if host.startswith("www."):
        host = host[4:]
    return host


def first_by_name(rows: Sequence[dict]) -> Dict[str, dict]:
    out: Dict[str, dict] = {}
    for row in sorted(rows, key=lambda r: name_key(r.get("name"))):
        key = name_key(row.get("name"))
        if key and key not in out:
            out[key] = row
    return out


def identities(official: Sequence[dict], corpus: Sequence[dict]) -> List[str]:
    keys = {name_key(r.get("name")) for r in official}
    keys |= {name_key(r.get("name")) for r in corpus}
    return sorted(k for k in keys if k)


def url_block(links: Sequence[dict], include_rows: bool) -> dict:
    sections: Counter[str] = Counter()
    hosts: Counter[str] = Counter()
    rows: List[dict] = []
    for link in links:
        url = str(link.get("url") or "")
        section = str(link.get("section") or "")
        host = host_of(url) if url else ""
        sections[section] += 1
        if host:
            hosts[host] += 1
        rows.append(
            {
                "description": link.get("description") or "",
                "host": host or None,
                "section": section,
                "title": link.get("title"),
                "url": url or None,
            }
        )
    rows.sort(
        key=lambda r: (
            str(r.get("section") or ""),
            str(r.get("host") or ""),
            str(r.get("title") or ""),
            str(r.get("url") or ""),
        )
    )
    block: Dict[str, Any] = {
        "by_host": [[k, hosts[k]] for k in sorted(hosts)],
        "by_section": [[k, sections[k]] for k in sorted(sections)],
        "total": len(links),
    }
    if include_rows:
        block["rows"] = rows
    return block


def union_bots(official: Sequence[dict], corpus: Sequence[dict]) -> List[dict]:
    off = first_by_name(official)
    cor = first_by_name(corpus)
    bots: List[dict] = []
    for key in sorted(set(off) | set(cor)):
        o = off.get(key)
        c = cor.get(key)
        if o and c:
            source = "both"
        elif o:
            source = "official"
        else:
            source = "corpus"
        name = (o or c or {}).get("name")
        bots.append(
            {
                "added_at": (c or {}).get("added_at") if c else None,
                "category": (o or {}).get("category") if o else (c or {}).get("category"),
                "contributor": (c or {}).get("contributor") if c else None,
                "creator": (o or {}).get("creator") if o else None,
                "name": name,
                "share_id": official_share_id(o) or ((c or {}).get("share_id") or None),
                "slug": (o or {}).get("slug") if o else None,
                "source": source,
                "updated_at_ms": (o or {}).get("updated_at_ms") if o else None,
                "url": ((c or {}).get("import_url") or (c or {}).get("source")) if c else None,
            }
        )
    bots.sort(key=lambda r: (name_key(r.get("name")), str(r.get("source") or "")))
    return bots


def missing_stamps(
    market_path: Optional[pathlib.Path],
    usecases_path: Optional[pathlib.Path],
    verb: str,
) -> Optional[str]:
    """Fail only when BOTH public sources are absent.

    A stranger clone has no desktop token, so official market/ never appears.
    Corpus-only (usecases/ from botdirectory) is a valid scan.
    """
    if market_path is None and usecases_path is None:
        return (
            "gb market %s: no market/<stamp>.json — run: gb market refresh; "
            "no usecases/<stamp>.json — run: gb market refresh --corpus" % verb
        )
    return None


def build_payload(
    root: pathlib.Path,
    include_urls: bool,
) -> Tuple[Optional[dict], Optional[str]]:
    m_path, m_doc, m_prev_path, m_prev_doc = stamp_pair(root, "market")
    u_path, u_doc, u_prev_path, u_prev_doc = stamp_pair(root, "usecases")
    miss = missing_stamps(m_path, u_path, "bots")
    if miss:
        return None, miss
    official = official_rows(m_doc)
    corpus = corpus_rows(u_doc)
    cached_bots, cached_links = cache_corpus(root)
    if cached_bots is not None:
        corpus = cached_bots
    bots = union_bots(official, corpus)
    prev_ids = set(identities(official_rows(m_prev_doc), corpus_rows(u_prev_doc)))
    cur_ids = set(identities(official, corpus))
    display = {name_key(b.get("name")): b.get("name") for b in bots}
    new_names = (
        sorted((display[k] for k in (cur_ids - prev_ids) if k in display), key=name_key)
        if (m_prev_path or u_prev_path)
        else []
    )
    creators = {name_key(r.get("creator")) for r in official if name_key(r.get("creator"))}
    builders = {
        name_key(r.get("contributor")) for r in corpus if name_key(r.get("contributor"))
    }
    off_keys = {name_key(r.get("name")) for r in official}
    cor_keys = {name_key(r.get("name")) for r in corpus}
    share_n = sum(1 for r in official if official_share_id(r))
    return {
        "schema": SCHEMA,
        "bots": bots,
        "corpus": len(cor_keys),
        "corpus_builders": len(builders),
        "limit": (
            "official rows often lack share_id — one-click deploy blocked "
            "until the scan carries it"
            if share_n < len(official)
            else None
        ),
        "new": new_names,
        "official": len(off_keys),
        "official_creators": len(creators),
        "one_click": bool(official) and share_n == len(official),
        "overlap": len(off_keys & cor_keys),
        "share_id_present": share_n,
        "stamps": {
            "market": m_path.name if m_path else None,
            "previous_market": m_prev_path.name if m_prev_path else None,
            "previous_usecases": u_prev_path.name if u_prev_path else None,
            "usecases": u_path.name if u_path else None,
        },
        "union": len(bots),
        "urls": url_block(cached_links if cached_links is not None else corpus_links(u_doc), include_urls),
    }, None


def print_human(payload: dict, *, new_only: bool, full: bool = False) -> None:
    stamps = payload["stamps"]
    urls = payload["urls"]
    emit(
        "BOT MARKET  official=%d corpus=%d overlap=%d union=%d"
        % (payload["official"], payload["corpus"], payload["overlap"], payload["union"])
    )
    emit(
        "  stamps  market=%s  usecases=%s"
        % (stamps.get("market"), stamps.get("usecases"))
    )
    if payload["official"] == 0:
        emit(
            "  official  0 — no market stamp (Grok Bot desktop login). "
            "Corpus still printed."
        )
    if payload["corpus"] == 0:
        emit(
            "  corpus  0 — no usecases stamp. Run: gb market refresh --corpus"
        )
    emit(
        "  builders  official_creators=%d  corpus_builders=%d"
        % (payload["official_creators"], payload["corpus_builders"])
    )
    official_n = payload["official"]
    share_n = payload["share_id_present"]
    deployable = sum(1 for b in payload.get("bots") or [] if b.get("share_id"))
    emit(
        "  share_id  %d/%d union rows have a live x.ai/bot id"
        % (deployable, payload["union"])
    )
    if official_n:
        if share_n < official_n:
            emit(
                "  official share_id  %d/%d — one-click deploy blocked "
                "until the scan carries it" % (share_n, official_n)
            )
        else:
            emit("  official share_id  %d/%d" % (share_n, official_n))
    emit("  plugin install  HUMAN Settings → Plugins. No install RPC.")
    emit(
        "  urls  n=%d  (URL finds, not deployable Bots; --urls dumps them)"
        % urls.get("total", 0)
    )
    for section, n in urls.get("by_section") or []:
        emit("    section  %s  %d" % (section or "(none)", n))
    for host, n in urls.get("by_host") or []:
        emit("    host     %s  %d" % (host, n))
    if urls.get("rows"):
        emit("  URL DUMP")
        for row in urls["rows"]:
            emit(
                "    %s  %s  %s"
                % (row.get("section") or "-", row.get("title") or "-", row.get("url") or "-")
            )
    bots = payload["bots"]
    if new_only:
        new_keys = {name_key(n) for n in (payload.get("new") or [])}
        bots = [b for b in bots if name_key(b.get("name")) in new_keys]
        emit("NEW  n=%d  (current official+corpus not in previous stamps)" % len(bots))
    shown = bots if (new_only or full) else bots[:PREVIEW_ROWS]
    emit("%-28s %-8s %-16s %-18s %s" % ("NAME", "SOURCE", "CATEGORY", "BUILDER", "WHEN"))
    for row in shown:
        builder = row.get("creator") or row.get("contributor") or "-"
        when = row.get("added_at") or row.get("updated_at_ms") or "-"
        emit(
            "%-28s %-8s %-16s %-18s %s"
            % (
                str(row.get("name") or "-")[:28],
                str(row.get("source") or "-")[:8],
                str(row.get("category") or "-")[:16],
                str(builder)[:18],
                str(when)[:16],
            )
        )
    hidden = 0 if (new_only or full) else max(0, len(bots) - PREVIEW_ROWS)
    if hidden:
        emit(
            "… %d more. Agent path: gb market bots --json   all rows: --full"
            % hidden
        )
    else:
        emit("next  gb market new   or   gb market bots --json")


def cmd_refresh(
    corpus: bool,
    runners: Optional[Dict[str, Callable[[], int]]] = None,
    quiet: bool = False,
) -> int:
    def note(text: str) -> None:
        if not quiet:
            emit(text)

    snap = BIN / "gb-market-snapshot.py"
    if not snap.is_file():
        note("gb market refresh: missing bin/gb-market-snapshot.py")
        return EXIT_ENVIRONMENT
    def _snap() -> int:
        proc = subprocess.run(
            [sys.executable, str(snap)],
            capture_output=True,
            text=True,
        )
        if proc.returncode == 0:
            line = (proc.stdout or "").strip().splitlines()
            if line:
                note(line[-1])
            return 0
        # Never relay a traceback. Last non-stack line, or a fixed sentence.
        blob = ((proc.stderr or "") + "\n" + (proc.stdout or "")).splitlines()
        clean = [
            ln
            for ln in blob
            if ln.strip()
            and "Traceback" not in ln
            and not ln.startswith("  File ")
            and not ln.startswith("    ")
            and "Error:" not in ln
        ]
        note(
            clean[-1]
            if clean
            else "gb market refresh: official catalog skipped — needs Grok Bot desktop login"
        )
        return int(proc.returncode or 1)

    run_snap = (runners or {}).get("snapshot") or _snap
    rc = run_snap()
    snap_ok = rc == 0
    if not snap_ok:
        note("gb market refresh: official catalog skipped (exit %s)" % rc)
        if not corpus:
            return int(rc) if isinstance(rc, int) and rc != 0 else EXIT_ENVIRONMENT
    if not corpus:
        return EXIT_OK
    use = BIN / "gb-usecases.py"
    if not use.is_file():
        note("gb market refresh --corpus: missing bin/gb-usecases.py")
        return EXIT_ENVIRONMENT

    def _use() -> int:
        proc = subprocess.run(
            [sys.executable, str(use)],
            capture_output=True,
            text=True,
        )
        if proc.returncode == 0:
            line = (proc.stdout or "").strip().splitlines()
            if line:
                note(line[0])
            return 0
        err = (proc.stderr or proc.stdout or "").strip().splitlines()
        note(err[-1] if err else "gb market refresh --corpus: usecases refresh failed")
        return int(proc.returncode or 1)

    run_use = (runners or {}).get("usecases") or _use
    rc2 = run_use()
    if rc2 != 0:
        note("gb market refresh --corpus: usecases refresh failed (exit %s)" % rc2)
        return int(rc2) if isinstance(rc2, int) and rc2 != 0 else EXIT_ENVIRONMENT
    if not snap_ok:
        note("next  gb market bots   (corpus-only; official catalog needs desktop login)")
    else:
        note("next  gb market bots")
    return EXIT_OK


def cmd_bots(
    root: pathlib.Path,
    as_json: bool,
    include_urls: bool,
    full: bool = False,
    offline: bool = False,
) -> int:
    if not offline:
        # Live pull is the source of truth. Stamp is a cache, not yesterday's market.
        cmd_refresh(True, quiet=as_json)
    payload, err = build_payload(root, include_urls)
    if err or payload is None:
        emit(err or "gb market bots: missing stamps — run: gb market refresh")
        return EXIT_ENVIRONMENT
    if as_json:
        emit(dumps(payload))
        return EXIT_OK
    print_human(payload, new_only=False, full=full)
    return EXIT_OK


def print_jobs_human(payload: dict) -> None:
    winners = list(payload.get("winners") or [])
    blocked = list(payload.get("blocked") or [])
    sel = payload.get("selector") or {}
    emit(
        "JOB ARMS  n=%d  blocked=%d  method=%s  pulls are recorded outcomes, not a rank"
        % (len(winners), len(blocked), sel.get("method") or "thompson")
    )
    emit(
        "%-28s %-10s %-16s %5s %-10s %5s %s"
        % ("NAME", "JOB", "SHARE_ID", "PULLS", "ORIGIN", "CHARS", "ADDED")
    )
    rows: List[Tuple[str, dict]] = [("win", w) for w in winners] + [
        ("block", b) for b in blocked
    ]
    rows.sort(key=lambda item: str(item[1].get("job") or ""))
    for kind, row in rows:
        job = str(row.get("job") or "-")
        if kind == "block":
            emit(
                "%-28s %-10s %-16s %5s %-10s %5s %s"
                % ("-", job[:10], "BLOCKED", "-", "-", "-", "-")
            )
            continue
        emit(
            "%-28s %-10s %-16s %5s %-10s %5s %s"
            % (
                str(row.get("name") or "-")[:28],
                job[:10],
                str(row.get("share_id") or "BLOCKED")[:16],
                str(row.get("pulls") if row.get("pulls") is not None else 0),
                str(row.get("origin") or "-")[:10],
                str(row.get("prompt_chars") if row.get("prompt_chars") is not None else "-"),
                str(row.get("added_at") or "-")[:10],
            )
        )
    for row in blocked:
        emit(
            "blocked  %s  %s  %s"
            % (row.get("job"), row.get("count"), row.get("reason") or "no share_id")
        )


def cmd_jobs(root: pathlib.Path, as_json: bool, offline: bool = False) -> int:
    if not offline:
        cmd_refresh(True, quiet=as_json)
    mdb = _market_db()
    mb = _market_bandit()
    path = root / "usecases" / mdb.DB_NAME
    err = mdb.refuse_path(path)
    if err:
        emit(err.replace("gb-market-db:", "gb market jobs:", 1))
        return EXIT_ENVIRONMENT
    store = mb.store_path(root)
    bandit_err = mb.refuse_path(store)
    if bandit_err:
        emit(bandit_err.replace("gb-market-bandit:", "gb market jobs:", 1))
        return EXIT_ENVIRONMENT
    try:
        rows = mdb.load_bots(path)
        picked = mb.pick_jobs(rows, store=store)
    except mdb.CacheRefused as e:
        emit(str(e).replace("gb-market-db:", "gb market jobs:", 1))
        return EXIT_ENVIRONMENT
    except mb.StoreRefused as e:
        emit(str(e).replace("gb-market-bandit:", "gb market jobs:", 1))
        return EXIT_ENVIRONMENT
    payload = {
        "bandit_user_version": mb.USER_VERSION,
        "blocked": picked.get("blocked") or [],
        "schema": JOBS_SCHEMA,
        "selector": picked.get("selector") or {},
        "user_version": mdb.USER_VERSION,
        "winners": picked.get("winners") or [],
    }
    if any(not w.get("share_id") for w in payload["winners"]):
        emit("gb market jobs: refuse — a winner is missing share_id")
        return EXIT_ENVIRONMENT
    if as_json:
        emit(dumps(payload))
        return EXIT_OK
    print_jobs_human(payload)
    return EXIT_OK


def cmd_new(root: pathlib.Path, as_json: bool, offline: bool = False) -> int:
    if not offline:
        cmd_refresh(True, quiet=as_json)
    payload, err = build_payload(root, False)
    if err or payload is None:
        emit((err or "gb market new: missing stamps").replace("gb market bots:", "gb market new:"))
        return EXIT_ENVIRONMENT
    stamps = payload["stamps"]
    if not (stamps.get("previous_market") or stamps.get("previous_usecases")):
        emit(
            "gb market new: need a previous market/ or usecases/ stamp — "
            "run: gb market refresh --corpus"
        )
        return EXIT_ENVIRONMENT
    if as_json:
        new_keys = {name_key(n) for n in (payload.get("new") or [])}
        focused = dict(payload)
        focused["bots"] = [
            b for b in payload["bots"] if name_key(b.get("name")) in new_keys
        ]
        emit(dumps(focused))
        return EXIT_OK
    print_human(payload, new_only=True)
    return EXIT_OK


def selftest() -> int:
    legs: List[Tuple[str, bool, str]] = []

    def check(name: str, ok: bool, detail: str = "") -> None:
        legs.append((name, bool(ok), detail))

    official_prev = {
        "schema": "gb-market/1",
        "bot_marketplace": {
            "rows": [
                {
                    "slug": "alpha",
                    "name": "Alpha",
                    "category": "Ops",
                    "creator": "ada",
                    "updated_at_ms": "1789000000000",
                }
            ]
        },
    }
    official_cur = {
        "schema": "gb-market/1",
        "bot_marketplace": {
            "rows": [
                {
                    "slug": "alpha",
                    "name": "Alpha",
                    "category": "Ops",
                    "creator": "ada",
                    "updated_at_ms": "1789000000000",
                    "share_id": "real-share-1",
                },
                {
                    "slug": "shared",
                    "name": "Shared Bot",
                    "category": "Sales",
                    "creator": "bev",
                    "updated_at_ms": "1789086400000",
                },
            ]
        },
    }
    corpus_prev = {
        "schema": "gb-usecases/1",
        "rows": [
            {
                "name": "Shared Bot",
                "category": "Sales",
                "contributor": "dee",
                "source": "https://x.com/dee/status/1",
                "added_at": "2026-08-01",
            }
        ],
        "links": [],
    }
    corpus_cur = {
        "schema": "gb-usecases/1",
        "rows": [
            {
                "name": "Shared Bot",
                "category": "Sales",
                "contributor": "dee",
                "source": "https://x.com/dee/status/1",
                "added_at": "2026-08-01",
            },
            {
                "name": "Gamma",
                "category": "Research",
                "contributor": "fin",
                "source": "https://x.com/fin/status/2",
                "added_at": "2026-09-10",
                "share_id": "gammaShare",
                "import_url": "https://x.ai/bot/gammaShare",
            },
        ],
        "links": [
            {
                "title": "Guide A",
                "url": "https://x.com/a/status/1",
                "section": "X",
                "description": "a find",
            },
            {
                "title": "Repo",
                "url": "https://github.com/elie222/botdirectory.ai",
                "section": "Repos",
                "description": "public corpus",
            },
        ],
    }

    with tempfile.TemporaryDirectory(prefix="gb-market-selftest-") as tmp:
        root = pathlib.Path(tmp)
        market = root / "market"
        usecases = root / "usecases"
        templates = root / "templates"
        market.mkdir()
        usecases.mkdir()
        templates.mkdir()
        (templates / "hello-computer.json").write_text(
            json.dumps({"id": "hello-computer", "name": "hello computer", "charter": "nope"})
        )
        (templates / "Zeta.json").write_text(
            json.dumps({"id": "zeta", "name": "Zeta", "charter": "not marketplace"})
        )
        (market / "2026-09-04T0600.json").write_text(json.dumps(official_prev) + "\n")
        (market / "2026-09-11T0600.json").write_text(json.dumps(official_cur) + "\n")
        (usecases / "2026-09-04T0600.json").write_text(json.dumps(corpus_prev) + "\n")
        (usecases / "2026-09-11T0600.json").write_text(json.dumps(corpus_cur) + "\n")

        code, out = _capture(lambda: cmd_bots(root, False, False, offline=True))
        check("bots-exit-ok", code == 0, str(code))
        check(
            "union-3-from-2-plus-2-overlap-1",
            "official=2" in out and "corpus=2" in out and "overlap=1" in out and "union=3" in out,
            out,
        )
        check("prints-official-and-corpus-names", "Alpha" in out and "Gamma" in out, out)
        check("prints-overlap", "Shared Bot" in out, out)
        check("templates-not-an-input", "Zeta" not in out and "hello computer" not in out, out)
        check(
            "share-id-limit-printed",
            "share_id" in out and "one-click deploy blocked" in out,
            out,
        )
        check("does-not-invent-share-id", "x.ai/bot/" not in out, out)
        check("urls-are-finds", "URL finds, not deployable" in out, out)
        check("urls-counts-without-dump", "host     x.com" in out and "Guide A" not in out, out)

        j1 = _capture(lambda: cmd_bots(root, True, False, offline=True))[1]
        j2 = _capture(lambda: cmd_bots(root, True, False, offline=True))[1]
        check("json-dumps-are-stable", j1 == j2 and j1.strip().startswith("{"), j1[:120])
        payload = json.loads(j1)
        check("schema", payload.get("schema") == SCHEMA, str(payload.get("schema")))
        check(
            "json-denominators",
            payload.get("official") == 2
            and payload.get("corpus") == 2
            and payload.get("overlap") == 1
            and payload.get("union") == 3,
            str({k: payload.get(k) for k in ("official", "corpus", "overlap", "union")}),
        )
        check("json-creators", payload.get("official_creators") == 2, str(payload.get("official_creators")))
        check("json-builders", payload.get("corpus_builders") == 2, str(payload.get("corpus_builders")))
        names = [b.get("name") for b in payload.get("bots") or []]
        sources = {b.get("name"): b.get("source") for b in payload.get("bots") or []}
        check("json-union-names", names == ["Alpha", "Gamma", "Shared Bot"], str(names))
        check(
            "json-sources",
            sources == {"Alpha": "official", "Gamma": "corpus", "Shared Bot": "both"},
            str(sources),
        )
        shares = {b.get("name"): b.get("share_id") for b in payload.get("bots") or []}
        check(
            "passes-through-only-real-share-id",
            shares == {"Alpha": "real-share-1", "Gamma": "gammaShare", "Shared Bot": None},
            str(shares),
        )
        check(
            "sorted-name-then-source",
            [b.get("source") for b in payload.get("bots") or []] == ["official", "corpus", "both"],
            str(payload.get("bots")),
        )
        check("new-since-known-pair", payload.get("new") == ["Gamma"], str(payload.get("new")))
        check("urls-total", (payload.get("urls") or {}).get("total") == 2, str(payload.get("urls")))
        check("urls-no-dump-by-default", "rows" not in (payload.get("urls") or {}), str(payload.get("urls")))
        check("stamps-present", payload.get("stamps", {}).get("market") == "2026-09-11T0600.json", str(payload.get("stamps")))

        u_code, u_out = _capture(lambda: cmd_bots(root, True, True, offline=True))
        u_payload = json.loads(u_out) if u_out.strip().startswith("{") else {}
        check("urls-dump-flag", u_code == 0 and len((u_payload.get("urls") or {}).get("rows") or []) == 2, u_out[:200])

        n_code, n_out = _capture(lambda: cmd_new(root, False, offline=True))
        check("new-exit-ok", n_code == 0, str(n_code))
        check("new-lists-gamma", "Gamma" in n_out and "NEW  n=1" in n_out, n_out)
        check("new-omits-old", "Alpha" not in n_out.split("NEW", 1)[-1], n_out)

        empty = root / "empty"
        empty.mkdir()
        ecode, eout = _capture(lambda: cmd_bots(empty, False, False, offline=True))
        check("missing-both-is-environment", ecode == EXIT_ENVIRONMENT, str(ecode))
        check("missing-names-refresh", "gb market refresh" in eout, eout)
        check("missing-names-corpus-refresh", "gb market refresh --corpus" in eout, eout)

        market_only = root / "market-only"
        (market_only / "market").mkdir(parents=True)
        (market_only / "market" / "2026-09-11T0600.json").write_text(json.dumps(official_cur) + "\n")
        mcode, mout = _capture(lambda: cmd_bots(market_only, False, False, offline=True))
        check("official-only-is-ok", mcode == 0 and "official=" in mout, mout)

        hello = root / "hello-corpus"
        (hello / "market").mkdir(parents=True)
        (hello / "usecases").mkdir()
        (hello / "market" / "2026-09-11T0600.json").write_text(json.dumps(official_cur) + "\n")
        hello_corpus = {
            "schema": "gb-usecases/1",
            "rows": [
                {
                    "name": "hello-computer",
                    "category": "Ops",
                    "contributor": "op",
                    "source": "https://x.com/op/status/9",
                    "added_at": "2026-09-01",
                }
            ],
            "links": [],
        }
        (hello / "usecases" / "2026-09-11T0600.json").write_text(json.dumps(hello_corpus) + "\n")
        hcode, hout = _capture(lambda: cmd_bots(hello, False, False, offline=True))
        check(
            "does-not-filter-corpus-hello-computer",
            hcode == 0 and "hello-computer" in hout,
            hout,
        )

        one_each = root / "one-each"
        (one_each / "market").mkdir(parents=True)
        (one_each / "usecases").mkdir()
        (one_each / "market" / "2026-09-11T0600.json").write_text(json.dumps(official_cur) + "\n")
        (one_each / "usecases" / "2026-09-11T0600.json").write_text(json.dumps(corpus_cur) + "\n")
        pcode, pout = _capture(lambda: cmd_new(one_each, False, offline=True))
        check(
            "new-without-previous-pair-is-environment",
            pcode == EXIT_ENVIRONMENT and "previous" in pout,
            pout,
        )

        rc_fail, out_fail = _capture(lambda: cmd_refresh(False, runners={"snapshot": lambda: 4}))
        check("refresh-fails-out-loud", rc_fail == 4 and "skipped" in out_fail, out_fail)
        rc_ok, _ = _capture(lambda: cmd_refresh(False, runners={"snapshot": lambda: 0}))
        check("refresh-market-ok", rc_ok == 0, str(rc_ok))
        rc_c, out_c = _capture(
            lambda: cmd_refresh(True, runners={"snapshot": lambda: 0, "usecases": lambda: 2})
        )
        check("refresh-corpus-fails-out-loud", rc_c == 2 and "usecases refresh failed" in out_c, out_c)
        rc_s, out_s = _capture(
            lambda: cmd_refresh(True, runners={"snapshot": lambda: 1, "usecases": lambda: 0})
        )
        check(
            "refresh-corpus-survives-snapshot-fail",
            rc_s == 0 and "corpus-only" in out_s,
            out_s,
        )

        corpus_only = root / "corpus-only"
        (corpus_only / "usecases").mkdir(parents=True)
        (corpus_only / "usecases" / "2026-09-11T0600.json").write_text(json.dumps(corpus_cur) + "\n")
        ccode, cout = _capture(lambda: cmd_bots(corpus_only, False, False, offline=True))
        check(
            "corpus-only-is-ok",
            ccode == 0 and "corpus=" in cout and "official=0" in cout,
            cout,
        )
        rc_q, out_q = _capture(
            lambda: cmd_refresh(True, runners={"snapshot": lambda: 0, "usecases": lambda: 0}, quiet=True)
        )
        check("refresh-quiet-is-silent", rc_q == 0 and out_q.strip() == "", repr(out_q))

        mdb = _market_db()
        mb = _market_bandit()
        jobs_root = root / "jobs-cache"
        (jobs_root / "usecases").mkdir(parents=True)
        planted = [
            mdb.canonicalize_row(
                {
                    "name": "Aaa Best",
                    "category": "Ops",
                    "origin": "both",
                    "prompt_chars": 9999,
                    "has_approval_language": True,
                    "added_at": "2026-12-31",
                    "charter": "no share must not win",
                }
            ),
            mdb.canonicalize_row(
                {
                    "name": "Zzz Weak",
                    "category": "Ops",
                    "share_id": "weakShare",
                    "from_shares": True,
                    "origin": "shares",
                    "prompt_chars": 1,
                    "has_approval_language": False,
                    "added_at": "2020-01-01",
                }
            ),
            mdb.canonicalize_row(
                {
                    "name": "Aaa Shares",
                    "category": "coding-shipping",
                    "share_id": "aaaShare",
                    "from_shares": True,
                    "origin": "shares",
                    "prompt_chars": 999,
                    "has_approval_language": True,
                    "added_at": "2026-12-31",
                }
            ),
            mdb.canonicalize_row(
                {
                    "name": "Zzz Both",
                    "category": "coding-shipping",
                    "share_id": "zzzShare",
                    "origin": "both",
                    "prompt_chars": 1,
                    "has_approval_language": False,
                    "added_at": "2020-01-01",
                }
            ),
            mdb.canonicalize_row(
                {
                    "name": "Aaa Personal",
                    "category": "Personal",
                    "share_id": "persShare",
                    "origin": "both",
                    "prompt_chars": 5000,
                    "has_approval_language": True,
                    "added_at": "2026-12-31",
                }
            ),
            mdb.canonicalize_row(
                {
                    "name": "Aaa Sales Ghost",
                    "category": "Sales",
                    "origin": "directory",
                    "prompt_chars": 800,
                    "added_at": "2026-12-31",
                }
            ),
        ]
        mdb.rebuild(jobs_root / "usecases" / mdb.DB_NAME, planted, [])
        jcode, jout = _capture(lambda: cmd_jobs(jobs_root, False, offline=True))
        check("jobs-exit-ok", jcode == 0, str(jcode) + jout[:200])
        check(
            "jobs-no-share-cannot-win",
            "Zzz Weak" in jout and "weakShare" in jout and "Aaa Best" not in jout,
            jout,
        )
        check(
            "jobs-selector-is-thompson",
            "method=thompson" in jout and "JOB ARMS" in jout and "PULLS" in jout,
            jout,
        )
        check(
            "jobs-none-never-printed",
            "Aaa Personal" not in jout and " persShare" not in jout and " none " not in jout,
            jout,
        )
        check(
            "jobs-blocked-not-invented",
            "BLOCKED" in jout and "sell" in jout and "Aaa Sales Ghost" not in jout,
            jout,
        )
        check("jobs-human-has-origin-chars-added", "shares" in jout and "2020-01-01" in jout, jout)
        jj = _capture(lambda: cmd_jobs(jobs_root, True, offline=True))[1]
        jpayload = json.loads(jj) if jj.strip().startswith("{") else {}
        jnames = [w.get("name") for w in jpayload.get("winners") or []]
        jsids = [w.get("share_id") for w in jpayload.get("winners") or []]
        jjobs = [w.get("job") for w in jpayload.get("winners") or []]
        check("jobs-json-schema", jpayload.get("schema") == JOBS_SCHEMA, str(jpayload.get("schema")))
        check(
            "jobs-json-winners-have-share-id",
            jsids and all(jsids) and "weakShare" in jsids,
            str(jsids),
        )
        check(
            "jobs-json-skips-none-and-no-share",
            "none" not in jjobs and "Aaa Best" not in jnames and all(jsids),
            str(jpayload),
        )
        check(
            "jobs-json-selector-envelope",
            (jpayload.get("selector") or {}).get("method") == "thompson"
            and (jpayload.get("selector") or {}).get("prior") == "beta(1,1)"
            and "weights" not in (jpayload.get("selector") or {})
            and jpayload.get("bandit_user_version") == mb.USER_VERSION,
            str(jpayload.get("selector")),
        )
        ship_names = set()
        for _ in range(40):
            again = json.loads(_capture(lambda: cmd_jobs(jobs_root, True, offline=True))[1])
            for w in again.get("winners") or []:
                if w.get("job") == "ship":
                    ship_names.add(w.get("name"))
        check(
            "jobs-no-outcome-explores-ship",
            ship_names == {"Aaa Shares", "Zzz Both"},
            str(ship_names),
        )
        blocked = jpayload.get("blocked") or []
        check(
            "jobs-json-blocked-count-reason",
            any(b.get("job") == "sell" and b.get("count") == 1 and b.get("reason") for b in blocked),
            str(blocked),
        )

        stale_root = root / "jobs-stale"
        (stale_root / "usecases").mkdir(parents=True)
        import sqlite3 as _sql

        stale = stale_root / "usecases" / "market.sqlite"
        conn = _sql.connect(str(stale))
        try:
            conn.executescript(mdb.SCHEMA_SQL)
            conn.execute("PRAGMA user_version = 0")
            conn.execute(
                "INSERT INTO bots (name_key, name, origin, taxonomy, job, share_id) "
                "VALUES ('x','X','both','ops','operate','sid')"
            )
            conn.commit()
        finally:
            conn.close()
        scode, sout = _capture(lambda: cmd_jobs(stale_root, False, offline=True))
        check(
            "jobs-offline-stale-version-refused",
            scode == EXIT_ENVIRONMENT and "user_version" in sout,
            sout,
        )
        bad_root = root / "jobs-bad"
        (bad_root / "usecases").mkdir(parents=True)
        (bad_root / "usecases" / "market.sqlite").write_bytes(b"not a sqlite database\n")
        bcode, bout = _capture(lambda: cmd_jobs(bad_root, False, offline=True))
        check(
            "jobs-offline-integrity-refused",
            bcode == EXIT_ENVIRONMENT and "refuse" in bout,
            bout,
        )
        bandit_bad = root / "jobs-bandit-bad"
        (bandit_bad / "usecases").mkdir(parents=True)
        mdb.rebuild(
            bandit_bad / "usecases" / mdb.DB_NAME,
            [
                mdb.canonicalize_row(
                    {
                        "name": "Zzz Weak",
                        "category": "Ops",
                        "share_id": "weakShare",
                        "from_shares": True,
                    }
                )
            ],
            [],
        )
        (bandit_bad / "usecases" / mb.DB_NAME).write_bytes(b"not a sqlite database\n")
        bbcode, bbout = _capture(lambda: cmd_jobs(bandit_bad, False, offline=True))
        check(
            "jobs-offline-bad-bandit-refused",
            bbcode == EXIT_ENVIRONMENT and "refuse" in bbout,
            bbout,
        )
        stamps_only = root / "jobs-stamps-only"
        (stamps_only / "usecases").mkdir(parents=True)
        (stamps_only / "usecases" / "2026-09-11T0600.json").write_text(json.dumps(corpus_cur) + "\n")
        tcode, tout = _capture(lambda: cmd_jobs(stamps_only, False, offline=True))
        check(
            "jobs-offline-requires-cache",
            tcode == EXIT_ENVIRONMENT and "cache" in tout.lower(),
            tout,
        )
        cli = _capture(lambda: body(["jobs", "--offline", "--root", str(jobs_root)]))
        check("jobs-cli-action", cli[0] == 0 and "Zzz Weak" in cli[1] and "Aaa Best" not in cli[1], cli[1][:300])
        help_txt = _build_parser().format_help()
        check(
            "jobs-no-persona-flag",
            "--persona" not in help_txt and "jobs" in help_txt,
            help_txt,
        )

    check("refresh-producer-exists", (BIN / "gb-market-snapshot.py").is_file(), "")
    check("corpus-refresh-producer-exists", (BIN / "gb-usecases.py").is_file(), "")

    failed = [n for n, ok, d in legs if not ok]
    for name, ok, detail in legs:
        if not ok:
            emit("FAIL %s: %s" % (name, detail[:240]))
    emit("SELFTEST %s - %d/%d" % ("FAIL" if failed else "PASS", len(legs) - len(failed), len(legs)))
    return 1 if failed else 0


def _capture(fn: Callable[[], int]) -> Tuple[int, str]:
    import contextlib
    import io

    buf = io.StringIO()
    err = io.StringIO()
    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(err):
        code = fn()
    return code, buf.getvalue() + err.getvalue()


def _build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="gb-market.py",
        description="Official marketplace ∪ public corpus ∪ curated URLs.",
    )
    ap.add_argument(
        "action",
        nargs="?",
        choices=("refresh", "bots", "new", "jobs"),
        default="bots",
        help="refresh | bots | new | jobs (one Thompson draw per job; not a frozen rank)",
    )
    ap.add_argument(
        "--corpus",
        action="store_true",
        help="refresh: also run gb-usecases.py (botdirectory ∪ live shares)",
    )
    ap.add_argument(
        "--urls",
        action="store_true",
        help="bots: dump curated link rows (default: counts by section/host)",
    )
    ap.add_argument(
        "--full",
        action="store_true",
        help="bots: print every row (default: first %d)" % PREVIEW_ROWS,
    )
    ap.add_argument("--json", action="store_true")
    ap.add_argument(
        "--offline",
        action="store_true",
        help="bots/new: stamps only; jobs: valid sqlite cache only",
    )
    ap.add_argument("--root", default="", help="artifact root (tests)")
    ap.add_argument("--selftest", action="store_true")
    return ap


def body(argv: Optional[Sequence[str]] = None) -> int:
    ap = _build_parser()
    args = ap.parse_args(list(argv) if argv is not None else None)
    if args.selftest:
        return selftest()
    if args.action == "refresh":
        return cmd_refresh(bool(args.corpus))
    root = pathlib.Path(args.root) if args.root else ROOT
    if args.action == "new":
        return cmd_new(root, bool(args.json), bool(args.offline))
    if args.action == "jobs":
        return cmd_jobs(root, bool(args.json), bool(args.offline))
    return cmd_bots(
        root,
        bool(args.json),
        bool(args.urls),
        bool(getattr(args, "full", False)),
        bool(args.offline),
    )


if __name__ == "__main__":
    gbmain(lambda: body())
