#!/usr/bin/env python3
"""gb-market-db — rebuildable sqlite cache for the live market catalog.

Stock sqlite3 (stdlib). Not fsqlite. Process gates from frankensqlite-mega-skill
and rust-cli-with-sqlite: oracle is the live catalogs; this file is a cache;
integrity_check is the corruption verdict; user_version is the schema cookie;
a planted malformed file must be refused; never reconstruct from a same-age
backup; never invent share_id; read PRAGMA values back.

See docs/MARKET-SYNC-STRATEGY.md.
"""
from __future__ import annotations

import datetime as dt
import json
import math
import os
import pathlib
import random
import sqlite3
import sys
import tempfile
from typing import Any, Dict, List, Optional, Sequence, Tuple

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from gbtypes import main as gbmain  # noqa: E402

USER_VERSION = 4
DB_NAME = "market.sqlite"
LOCK_NAME = "market.sqlite.lock"

# Documented linear predictor. Measured features only — not name, not charter words.
# Recency is days since added_at, clipped to [0, RECENCY_CAP_DAYS]; missing added_at
# is the cap (oldest). Weight is -1/cap so the feature stays on the same scale as
# the 0/1 flags. This is a prior mean, not a frozen ranking: Thompson sampling
# draws from N(mean, PRIOR_VARIANCE) until outcomes shrink the posterior.
SCORE_WEIGHTS = {
    "approval": 1.0,
    "both": 1.0,
    "log1p_prompt_chars": 0.2,
    "recency_days": -1.0 / 365.0,
}
RECENCY_CAP_DAYS = 365
PRIOR_VARIANCE = 4.0
OBS_VARIANCE = 1.0
SELECTOR_METHOD = "thompson"

SCHEMA_SQL = """
CREATE TABLE bots (
  name_key TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  category TEXT,
  category_src TEXT,
  taxonomy TEXT NOT NULL,
  job TEXT NOT NULL,
  share_id TEXT,
  import_url TEXT,
  charter TEXT,
  charter_chars INTEGER NOT NULL DEFAULT 0,
  contributor TEXT,
  source TEXT,
  added_at TEXT,
  origin TEXT NOT NULL,
  has_approval_language INTEGER NOT NULL DEFAULT 0,
  prompt_chars INTEGER NOT NULL DEFAULT 0,
  integrations_json TEXT NOT NULL DEFAULT '[]'
);
CREATE TABLE links (
  url TEXT PRIMARY KEY,
  title TEXT,
  section TEXT,
  description TEXT
);
CREATE TABLE meta (
  k TEXT PRIMARY KEY,
  v TEXT NOT NULL
);
CREATE TABLE bandit_outcomes (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name_key TEXT NOT NULL,
  job TEXT NOT NULL,
  share_id TEXT NOT NULL,
  reward REAL NOT NULL,
  recorded_at TEXT NOT NULL
);
CREATE TABLE bandit_posterior (
  name_key TEXT PRIMARY KEY,
  job TEXT NOT NULL,
  share_id TEXT NOT NULL,
  mu REAL NOT NULL,
  variance REAL NOT NULL,
  pulls INTEGER NOT NULL DEFAULT 0,
  reward_sum REAL NOT NULL DEFAULT 0
);
"""



# Explicit aliases only. Upstream `category` is never overwritten.
# Personal (directory) and personal-admin (shares) are the same drawer.
# Sales and customer-sales are the same drawer.
# Everything else keeps its own bucket. Unknown → unmapped (not guessed).
TAXONOMY_MAP = {
    "personal": "personal",
    "personal-admin": "personal",
    "productivity": "productivity",
    "marketing": "marketing",
    "ops": "ops",
    "sales": "sales",
    "customer-sales": "sales",
    "success": "success",
    "research-briefings": "research",
    "teams-handoffs": "teams",
    "content-publishing": "publishing",
    "coding-shipping": "engineering",
    "finance-ops": "finance",
    "inbox-calendar": "calendar",
    "uncategorised": "unmapped",
    "uncategorized": "unmapped",
}


def taxonomy_of(category: Any) -> str:
    key = " ".join(str(category or "").casefold().replace("_", "-").split())
    return TAXONOMY_MAP.get(key, "unmapped")


# Jobs sit on taxonomy only. Junk drawers stay none.
# Do not read name or charter. decide/refuse have no taxonomy yet — none.
JOB_FROM_TAXONOMY = {
    "research": "brief",
    "finance": "spend",
    "calendar": "calendar",
    "sales": "sell",
    "engineering": "ship",
    "publishing": "publish",
    "ops": "operate",
    "teams": "handoff",
    "marketing": "market",
}


def job_of(taxonomy: Any) -> str:
    return JOB_FROM_TAXONOMY.get(str(taxonomy or ""), "none")


def _real_share_id(row: Dict[str, Any]) -> Optional[str]:
    sid = row.get("share_id")
    if sid in (None, ""):
        return None
    text = str(sid).strip()
    return text or None


def _origin_both(row: Dict[str, Any]) -> int:
    return 1 if str(row.get("origin") or "") == "both" else 0


def _approval(row: Dict[str, Any]) -> int:
    return 1 if row.get("has_approval_language") else 0


def _prompt_chars(row: Dict[str, Any]) -> int:
    try:
        return int(row.get("prompt_chars") or 0)
    except (TypeError, ValueError):
        return 0


def _added_at(row: Dict[str, Any]) -> str:
    return str(row.get("added_at") or "")


def _row_name_key(row: Dict[str, Any]) -> str:
    return str(row.get("name_key") or name_key(row.get("name")))


def _parse_added_at(value: Any) -> Optional[dt.date]:
    text = str(value or "").strip()[:10]
    if len(text) != 10 or text[4] != "-" or text[7] != "-":
        return None
    try:
        return dt.date.fromisoformat(text)
    except ValueError:
        return None


def arm_features(row: Dict[str, Any], as_of: dt.date) -> Dict[str, float]:
    """Measured features only. Name and charter words are not features."""
    added = _parse_added_at(row.get("added_at"))
    if added is None:
        recency = float(RECENCY_CAP_DAYS)
    else:
        recency = float(min(max((as_of - added).days, 0), RECENCY_CAP_DAYS))
    return {
        "approval": float(_approval(row)),
        "both": float(_origin_both(row)),
        "log1p_prompt_chars": math.log1p(max(0, _prompt_chars(row))),
        "recency_days": recency,
    }


def linear_score(features: Dict[str, float]) -> float:
    return sum(float(SCORE_WEIGHTS[k]) * float(features[k]) for k in SCORE_WEIGHTS)


def fold_outcomes(mu0: float, rewards: Sequence[float]) -> Tuple[float, float, int, float]:
    """Gaussian conjugate update. Prior N(mu0, PRIOR_VARIANCE); obs var OBS_VARIANCE."""
    mu = float(mu0)
    var = float(PRIOR_VARIANCE)
    n = 0
    reward_sum = 0.0
    for raw in rewards:
        y = float(raw)
        prec = (1.0 / var) + (1.0 / OBS_VARIANCE)
        mu = ((mu / var) + (y / OBS_VARIANCE)) / prec
        var = 1.0 / prec
        n += 1
        reward_sum += y
    return mu, var, n, reward_sum


def selector_meta(as_of: dt.date) -> Dict[str, Any]:
    return {
        "as_of": as_of.isoformat(),
        "features": sorted(SCORE_WEIGHTS),
        "method": SELECTOR_METHOD,
        "obs_variance": OBS_VARIANCE,
        "predictor": "linear",
        "prior_variance": PRIOR_VARIANCE,
        "weights": dict(SCORE_WEIGHTS),
    }


def _arm_state(
    row: Dict[str, Any],
    as_of: dt.date,
    posteriors: Optional[Dict[str, Dict[str, Any]]],
    outcomes: Optional[Dict[str, List[float]]],
) -> Tuple[float, float, int, Dict[str, float], float]:
    feats = arm_features(row, as_of)
    mu0 = linear_score(feats)
    key = _row_name_key(row)
    stored = (posteriors or {}).get(key)
    if stored and stored.get("mu") is not None and stored.get("variance") is not None:
        return (
            float(stored["mu"]),
            float(stored["variance"]),
            int(stored.get("pulls") or 0),
            feats,
            mu0,
        )
    rewards = (outcomes or {}).get(key) or []
    mu, var, n, _ = fold_outcomes(mu0, rewards)
    return mu, var, n, feats, mu0


def job_winners(
    rows: Sequence[Dict[str, Any]],
    *,
    posteriors: Optional[Dict[str, Dict[str, Any]]] = None,
    outcomes: Optional[Dict[str, List[float]]] = None,
    rng: Optional[random.Random] = None,
    as_of: Optional[dt.date] = None,
) -> Dict[str, Any]:
    """One deployable arm per job via Thompson sampling over a linear prior.

    Skip `none`. Never invent share_id. A job with rows but no share_id is
    blocked. Name alpha and charter words are not selection keys. With no
    outcomes the posterior is uninformative (wide prior variance), so draws
    explore — they are not a frozen argmax of the linear score.
    """
    when = as_of or dt.date.today()
    draw = rng or random.Random()
    by_job: Dict[str, List[Dict[str, Any]]] = {}
    for row in rows:
        job = str(row.get("job") or "none")
        if job in ("", "none"):
            continue
        by_job.setdefault(job, []).append(dict(row))

    winners: List[Dict[str, Any]] = []
    blocked: List[Dict[str, Any]] = []
    for job in sorted(by_job):
        group = by_job[job]
        deployable = [r for r in group if _real_share_id(r)]
        if not deployable:
            blocked.append(
                {
                    "count": len(group),
                    "job": job,
                    "reason": "no share_id",
                }
            )
            continue
        best_sample: Optional[float] = None
        chosen: List[Tuple[Dict[str, Any], float, float, int, Dict[str, float], float]] = []
        for row in deployable:
            mu, var, pulls, feats, mu0 = _arm_state(row, when, posteriors, outcomes)
            sigma = math.sqrt(var) if var > 0 else 0.0
            sample = draw.gauss(mu, sigma) if sigma > 0 else mu
            item = (row, sample, mu, pulls, feats, mu0)
            if best_sample is None or sample > best_sample:
                best_sample = sample
                chosen = [item]
            elif sample == best_sample:
                chosen.append(item)
        top, sample, _mu, pulls, feats, mu0 = draw.choice(chosen)
        sid = _real_share_id(top)
        if not sid:
            blocked.append(
                {
                    "count": len(group),
                    "job": job,
                    "reason": "no share_id",
                }
            )
            continue
        winners.append(
            {
                "added_at": top.get("added_at"),
                "features": feats,
                "has_approval_language": bool(top.get("has_approval_language")),
                "job": job,
                "name": top.get("name"),
                "name_key": _row_name_key(top),
                "origin": top.get("origin"),
                "prompt_chars": _prompt_chars(top),
                "pulls": pulls,
                "sample": sample,
                "score": mu0,
                "share_id": sid,
            }
        )
    return {
        "blocked": blocked,
        "selector": selector_meta(when),
        "winners": winners,
    }


def _table_names(conn: sqlite3.Connection) -> set:
    return {str(r[0]) for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def read_bandit_outcomes(path: pathlib.Path) -> List[Dict[str, Any]]:
    """Copy outcome rows from an existing cache. Missing table → empty, not a guess."""
    if not path.is_file():
        return []
    try:
        conn = sqlite3.connect("file:%s?mode=ro" % path.as_posix(), uri=True)
    except sqlite3.Error:
        return []
    try:
        if "bandit_outcomes" not in _table_names(conn):
            return []
        conn.row_factory = sqlite3.Row
        return [
            {
                "job": row["job"],
                "name_key": row["name_key"],
                "recorded_at": row["recorded_at"],
                "reward": float(row["reward"]),
                "share_id": row["share_id"],
            }
            for row in conn.execute(
                "SELECT name_key, job, share_id, reward, recorded_at "
                "FROM bandit_outcomes ORDER BY id"
            )
        ]
    except sqlite3.Error:
        return []
    finally:
        conn.close()


def load_outcomes(path: pathlib.Path) -> Dict[str, List[float]]:
    by: Dict[str, List[float]] = {}
    for row in read_bandit_outcomes(path):
        by.setdefault(str(row["name_key"]), []).append(float(row["reward"]))
    return by


def load_posteriors(path: pathlib.Path) -> Dict[str, Dict[str, Any]]:
    err = refuse_path(path)
    if err:
        raise CacheRefused(err)
    conn = sqlite3.connect("file:%s?mode=ro" % path.as_posix(), uri=True)
    conn.row_factory = sqlite3.Row
    try:
        apply_pragmas(conn)
        if "bandit_posterior" not in _table_names(conn):
            return {}
        out: Dict[str, Dict[str, Any]] = {}
        for row in conn.execute(
            "SELECT name_key, job, share_id, mu, variance, pulls, reward_sum "
            "FROM bandit_posterior"
        ):
            out[str(row["name_key"])] = {
                "job": row["job"],
                "mu": float(row["mu"]),
                "name_key": row["name_key"],
                "pulls": int(row["pulls"] or 0),
                "reward_sum": float(row["reward_sum"] or 0),
                "share_id": row["share_id"],
                "variance": float(row["variance"]),
            }
        return out
    finally:
        conn.close()


def _upsert_posterior(
    conn: sqlite3.Connection,
    row: Dict[str, Any],
    as_of: dt.date,
    rewards: Sequence[float],
) -> Dict[str, Any]:
    sid = _real_share_id(row)
    if not sid:
        raise CacheRefused("gb-market-db: refuse outcome — arm has no share_id")
    job = str(row.get("job") or "none")
    if job in ("", "none"):
        raise CacheRefused("gb-market-db: refuse outcome — job none is not an arm")
    feats = arm_features(row, as_of)
    mu, var, pulls, reward_sum = fold_outcomes(linear_score(feats), rewards)
    key = _row_name_key(row)
    conn.execute(
        """
        INSERT INTO bandit_posterior (
          name_key, job, share_id, mu, variance, pulls, reward_sum
        ) VALUES (?,?,?,?,?,?,?)
        ON CONFLICT(name_key) DO UPDATE SET
          job=excluded.job,
          share_id=excluded.share_id,
          mu=excluded.mu,
          variance=excluded.variance,
          pulls=excluded.pulls,
          reward_sum=excluded.reward_sum
        """,
        (key, job, sid, mu, var, pulls, reward_sum),
    )
    return {
        "job": job,
        "mu": mu,
        "name_key": key,
        "pulls": pulls,
        "reward_sum": reward_sum,
        "share_id": sid,
        "variance": var,
    }


def _recompute_posteriors(
    conn: sqlite3.Connection,
    bots: Sequence[Dict[str, Any]],
    outcome_rows: Sequence[Dict[str, Any]],
    as_of: dt.date,
) -> None:
    rewards: Dict[str, List[float]] = {}
    for item in outcome_rows:
        rewards.setdefault(str(item["name_key"]), []).append(float(item["reward"]))
    by_key = {_row_name_key(r): r for r in bots}
    for key, ys in rewards.items():
        row = by_key.get(key)
        if row is None or not _real_share_id(row):
            continue
        job = str(row.get("job") or "none")
        if job in ("", "none"):
            continue
        _upsert_posterior(conn, row, as_of, ys)


def record_outcome(
    path: pathlib.Path,
    *,
    name_key: str,
    reward: float,
    recorded_at: Optional[str] = None,
    as_of: Optional[dt.date] = None,
) -> Dict[str, Any]:
    """Append one recorded outcome and refresh that arm's posterior. Not a deploy."""
    err = refuse_path(path)
    if err:
        raise CacheRefused(err)
    when = as_of or dt.date.today()
    stamp = recorded_at or dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()
    key = " ".join(str(name_key or "").casefold().split())
    lock = _lock(path.parent / LOCK_NAME)
    try:
        conn = sqlite3.connect(str(path))
        conn.row_factory = sqlite3.Row
        try:
            apply_pragmas(conn, write=True)
            found = conn.execute("SELECT * FROM bots WHERE name_key = ?", (key,)).fetchone()
            if found is None:
                raise CacheRefused("gb-market-db: refuse outcome — unknown name_key %r" % key)
            integ = json.loads(found["integrations_json"] or "[]")
            row = {
                "added_at": found["added_at"],
                "has_approval_language": bool(found["has_approval_language"]),
                "job": found["job"],
                "name": found["name"],
                "name_key": found["name_key"],
                "origin": found["origin"],
                "prompt_chars": found["prompt_chars"],
                "share_id": found["share_id"],
                "integrations": integ,
            }
            sid = _real_share_id(row)
            if not sid:
                raise CacheRefused("gb-market-db: refuse outcome — arm has no share_id")
            job = str(row.get("job") or "none")
            if job in ("", "none"):
                raise CacheRefused("gb-market-db: refuse outcome — job none is not an arm")
            conn.execute(
                "INSERT INTO bandit_outcomes (name_key, job, share_id, reward, recorded_at) "
                "VALUES (?,?,?,?,?)",
                (key, job, sid, float(reward), stamp),
            )
            rewards = [
                float(r[0])
                for r in conn.execute(
                    "SELECT reward FROM bandit_outcomes WHERE name_key = ? ORDER BY id",
                    (key,),
                )
            ]
            posted = _upsert_posterior(conn, row, when, rewards)
            conn.commit()
            ok, text = integrity_ok(conn)
            if not ok:
                raise CacheRefused("integrity_check after outcome: %s" % text)
            return posted
        finally:
            conn.close()
    finally:
        try:
            import fcntl

            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
        except Exception:
            pass
        lock.close()


class CacheRefused(Exception):
    """integrity_check failed, user_version mismatch, or the file is not a database."""


def name_key(name: Any) -> str:
    return " ".join(str(name or "").casefold().split())


def canonicalize_row(row: Dict[str, Any], *, origin_hint: str = "") -> Dict[str, Any]:
    """Every cache row has the same keys. share_id is passed through, never invented."""
    origin = str(row.get("origin") or origin_hint or "")
    if not origin:
        if row.get("from_shares") and row.get("share_id") and row.get("prompt_chars"):
            origin = "both"
        elif row.get("from_shares"):
            origin = "shares"
        else:
            origin = "directory"
    if row.get("share_id") and origin == "directory":
        origin = "both"
    integ = row.get("integrations") or []
    if not isinstance(integ, list):
        integ = []
    charter = str(row.get("charter") or "")
    sid = row.get("share_id") or None
    if sid in ("", None):
        sid = None
    else:
        sid = str(sid)
    return {
        "added_at": str(row.get("added_at") or "")[:10] or None,
        "category": row.get("category") or "Uncategorised",
        "category_src": row.get("category_src") or origin,
        "taxonomy": taxonomy_of(row.get("category") or "Uncategorised"),
        "job": job_of(taxonomy_of(row.get("category") or "Uncategorised")),
        "charter": charter,
        "charter_chars": len(charter),
        "contributor": row.get("contributor"),
        "has_approval_language": 1 if row.get("has_approval_language") else 0,
        "import_url": row.get("import_url") or None,
        "integrations": [str(x) for x in integ if x],
        "name": row.get("name"),
        "name_key": name_key(row.get("name")),
        "origin": origin,
        "prompt_chars": int(row.get("prompt_chars") or 0),
        "share_id": sid,
        "source": row.get("source"),
    }


def _pragma(conn: sqlite3.Connection, sql: str) -> List[tuple]:
    cur = conn.execute(sql)
    rows = list(cur.fetchall())
    cur.close()
    return rows


def integrity_ok(conn: sqlite3.Connection) -> Tuple[bool, str]:
    rows = _pragma(conn, "PRAGMA integrity_check")
    text = rows[0][0] if rows else ""
    return text == "ok", str(text)


def user_version(conn: sqlite3.Connection) -> int:
    rows = _pragma(conn, "PRAGMA user_version")
    return int(rows[0][0]) if rows else -1


def apply_pragmas(conn: sqlite3.Connection, *, write: bool = False) -> None:
    conn.execute("PRAGMA busy_timeout = 5000")
    if write:
        # Full-file rebuild + os.replace. WAL sidecars would be orphaned on replace.
        # One writer. Do not claim fsqlite MVCC or concurrent WAL.
        mode = _pragma(conn, "PRAGMA journal_mode = DELETE")
        if not mode or str(mode[0][0]).lower() != "delete":
            raise CacheRefused("journal_mode did not read back as delete: %r" % (mode,))
        conn.execute("PRAGMA synchronous = FULL")
        syn = _pragma(conn, "PRAGMA synchronous")
        if not syn or int(syn[0][0]) < 1:
            raise CacheRefused("synchronous did not read back: %r" % (syn,))
    conn.execute("PRAGMA foreign_keys = ON")
    fk = _pragma(conn, "PRAGMA foreign_keys")
    if not fk or int(fk[0][0]) != 1:
        raise CacheRefused("foreign_keys did not read back as 1: %r" % (fk,))


def refuse_path(path: pathlib.Path) -> Optional[str]:
    """None if the cache is usable. A sentence if it must not be read or repaired in place."""
    if not path.is_file():
        return "gb-market-db: no cache at %s" % path
    try:
        conn = sqlite3.connect("file:%s?mode=ro" % path.as_posix(), uri=True)
    except sqlite3.Error as e:
        return "gb-market-db: refuse %s — %s" % (path.name, e)
    try:
        apply_pragmas(conn)
        ok, text = integrity_ok(conn)
        if not ok:
            return "gb-market-db: refuse %s — integrity_check=%s" % (path.name, text)
        ver = user_version(conn)
        if ver != USER_VERSION:
            return (
                "gb-market-db: refuse %s — user_version=%s want %s"
                % (path.name, ver, USER_VERSION)
            )
        n = conn.execute("SELECT COUNT(*) FROM bots").fetchone()[0]
        if int(n) < 1:
            return "gb-market-db: refuse %s — empty bots table is not a corpus" % path.name
    except sqlite3.Error as e:
        return "gb-market-db: refuse %s — %s" % (path.name, e)
    except CacheRefused as e:
        return "gb-market-db: refuse %s — %s" % (path.name, e)
    finally:
        conn.close()
    return None


def _lock(lock_path: pathlib.Path):
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    fh = open(lock_path, "a+")
    try:
        import fcntl

        fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as e:
        fh.close()
        raise CacheRefused("gb-market-db: cache locked — %s" % e)
    except ImportError:
        pass
    return fh


def rebuild(
    path: pathlib.Path,
    bots: Sequence[Dict[str, Any]],
    links: Sequence[Dict[str, Any]],
    meta: Optional[Dict[str, str]] = None,
) -> pathlib.Path:
    """Write a new cache next to path, verify, then replace. Never repair in place."""
    if not bots:
        raise CacheRefused("gb-market-db: refuse write — empty bots is not a corpus")
    path.parent.mkdir(parents=True, exist_ok=True)
    prior_outcomes = read_bandit_outcomes(path)
    lock = _lock(path.parent / LOCK_NAME)
    tmp: Optional[pathlib.Path] = None
    try:
        fd, raw = tempfile.mkstemp(prefix="market.", suffix=".sqlite.next", dir=str(path.parent))
        os.close(fd)
        tmp = pathlib.Path(raw)
        conn = sqlite3.connect(str(tmp))
        try:
            apply_pragmas(conn, write=True)
            conn.executescript(SCHEMA_SQL)
            conn.execute("PRAGMA user_version = %d" % USER_VERSION)
            got = user_version(conn)
            if got != USER_VERSION:
                raise CacheRefused("user_version wrote %s read back %s" % (USER_VERSION, got))
            written: List[Dict[str, Any]] = []
            for row in bots:
                c = canonicalize_row(dict(row))
                if not c["name_key"] or not c["name"]:
                    continue
                written.append(c)
                conn.execute(
                    """
                    INSERT INTO bots (
                      name_key, name, category, category_src, taxonomy, job, share_id, import_url,
                      charter, charter_chars, contributor, source, added_at, origin,
                      has_approval_language, prompt_chars, integrations_json
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        c["name_key"],
                        c["name"],
                        c["category"],
                        c["category_src"],
                        c["taxonomy"],
                        c["job"],
                        c["share_id"],
                        c["import_url"],
                        c["charter"],
                        c["charter_chars"],
                        c["contributor"],
                        c["source"],
                        c["added_at"],
                        c["origin"],
                        c["has_approval_language"],
                        c["prompt_chars"],
                        json.dumps(c["integrations"], separators=(",", ":")),
                    ),
                )
            seen = set()
            for link in links:
                url = str(link.get("url") or "")
                if not url or url in seen:
                    continue
                seen.add(url)
                conn.execute(
                    "INSERT INTO links (url, title, section, description) VALUES (?,?,?,?)",
                    (
                        url,
                        link.get("title"),
                        link.get("section"),
                        link.get("description"),
                    ),
                )
            for k, v in (meta or {}).items():
                conn.execute("INSERT INTO meta (k, v) VALUES (?,?)", (str(k), str(v)))
            for item in prior_outcomes:
                conn.execute(
                    "INSERT INTO bandit_outcomes (name_key, job, share_id, reward, recorded_at) "
                    "VALUES (?,?,?,?,?)",
                    (
                        item["name_key"],
                        item["job"],
                        item["share_id"],
                        float(item["reward"]),
                        item["recorded_at"],
                    ),
                )
            _recompute_posteriors(
                conn,
                written,
                prior_outcomes,
                dt.date.today(),
            )
            conn.commit()
            ok, text = integrity_ok(conn)
            if not ok:
                raise CacheRefused("integrity_check after rebuild: %s" % text)
            n = conn.execute("SELECT COUNT(*) FROM bots").fetchone()[0]
            if int(n) < 1:
                raise CacheRefused("rebuild wrote zero bots")
        finally:
            conn.close()
        os.replace(str(tmp), str(path))
        tmp = None
        err = refuse_path(path)
        if err:
            raise CacheRefused(err)
        return path
    finally:
        if tmp is not None and tmp.exists():
            tmp.unlink()
        try:
            import fcntl

            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
        except Exception:
            pass
        lock.close()


def load_bots(path: pathlib.Path) -> List[Dict[str, Any]]:
    err = refuse_path(path)
    if err:
        raise CacheRefused(err)
    conn = sqlite3.connect("file:%s?mode=ro" % path.as_posix(), uri=True)
    conn.row_factory = sqlite3.Row
    try:
        apply_pragmas(conn)
        out: List[Dict[str, Any]] = []
        for row in conn.execute(
            "SELECT * FROM bots ORDER BY name COLLATE NOCASE, origin"
        ):
            integ = json.loads(row["integrations_json"] or "[]")
            out.append(
                {
                    "added_at": row["added_at"],
                    "category": row["category"],
                    "category_src": row["category_src"],
                    "taxonomy": row["taxonomy"],
                    "job": row["job"],
                    "charter": row["charter"] or "",
                    "contributor": row["contributor"],
                    "has_approval_language": bool(row["has_approval_language"]),
                    "import_url": row["import_url"],
                    "integrations": integ,
                    "name": row["name"],
                    "name_key": row["name_key"],
                    "origin": row["origin"],
                    "prompt_chars": row["prompt_chars"],
                    "share_id": row["share_id"],
                    "source": row["source"],
                }
            )
        return out
    finally:
        conn.close()


def load_links(path: pathlib.Path) -> List[Dict[str, Any]]:
    err = refuse_path(path)
    if err:
        raise CacheRefused(err)
    conn = sqlite3.connect("file:%s?mode=ro" % path.as_posix(), uri=True)
    conn.row_factory = sqlite3.Row
    try:
        apply_pragmas(conn)
        return [
            {
                "description": row["description"] or "",
                "section": row["section"] or "",
                "title": row["title"],
                "url": row["url"],
            }
            for row in conn.execute("SELECT * FROM links ORDER BY section, title, url")
        ]
    finally:
        conn.close()


def selftest() -> int:
    legs: List[Tuple[str, bool, str]] = []

    def check(name: str, ok: bool, detail: str = "") -> None:
        legs.append((name, bool(ok), detail))

    with tempfile.TemporaryDirectory(prefix="gb-market-db-") as tmp:
        root = pathlib.Path(tmp)
        good = root / DB_NAME
        bots = [
            {
                "name": "Alpha Desk",
                "category": "Ops",
                "charter": "from dir",
                "contributor": "ada",
                "source": "https://x.com/ada/status/1",
                "added_at": "2026-09-01",
                "prompt_chars": 12,
                "integrations": ["Gmail"],
                "has_approval_language": True,
            },
            {
                "name": "alpha desk",
                "category": "ops",
                "share_id": "realShare1",
                "import_url": "https://x.ai/bot/realShare1",
                "charter": "from share",
                "from_shares": True,
                "added_at": "2026-09-10",
            },
            {
                "name": "Gamma Desk",
                "category": "Sales",
                "share_id": "g1",
                "import_url": "https://x.ai/bot/g1",
                "charter": "only share",
                "from_shares": True,
                "added_at": "2026-09-11",
            },
        ]
        # caller merges first; here we write already-merged rows
        merged = [
            canonicalize_row(bots[0]),
        ]
        merged[0]["share_id"] = "realShare1"
        merged[0]["import_url"] = "https://x.ai/bot/realShare1"
        merged[0]["origin"] = "both"
        merged.append(canonicalize_row(bots[2], origin_hint="shares"))
        rebuild(
            good,
            merged,
            [{"title": "Guide", "url": "https://x.com/a/status/1", "section": "X", "description": "a"}],
            {"corpus_repo": "elie222/botdirectory.ai"},
        )
        loaded = load_bots(good)
        check("rebuild-count", len(loaded) == 2, str(len(loaded)))
        by = {r["name_key"]: r for r in loaded}
        check(
            "share-id-passthrough",
            by["alpha desk"]["share_id"] == "realShare1"
            and by["gamma desk"]["share_id"] == "g1",
            str(by),
        )
        check(
            "does-not-invent-share-id",
            all(r["share_id"] in ("realShare1", "g1") for r in loaded),
            str([r["share_id"] for r in loaded]),
        )
        check("dir-charter-wins-if-caller-merged", by["alpha desk"]["charter"] == "from dir", by["alpha desk"]["charter"])
        conn = sqlite3.connect(str(good))
        try:
            check("user_version-readback", user_version(conn) == USER_VERSION, str(user_version(conn)))
            ok, text = integrity_ok(conn)
            check("integrity-ok", ok, text)
            mode = _pragma(conn, "PRAGMA journal_mode")
            check("journal-delete-readback", bool(mode) and str(mode[0][0]).lower() == "delete", str(mode))
        finally:
            conn.close()
        check("refuse-path-clean", refuse_path(good) is None, str(refuse_path(good)))

        planted = root / "planted.sqlite"
        planted.write_bytes(b"this is not a sqlite database\n")
        msg = refuse_path(planted)
        check(
            "planted-malformed-refused",
            msg is not None and "refuse" in msg,
            str(msg),
        )
        try:
            load_bots(planted)
            check("load-malformed-raises", False, "raised nothing")
        except CacheRefused as e:
            check("load-malformed-raises", "refuse" in str(e), str(e))

        empty = root / "empty.sqlite"
        try:
            rebuild(empty, [], [])
            check("empty-bots-refused", False, "wrote empty")
        except CacheRefused as e:
            check("empty-bots-refused", "empty" in str(e).lower(), str(e))

        stale = root / "stale.sqlite"
        conn = sqlite3.connect(str(stale))
        try:
            conn.executescript(SCHEMA_SQL)
            conn.execute("PRAGMA user_version = 0")
            conn.execute(
                "INSERT INTO bots (name_key, name, origin, taxonomy, job) VALUES ('x','X','directory','ops','operate')"
            )
            conn.commit()
        finally:
            conn.close()
        msg = refuse_path(stale)
        check(
            "stale-user-version-refused",
            msg is not None and "user_version" in msg,
            str(msg),
        )

        check(
            "personal-aliases-same-taxonomy",
            taxonomy_of("Personal") == "personal" and taxonomy_of("personal-admin") == "personal",
            "%s %s" % (taxonomy_of("Personal"), taxonomy_of("personal-admin")),
        )
        check(
            "sales-aliases-same-taxonomy",
            taxonomy_of("Sales") == "sales" and taxonomy_of("customer-sales") == "sales",
            "%s %s" % (taxonomy_of("Sales"), taxonomy_of("customer-sales")),
        )
        check(
            "unknown-category-is-unmapped",
            taxonomy_of("MadeUp Drawer") == "unmapped",
            taxonomy_of("MadeUp Drawer"),
        )
        check(
            "does-not-guess-from-charter",
            taxonomy_of("Keeps a coding agent iterating") == "unmapped",
            taxonomy_of("Keeps a coding agent iterating"),
        )
        loaded_tax = {r["name_key"]: r["taxonomy"] for r in loaded}
        check("ops-maps-on-row", loaded_tax.get("alpha desk") == "ops", str(loaded_tax))
        check("sales-maps-on-row", loaded_tax.get("gamma desk") == "sales", str(loaded_tax))
        weird = canonicalize_row({"name": "Zed", "category": "Brand New Shelf", "from_shares": True})
        check(
            "upstream-category-preserved",
            weird["category"] == "Brand New Shelf" and weird["taxonomy"] == "unmapped",
            str(weird),
        )
        check("research-is-brief", job_of("research") == "brief", job_of("research"))
        check("personal-has-no-job", job_of("personal") == "none", job_of("personal"))
        check("productivity-has-no-job", job_of("productivity") == "none", job_of("productivity"))
        check("success-has-no-job", job_of("success") == "none", job_of("success"))
        named = canonicalize_row({"name": "Morning Briefing", "category": "Personal"})
        check(
            "name-does-not-assign-job",
            named["job"] == "none" and named["taxonomy"] == "personal",
            str(named),
        )
        check("ops-job-on-row", loaded[0]["job"] == "operate", str(loaded[0]))
        check("sales-job-on-row", [r["job"] for r in loaded if r["name_key"]=="gamma desk"] == ["sell"], str(loaded))

        missing = refuse_path(root / "nope.sqlite")
        check("missing-is-refused", missing is not None and "no cache" in missing, str(missing))

        # job_winners: one deployable pick per job. Name alpha is not a ranking key.
        no_share_best = canonicalize_row(
            {
                "name": "Aaa Best",
                "category": "Ops",
                "origin": "both",
                "prompt_chars": 9999,
                "has_approval_language": True,
                "added_at": "2026-12-31",
                "charter": "looks like the winner if share_id is ignored",
            }
        )
        weak_share = canonicalize_row(
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
        )
        shares_first = canonicalize_row(
            {
                "name": "Aaa Shares",
                "category": "Ops",
                "share_id": "aaaShare",
                "from_shares": True,
                "origin": "shares",
                "prompt_chars": 999,
                "has_approval_language": True,
                "added_at": "2026-12-31",
            }
        )
        both_later = canonicalize_row(
            {
                "name": "Zzz Both",
                "category": "Ops",
                "share_id": "zzzShare",
                "origin": "both",
                "prompt_chars": 1,
                "has_approval_language": False,
                "added_at": "2020-01-01",
            }
        )
        personal = canonicalize_row(
            {
                "name": "Aaa Personal",
                "category": "Personal",
                "share_id": "persShare",
                "origin": "both",
                "prompt_chars": 5000,
                "has_approval_language": True,
                "added_at": "2026-12-31",
            }
        )
        sales_ghost = canonicalize_row(
            {
                "name": "Aaa Sales Ghost",
                "category": "Sales",
                "origin": "directory",
                "prompt_chars": 800,
                "added_at": "2026-12-31",
            }
        )
        picked = job_winners(
            [no_share_best, weak_share, personal, sales_ghost]
        )
        win_by_job = {w["job"]: w for w in picked["winners"]}
        blocked_by_job = {b["job"]: b for b in picked["blocked"]}
        check(
            "no-share-cannot-win",
            win_by_job.get("operate", {}).get("share_id") == "weakShare"
            and win_by_job.get("operate", {}).get("name") == "Zzz Weak"
            and all(w.get("share_id") for w in picked["winners"]),
            str(picked),
        )
        both_pick = job_winners(
            [shares_first, both_later, personal],
            rng=random.Random(0),
            as_of=dt.date(2026, 9, 13),
        )
        operate = {w["job"]: w for w in both_pick["winners"]}.get("operate") or {}
        check(
            "selector-is-not-lexicographic-sort",
            operate.get("share_id") in ("zzzShare", "aaaShare")
            and operate.get("share_id")
            and both_pick.get("selector", {}).get("method") == "thompson"
            and both_pick.get("selector", {}).get("predictor") == "linear",
            str(both_pick),
        )
        check(
            "none-never-a-winner",
            all(w.get("job") != "none" for w in picked["winners"])
            and all(w.get("job") != "none" for w in both_pick["winners"])
            and "none" not in blocked_by_job
            and personal["job"] == "none",
            str(picked),
        )
        check(
            "no-share-job-is-blocked",
            "sell" in blocked_by_job
            and blocked_by_job["sell"].get("count") == 1
            and blocked_by_job["sell"].get("reason")
            and "sell" not in win_by_job
            and all(w.get("share_id") for w in picked["winners"]),
            str(picked),
        )
        check(
            "does-not-invent-decide-refuse",
            all(w.get("job") not in ("decide", "refuse") for w in picked["winners"])
            and all(b.get("job") not in ("decide", "refuse") for b in picked["blocked"]),
            str(picked),
        )

        as_of = __import__("datetime").date(2026, 9, 13)
        same_a = canonicalize_row(
            {
                "name": "Aaa Same",
                "category": "Ops",
                "share_id": "aaaSame",
                "origin": "both",
                "prompt_chars": 100,
                "has_approval_language": True,
                "added_at": "2026-08-01",
            }
        )
        same_z = canonicalize_row(
            {
                "name": "Zzz Same",
                "category": "Ops",
                "share_id": "zzzSame",
                "origin": "both",
                "prompt_chars": 100,
                "has_approval_language": True,
                "added_at": "2026-08-01",
            }
        )
        same_feats = arm_features(same_a, as_of)
        check(
            "features-measured-only",
            set(same_feats) == {"approval", "both", "log1p_prompt_chars", "recency_days"}
            and same_feats == arm_features(same_z, as_of)
            and "name" not in same_feats
            and "charter" not in same_feats,
            str(same_feats),
        )
        same_names = set()
        for seed in range(40):
            drawn = job_winners([same_a, same_z], rng=__import__("random").Random(seed), as_of=as_of)
            operate = [w for w in drawn["winners"] if w.get("job") == "operate"]
            if operate:
                same_names.add(operate[0].get("name"))
        check(
            "same-features-not-name-alpha",
            same_names == {"Aaa Same", "Zzz Same"},
            str(same_names),
        )
        mixed_names = set()
        for seed in range(80):
            drawn = job_winners(
                [shares_first, both_later],
                rng=__import__("random").Random(seed),
                as_of=as_of,
            )
            operate = [w for w in drawn["winners"] if w.get("job") == "operate"]
            if operate:
                mixed_names.add(operate[0].get("name"))
        check(
            "no-outcome-not-frozen-ranking",
            mixed_names == {"Aaa Shares", "Zzz Both"},
            str(mixed_names),
        )

        bandit_db = root / "bandit.sqlite"
        rebuild(bandit_db, [shares_first, both_later, no_share_best], [])
        check("posterior-starts-empty", load_posteriors(bandit_db) == {}, str(load_posteriors(bandit_db)))
        before_mu = linear_score(arm_features(shares_first, as_of))
        record_outcome(
            bandit_db,
            name_key="aaa shares",
            reward=1.0,
            recorded_at="2026-09-13T00:00:00+00:00",
            as_of=as_of,
        )
        posts = load_posteriors(bandit_db)
        aaa_post = posts.get("aaa shares") or {}
        check(
            "posterior-updates-on-outcome",
            aaa_post.get("pulls") == 1
            and aaa_post.get("reward_sum") == 1.0
            and aaa_post.get("share_id") == "aaaShare"
            and aaa_post.get("mu") is not None
            and abs(float(aaa_post["mu"]) - before_mu) > 1e-12,
            str(posts),
        )
        try:
            record_outcome(bandit_db, name_key="aaa best", reward=1.0, as_of=as_of)
            check("outcome-refuses-no-share", False, "wrote outcome for no-share arm")
        except CacheRefused as e:
            check("outcome-refuses-no-share", "share_id" in str(e).lower(), str(e))
        rebuild(bandit_db, [shares_first, both_later, no_share_best], [])
        kept = load_posteriors(bandit_db)
        check(
            "rebuild-keeps-outcomes",
            (kept.get("aaa shares") or {}).get("pulls") == 1,
            str(kept),
        )

    failed = [n for n, ok, _ in legs if not ok]
    for name, ok, detail in legs:
        mark = "ok  " if ok else "FAIL"
        print("  %s %s%s" % (mark, name, (" — " + detail[:200]) if (detail and not ok) else ""))
    print("SELFTEST %s - %d/%d" % ("FAIL" if failed else "PASS", len(legs) - len(failed), len(legs)))
    return 1 if failed else 0


def body(argv: Optional[Sequence[str]] = None) -> int:
    import argparse

    ap = argparse.ArgumentParser(prog="gb-market-db.py")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args(list(argv) if argv is not None else None)
    if args.selftest:
        return selftest()
    print("gb-market-db: rebuild happens from gb-usecases.py; --selftest is the gate", file=sys.stderr)
    return 2


if __name__ == "__main__":
    gbmain(lambda: body())
