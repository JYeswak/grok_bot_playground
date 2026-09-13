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
JOBS_SCHEMA = "gb-market-jobs/4"
VERDICT_SCHEMA = "gb-market-verdict/1"
DEPLOY_SCHEMA = "gb-market-deploy/1"
SHARE_HOST = "https://x.ai/bot/"
PREVIEW_ROWS = 20
EXIT_OK, EXIT_USAGE, EXIT_ENVIRONMENT, EXIT_REFUSED = 0, 2, 3, 5
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
    winners = list(payload.get("draws") or payload.get("winners") or [])
    blocked = list(payload.get("blocked") or [])
    sel = payload.get("selector") or {}
    emit(
        "JOB DRAWS  n=%d  blocked=%d  method=%s  impression until keep/skip"
        % (len(winners), len(blocked), sel.get("method") or "thompson")
    )
    emit(
        "%-28s %-10s %5s %5s %5s %5s %-10s"
        % ("NAME", "JOB", "IMPR", "PULLS", "ALPHA", "BETA", "ORIGIN")
    )
    rows: List[Tuple[str, dict]] = [("win", w) for w in winners] + [
        ("block", b) for b in blocked
    ]
    rows.sort(key=lambda item: str(item[1].get("job") or ""))
    for kind, row in rows:
        job = str(row.get("job") or "-")
        if kind == "block":
            emit(
                "%-28s %-10s %5s %5s %5s %5s %-10s"
                % ("-", job[:10], "-", "-", "-", "-", "-")
            )
            emit("  SHARE_ID  BLOCKED")
            continue
        emit(
            "%-28s %-10s %5s %5s %5s %5s %-10s"
            % (
                str(row.get("name") or "-")[:28],
                job[:10],
                str(row.get("impressions") if row.get("impressions") is not None else 0),
                str(row.get("pulls") if row.get("pulls") is not None else 0),
                str(row.get("alpha") if row.get("alpha") is not None else "-"),
                str(row.get("beta") if row.get("beta") is not None else "-"),
                str(row.get("origin") or "-")[:10],
            )
        )
        emit("  SHARE_ID  %s" % (row.get("share_id") or "BLOCKED"))
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
        draws = []
        for row in list(picked.get("draws") or picked.get("winners") or []):
            arm = mb.record_impression(
                store,
                job=str(row.get("job") or ""),
                name_key=str(row.get("name_key") or ""),
                share_id=str(row.get("share_id") or ""),
            )
            updated = dict(row)
            updated["alpha"] = arm.get("alpha")
            updated["beta"] = arm.get("beta")
            updated["impressions"] = arm.get("impressions")
            updated["pulls"] = arm.get("pulls")
            draws.append(updated)
    except mdb.CacheRefused as e:
        emit(str(e).replace("gb-market-db:", "gb market jobs:", 1))
        return EXIT_ENVIRONMENT
    except mb.StoreRefused as e:
        emit(str(e).replace("gb-market-bandit:", "gb market jobs:", 1))
        return EXIT_ENVIRONMENT
    payload = {
        "bandit_user_version": mb.USER_VERSION,
        "blocked": picked.get("blocked") or [],
        "draws": draws,
        "schema": JOBS_SCHEMA,
        "selector": picked.get("selector") or {},
        "user_version": mdb.USER_VERSION,
    }
    if any(not w.get("share_id") for w in payload["draws"]):
        emit("gb market jobs: refuse — a draw is missing share_id")
        return EXIT_ENVIRONMENT
    if as_json:
        emit(dumps(payload))
        return EXIT_OK
    print_jobs_human(payload)
    return EXIT_OK


def cmd_verdict(
    root: pathlib.Path,
    kind: str,
    share_id: Optional[str],
    as_json: bool,
) -> int:
    mb = _market_bandit()
    sid = mb.real_share_id({"share_id": share_id})
    if not sid:
        emit("gb market %s: refuse — no share_id" % kind)
        return EXIT_USAGE
    store = mb.store_path(root)
    err = mb.refuse_path(store)
    if err:
        emit(err.replace("gb-market-bandit:", "gb market %s:" % kind, 1))
        return EXIT_ENVIRONMENT
    try:
        arm = mb.apply_verdict(store, sid, kind)
    except mb.StoreRefused as e:
        emit(str(e).replace("gb-market-bandit:", "gb market %s:" % kind, 1))
        return EXIT_ENVIRONMENT
    payload = {
        "kind": kind,
        "schema": VERDICT_SCHEMA,
        "subject": {
            "alpha": arm.get("alpha"),
            "banned": bool(arm.get("banned")),
            "beta": arm.get("beta"),
            "job": arm.get("job"),
            "name_key": arm.get("name_key"),
            "pulls": arm.get("pulls"),
            "share_id": arm.get("share_id"),
        },
    }
    if as_json:
        emit(dumps(payload))
        return EXIT_OK
    emit(
        "%s  job=%s  name_key=%s  share_id=%s  pulls=%s  alpha=%s  beta=%s%s"
        % (
            kind.upper(),
            arm.get("job"),
            arm.get("name_key"),
            arm.get("share_id"),
            arm.get("pulls"),
            arm.get("alpha"),
            arm.get("beta"),
            "  banned" if arm.get("banned") else "",
        )
    )
    return EXIT_OK


class DeployRefused(Exception):
    """Usage or environment refuse while resolving a named share_id. Never invents one."""

    def __init__(self, message: str, code: int) -> None:
        super().__init__(message)
        self.code = code


def share_url(share_id: str) -> str:
    """Official one-click URL. Full share_id, never a 16-char slice."""
    return "%s%s" % (SHARE_HOST, share_id)


def catalog_share_ids_with_prefix(bots: Sequence[dict], prefix: str) -> List[str]:
    mdb = _market_db()
    sid = mdb.real_share_id({"share_id": prefix})
    if not sid:
        return []
    found = []
    for row in bots:
        got = mdb.real_share_id(row)
        if got and got.startswith(sid):
            found.append(got)
    return sorted(set(found))


def catalog_row_for_share_id(bots: Sequence[dict], share_id: str) -> Optional[dict]:
    mdb = _market_db()
    sid = mdb.real_share_id({"share_id": share_id})
    if not sid:
        return None
    hits = [r for r in bots if mdb.real_share_id(r) == sid]
    if not hits:
        return None
    hits.sort(key=lambda r: name_key(r.get("name")))
    return hits[0]


def resolve_deploy_share_id(
    token: str,
    bots: Sequence[dict],
    store: pathlib.Path,
    mb: Any,
) -> str:
    """Exact catalog or bandit hit, else a unique prefix across both. Never guesses."""
    sid = mb.real_share_id({"share_id": token})
    if not sid:
        raise DeployRefused("gb market deploy: refuse — no share_id", EXIT_USAGE)
    catalog_exact = catalog_row_for_share_id(bots, sid) is not None
    bandit_hits = mb.list_share_ids_with_prefix(store, sid)
    if catalog_exact or mb.latest_subject(store, sid) is not None:
        if mb.latest_subject(store, sid) is not None:
            resolved = mb.resolve_share_id(store, sid, kind="deploy")
        else:
            resolved = sid
    else:
        catalog_hits = catalog_share_ids_with_prefix(bots, sid)
        matches = sorted(set(catalog_hits) | set(bandit_hits))
        if not matches:
            raise DeployRefused(
                "gb market deploy: refuse — not in catalog (or not in store)",
                EXIT_ENVIRONMENT,
            )
        if len(matches) > 1:
            raise DeployRefused(
                "gb market deploy: refuse — ambiguous share_id prefix",
                EXIT_ENVIRONMENT,
            )
        resolved = matches[0]
    if catalog_row_for_share_id(bots, resolved) is None:
        raise DeployRefused(
            "gb market deploy: refuse — not in catalog (or not in store)",
            EXIT_ENVIRONMENT,
        )
    return resolved


def deploy_card(row: dict) -> dict:
    mdb = _market_db()
    sid = mdb.real_share_id(row) or ""
    return {
        "category": row.get("category") or "",
        "charter": str(row.get("charter") or ""),
        "job": row.get("job") or "none",
        "name": row.get("name") or "",
        "origin": row.get("origin") or "",
        "share_id": sid,
        "share_url": share_url(sid),
    }


def print_deploy_human(cards: Sequence[dict]) -> None:
    for i, card in enumerate(cards):
        if i:
            emit("")
        emit("NAME       %s" % (card.get("name") or "-"))
        emit("JOB        %s" % (card.get("job") or "none"))
        emit("SHARE_URL  %s" % (card.get("share_url") or ""))
        origin = card.get("origin") or "-"
        category = card.get("category") or "-"
        emit("ORIGIN     %s  category=%s" % (origin, category))
        charter = card.get("charter") or ""
        if charter:
            emit("CHARTER")
            emit(charter if charter.endswith("\n") else charter + "\n")
        else:
            emit("CHARTER MISSING")


def cmd_deploy(
    root: pathlib.Path,
    share_ids: Sequence[str],
    as_json: bool,
    offline: bool = False,
    apply: bool = False,
) -> int:
    if apply:
        emit(
            "gb market deploy: refuse — open the SHARE_URL "
            "(do not call CreateGrokBot or templates)"
        )
        return EXIT_REFUSED
    tokens = [str(t).strip() for t in share_ids if str(t).strip()]
    if not tokens:
        emit("gb market deploy: refuse — no share_id")
        return EXIT_USAGE
    if not offline:
        cmd_refresh(True, quiet=as_json)
    mdb = _market_db()
    mb = _market_bandit()
    path = root / "usecases" / mdb.DB_NAME
    err = mdb.refuse_path(path)
    if err:
        emit(err.replace("gb-market-db:", "gb market deploy:", 1))
        return EXIT_ENVIRONMENT
    store = mb.store_path(root)
    bandit_err = mb.refuse_path(store)
    if bandit_err:
        emit(bandit_err.replace("gb-market-bandit:", "gb market deploy:", 1))
        return EXIT_ENVIRONMENT
    try:
        bots = mdb.load_bots(path)
        cards = []
        for token in tokens:
            resolved = resolve_deploy_share_id(token, bots, store, mb)
            row = catalog_row_for_share_id(bots, resolved)
            if row is None:
                raise DeployRefused(
                    "gb market deploy: refuse — not in catalog (or not in store)",
                    EXIT_ENVIRONMENT,
                )
            cards.append(deploy_card(row))
    except DeployRefused as e:
        emit(str(e))
        return e.code
    except mdb.CacheRefused as e:
        emit(str(e).replace("gb-market-db:", "gb market deploy:", 1))
        return EXIT_ENVIRONMENT
    except mb.StoreRefused as e:
        emit(str(e).replace("gb-market-bandit:", "gb market deploy:", 1))
        return EXIT_ENVIRONMENT
    payload = {
        "rows": [
            {
                "charter": c.get("charter") or "",
                "job": c.get("job"),
                "name": c.get("name"),
                "origin": c.get("origin"),
                "share_id": c.get("share_id"),
                "share_url": c.get("share_url"),
            }
            for c in cards
        ],
        "schema": DEPLOY_SCHEMA,
    }
    if as_json:
        emit(dumps(payload))
        return EXIT_OK
    print_deploy_human(cards)
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
            mdb.canonicalize_row(
                {
                    "name": "Long Share Desk",
                    "category": "finance-ops",
                    "share_id": "ANv3NrqPfRcS9PdXku7h8",
                    "from_shares": True,
                    "origin": "shares",
                    "prompt_chars": 40,
                    "added_at": "2026-09-13",
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
            "method=thompson" in jout
            and "JOB DRAWS" in jout
            and "PULLS" in jout
            and "ALPHA" in jout
            and "BETA" in jout
            and "WINNER" not in jout,
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
        check("jobs-human-has-origin-alpha-beta", "shares" in jout and "ALPHA" in jout, jout)
        check(
            "jobs-human-prints-full-share-id",
            "ANv3NrqPfRcS9PdXku7h8" in jout,
            jout,
        )
        jj = _capture(lambda: cmd_jobs(jobs_root, True, offline=True))[1]
        jpayload = json.loads(jj) if jj.strip().startswith("{") else {}
        jnames = [w.get("name") for w in jpayload.get("draws") or []]
        jsids = [w.get("share_id") for w in jpayload.get("draws") or []]
        jjobs = [w.get("job") for w in jpayload.get("draws") or []]
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
            for w in again.get("draws") or []:
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
        long_draw = next(
            (w for w in jpayload.get("draws") or [] if w.get("share_id") == "ANv3NrqPfRcS9PdXku7h8"),
            {},
        )
        check(
            "jobs-json-impressions-after-draw",
            int(long_draw.get("impressions") or 0) >= 1
            and all(int(w.get("impressions") or 0) >= 1 for w in jpayload.get("draws") or []),
            str(long_draw) + str(jpayload.get("draws")),
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
        keep = _capture(lambda: body(["keep", "weakShare", "--offline", "--root", str(jobs_root)]))
        check(
            "cli-keep-raises-alpha",
            keep[0] == 0 and "KEEP" in keep[1] and "alpha=2" in keep[1] and "WINNER" not in keep[1],
            keep[1][:300],
        )
        long_sid = "ANv3NrqPfRcS9PdXku7h8"
        long_prefix = long_sid[:16]
        pref = _capture(lambda: body(["keep", long_prefix, "--offline", "--root", str(jobs_root)]))
        check(
            "cli-keep-unique-truncated-prefix",
            pref[0] == 0 and "KEEP" in pref[1] and long_sid in pref[1],
            pref[1][:300],
        )
        collide_sid = long_prefix + "zzzzz"
        mb.record_impression(
            mb.store_path(jobs_root),
            job="market",
            name_key="collide desk",
            share_id=collide_sid,
        )
        amb = _capture(lambda: body(["keep", long_prefix, "--offline", "--root", str(jobs_root)]))
        check(
            "cli-keep-ambiguous-prefix-refused",
            amb[0] == EXIT_ENVIRONMENT and "ambiguous" in amb[1].lower(),
            amb[1],
        )
        full = _capture(lambda: body(["skip", long_sid, "--offline", "--root", str(jobs_root)]))
        check(
            "cli-skip-full-share-id",
            full[0] == 0 and "SKIP" in full[1] and long_sid in full[1],
            full[1][:300],
        )
        skip = _capture(lambda: body(["skip", "weakShare", "--offline", "--root", str(jobs_root)]))
        check(
            "cli-skip-raises-beta",
            skip[0] == 0 and "SKIP" in skip[1] and "beta=2" in skip[1],
            skip[1][:300],
        )
        for kind in ("keep", "skip", "ban"):
            nosid = _capture(lambda: body([kind, "--offline", "--root", str(jobs_root)]))
            check(
                "cli-%s-no-share-refused" % kind,
                nosid[0] == EXIT_USAGE and "no share_id" in nosid[1],
                nosid[1],
            )
        missing = _capture(lambda: body(["ban", "noSuchShare", "--offline", "--root", str(jobs_root)]))
        check(
            "cli-ban-not-in-store-refused",
            missing[0] == EXIT_ENVIRONMENT and "not in store" in missing[1],
            missing[1],
        )
        ban = _capture(lambda: body(["ban", "weakShare", "--offline", "--root", str(jobs_root)]))
        check("cli-ban-marks-ineligible", ban[0] == 0 and "banned" in ban[1], ban[1][:300])
        after_ban = _capture(lambda: body(["jobs", "--offline", "--json", "--root", str(jobs_root)]))
        after_payload = json.loads(after_ban[1]) if after_ban[1].strip().startswith("{") else {}
        after_sids = [w.get("share_id") for w in after_payload.get("draws") or []]
        check(
            "cli-banned-not-drawn",
            after_ban[0] == 0 and "weakShare" not in after_sids,
            str(after_sids) + after_ban[1][:200],
        )
        help_txt = _build_parser().format_help()
        check(
            "jobs-no-persona-flag",
            "--persona" not in help_txt
            and "pack" not in help_txt
            and "jobs" in help_txt
            and "keep" in help_txt
            and "skip" in help_txt
            and "ban" in help_txt
            and "deploy" in help_txt,
            help_txt,
        )

        deploy_root = root / "deploy-cache"
        (deploy_root / "usecases").mkdir(parents=True)
        long_sid = "ANv3NrqPfRcS9PdXku7h8"
        long_prefix = long_sid[:16]
        deploy_charter = "paste this charter for the long share desk"
        mdb.rebuild(
            deploy_root / "usecases" / mdb.DB_NAME,
            [
                mdb.canonicalize_row(
                    {
                        "name": "Long Share Desk",
                        "category": "finance-ops",
                        "share_id": long_sid,
                        "from_shares": True,
                        "origin": "shares",
                        "charter": deploy_charter,
                        "prompt_chars": len(deploy_charter),
                    }
                ),
                mdb.canonicalize_row(
                    {
                        "name": "Empty Charter Desk",
                        "category": "Ops",
                        "share_id": "emptyShareXYZ",
                        "from_shares": True,
                        "origin": "shares",
                        "charter": "",
                    }
                ),
                mdb.canonicalize_row(
                    {
                        "name": "Aaa Personal",
                        "category": "Personal",
                        "share_id": "persShare",
                        "origin": "both",
                        "charter": "personal admin is job none",
                    }
                ),
                mdb.canonicalize_row(
                    {
                        "name": "Prefix Twin A",
                        "category": "Ops",
                        "share_id": "prefixTwinAAA",
                        "from_shares": True,
                        "origin": "shares",
                        "charter": "twin a",
                    }
                ),
                mdb.canonicalize_row(
                    {
                        "name": "Prefix Twin B",
                        "category": "Ops",
                        "share_id": "prefixTwinBBB",
                        "from_shares": True,
                        "origin": "shares",
                        "charter": "twin b",
                    }
                ),
                mdb.canonicalize_row(
                    {
                        "name": "Aaa Shares",
                        "category": "coding-shipping",
                        "share_id": "aaaShare",
                        "from_shares": True,
                        "origin": "shares",
                        "charter": "ship this",
                    }
                ),
            ],
            [],
        )
        mb.record_impression(
            mb.store_path(deploy_root),
            job="spend",
            name_key="long share desk",
            share_id=long_sid,
        )
        exact = _capture(
            lambda: body(["deploy", long_sid, "--offline", "--root", str(deploy_root)])
        )
        exact_url = "https://x.ai/bot/%s" % long_sid
        check(
            "deploy-exact-id-prints-full-url-and-charter",
            exact[0] == 0
            and exact_url in exact[1]
            and deploy_charter in exact[1]
            and "Long Share Desk" in exact[1]
            and "JOB        spend" in exact[1],
            exact[1][:400],
        )
        pref = _capture(
            lambda: body(
                ["deploy", long_prefix, "--offline", "--root", str(deploy_root)]
            )
        )
        check(
            "deploy-unique-prefix",
            pref[0] == 0 and exact_url in pref[1] and long_sid in pref[1],
            pref[1][:400],
        )
        check(
            "deploy-never-truncates-share-id",
            exact_url in exact[1]
            and exact_url in pref[1]
            and all(
                not line.strip().endswith("https://x.ai/bot/" + long_prefix)
                for line in (exact[1] + pref[1]).splitlines()
            ),
            exact[1][:200] + pref[1][:200],
        )
        amb = _capture(
            lambda: body(
                ["deploy", "prefixTwin", "--offline", "--root", str(deploy_root)]
            )
        )
        check(
            "deploy-ambiguous-prefix-refused",
            amb[0] == EXIT_ENVIRONMENT and "ambiguous" in amb[1].lower(),
            amb[1],
        )
        missing = _capture(
            lambda: body(
                ["deploy", "noSuchShare", "--offline", "--root", str(deploy_root)]
            )
        )
        check(
            "deploy-missing-id-refused",
            missing[0] == EXIT_ENVIRONMENT
            and "not in catalog" in missing[1]
            and "not in store" in missing[1],
            missing[1],
        )
        nosid = _capture(
            lambda: body(["deploy", "--offline", "--root", str(deploy_root)])
        )
        check(
            "deploy-no-share-id-is-usage",
            nosid[0] == EXIT_USAGE and "no share_id" in nosid[1],
            nosid[1],
        )
        none_job = _capture(
            lambda: body(
                ["deploy", "persShare", "--offline", "--root", str(deploy_root)]
            )
        )
        check(
            "deploy-job-none-allowed-when-named",
            none_job[0] == 0
            and "JOB        none" in none_job[1]
            and "https://x.ai/bot/persShare" in none_job[1],
            none_job[1][:300],
        )
        empty_c = _capture(
            lambda: body(
                ["deploy", "emptyShareXYZ", "--offline", "--root", str(deploy_root)]
            )
        )
        check(
            "deploy-empty-charter-prints-missing-and-url",
            empty_c[0] == 0
            and "CHARTER MISSING" in empty_c[1]
            and "https://x.ai/bot/emptyShareXYZ" in empty_c[1],
            empty_c[1][:300],
        )
        applied = _capture(
            lambda: body(
                [
                    "deploy",
                    long_sid,
                    "--apply",
                    "--offline",
                    "--root",
                    str(deploy_root),
                ]
            )
        )
        check(
            "deploy-apply-refuses-open-share-url",
            applied[0] == EXIT_REFUSED
            and "SHARE_URL" in applied[1]
            and "CreateGrokBot" in applied[1]
            and "templates" in applied[1]
            and applied[1].count("\n") <= 2,
            applied[1],
        )
        dj = _capture(
            lambda: body(
                [
                    "deploy",
                    long_sid,
                    "persShare",
                    "--offline",
                    "--json",
                    "--root",
                    str(deploy_root),
                ]
            )
        )
        dpayload = json.loads(dj[1]) if dj[1].strip().startswith("{") else {}
        drows = dpayload.get("rows") or []
        check(
            "deploy-json-schema-and-rows",
            dj[0] == 0
            and dpayload.get("schema") == DEPLOY_SCHEMA
            and [r.get("share_id") for r in drows] == [long_sid, "persShare"]
            and drows[0].get("share_url") == exact_url
            and drows[0].get("charter") == deploy_charter
            and drows[0].get("job") == "spend"
            and set(drows[0]) == {
                "charter",
                "job",
                "name",
                "origin",
                "share_id",
                "share_url",
            },
            str(dpayload)[:400],
        )
        deploy_stale = root / "deploy-stale"
        (deploy_stale / "usecases").mkdir(parents=True)
        import sqlite3 as _sql_deploy

        stale_db = deploy_stale / "usecases" / "market.sqlite"
        conn = _sql_deploy.connect(str(stale_db))
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
        dscode, dsout = _capture(
            lambda: body(
                ["deploy", "sid", "--offline", "--root", str(deploy_stale)]
            )
        )
        check(
            "deploy-offline-stale-version-refused",
            dscode == EXIT_ENVIRONMENT and "user_version" in dsout,
            dsout,
        )
        deploy_bad_bandit = root / "deploy-bandit-bad"
        (deploy_bad_bandit / "usecases").mkdir(parents=True)
        mdb.rebuild(
            deploy_bad_bandit / "usecases" / mdb.DB_NAME,
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
        (deploy_bad_bandit / "usecases" / mb.DB_NAME).write_bytes(
            b"not a sqlite database\n"
        )
        dbbcode, dbbout = _capture(
            lambda: body(
                ["deploy", "weakShare", "--offline", "--root", str(deploy_bad_bandit)]
            )
        )
        check(
            "deploy-offline-bad-bandit-refused",
            dbbcode == EXIT_ENVIRONMENT and "refuse" in dbbout,
            dbbout,
        )
        called: List[Tuple[str, Tuple[Any, ...]]] = []
        orig_run = subprocess.run
        orig_call = subprocess.call
        orig_popen = subprocess.Popen
        orig_check_call = subprocess.check_call
        orig_check_output = subprocess.check_output

        def _track(name: str, orig: Any) -> Any:
            def inner(*a: Any, **k: Any) -> Any:
                called.append((name, a))
                return orig(*a, **k)

            return inner

        subprocess.run = _track("run", orig_run)
        subprocess.call = _track("call", orig_call)
        subprocess.Popen = _track("popen", orig_popen)  # type: ignore[assignment]
        subprocess.check_call = _track("check_call", orig_check_call)
        subprocess.check_output = _track("check_output", orig_check_output)
        try:
            role_rc, role_out = _capture(
                lambda: body(
                    ["deploy", long_sid, "--offline", "--root", str(deploy_root)]
                )
            )
        finally:
            subprocess.run = orig_run
            subprocess.call = orig_call
            subprocess.Popen = orig_popen
            subprocess.check_call = orig_check_call
            subprocess.check_output = orig_check_output
        role_blob = str(called) + role_out
        import inspect as _inspect

        deploy_src = (
            _inspect.getsource(cmd_deploy)
            + _inspect.getsource(resolve_deploy_share_id)
            + _inspect.getsource(print_deploy_human)
        )
        check(
            "deploy-does-not-invoke-gb-role",
            role_rc == 0
            and "gb-role" not in role_blob
            and "gb-role" not in deploy_src
            and "first-hour" not in deploy_src
            and not any("gb-role" in str(c) for c in called),
            role_blob[:300] + deploy_src[:200],
        )
        check(
            "deploy-help-no-persona-or-pack",
            "--persona" not in help_txt and "pack" not in help_txt,
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
        choices=("refresh", "bots", "new", "jobs", "keep", "skip", "ban", "deploy"),
        default="bots",
        help="refresh | bots | new | jobs | keep | skip | ban | deploy",
    )
    ap.add_argument(
        "share_ids",
        nargs="*",
        default=[],
        help=(
            "deploy: one or more catalog share_ids or unique prefixes. "
            "keep/skip/ban: one stored share_id or unique prefix"
        ),
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
        help="bots/new: stamps only; jobs/deploy: valid catalog cache only",
    )
    ap.add_argument(
        "--apply",
        action="store_true",
        help="deploy: refused — open the SHARE_URL (does not create Bots)",
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
    share_ids = list(getattr(args, "share_ids", None) or [])
    if args.action == "deploy":
        return cmd_deploy(
            root,
            share_ids,
            bool(args.json),
            bool(args.offline),
            bool(getattr(args, "apply", False)),
        )
    if args.action in ("keep", "skip", "ban"):
        if len(share_ids) > 1:
            emit("gb market %s: refuse — one share_id" % args.action)
            return EXIT_USAGE
        return cmd_verdict(
            root,
            args.action,
            share_ids[0] if share_ids else None,
            bool(args.json),
        )
    return cmd_bots(
        root,
        bool(args.json),
        bool(args.urls),
        bool(getattr(args, "full", False)),
        bool(args.offline),
    )


if __name__ == "__main__":
    gbmain(lambda: body())
