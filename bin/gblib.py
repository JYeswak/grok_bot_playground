"""gblib — the readers every script in bin/ needs, and the platform they all stand on.

Extracted 2026-09-11 after `ripwire . --quality-delta --scope=bin` flagged the fourth copy of the
same JSON loader (`new-clone-of-reused-helper`, plus two `duplication` groups). Rule of Three: the
second copy is honest, the fourth is a maintenance liability — a fix to one of them is a fix to
none of the others.

Deliberately tiny. This is a shared-reader module, not a framework: anything with policy in it
belongs in the script that owns the policy, so the gate cannot start depending on the puller.
Platform resolution is here for exactly that reason — it is not policy, it is the ANSWER TO
"where does this machine keep the thing you are about to read", and four scripts were each
answering it with the same hardcoded macOS string.
"""

from __future__ import annotations

import base64
import dataclasses
import json
import os
import pathlib
import sys
from typing import Any, Dict, List, Mapping, Optional, Tuple


def load(p: pathlib.Path | str) -> Any:
    """Parse JSON, or None. Unreadable and absent are the same answer on purpose — every caller
    here treats 'I could not read it' as 'not measured', never as an empty value."""
    try:
        return json.loads(pathlib.Path(p).read_text(errors="replace"))
    except Exception:
        return None


def dated_children(d: pathlib.Path, suffix: str = "") -> List[pathlib.Path]:
    """Children named `YYYY-MM-DD…`, ascending. The date prefix is the sort key and the filter:
    anything not date-stamped is not an artifact of a tick."""
    if not d.is_dir():
        return []
    rows = [
        c
        for c in d.iterdir()
        if c.name.endswith(suffix) and c.name[:10].count("-") == 2
    ]
    return sorted(rows, key=lambda c: c.name)


def newest_audit(
    root: pathlib.Path,
) -> Tuple[Optional[pathlib.Path], Optional[Dict[str, Any]]]:
    """The most recent deployment audit, as (path, parsed). (None, None) when there is none —
    callers decide whether that is an ERROR, because for some of them it is."""
    rows = dated_children(root / "deployment", ".json")
    return (rows[-1], load(rows[-1])) if rows else (None, None)


# ---------------------------------------------------------------------------------------------
# Platform resolution
#
# MEASURED 2026-09-11, before this block existed: four files hardcoded the macOS support
# directory, two shelled out to `security find-generic-password`, one drove `launchctl`, and
# `grep -rl 'sys.platform\\|platform.system' bin/` returned ZERO. A Windows or Linux operator who
# installed the published package therefore hit the same failure class wave 4 had just removed
# for fresh clones: the macOS path does not exist on his machine, every producer reports its
# input absent, and the board prints a wall of ERRORs. The tool looks broken. The truth is either
# "this OS is not verified yet" or "the support directory lives somewhere else here", and neither
# sentence existed anywhere in the tool.
#
# WHAT THE VENDOR ACTUALLY SHIPS (docs.x.ai/grok-bot/faq, "Which platforms are supported?", read
# live 2026-09-11): macOS on Apple silicon and Intel; Windows on x64 and Arm64; Linux on x64 and
# Arm64 as a .deb, an .rpm or an AppImage — those three are the DESKTOP app — plus iPhone on
# iOS 18 or later and Android 9 or later, which are COMPANION apps ("Use the Grok Bot desktop app
# on macOS, Windows, or Linux, or the companion app on iOS or Android. The same Bots and
# conversations sync across your signed-in devices."). A companion client holds no local session
# and no support directory: the Bots run on a persistent cloud computer and the phone is a client
# of it. So there is nothing on a phone for this tool to audit, and saying so is the answer.
#
# THREE STATUSES, AND THE HONEST MIDDLE ONE.
#   SUPPORTED    a MEASUREMENT. This operator runs macOS; every path and capability below is
#                read off a live install.
#   UNVERIFIED   an INFERENCE. The vendor ships a desktop client for Windows and Linux; that
#                client is Electron (measured here: an `app.asar` archive, Chromium `v10`
#                safeStorage blobs, `sand-*.json` beside them under the userData directory); and
#                Electron's `app.getPath('userData')` is `%APPDATA%/<name>` on Windows and
#                `$XDG_CONFIG_HOME/<name>` on Linux — the same API whose macOS arm is the path
#                measured here. That is a good inference, and it is still an inference: nobody
#                in this repo has ever run the Windows or the Linux client.
#   UNSUPPORTED  a REFUSAL. No desktop client is published for this platform, so there is no
#                local state to read and no path worth guessing at.
#
# Reporting an inference as a measurement is the one thing this repo exists not to do. That is
# why UNVERIFIED is a status rather than a footnote, and why an override (below) can move the
# PATH without ever moving the STATUS.
# ---------------------------------------------------------------------------------------------

# The support directory's leaf name. Electron derives it from the app's product name, and the
# macOS install measured here uses exactly this, spaces included.
APP_DIR = "Grok Bot"

SUPPORTED, UNVERIFIED, UNSUPPORTED = "SUPPORTED", "UNVERIFIED", "UNSUPPORTED"
AVAILABLE, PARTIAL, NOT_IMPLEMENTED = "AVAILABLE", "PARTIAL", "NOT_IMPLEMENTED"

# THE TWO OVERRIDES, and why they are shaped differently.
#
# `GB_SUPPORT_DIR` is OPERATOR surface. It answers the real question a Windows or Linux user has
# on day one — "the client is installed, your guess at the path is wrong, here is where it
# actually is" — and it is the only way that user can make the tool work before anyone here owns
# that hardware. It moves the PATH. It deliberately does NOT move the STATUS: pointing the tool
# at a directory does not verify an operating system, and an override that silently promoted
# UNVERIFIED to SUPPORTED would launder a guess into a measurement.
#
# `GB_PLATFORM` is TEST surface, and it is dangerous, so it is loud. It forces the platform
# string, which is what lets `fixtures/platform/` exercise the Windows and Linux codepaths
# deterministically on a Mac — the only rigorous option available to a team that owns neither
# box. Every answer produced under it carries `forced: true`, so a forced resolution can never be
# read back as a measurement of the machine it ran on.
SUPPORT_DIR_ENV = "GB_SUPPORT_DIR"
PLATFORM_ENV = "GB_PLATFORM"

# Each capability, in the order the tool needs them, with the question it answers.
CAPABILITIES: Tuple[Tuple[str, str], ...] = (
    (
        "credential_read",
        "read the desktop client's own session so the account can be pulled",
    ),
    ("scheduler_install", "install the weekly tick as a scheduled job"),
    ("deployment_audit", "audit the client installed on this machine"),
    ("local_exec_policy", "record this desktop's Execution-on-Local-Computer setting"),
)


@dataclasses.dataclass(frozen=True)
class Capability:
    """One thing this tool can or cannot do here, and what to do about it.

    `remediation` is populated exactly when `status` is not AVAILABLE. A capability that is
    missing without naming its workaround is the dead end this whole block exists to remove.
    """

    name: str
    status: str
    detail: str
    remediation: Optional[str] = None

    @property
    def available(self) -> bool:
        return self.status == AVAILABLE

    def to_json(self) -> Dict[str, Any]:
        return {
            "capability": self.name,
            "status": self.status,
            "detail": self.detail,
            "remediation": self.remediation,
        }


@dataclasses.dataclass(frozen=True)
class Platform:
    """Where this machine keeps the Grok Bot desktop client's state, and how much of that answer
    is measured rather than inferred.

    `support_dir` is Optional on purpose. On a platform with no desktop client there is no such
    directory, and returning the macOS path anyway would make every producer report a REAL file
    as absent — an absence this tool manufactured. None means "this tool does not know, and will
    not guess", which is a fact a caller can branch on.
    """

    os: str
    sys_platform: str
    status: str
    support_dir: Optional[pathlib.Path]
    support_dir_exists: bool
    path_source: str
    forced: bool
    why: str
    remediation: Optional[str]
    capabilities: Tuple[Capability, ...]

    @property
    def supported(self) -> bool:
        return self.status == SUPPORTED

    def capability(self, name: str) -> Capability:
        for cap in self.capabilities:
            if cap.name == name:
                return cap
        raise KeyError(
            f"no capability {name!r}; known: {[c.name for c in self.capabilities]}"
        )

    def to_json(self) -> Dict[str, Any]:
        return {
            "os": self.os,
            "sys_platform": self.sys_platform,
            "status": self.status,
            "support_dir": str(self.support_dir) if self.support_dir else None,
            "support_dir_exists": self.support_dir_exists,
            "path_source": self.path_source,
            "forced": self.forced,
            "why": self.why,
            "remediation": self.remediation,
            "capabilities": [c.to_json() for c in self.capabilities],
        }


def _home(env: Mapping[str, str]) -> pathlib.Path:
    """The user's home, taken from the ENVIRONMENT rather than from the process.

    `os.path.expanduser` reads the live `os.environ` and the password database, which makes it
    unaskable: there is no way to find out what it would answer on a machine whose HOME is
    somewhere else. Reading the mapping is what lets a fixture plant a foreign home and get a
    deterministic answer on this Mac. The fallback keeps the production answer exactly right,
    because in production the mapping IS `os.environ`.
    """
    raw = env.get("HOME") or env.get("USERPROFILE")
    return pathlib.Path(raw) if raw else pathlib.Path(os.path.expanduser("~"))


def _os_name(sys_platform: str, env: Mapping[str, str]) -> str:
    """The platform family, as this tool names it.

    Android is tested FIRST because Termux reports `sys.platform == "linux"`: a phone that looks
    like Linux to the interpreter would otherwise be handed the Linux desktop's userData path and
    told the client is merely missing, when the truth is that no desktop client exists for it.
    """
    if (
        env.get("ANDROID_ROOT")
        or env.get("ANDROID_DATA")
        or "com.termux" in (env.get("PREFIX") or "")
    ):
        return "android"
    if sys_platform == "darwin":
        return "macos"
    if sys_platform.startswith(("win", "cygwin", "msys")):
        return "windows"
    if sys_platform.startswith("linux"):
        return "linux"
    if sys_platform in ("ios", "iphoneos", "ipados"):
        return "ios"
    return sys_platform or "unknown"


def _default_support_dir(
    os_name: str, env: Mapping[str, str]
) -> Tuple[Optional[pathlib.Path], str]:
    """(the directory this OS's client would use, where that answer comes from)."""
    if os_name == "macos":
        return (
            _home(env) / "Library" / "Application Support" / APP_DIR,
            "the macOS Application Support directory, measured on a live install",
        )
    if os_name == "windows":
        base = env.get("APPDATA") or env.get("LOCALAPPDATA")
        root = pathlib.Path(base) if base else _home(env) / "AppData" / "Roaming"
        return root / APP_DIR, "%APPDATA% — Electron's userData directory on Windows"
    if os_name == "linux":
        base = env.get("XDG_CONFIG_HOME")
        root = pathlib.Path(base) if base else _home(env) / ".config"
        return (
            root / APP_DIR,
            "$XDG_CONFIG_HOME or ~/.config — Electron's userData directory on Linux",
        )
    return (
        None,
        "no desktop client is published for this platform, so there is no such directory",
    )


def _capabilities(os_name: str) -> Tuple[Capability, ...]:
    """What this tool can actually do on `os_name`. One row per capability, always all of them —
    an absent row would be indistinguishable from an unasked question."""
    policy = Capability(
        "local_exec_policy",
        AVAILABLE,
        "recorded by a human with bin/gb-record-inventory.py; it is the one field with no server "
        "record anywhere, so it does not depend on the operating system",
    )
    if os_name == "macos":
        return (
            Capability(
                "credential_read",
                AVAILABLE,
                "`security find-generic-password -s 'Grok Bot Safe Storage'` returns the "
                "Electron safeStorage password; macOS prompts for Allow the first time",
            ),
            Capability(
                "scheduler_install",
                AVAILABLE,
                "bin/gb-install-schedule.sh installs the weekly tick as a launchd agent",
            ),
            Capability(
                "deployment_audit",
                AVAILABLE,
                "bin/gb-deployment-audit.py reads the support directory plus the .app "
                "bundle's Info.plist, code signature and app.asar",
            ),
            policy,
        )
    if os_name in ("windows", "linux"):
        secrets = (
            "Electron safeStorage on Windows encrypts with DPAPI (CryptProtectData), not "
            "with a keychain item, and `security` does not exist here"
            if os_name == "windows"
            else "Electron safeStorage on Linux uses the desktop secret service "
            "(libsecret/gnome-keyring, or kwallet); the item's schema and label are "
            "unknown to this repo and nothing here drives `secret-tool`"
        )
        scheduler = (
            "the Windows equivalent is Task Scheduler (`schtasks`)"
            if os_name == "windows"
            else "the Linux equivalent is a systemd --user timer, or cron"
        )
        return (
            Capability(
                "credential_read",
                NOT_IMPLEMENTED,
                f"{secrets} — no decrypted session can be obtained on this platform",
                "run `gb-pull-inventory.py` from a signed-in macOS desktop; the account "
                "read is server-side and the artifact it writes is platform-neutral",
            ),
            Capability(
                "scheduler_install",
                NOT_IMPLEMENTED,
                f"bin/gb-install-schedule.sh drives launchctl; {scheduler}, and it is not "
                f"implemented here",
                "schedule bin/gb-weekly.sh yourself, weekly, and keep the 8-day snapshot "
                "ceiling g3 enforces",
            ),
            Capability(
                "deployment_audit",
                PARTIAL,
                "the JSON under the support directory reads the same way on every "
                "platform, but there is no .app bundle here, so the client's version and "
                "code signature cannot be read the way g4 and g7 expect them",
                f"point {SUPPORT_DIR_ENV} at the real userData directory if the inferred "
                f"path is wrong, and expect g4/g7 to report the app facts unmeasured",
            ),
            policy,
        )
    nothing = (
        "no desktop client is published for this platform. The Grok Bot app here is a "
        "COMPANION client of the cloud computer (docs.x.ai/grok-bot/mobile), so it keeps "
        "no local session and no support directory for this tool to read"
    )
    return (
        Capability(
            "credential_read",
            NOT_IMPLEMENTED,
            nothing,
            "run this tool on the macOS, Windows or Linux desktop that is signed in",
        ),
        Capability(
            "scheduler_install",
            NOT_IMPLEMENTED,
            nothing,
            "run the weekly tick on a desktop",
        ),
        Capability(
            "deployment_audit",
            NOT_IMPLEMENTED,
            nothing,
            "audit a desktop instead; there is nothing installed here to audit",
        ),
        policy,
    )


def platform_support(
    sys_platform: Optional[str] = None, env: Optional[Mapping[str, str]] = None
) -> Platform:
    """Resolve this machine: which OS, how well this tool knows it, where the client's state
    lives, and what can be done here.

    A PURE FUNCTION of (platform string, environment). Both are parameters rather than reads of
    `sys.platform` and `os.environ` so that the Windows and Linux branches are reachable from a
    test on any machine — which is the whole reason this repo can make a claim about an operating
    system nobody here owns. Production calls pass neither and get the live machine.
    """
    environ: Mapping[str, str] = os.environ if env is None else env
    forced_raw = environ.get(PLATFORM_ENV)
    forced = sys_platform is not None or bool(forced_raw)
    plat = sys_platform or forced_raw or sys.platform

    os_name = _os_name(plat, environ)
    status = (
        SUPPORTED
        if os_name == "macos"
        else UNVERIFIED
        if os_name in ("windows", "linux")
        else UNSUPPORTED
    )

    support, path_source = _default_support_dir(os_name, environ)
    override = environ.get(SUPPORT_DIR_ENV)
    if override:
        support = pathlib.Path(override).expanduser()
        # The override moves the path and NOTHING else. Saying so in `path_source` is what keeps
        # a `gb platform --json` transcript honest when someone pastes it into a bug report.
        path_source = f"{SUPPORT_DIR_ENV}={override} (an override moves the path, never the status)"

    why, remediation = _verdict(os_name, status, support, path_source, bool(override))
    return Platform(
        os=os_name,
        sys_platform=plat,
        status=status,
        support_dir=support,
        support_dir_exists=bool(support and support.is_dir()),
        path_source=path_source,
        forced=forced,
        why=why,
        remediation=remediation,
        capabilities=_capabilities(os_name),
    )


def _verdict(
    os_name: str,
    status: str,
    support: Optional[pathlib.Path],
    path_source: str,
    overridden: bool,
) -> Tuple[str, Optional[str]]:
    """The one sentence this platform gets, and the command that addresses it.

    Carried on the dataclass rather than re-derived at each call site, for the same reason
    `InstanceState.why` is: a route that explains itself differently depending on which verb
    printed it is a route nobody trusts.

    `overridden` is separate from `status` because they answer different questions. An operator
    who exported the override has TOLD this tool where the directory is: calling that path
    INFERRED would be the mirror of the error this block exists to prevent — reporting a
    measurement as a guess — and it would leave him reading a remediation that asks him to do
    the thing he has already done. What stays unverified in that case is the operating system,
    not the path.
    """
    if status == UNSUPPORTED:
        return (
            f"{os_name} has no Grok Bot desktop client, so this machine holds no deployment "
            f"to measure — that is a fact about the platform, not a failure of this tool",
            "run gb on the macOS, Windows or Linux desktop that is signed in",
        )
    if status == UNVERIFIED:
        seen = "exists" if support and support.is_dir() else "does not exist"
        if overridden:
            return (
                f"the vendor ships a {os_name} desktop client and nobody in this repo has "
                f"ever run one, so the platform stays UNVERIFIED — but the path is not a "
                f"guess here: {SUPPORT_DIR_ENV} names it, and it {seen}",
                None
                if support and support.is_dir()
                else f"{SUPPORT_DIR_ENV} points at {support}, which does not exist — check the "
                f"path, or unset it to fall back on the inferred one",
            )
        return (
            f"the vendor ships a {os_name} desktop client, but nobody in this repo has ever "
            f"run one: the support directory is INFERRED from {path_source}, and it {seen}",
            None
            if support and support.is_dir()
            else f"if the client is installed, find its userData directory and export "
            f"{SUPPORT_DIR_ENV}=<that path>; the inferred path is a guess, not a measurement",
        )
    if support and support.is_dir():
        return (
            f"macOS is the platform this tool is measured on, and the support directory "
            f"exists at {support}",
            None,
        )
    return (
        "macOS is the platform this tool is measured on, but the support directory does not "
        "exist — the Grok Bot desktop app has never run here",
        "install and launch the Grok Bot desktop app, then re-run `gb setup --apply`",
    )


def unavailable(plat: Platform, capability: str) -> Optional[Capability]:
    """The capability row when it is NOT usable here, else None.

    The gating primitive every platform-dependent producer calls before it shells out. Returning
    the ROW rather than a bool is deliberate: the caller needs the detail and the remediation to
    refuse honestly, and a bare `False` is how a refusal ends up with no reason attached.
    """
    cap = plat.capability(capability)
    return None if cap.available else cap


def platform_refusal(
    tool: str, plat: Platform, capability: Optional[str] = None
) -> Optional[str]:
    """The stderr block a producer prints instead of pretending, or None when nothing is refused.

    TWO GATES, because producers need two different questions answered, and conflating them
    would produce a second kind of manufactured absence.

      with `capability`    "can this machine do the thing at all". The credential read is the
                           one that genuinely cannot happen off macOS: the session is sealed by
                           DPAPI on Windows and by the desktop secret service on Linux, and
                           nothing here opens either.
      without it           "is there a support directory to read at all". That is the weaker
                           gate the path-readers need. On Windows and Linux the client's
                           `sand-*.json` reads exactly the way it does here once the path is
                           right, so refusing those producers there would invent an absence
                           rather than report one.

    Returns TEXT, never an exit code and never an exception. The producer owns its exit code —
    by contract that is 3 ENVIRONMENT, because the operator typed the right thing and this
    machine cannot do it — and a library that raises `SystemExit` is a library that cannot be
    called from a test.
    """
    if capability is not None:
        cap = unavailable(plat, capability)
        if cap is None:
            return None
        return (
            f"{tool}: {cap.name} is {cap.status} on {plat.os} ({plat.status}).\n"
            f"    {cap.detail}.\n"
            f"    fix: {cap.remediation}"
        )
    if plat.support_dir is not None:
        return None
    return (
        f"{tool}: no Grok Bot support directory exists on {plat.os} ({plat.status}).\n"
        f"    {plat.why}.\n"
        f"    fix: {plat.remediation}\n"
        f"    or:  export {SUPPORT_DIR_ENV}=<path> if you know where the client keeps it"
    )


def support_dir(env: Optional[Mapping[str, str]] = None) -> Optional[pathlib.Path]:
    """Where the desktop client keeps its state on this machine, or None when this tool does not
    know. One definition, because five scripts read it."""
    return platform_support(env=env).support_dir


def statsig_config(support: Optional[pathlib.Path | str] = None) -> Dict[str, Any]:
    """The client's evaluated Statsig payload, or {}. `config` is JSON-inside-JSON in the
    bootstrap file — the double decode is the vendor's shape, not ours."""
    where = pathlib.Path(support) if support is not None else support_dir()
    if where is None:
        return {}
    boot = load(where / "sand-statsig-bootstrap.json") or {}
    raw = boot.get("config")
    if not isinstance(raw, str):
        return {}
    try:
        parsed = json.loads(raw)
    except Exception:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def unb32(name: str) -> str:
    """Decode the desktop client's base32 blob filenames back into their slice keys.

    The client stores `sand-client-persistence/<base32>.blob`; the key inside names what the blob
    holds (roster, transcript replica, drafts). Padding is restored because the client strips it.
    Returns "" for anything that does not decode — an unreadable name is not an error, it is a
    blob this repo has no business reading.
    """
    s = name.removesuffix(".blob").upper()
    s += "=" * ((8 - len(s) % 8) % 8)
    try:
        return base64.b32decode(s).decode()
    except Exception:
        return ""
