#!/usr/bin/env python3
"""gb-market-bandit — Thompson sampling over market job arms.

Stock sqlite3 (stdlib). Not fsqlite. Posterior is a SEPARATE file from the
catalog cache: usecases/bandit.sqlite. Catalog rebuild must not wipe it (C49).
Arm identity is job + name_key + share_id (C106). Same name, different
share_id is a different arm.

Prior is Beta(1,1) per arm. A draw is two Gamma(shape, 1) samples:
θ = Ga(α,1) / (Ga(α,1) + Ga(β,1)). hit += α, miss += β. Select does not
invent a reward. Features on a catalog row are display fields only.

See docs/MARKET-SYNC-STRATEGY.md.
"""
from __future__ import annotations

import datetime as dt
import os
import pathlib
import random
import sqlite3
import sys
import tempfile
from typing import Any, Dict, List, Optional, Sequence, Tuple

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from gbtypes import main as gbmain  # noqa: E402

USER_VERSION = 2
DB_NAME = "bandit.sqlite"
LOCK_NAME = "bandit.sqlite.lock"
PRODUCT_DIR = "usecases"
PRIOR_ALPHA = 1.0
PRIOR_BETA = 1.0
SELECTOR_METHOD = "thompson"
KIND_IMPRESSION = "impression"
KIND_KEEP = "keep"
KIND_SKIP = "skip"
KIND_BAN = "ban"

SCHEMA_SQL = """
CREATE TABLE arms (
  job TEXT NOT NULL,
  name_key TEXT NOT NULL,
  share_id TEXT NOT NULL,
  alpha REAL NOT NULL DEFAULT 1,
  beta REAL NOT NULL DEFAULT 1,
  pulls INTEGER NOT NULL DEFAULT 0,
  hits INTEGER NOT NULL DEFAULT 0,
  misses INTEGER NOT NULL DEFAULT 0,
  impressions INTEGER NOT NULL DEFAULT 0,
  banned INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (job, name_key, share_id)
);
CREATE TABLE outcomes (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  job TEXT NOT NULL,
  name_key TEXT NOT NULL,
  share_id TEXT NOT NULL,
  hit INTEGER,
  kind TEXT NOT NULL,
  recorded_at TEXT NOT NULL
);
CREATE TABLE meta (
  k TEXT PRIMARY KEY,
  v TEXT NOT NULL
);
"""


class StoreRefused(Exception):
    """integrity_check failed, user_version mismatch, or the file is not a database."""


def store_path(root: pathlib.Path) -> pathlib.Path:
    """Product posterior. Never /tmp. Catalog rebuild does not touch this file."""
    return pathlib.Path(root) / PRODUCT_DIR / DB_NAME


def name_key(name: Any) -> str:
    return " ".join(str(name or "").casefold().split())


def real_share_id(row: Dict[str, Any]) -> Optional[str]:
    sid = row.get("share_id")
    if sid in (None, ""):
        return None
    text = str(sid).strip()
    return text or None


def _arm_id(job: str, key: str, share: str) -> Tuple[str, str, str]:
    return (str(job), name_key(key), str(share))


def thompson_sample(alpha: float, beta: float, rng: random.Random) -> float:
    """Beta(α, β) via two Gamma(shape, scale=1) draws. Not a feature score."""
    a = float(alpha)
    b = float(beta)
    if a <= 0 or b <= 0:
        raise StoreRefused("gb-market-bandit: Beta parameters must be positive")
    x = rng.gammavariate(a, 1.0)
    y = rng.gammavariate(b, 1.0)
    total = x + y
    if total <= 0.0:
        return 0.5
    return x / total


def selector_meta() -> Dict[str, Any]:
    return {
        "method": SELECTOR_METHOD,
        "prior": "beta(1,1)",
        "store": "%s/%s" % (PRODUCT_DIR, DB_NAME),
    }


def display_fields(row: Dict[str, Any]) -> Dict[str, Any]:
    """Catalog columns for printing. Not ranking keys. Not a prior mean."""
    return {
        "added_at": row.get("added_at"),
        "has_approval_language": bool(row.get("has_approval_language")),
        "origin": row.get("origin"),
        "prompt_chars": row.get("prompt_chars"),
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
        mode = _pragma(conn, "PRAGMA journal_mode = DELETE")
        if not mode or str(mode[0][0]).lower() != "delete":
            raise StoreRefused("journal_mode did not read back as delete: %r" % (mode,))
        conn.execute("PRAGMA synchronous = FULL")
        syn = _pragma(conn, "PRAGMA synchronous")
        if not syn or int(syn[0][0]) < 1:
            raise StoreRefused("synchronous did not read back: %r" % (syn,))
    conn.execute("PRAGMA foreign_keys = ON")
    fk = _pragma(conn, "PRAGMA foreign_keys")
    if not fk or int(fk[0][0]) != 1:
        raise StoreRefused("foreign_keys did not read back as 1: %r" % (fk,))


def refuse_path(path: pathlib.Path) -> Optional[str]:
    """None if missing (cold start) or usable. A sentence if the file must not be read."""
    if not path.is_file():
        return None
    try:
        conn = sqlite3.connect("file:%s?mode=ro" % path.as_posix(), uri=True)
    except sqlite3.Error as e:
        return "gb-market-bandit: refuse %s — %s" % (path.name, e)
    try:
        apply_pragmas(conn)
        ok, text = integrity_ok(conn)
        if not ok:
            return "gb-market-bandit: refuse %s — integrity_check=%s" % (path.name, text)
        ver = user_version(conn)
        if ver != USER_VERSION:
            return (
                "gb-market-bandit: refuse %s — user_version=%s want %s"
                % (path.name, ver, USER_VERSION)
            )
    except sqlite3.Error as e:
        return "gb-market-bandit: refuse %s — %s" % (path.name, e)
    except StoreRefused as e:
        return "gb-market-bandit: refuse %s — %s" % (path.name, e)
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
        raise StoreRefused("gb-market-bandit: store locked — %s" % e)
    except ImportError:
        pass
    return fh


def _create_empty(path: pathlib.Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lock = _lock(path.parent / LOCK_NAME)
    tmp: Optional[pathlib.Path] = None
    try:
        fd, raw = tempfile.mkstemp(prefix="bandit.", suffix=".sqlite.next", dir=str(path.parent))
        os.close(fd)
        tmp = pathlib.Path(raw)
        conn = sqlite3.connect(str(tmp))
        try:
            apply_pragmas(conn, write=True)
            conn.executescript(SCHEMA_SQL)
            conn.execute("PRAGMA user_version = %d" % USER_VERSION)
            got = user_version(conn)
            if got != USER_VERSION:
                raise StoreRefused("user_version wrote %s read back %s" % (USER_VERSION, got))
            conn.commit()
            ok, text = integrity_ok(conn)
            if not ok:
                raise StoreRefused("integrity_check after create: %s" % text)
        finally:
            conn.close()
        os.replace(str(tmp), str(path))
        tmp = None
        err = refuse_path(path)
        if err:
            raise StoreRefused(err)
    finally:
        if tmp is not None and tmp.exists():
            tmp.unlink()
        try:
            import fcntl

            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
        except Exception:
            pass
        lock.close()


def ensure_store(path: pathlib.Path) -> pathlib.Path:
    """Create an empty posterior file if missing. Caller chooses the path; default is usecases/."""
    err = refuse_path(path)
    if err:
        raise StoreRefused(err)
    if not path.is_file():
        _create_empty(path)
    return path


def load_posteriors(path: pathlib.Path) -> Dict[Tuple[str, str, str], Dict[str, Any]]:
    err = refuse_path(path)
    if err:
        raise StoreRefused(err)
    if not path.is_file():
        return {}
    conn = sqlite3.connect("file:%s?mode=ro" % path.as_posix(), uri=True)
    conn.row_factory = sqlite3.Row
    try:
        apply_pragmas(conn)
        out: Dict[Tuple[str, str, str], Dict[str, Any]] = {}
        for row in conn.execute(
            "SELECT job, name_key, share_id, alpha, beta, pulls, hits, misses, "
            "impressions, banned FROM arms"
        ):
            key = _arm_id(row["job"], row["name_key"], row["share_id"])
            out[key] = {
                "alpha": float(row["alpha"]),
                "beta": float(row["beta"]),
                "banned": bool(row["banned"]),
                "hits": int(row["hits"] or 0),
                "impressions": int(row["impressions"] or 0),
                "job": key[0],
                "misses": int(row["misses"] or 0),
                "name_key": key[1],
                "pulls": int(row["pulls"] or 0),
                "share_id": key[2],
            }
        return out
    finally:
        conn.close()


def _empty_arm(key: Tuple[str, str, str]) -> Dict[str, Any]:
    return {
        "alpha": PRIOR_ALPHA,
        "banned": False,
        "beta": PRIOR_BETA,
        "hits": 0,
        "impressions": 0,
        "job": key[0],
        "misses": 0,
        "name_key": key[1],
        "pulls": 0,
        "share_id": key[2],
    }


def _read_arm(conn: sqlite3.Connection, key: Tuple[str, str, str]) -> Optional[Dict[str, Any]]:
    found = conn.execute(
        "SELECT alpha, beta, pulls, hits, misses, impressions, banned FROM arms "
        "WHERE job = ? AND name_key = ? AND share_id = ?",
        key,
    ).fetchone()
    if not found:
        return None
    return {
        "alpha": float(found[0]),
        "banned": bool(found[6]),
        "beta": float(found[1]),
        "hits": int(found[3] or 0),
        "impressions": int(found[5] or 0),
        "job": key[0],
        "misses": int(found[4] or 0),
        "name_key": key[1],
        "pulls": int(found[2] or 0),
        "share_id": key[2],
    }


def _write_arm(conn: sqlite3.Connection, arm: Dict[str, Any]) -> None:
    conn.execute(
        """
        INSERT INTO arms (
          job, name_key, share_id, alpha, beta, pulls, hits, misses, impressions, banned
        ) VALUES (?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(job, name_key, share_id) DO UPDATE SET
          alpha=excluded.alpha,
          beta=excluded.beta,
          pulls=excluded.pulls,
          hits=excluded.hits,
          misses=excluded.misses,
          impressions=excluded.impressions,
          banned=excluded.banned
        """,
        (
            arm["job"],
            arm["name_key"],
            arm["share_id"],
            arm["alpha"],
            arm["beta"],
            arm["pulls"],
            arm["hits"],
            arm["misses"],
            arm["impressions"],
            1 if arm.get("banned") else 0,
        ),
    )


def latest_subject(path: pathlib.Path, share_id: str) -> Optional[Dict[str, Any]]:
    """Latest/only arm in the store for this share_id. Does not invent one."""
    sid = real_share_id({"share_id": share_id})
    if not sid:
        return None
    err = refuse_path(path)
    if err:
        raise StoreRefused(err)
    if not path.is_file():
        return None
    conn = sqlite3.connect("file:%s?mode=ro" % path.as_posix(), uri=True)
    try:
        apply_pragmas(conn)
        row = conn.execute(
            "SELECT job, name_key, share_id FROM outcomes WHERE share_id = ? "
            "ORDER BY id DESC LIMIT 1",
            (sid,),
        ).fetchone()
        if row is None:
            row = conn.execute(
                "SELECT job, name_key, share_id FROM arms WHERE share_id = ? "
                "ORDER BY impressions DESC, job, name_key LIMIT 1",
                (sid,),
            ).fetchone()
        if row is None:
            return None
        key = _arm_id(row[0], row[1], row[2])
        return _read_arm(conn, key) or _empty_arm(key)
    finally:
        conn.close()


def record_outcome(
    path: pathlib.Path,
    *,
    job: str,
    name_key: str,
    share_id: str,
    hit: bool,
    n: int = 1,
    recorded_at: Optional[str] = None,
) -> Dict[str, Any]:
    """Write n keep/skip outcomes. Select does not call this. Not an impression."""
    if n < 1:
        raise StoreRefused("gb-market-bandit: refuse outcome — n must be >= 1")
    sid = real_share_id({"share_id": share_id})
    if not sid:
        raise StoreRefused("gb-market-bandit: refuse outcome — arm has no share_id")
    job_s = str(job or "")
    if job_s in ("", "none"):
        raise StoreRefused("gb-market-bandit: refuse outcome — job none is not an arm")
    key = _arm_id(job_s, name_key, sid)
    stamp = recorded_at or dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()
    kind = KIND_KEEP if hit else KIND_SKIP
    ensure_store(path)
    lock = _lock(path.parent / LOCK_NAME)
    try:
        conn = sqlite3.connect(str(path))
        try:
            apply_pragmas(conn, write=True)
            arm = _read_arm(conn, key) or _empty_arm(key)
            conn.executemany(
                "INSERT INTO outcomes (job, name_key, share_id, hit, kind, recorded_at) "
                "VALUES (?,?,?,?,?,?)",
                [(key[0], key[1], key[2], 1 if hit else 0, kind, stamp) for _ in range(n)],
            )
            if hit:
                arm["alpha"] += n
                arm["hits"] += n
            else:
                arm["beta"] += n
                arm["misses"] += n
            arm["pulls"] += n
            _write_arm(conn, arm)
            conn.commit()
            ok, text = integrity_ok(conn)
            if not ok:
                raise StoreRefused("integrity_check after outcome: %s" % text)
            return arm
        finally:
            conn.close()
    finally:
        try:
            import fcntl

            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
        except Exception:
            pass
        lock.close()


def record_impression(
    path: pathlib.Path,
    *,
    job: str,
    name_key: str,
    share_id: str,
    recorded_at: Optional[str] = None,
) -> Dict[str, Any]:
    """A draw is an impression. Does not change alpha/beta. Not a keep/skip."""
    sid = real_share_id({"share_id": share_id})
    if not sid:
        raise StoreRefused("gb-market-bandit: refuse impression — arm has no share_id")
    job_s = str(job or "")
    if job_s in ("", "none"):
        raise StoreRefused("gb-market-bandit: refuse impression — job none is not an arm")
    key = _arm_id(job_s, name_key, sid)
    stamp = recorded_at or dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()
    ensure_store(path)
    lock = _lock(path.parent / LOCK_NAME)
    try:
        conn = sqlite3.connect(str(path))
        try:
            apply_pragmas(conn, write=True)
            arm = _read_arm(conn, key) or _empty_arm(key)
            arm["impressions"] = int(arm.get("impressions") or 0) + 1
            conn.execute(
                "INSERT INTO outcomes (job, name_key, share_id, hit, kind, recorded_at) "
                "VALUES (?,?,?,?,?,?)",
                (key[0], key[1], key[2], None, KIND_IMPRESSION, stamp),
            )
            _write_arm(conn, arm)
            conn.commit()
            ok, text = integrity_ok(conn)
            if not ok:
                raise StoreRefused("integrity_check after impression: %s" % text)
            return arm
        finally:
            conn.close()
    finally:
        try:
            import fcntl

            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
        except Exception:
            pass
        lock.close()


def apply_verdict(
    path: pathlib.Path,
    share_id: str,
    kind: str,
    recorded_at: Optional[str] = None,
) -> Dict[str, Any]:
    """keep / skip / ban the latest store subject for share_id. Never invents a share_id."""
    sid = real_share_id({"share_id": share_id})
    if not sid:
        raise StoreRefused("gb-market-bandit: refuse %s — no share_id" % kind)
    if kind not in (KIND_KEEP, KIND_SKIP, KIND_BAN):
        raise StoreRefused("gb-market-bandit: refuse — unknown verdict %r" % kind)
    subject = latest_subject(path, sid)
    if subject is None:
        raise StoreRefused("gb-market-bandit: refuse %s — not in store" % kind)
    if kind == KIND_KEEP:
        return record_outcome(
            path,
            job=subject["job"],
            name_key=subject["name_key"],
            share_id=sid,
            hit=True,
            recorded_at=recorded_at,
        )
    if kind == KIND_SKIP:
        return record_outcome(
            path,
            job=subject["job"],
            name_key=subject["name_key"],
            share_id=sid,
            hit=False,
            recorded_at=recorded_at,
        )
    stamp = recorded_at or dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()
    ensure_store(path)
    lock = _lock(path.parent / LOCK_NAME)
    try:
        conn = sqlite3.connect(str(path))
        try:
            apply_pragmas(conn, write=True)
            key = _arm_id(subject["job"], subject["name_key"], sid)
            arm = _read_arm(conn, key) or _empty_arm(key)
            arm["banned"] = True
            conn.execute(
                "INSERT INTO outcomes (job, name_key, share_id, hit, kind, recorded_at) "
                "VALUES (?,?,?,?,?,?)",
                (key[0], key[1], key[2], None, KIND_BAN, stamp),
            )
            _write_arm(conn, arm)
            conn.commit()
            ok, text = integrity_ok(conn)
            if not ok:
                raise StoreRefused("integrity_check after ban: %s" % text)
            return arm
        finally:
            conn.close()
    finally:
        try:
            import fcntl

            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
        except Exception:
            pass
        lock.close()


def pick_jobs(
    rows: Sequence[Dict[str, Any]],
    *,
    store: Optional[pathlib.Path] = None,
    posteriors: Optional[Dict[Tuple[str, str, str], Dict[str, Any]]] = None,
    rng: Optional[random.Random] = None,
) -> Dict[str, Any]:
    """One Thompson draw per job. Features are not a prior. Select writes nothing."""
    draw = rng or random.Random()
    if posteriors is None:
        if store is not None:
            err = refuse_path(store)
            if err:
                raise StoreRefused(err)
            posteriors = load_posteriors(store)
        else:
            posteriors = {}

    by_job: Dict[str, List[Dict[str, Any]]] = {}
    for row in rows:
        job = str(row.get("job") or "none")
        if job in ("", "none"):
            continue
        by_job.setdefault(job, []).append(dict(row))

    draws: List[Dict[str, Any]] = []
    blocked: List[Dict[str, Any]] = []
    for job in sorted(by_job):
        group = by_job[job]
        deployable = [r for r in group if real_share_id(r)]
        if not deployable:
            blocked.append({"count": len(group), "job": job, "reason": "no share_id"})
            continue
        best_sample: Optional[float] = None
        chosen: List[Tuple[Dict[str, Any], float, Dict[str, Any]]] = []
        banned_n = 0
        for row in deployable:
            sid = real_share_id(row)
            if not sid:
                continue
            key = _arm_id(job, row.get("name_key") or name_key(row.get("name")), sid)
            state = posteriors.get(key) or _empty_arm(key)
            if state.get("banned"):
                banned_n += 1
                continue
            sample = thompson_sample(float(state["alpha"]), float(state["beta"]), draw)
            item = (row, sample, state)
            if best_sample is None or sample > best_sample:
                best_sample = sample
                chosen = [item]
            elif sample == best_sample:
                chosen.append(item)
        if not chosen:
            blocked.append(
                {
                    "count": banned_n or len(deployable),
                    "job": job,
                    "reason": "banned" if banned_n else "no share_id",
                }
            )
            continue
        top, sample, state = draw.choice(chosen)
        sid = real_share_id(top)
        if not sid:
            blocked.append({"count": len(group), "job": job, "reason": "no share_id"})
            continue
        drawn = {
            "alpha": float(state.get("alpha") or PRIOR_ALPHA),
            "banned": bool(state.get("banned")),
            "beta": float(state.get("beta") or PRIOR_BETA),
            "impressions": int(state.get("impressions") or 0),
            "job": job,
            "name": top.get("name"),
            "name_key": name_key(top.get("name_key") or top.get("name")),
            "pulls": int(state.get("pulls") or 0),
            "sample": sample,
            "share_id": sid,
        }
        drawn.update(display_fields(top))
        draws.append(drawn)
    return {"blocked": blocked, "draws": draws, "selector": selector_meta(), "winners": draws}


def selftest() -> int:
    legs: List[Tuple[str, bool, str]] = []

    def check(name: str, ok: bool, detail: str = "") -> None:
        legs.append((name, bool(ok), detail))

    src = pathlib.Path(__file__).read_text().split("def selftest()", 1)[0]
    banned = ["SCORE" + "_WEIGHTS", "linear" + "_score", "PRIOR" + "_VARIANCE", "both-beats-" + "shares-only"]
    check(
        "no-feature-prior-in-selector",
        all(token not in src for token in banned) and "ucb1" not in src.casefold(),
        "feature prior or UCB1 leaked into bandit",
    )
    product = store_path(pathlib.Path("/workspace"))
    check(
        "product-store-under-usecases",
        product.as_posix().endswith("usecases/bandit.sqlite") and "/tmp" not in product.as_posix(),
        str(product),
    )

    rng = random.Random(0)
    sample = thompson_sample(1.0, 1.0, rng)
    check("gamma-beta-unit-interval", 0.0 < sample < 1.0, str(sample))

    no_share = {
        "name": "Aaa Best",
        "name_key": "aaa best",
        "job": "operate",
        "origin": "both",
        "prompt_chars": 9999,
        "has_approval_language": True,
        "added_at": "2026-12-31",
        "share_id": None,
    }
    weak = {
        "name": "Zzz Weak",
        "name_key": "zzz weak",
        "job": "operate",
        "origin": "shares",
        "prompt_chars": 1,
        "has_approval_language": False,
        "added_at": "2020-01-01",
        "share_id": "weakShare",
    }
    aaa = {
        "name": "Aaa Shares",
        "name_key": "aaa shares",
        "job": "operate",
        "origin": "shares",
        "prompt_chars": 999,
        "has_approval_language": True,
        "added_at": "2026-12-31",
        "share_id": "aaaShare",
    }
    zzz = {
        "name": "Zzz Both",
        "name_key": "zzz both",
        "job": "operate",
        "origin": "both",
        "prompt_chars": 1,
        "has_approval_language": False,
        "added_at": "2020-01-01",
        "share_id": "zzzShare",
    }
    personal = {
        "name": "Aaa Personal",
        "name_key": "aaa personal",
        "job": "none",
        "origin": "both",
        "prompt_chars": 5000,
        "has_approval_language": True,
        "added_at": "2026-12-31",
        "share_id": "persShare",
    }
    ghost = {
        "name": "Aaa Sales Ghost",
        "name_key": "aaa sales ghost",
        "job": "sell",
        "origin": "directory",
        "prompt_chars": 800,
        "added_at": "2026-12-31",
        "share_id": None,
    }
    same_a = {
        "name": "Aaa Same",
        "name_key": "aaa same",
        "job": "operate",
        "origin": "both",
        "prompt_chars": 100,
        "has_approval_language": True,
        "added_at": "2026-08-01",
        "share_id": "aaaSame",
    }
    same_z = {
        "name": "Zzz Same",
        "name_key": "zzz same",
        "job": "operate",
        "origin": "both",
        "prompt_chars": 100,
        "has_approval_language": True,
        "added_at": "2026-08-01",
        "share_id": "zzzSame",
    }
    twin_b = {
        "name": "Same Name",
        "name_key": "same name",
        "job": "ship",
        "share_id": "shareB",
        "origin": "shares",
        "prompt_chars": 0,
    }
    twin_c = {
        "name": "Same Name",
        "name_key": "same name",
        "job": "ship",
        "share_id": "shareC",
        "origin": "shares",
        "prompt_chars": 0,
    }

    only = pick_jobs([no_share, weak, personal, ghost], rng=random.Random(0))
    win_by = {w["job"]: w for w in only["winners"]}
    blocked_by = {b["job"]: b for b in only["blocked"]}
    check(
        "no-share-cannot-win",
        win_by.get("operate", {}).get("share_id") == "weakShare"
        and win_by.get("operate", {}).get("name") == "Zzz Weak"
        and all(w.get("share_id") for w in only["winners"]),
        str(only),
    )
    check(
        "none-never-a-winner",
        all(w.get("job") != "none" for w in only["winners"])
        and "none" not in blocked_by
        and personal["job"] == "none",
        str(only),
    )
    check(
        "no-share-job-is-blocked",
        "sell" in blocked_by
        and blocked_by["sell"].get("count") == 1
        and "sell" not in win_by,
        str(only),
    )
    check(
        "does-not-invent-decide-refuse",
        all(w.get("job") not in ("decide", "refuse") for w in only["winners"])
        and all(b.get("job") not in ("decide", "refuse") for b in only["blocked"]),
        str(only),
    )
    check(
        "selector-is-thompson-beta",
        only["selector"].get("method") == "thompson"
        and only["selector"].get("prior") == "beta(1,1)",
        str(only["selector"]),
    )
    check(
        "features-are-display-only",
        win_by["operate"].get("origin") == "shares"
        and win_by["operate"].get("prompt_chars") == 1
        and win_by["operate"].get("pulls") == 0,
        str(win_by["operate"]),
    )

    same_names = set()
    for seed in range(40):
        drawn = pick_jobs([same_a, same_z], rng=random.Random(seed))
        operate = [w for w in drawn["winners"] if w.get("job") == "operate"]
        if operate:
            same_names.add(operate[0].get("name"))
    check(
        "same-prior-not-name-alpha",
        same_names == {"Aaa Same", "Zzz Same"},
        str(same_names),
    )

    mixed = set()
    for seed in range(80):
        drawn = pick_jobs([aaa, zzz], rng=random.Random(seed))
        operate = [w for w in drawn["winners"] if w.get("job") == "operate"]
        if operate:
            mixed.add(operate[0].get("name"))
    check(
        "cold-start-not-frozen-ranking",
        mixed == {"Aaa Shares", "Zzz Both"},
        str(mixed),
    )
    check(
        "cold-start-does-not-pin-old-champions",
        mixed != {"Zzz Both"} and mixed != {"Aaa Shares"},
        str(mixed),
    )

    pulls_before = pick_jobs([aaa, zzz], rng=random.Random(1))
    check(
        "select-does-not-invent-reward",
        all(w.get("pulls") == 0 for w in pulls_before["winners"]),
        str(pulls_before),
    )

    with tempfile.TemporaryDirectory(prefix="gb-market-bandit-") as tmp:
        root = pathlib.Path(tmp)
        store = store_path(root)
        check("selftest-store-not-repo-product", store != product, str(store))
        check("posterior-starts-empty", load_posteriors(store) == {}, str(load_posteriors(store)))

        try:
            record_outcome(store, job="operate", name_key="aaa best", share_id="", hit=True)
            check("outcome-refuses-no-share", False, "wrote")
        except StoreRefused as e:
            check("outcome-refuses-no-share", "share_id" in str(e).lower(), str(e))
        try:
            record_outcome(store, job="none", name_key="aaa personal", share_id="persShare", hit=True)
            check("outcome-refuses-job-none", False, "wrote")
        except StoreRefused as e:
            check("outcome-refuses-job-none", "none" in str(e).lower(), str(e))

        record_outcome(
            store,
            job="operate",
            name_key="aaa shares",
            share_id="aaaShare",
            hit=True,
            n=1000,
        )
        posts = load_posteriors(store)
        trained = posts.get(_arm_id("operate", "aaa shares", "aaaShare")) or {}
        check(
            "posterior-updates-on-outcome",
            trained.get("pulls") == 1000
            and trained.get("hits") == 1000
            and trained.get("alpha") == 1001.0
            and trained.get("beta") == 1.0,
            str(trained),
        )
        after = pick_jobs([aaa, zzz], store=store, rng=random.Random(0))
        operate = {w["job"]: w for w in after["winners"]}.get("operate") or {}
        check(
            "thousand-hits-selects-trained-arm",
            operate.get("share_id") == "aaaShare" and operate.get("name") == "Aaa Shares",
            str(after),
        )
        check("select-still-does-not-write-reward", operate.get("pulls") == 1000, str(operate))

        record_outcome(store, job="ship", name_key="same name", share_id="shareB", hit=True, n=5)
        twins = pick_jobs([twin_b, twin_c], store=store, rng=random.Random(0))
        ship = {w["job"]: w for w in twins["winners"]}.get("ship") or {}
        check(
            "same-name-different-share-is-different-arm",
            (_arm_id("ship", "same name", "shareB") in load_posteriors(store))
            and (_arm_id("ship", "same name", "shareC") not in load_posteriors(store) or (load_posteriors(store).get(_arm_id("ship", "same name", "shareC")) or {}).get("pulls", 0) == 0)
            and ship.get("share_id") in ("shareB", "shareC"),
            str((ship, list(load_posteriors(store)))),
        )

        record_impression(
            store, job="operate", name_key="zzz both", share_id="zzzShare"
        )
        before_keep = load_posteriors(store)[_arm_id("operate", "zzz both", "zzzShare")]
        kept_arm = apply_verdict(store, "zzzShare", KIND_KEEP)
        check(
            "keep-raises-alpha",
            kept_arm["alpha"] == before_keep["alpha"] + 1
            and kept_arm["beta"] == before_keep["beta"]
            and kept_arm["pulls"] == before_keep["pulls"] + 1,
            str((before_keep, kept_arm)),
        )
        skipped_arm = apply_verdict(store, "zzzShare", KIND_SKIP)
        check(
            "skip-raises-beta",
            skipped_arm["beta"] == kept_arm["beta"] + 1
            and skipped_arm["alpha"] == kept_arm["alpha"],
            str(skipped_arm),
        )
        record_outcome(
            store, job="operate", name_key="aaa shares", share_id="aaaShare", hit=True, n=1
        )
        apply_verdict(store, "aaaShare", KIND_BAN)
        banned_state = load_posteriors(store)[_arm_id("operate", "aaa shares", "aaaShare")]
        check("ban-is-hard-filter-not-beta-bump", banned_state.get("banned") is True, str(banned_state))
        never = set()
        for seed in range(40):
            drawn = pick_jobs([aaa, zzz], store=store, rng=random.Random(seed))
            operate = [w for w in drawn["draws"] if w.get("job") == "operate"]
            if operate:
                never.add(operate[0].get("share_id"))
        check(
            "banned-arm-not-selected-even-if-rewarded",
            never == {"zzzShare"} and "aaaShare" not in never,
            str(never),
        )
        for kind in (KIND_KEEP, KIND_SKIP, KIND_BAN):
            try:
                apply_verdict(store, "", kind)
                check("no-share-cannot-%s" % kind, False, "wrote")
            except StoreRefused as e:
                check("no-share-cannot-%s" % kind, "share_id" in str(e).lower(), str(e))
        try:
            apply_verdict(store, "missingShare", KIND_KEEP)
            check("keep-refuses-not-in-store", False, "wrote")
        except StoreRefused as e:
            check("keep-refuses-not-in-store", "not in store" in str(e).lower(), str(e))

        catalog = root / PRODUCT_DIR / "market.sqlite"
        catalog.parent.mkdir(parents=True, exist_ok=True)
        catalog.write_bytes(b"rebuilt-catalog-placeholder\n")
        kept = load_posteriors(store)
        check(
            "catalog-rebuild-does-not-wipe-posterior",
            (kept.get(_arm_id("operate", "aaa shares", "aaaShare")) or {}).get("pulls") >= 1000,
            str(kept),
        )

        planted = root / "planted.sqlite"
        planted.write_bytes(b"this is not a sqlite database\n")
        msg = refuse_path(planted)
        check("planted-malformed-refused", msg is not None and "refuse" in msg, str(msg))
        try:
            load_posteriors(planted)
            check("load-malformed-raises", False, "raised nothing")
        except StoreRefused as e:
            check("load-malformed-raises", "refuse" in str(e), str(e))

        stale = root / "stale.sqlite"
        conn = sqlite3.connect(str(stale))
        try:
            conn.executescript(SCHEMA_SQL)
            conn.execute("PRAGMA user_version = 0")
            conn.commit()
        finally:
            conn.close()
        msg = refuse_path(stale)
        check(
            "stale-user-version-refused",
            msg is not None and "user_version" in msg,
            str(msg),
        )

        fresh = root / "fresh.sqlite"
        ensure_store(fresh)
        conn = sqlite3.connect(str(fresh))
        try:
            check("user_version-readback", user_version(conn) == USER_VERSION, str(user_version(conn)))
            ok, text = integrity_ok(conn)
            check("integrity-ok", ok, text)
        finally:
            conn.close()

    failed = [n for n, ok, _ in legs if not ok]
    for name, ok, detail in legs:
        mark = "ok  " if ok else "FAIL"
        print("  %s %s%s" % (mark, name, (" — " + detail[:200]) if (detail and not ok) else ""))
    print("SELFTEST %s - %d/%d" % ("FAIL" if failed else "PASS", len(legs) - len(failed), len(legs)))
    return 1 if failed else 0


def body(argv: Optional[Sequence[str]] = None) -> int:
    import argparse

    ap = argparse.ArgumentParser(prog="gb-market-bandit.py")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args(list(argv) if argv is not None else None)
    if args.selftest:
        return selftest()
    print(
        "gb-market-bandit: posterior store for gb market jobs; --selftest is the gate",
        file=sys.stderr,
    )
    return 2


if __name__ == "__main__":
    gbmain(lambda: body())
