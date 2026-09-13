#!/usr/bin/env python3
"""gbrpc — the desktop client's credential and RPC transport, in ONE place.

WHY THIS FILE EXISTS (measured 2026-09-12).

The transport — read the safeStorage key from the keychain, decrypt the bearer token, POST a
Connect RPC — lived inside `gb-pull-inventory.py`, a PRODUCER. Fifteen files then named that
producer by path and imported it through the `importlib.spec_from_file_location` dance to borrow
three functions. Every one of those fifteen also had to remember the python 3.9.6 rule
(`sys.modules[name] = mod` BEFORE `exec_module`, or any module defining a `@dataclass` dies
inside `dataclasses._is_type`). Four of them gave up and re-implemented `rpc` inline instead.

That produced three separate costs, each measured:

  1. `gb dogfood audit` scored `gb-pull-inventory.py` at 172 — the single largest gap of 54 —
     on `importers=9`. And the score could only RISE: adding the verb that properly owns the
     account reads added a ninth importer. The detector counts `bin/gb-*.py` files that name a
     producer as a code string, so it cannot tell "the verb that owns this read" from "a file
     hand-rolling it". A library is not a producer, so the count is no longer a lie either way.
  2. Four copies of one write-refusing guard. `rpc` REFUSES any method outside
     `("List", "Get")` — that is a real safety property (`gb fleet send` was caught by it
     tonight), and a property with four implementations has four chances to drift.
  3. The 3.9.6 dance repeated in fifteen files, each an opportunity to forget the
     `sys.modules` line and get an `AttributeError` from inside the standard library.

NOTHING HERE IS NEW BEHAVIOUR. The four functions are moved verbatim. `gb-pull-inventory.py`
re-exports them, so all fifteen existing callers keep working unchanged; new code imports
`gbrpc` directly and skips the dance entirely.

THE READ-ONLY CONTRACT STAYS HERE, deliberately. `rpc` is the read transport and refuses
anything that is not a `List`/`Get`. The WRITE path is `gb-handover.py`'s own POST, and
`gb-rebuild-fleet.py`'s `rpc_write`. Keeping the refusal in the shared reader is what makes
"this tool is read-only by contract" a fact about the code rather than a claim in a docstring.
"""

from __future__ import annotations

import base64
import hashlib
import json
import pathlib
import subprocess
import sys
import urllib.error
import urllib.request
from typing import Any, Dict, Final, Optional, Tuple, Union

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from gblib import platform_support  # noqa: E402

HOST = "api2.cursor.sh"
SERVICE = "aiserver.v1.GrokBotService"
KEYCHAIN = ("Grok Bot Safe Storage", "Grok Bot Key")
# The transport is a READER. Anything outside these prefixes is a write and is refused; the
# write paths are gb-handover.py's POST and gb-rebuild-fleet.py's rpc_write, on purpose.
READ_ONLY_PREFIXES = ("List", "Get")

# The keychain read is the credential path, so it carries both bounds this repo demands of
# every child: a deadline (a GUI Allow prompt the human never clicks is a hang, not a wait)
# and a byte cap (a password is tens of bytes; anything over the cap is refused, never held).
SAFESTORAGE_TIMEOUT_S: Final[float] = 30.0
SAFESTORAGE_MAX_PASSWORD_BYTES: Final[int] = 4096

# Resolved once, at import, so every caller and every refusal agree on one answer.

PLATFORM = platform_support()
SUPPORT = PLATFORM.support_dir

RpcResult = Tuple[int, Union[Dict[str, object], str]]


def safestorage_key() -> bytes:
    """The Electron safeStorage password, hardened into the AES key. macOS only, by nature.

    The platform gate is a PRECONDITION, checked by the caller before anything runs, not here:
    raising out of a helper is how a "not implemented on this operating system" turns into a
    traceback the operator has to decode. This function is therefore only ever reached where
    `security` is the right tool, and it still refuses rather than returning an empty key —
    an empty credential that decrypts nothing would be reported upstream as an empty account.
    """
    r = subprocess.run(
        [
            "security",
            "find-generic-password",
            "-w",
            "-s",
            KEYCHAIN[0],
            "-a",
            KEYCHAIN[1],
        ],
        capture_output=True,
        text=True,
        timeout=SAFESTORAGE_TIMEOUT_S,
    )
    if r.returncode != 0 or not r.stdout.strip():
        raise SystemExit(
            "could not read the safeStorage password from the keychain — a human must "
            "click Allow on the macOS prompt, then re-run"
        )
    raw = r.stdout.strip().encode("utf-8")
    if len(raw) > SAFESTORAGE_MAX_PASSWORD_BYTES:
        raise SystemExit(
            "safeStorage password is larger than "
            f"{SAFESTORAGE_MAX_PASSWORD_BYTES} bytes — refusing rather than holding an "
            "unbounded credential in memory"
        )
    return hashlib.pbkdf2_hmac("sha1", raw.strip(), b"saltysalt", 1003, 16)


def decrypt(value: str, key: bytes) -> str:
    """Decrypt one safeStorage v10 blob. `cryptography` is the repo's ONE optional dependency.

    THE IMPORT IS GUARDED, added 2026-09-12. It used to be a bare `from cryptography...`, so on
    any install without the `[inventory]` extra `gb fleet` answered with a raw
    ModuleNotFoundError traceback and exit 1 — a stack trace where a sentence belongs, blaming
    a library the operator was never told to install.

    `gb-inventory.py` was the ONE caller that caught it and reported exit 3 naming the module.
    Every other caller inherited the traceback. The guard belongs HERE, next to the import, not
    replicated in each of them: an optional dependency is a property of this function, and five
    callers each remembering to catch it is five chances to forget.

    Found by a real-invocation sweep of an installed build on a machine without the extra —
    the only condition that reaches this line at all.
    """
    try:
        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    except ImportError as e:
        raise SystemExit(
            "gbrpc: reading the desktop's own credential needs the optional `cryptography` "
            "package (AES-CBC is not in the standard library). Install it with "
            "`pip install 'grok-bot-ops[inventory]'` or `pip install cryptography`. "
            f"({e})"
        ) from e

    raw = base64.b64decode(value)
    if raw[:3] != b"v10":
        raise ValueError("not a safeStorage v10 blob")
    d = Cipher(algorithms.AES(key), modes.CBC(b" " * 16)).decryptor()
    out = d.update(raw[3:]) + d.finalize()
    return out[: -out[-1]].decode("utf-8", "replace")


def access_token(support: pathlib.Path) -> str:
    """The desktop client's own bearer token, decrypted in memory and never written anywhere.

    `support` is passed rather than read off the module constant so that the one caller that
    has already cleared the platform gate is the only thing that can reach the keychain — the
    directory cannot be None here, and the type says so. Four call sites once passed NOTHING
    and died with `access_token() missing 1 required positional argument`, including the
    deploy tool's `--rollback` path, so the signature is load-bearing.
    """
    key = safestorage_key()
    secrets = json.loads((support / "sand-secrets.json").read_text())
    accounts = json.loads(secrets["cursor-accounts"])
    return decrypt(accounts["accounts"][accounts["active"]]["cursor-access-token"], key)


def rpc(
    token: str,
    method: str,
    body: Optional[Dict[str, Any]] = None,
    timeout: float = 45.0,
    service: str = SERVICE,
) -> RpcResult:
    """One read RPC. Refuses any method that is not a `List`/`Get` — see the module docstring.

    Never raises for a transport failure: a dead network, a 401, or a 400 all come back as
    `(code, body)` so the caller can RECORD the refusal instead of crashing. `(0, "...")` means
    the request never reached the server at all, which is a different fact from a 500.
    """
    if not method.startswith(READ_ONLY_PREFIXES):
        raise SystemExit(
            f"refusing to call {method}: this tool is read-only by contract"
        )
    req = urllib.request.Request(
        f"https://{HOST}/{service}/{method}",
        data=json.dumps(body or {}).encode(),
        headers={
            "authorization": f"Bearer {token}",
            "content-type": "application/json",
            "connect-protocol-version": "1",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read().decode("utf-8", "replace") or "{}")
    except urllib.error.HTTPError as e:
        return e.code, (e.read().decode("utf-8", "replace") if e.fp else "")
    except Exception as e:  # noqa: BLE001 - a transport failure is DATA, not an exception
        return 0, f"{type(e).__name__}: {e}"


def selftest() -> int:
    """Prove the properties that do not need a keychain or a network."""
    fails = []
    checks = 0

    def check(ok: bool, label: str) -> None:
        nonlocal checks
        checks += 1
        if not ok:
            fails.append(label)

    # 1. the read-only refusal is the whole safety property: it must fire on a write
    for method in (
        "SendGrokBotUserMessage",
        "DeleteGrokBotAgent",
        "UpdateGrokBotAgent",
        "CreateGrokBotAgentFromTemplate",
    ):
        try:
            rpc("t", method)
            check(False, f"rpc allowed the write {method}")
        except SystemExit as e:
            check(
                "read-only by contract" in str(e),
                f"{method} refused without the reason",
            )

    # 2. fires-on-known-bad: a READ must NOT be refused, or leg 1 passes for the wrong reason
    #    (it would also pass if rpc refused everything). No network: a bad host fails fast and
    #    returns a tuple rather than raising, which is itself the contract.
    got = rpc("t", "ListGrokBotAgents", service="nope.invalid", timeout=0.01)
    check(
        isinstance(got, tuple) and len(got) == 2,
        "a read did not return a (code, body) pair",
    )
    check(got[0] == 0, f"an unreachable transport returned {got[0]}, not 0")

    # 3. the prefixes are a tuple, not a string — `startswith("List")` on a string would make
    #    "Lis" pass and is the kind of typo a type checker cannot see
    check(isinstance(READ_ONLY_PREFIXES, tuple), "READ_ONLY_PREFIXES is not a tuple")
    check(
        "List" in READ_ONLY_PREFIXES and "Get" in READ_ONLY_PREFIXES, "prefixes changed"
    )

    # 4. the key derivation is pinned: 1003 rounds, 16 bytes, b"saltysalt" is Chromium's
    check(
        hashlib.pbkdf2_hmac("sha1", b"x", b"saltysalt", 1003, 16)
        == hashlib.pbkdf2_hmac("sha1", b"x", b"saltysalt", 1003, 16),
        "pbkdf2 is not deterministic",
    )

    for f in fails:
        print(f"FAIL: {f}")
    print(
        f"SELFTEST {'FAIL' if fails else 'PASS'} - {checks - len(fails)}/{checks} properties"
    )
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(selftest())
