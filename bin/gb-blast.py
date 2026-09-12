#!/usr/bin/env python3
"""gb-blast — what your Bots can do UNATTENDED, and how bad it could get.

The first question a security-minded operator asks about an always-on agent is not "does it
work". It is "what can it reach while I am asleep, and what stops it". Nothing in this tool
answered that, and the whole answer was already sitting on disk in the deployment audit. This
is wizard idea SKEPTIC-4, `gb blast`, finally built.

WHAT IT MEASURES, and the denominator of every figure (re-derived here, not quoted):

  TOOLS      `mcp.servers[]` in the newest audit that ATTEMPTED a live read. 245 registered
             tool INSTANCES across 16 registered servers — but only 12 of those servers are
             `connected`; 3 answer `needsAuth` and 1 answers `error`, and all four contribute
             exactly 0 tools. "245 across 16" is therefore true and misleading: it is 245
             across 12.
  CAPABILITY The same census deduplicated to (connector family, tool). 112 DISTINCT pairs,
             because Gmail is installed four times (four mailboxes) and Drive and Calendar
             three times each. Four installs of Gmail are four ACCOUNTS, not four scopes: each
             carries the same 31 tools.
  REACH      Per Bot. The effective MCP config is ACCOUNT-level — `mcp.scope_note` records
             that `GetEffectiveMcpConfigForUser` returns no per-Bot scope — so every Bot
             reaches every connected server. Reach is IDENTICAL for all 13 Bots. That is not
             a simplification; it is the finding.
  UNATTENDED A Bot with at least one ENABLED routine runs without a human present. 2 of 13.
             An idle Bot holding 142 write-shaped tools and a cron-scheduled one holding the
             same 142 are different risks, and only the second one is a schedule.
  NOTIFY     `bots[].notifications_enabled`. 0 of 13 on this account: no Bot can interrupt its
             operator, so nothing about an unattended write arrives before the operator looks.
  REVIEW     The account's auto-review rules, read out of the newest `inventory/` artifact.

A NUMBER THIS FILE CORRECTS. The prior pass reported "112 of 245 registered MCP tools are
write-shaped, Gmail alone contributes 80". Re-derived here with the rule set printed below,
the write-shaped count is 142 of 245 instances (52 of 112 distinct capabilities) and Gmail
contributes 96 of 142. 112 does not reproduce under this rule set, and 112 is also exactly the
number of DISTINCT (connector, tool) pairs in the same census — a count of capabilities, not a
count of writes. The prior prefix list was not recorded, so the figure cannot be reconstructed;
that is why every number below prints the rule that produced it.

TWO HEURISTICS, BOTH LABELLED WHEREVER THEY SURFACE.

  1. TOOL CLASSIFICATION is a NAME heuristic — the leading verb token of the tool name, against
     the READ_VERBS and WRITE_VERBS sets below. An unrecognised verb is `unknown`, NEVER folded
     into read: a tool nobody classified must not be counted safe.

     FALSE-POSITIVE DIRECTION, stated once so it can be argued with:
       - It OVER-calls write on verbs that name an act with small blast radius.
         `apply_sensitive_message_label` relabels one message; it is counted with `execute_sql`.
       - It UNDER-states risk on the read side. A read-shaped tool is an EXFILTRATION path:
         `get_message`, `download_file_content` and `search_threads` move your data out of your
         account. READ-SHAPED IS NOT HARMLESS. This verb ranks on write because write is the
         irreversible half, not because reads are safe.
       - It cannot see a tool whose NAME LIES. Nothing here can.

     THE TRUTHFUL SIGNAL EXISTS AND IS NOT ON DISK. MCP tool definitions carry `annotations`
     with `readOnlyHint` and `destructiveHint`, which is the server's own answer to this
     question. `bin/gb-deployment-audit.py` keeps only `name` and a count from
     `DashboardService/ListSandMcpTools` and discards the rest, so the repo has zero
     occurrences of either hint (`grep -rn 'readOnlyHint\\|destructiveHint' bin/` -> nothing).
     Fixing that is a PRODUCER change in the audit, not something this file can work around,
     and until it lands every classification here is a guess about a name.

  2. REVIEW COVERAGE is a RULE-KEYWORD heuristic. The account's auto-review rules are English
     sentences ("Delete or overwrite any file or record"); no artifact on disk binds a rule to
     a tool name. RULE_LEXICON maps a word that appears in a rule to tool-name tokens that name
     the same act or object, and a write-shaped tool is "named by a rule" when its tokens
     intersect. This measures what the rules SAY, not what the platform ENFORCES — this file
     cannot observe enforcement and does not claim to.

     With no rules readable, coverage is ZERO, not total. An unmeasured gate is an open gate.

CHARTER TEXT IS NOT AVAILABLE. The brief asked whether each charter contains approval language.
It cannot be answered from this account's artifacts: `bots[]` carries `description_chars` and no
body, and so does `inventory/*.json`. POSITIVE CONTROL, so the absence is a measurement and not
a failed grep: `templates/*.json` DO carry a full `charter` string plus an `approval_boundary`
field, and this file reports finding them. The approval column therefore comes from the ACCOUNT's
auto-review rules, and says so. Closing that gap is roadmap row `no-charter-text-in-corpus`.

  gb-blast.py                       # the ranked table and the worst-cell count
  gb-blast.py --json                # the whole join, machine-readable
  gb-blast.py --selftest            # 20 properties, inline fixtures, no network
"""

from __future__ import annotations

import collections
import dataclasses
import json
import pathlib
import re
import sys
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import gbargs  # noqa: E402
import gbtypes  # noqa: E402
from gblib import dated_children, load, newest_audit  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[1]

SCHEMA = "gb-blast/1"

# The label attached to every figure these rules produce. It travels into the JSON and into the
# rendered table, because a heuristic that is only disclosed in a docstring is not disclosed.
TOOL_SIGNAL = "name-heuristic:leading-verb"
RULE_SIGNAL = "rule-keyword-heuristic"

# Retrieval verbs. Deliberately narrow: a verb earns a place here only when it names fetching
# something that already exists. `suggest`, `generate` and `confirm` are NOT here — they are
# left unclassified on purpose, because guessing them read-shaped is the failure this set
# exists to avoid.
READ_VERBS: Tuple[str, ...] = (
    "check",
    "count",
    "describe",
    "download",
    "fetch",
    "get",
    "list",
    "query",
    "read",
    "search",
    "view",
)

# Mutating verbs. Wider than this account needs, so the classifier does not silently degrade on
# a connector nobody here has installed.
WRITE_VERBS: Tuple[str, ...] = (
    "add",
    "apply",
    "approve",
    "archive",
    "assign",
    "cancel",
    "clear",
    "copy",
    "create",
    "delete",
    "deploy",
    "disable",
    "drop",
    "edit",
    "enable",
    "execute",
    "export",
    "forward",
    "grant",
    "import",
    "insert",
    "install",
    "invoke",
    "label",
    "mark",
    "merge",
    "move",
    "patch",
    "pause",
    "post",
    "publish",
    "purge",
    "put",
    "rebase",
    "remove",
    "rename",
    "reply",
    "reset",
    "respond",
    "restart",
    "restore",
    "revoke",
    "run",
    "schedule",
    "send",
    "set",
    "share",
    "start",
    "stop",
    "subscribe",
    "sync",
    "trash",
    "truncate",
    "unlabel",
    "uninstall",
    "unmark",
    "unsubscribe",
    "untrash",
    "update",
    "upload",
    "upsert",
    "write",
)

READ_SET: Set[str] = set(READ_VERBS)
WRITE_SET: Set[str] = set(WRITE_VERBS)

KIND_READ = "read"
KIND_WRITE = "write"
KIND_UNKNOWN = "unknown"

# A word that can appear in an auto-review rule -> the tool-name tokens that name the same act
# or the same object. Kept deliberately tight. The first draft mapped "change" onto `apply`,
# `copy` and `respond` and reported 142 of 142 write-shaped tools reviewed, which is the shape
# of a rule that cannot fail — it told the operator every write was gated and would have said
# so whatever the rules were.
RULE_LEXICON: Dict[str, Tuple[str, ...]] = {
    "blog": ("article", "post"),
    "cards": ("card", "credit"),
    "delete": ("delete", "drop", "purge", "remove", "trash", "truncate"),
    "email": ("draft", "mail", "message", "thread"),
    "file": ("attachment", "document", "file"),
    "message": ("draft", "message", "thread"),
    "overwrite": ("patch", "replace", "reset", "update", "upsert"),
    "payment": ("billing", "charge", "invoice", "pay", "payment"),
    "production": (
        "branch",
        "deploy",
        "edge",
        "function",
        "migration",
        "pause",
        "project",
        "restore",
        "sql",
    ),
    "publish": ("post", "publish", "release"),
    "purchase": ("buy", "checkout", "order", "purchase"),
    "record": ("record", "row", "sql", "table"),
    "send": ("forward", "reply", "send"),
    "setting": ("config", "filter", "permission", "preference", "setting"),
    "social": ("post", "tweet"),
    "system": ("branch", "project", "subscription", "webhooks"),
    "website": ("deploy", "domain", "site"),
}

# Statuses that mean the server answered with a tool list. Anything else contributes zero tools
# and is still counted as REGISTERED, because "16 servers" and "12 servers with any reach" are
# different facts and collapsing them is how a census flatters itself.
LIVE_STATUS = "connected"


# ---------------------------------------------------------------------------------------------
# Names
# ---------------------------------------------------------------------------------------------
def tokens(text: str) -> Set[str]:
    """`create_users_bookmark` -> {create, users, bookmark}. One splitter for rules and tools
    alike, so the two sides of the coverage join cannot disagree about what a word is."""
    return {t for t in re.split(r"[^a-z0-9]+", text.lower()) if t}


def strip_server_prefix(server_id: str, tool: str) -> str:
    """`user-Gmail-send_message` -> `send_message`, given its server.

    Only strips the EXACT `<server_id>-` prefix. A tool name that does not carry its server's
    prefix is returned untouched rather than trimmed by length, because trimming by length is
    how a classifier ends up reading the middle of a word as a verb.
    """
    head = server_id + "-"
    return (
        tool[len(head) :] if tool.startswith(head) and len(tool) > len(head) else tool
    )


def family_of(server_id: str) -> str:
    """`user-Gmail--chiefzester` -> `user-Gmail`. The CONNECTOR, not the install.

    Four Gmail installs are four mailboxes sharing one tool surface. Counting them as four
    connectors would put the same 31 capabilities on four rows and make Gmail look like variety
    instead of concentration.
    """
    return server_id.split("--", 1)[0]


def verb_of(basename: str) -> str:
    """The leading token. `send_message` -> `send`; a bare `reply` -> `reply`."""
    return basename.split("_", 1)[0].lower()


def classify(basename: str) -> str:
    """READ, WRITE or UNKNOWN — from the name, and only from the name.

    UNKNOWN is a real third answer. Folding it into read would let any connector whose verbs
    this file has never seen report a blast radius of zero.
    """
    verb = verb_of(basename)
    if verb in WRITE_SET:
        return KIND_WRITE
    if verb in READ_SET:
        return KIND_READ
    return KIND_UNKNOWN


# ---------------------------------------------------------------------------------------------
# Review rules
# ---------------------------------------------------------------------------------------------
def rule_tokens(rule: str) -> Set[str]:
    """The tool-name tokens an English review rule names, via RULE_LEXICON.

    A rule contributing NO tokens is a rule this file cannot map. It still appears in the
    output, marked, rather than being dropped — "Make a purchase, a payment, or use cards"
    matches nothing on this account because no installed connector spends money, and that is
    worth printing.
    """
    out: Set[str] = set()
    for word in tokens(rule):
        out.update(RULE_LEXICON.get(word, ()))
    return out


def rules_naming(basename: str, rules: Sequence[str]) -> Tuple[str, ...]:
    """Which rules name this tool. Empty tuple = no rule mentions the act it performs."""
    tool = tokens(basename)
    return tuple(r for r in rules if tool & rule_tokens(r))


# ---------------------------------------------------------------------------------------------
# Rows
# ---------------------------------------------------------------------------------------------
@dataclasses.dataclass(frozen=True)
class Tool:
    """One REGISTERED tool instance. Two installs of Gmail produce two of these per capability."""

    server_id: str
    family: str
    name: str
    basename: str
    verb: str
    kind: str
    named_by: Tuple[str, ...]

    @property
    def gated(self) -> bool:
        return bool(self.named_by)


@dataclasses.dataclass(frozen=True)
class ConnectorRow:
    family: str
    installs: int
    tools: int
    write: int
    read: int
    unknown: int
    gated_write: int
    ungated_write: int
    ungated_names: Tuple[str, ...]

    def as_json(self) -> Dict[str, Any]:
        return {
            "connector": self.family,
            "installs": self.installs,
            "tool_instances": self.tools,
            "write_shaped": self.write,
            "read_shaped": self.read,
            "unclassified": self.unknown,
            "write_named_by_a_rule": self.gated_write,
            "write_named_by_no_rule": self.ungated_write,
            "write_named_by_no_rule_names": list(self.ungated_names),
            "signal": {"classification": TOOL_SIGNAL, "coverage": RULE_SIGNAL},
        }


@dataclasses.dataclass(frozen=True)
class BotRow:
    name: str
    uuid: str
    routines: int
    enabled_routines: int
    schedules: Tuple[str, ...]
    unattended: bool
    can_notify: bool
    write_reach: int
    ungated_write_reach: int
    idle_days: float
    worst_cell: bool

    def as_json(self) -> Dict[str, Any]:
        return {
            "bot": self.name,
            "uuid": self.uuid,
            "routines": self.routines,
            "enabled_routines": self.enabled_routines,
            "schedules": list(self.schedules),
            "unattended": self.unattended,
            "can_notify": self.can_notify,
            "write_reach": self.write_reach,
            "write_reach_named_by_no_rule": self.ungated_write_reach,
            "idle_days": self.idle_days,
            "worst_cell": self.worst_cell,
        }


# ---------------------------------------------------------------------------------------------
# Census
# ---------------------------------------------------------------------------------------------
def tools_from(servers: Sequence[Dict[str, Any]], rules: Sequence[str]) -> List[Tool]:
    """Every registered tool instance, classified, with the rules that name it."""
    out: List[Tool] = []
    for server in servers:
        sid = str(server.get("server_id") or "")
        for raw in server.get("tool_names") or []:
            name = str(raw)
            base = strip_server_prefix(sid, name)
            out.append(
                Tool(
                    server_id=sid,
                    family=family_of(sid),
                    name=name,
                    basename=base,
                    verb=verb_of(base),
                    kind=classify(base),
                    named_by=rules_naming(base, rules),
                )
            )
    return out


def connector_rows(
    tools: Sequence[Tool], servers: Sequence[Dict[str, Any]]
) -> List[ConnectorRow]:
    """One row per CONNECTOR family, ranked by write-shaped instances."""
    installs: "collections.Counter[str]" = collections.Counter(
        family_of(str(s.get("server_id") or "")) for s in servers
    )
    families = sorted({t.family for t in tools})
    rows: List[ConnectorRow] = []
    for fam in families:
        mine = [t for t in tools if t.family == fam]
        writes = [t for t in mine if t.kind == KIND_WRITE]
        ungated = [t for t in writes if not t.gated]
        rows.append(
            ConnectorRow(
                family=fam,
                installs=installs.get(fam, 0),
                tools=len(mine),
                write=len(writes),
                read=sum(1 for t in mine if t.kind == KIND_READ),
                unknown=sum(1 for t in mine if t.kind == KIND_UNKNOWN),
                gated_write=len(writes) - len(ungated),
                ungated_write=len(ungated),
                ungated_names=tuple(sorted({t.basename for t in ungated})),
            )
        )
    rows.sort(key=lambda r: (-r.write, -r.tools, r.family))
    return rows


def distinct_capabilities(tools: Iterable[Tool]) -> Dict[Tuple[str, str], str]:
    """(connector family, tool basename) -> kind. The deduplicated capability set."""
    return {(t.family, t.basename): t.kind for t in tools}


# ---------------------------------------------------------------------------------------------
# Bots
# ---------------------------------------------------------------------------------------------
def routine_index(audit: Dict[str, Any]) -> Dict[str, List[Dict[str, Any]]]:
    """Bot name -> its routine rows. Name-keyed because `bots[]` and `routines.bots[]` are two
    server reads that agree on the name and not always on the identifier."""
    out: Dict[str, List[Dict[str, Any]]] = {}
    for row in (audit.get("routines") or {}).get("bots") or []:
        name = str(row.get("name") or "")
        rs = [r for r in (row.get("routines") or []) if isinstance(r, dict)]
        out[name] = rs
    return out


def is_unattended(routines: Sequence[Dict[str, Any]]) -> bool:
    """A Bot runs unattended when at least one routine is ENABLED and carries a trigger.

    A DISABLED routine is a plan, not a schedule. Counting it would put every Bot that ever had
    a routine into the unattended column and make the count useless.
    """
    for r in routines:
        if not bool(r.get("enabled")):
            continue
        if str(r.get("schedule") or "").strip() or str(r.get("trigger") or "").strip():
            return True
    return False


def bot_rows(
    audit: Dict[str, Any], write_reach: int, ungated_reach: int
) -> List[BotRow]:
    """One row per Bot. Reach is the SAME on every row by construction — see `scope_block`."""
    index = routine_index(audit)
    rows: List[BotRow] = []
    for b in audit.get("bots") or []:
        name = str(b.get("name") or "")
        rs = index.get(name, [])
        enabled = [r for r in rs if bool(r.get("enabled"))]
        unattended = is_unattended(rs)
        can_notify = bool(b.get("notifications_enabled"))
        rows.append(
            BotRow(
                name=name,
                uuid=str(b.get("id") or b.get("uuid") or ""),
                routines=len(rs),
                enabled_routines=len(enabled),
                schedules=tuple(str(r.get("schedule") or "").strip() for r in enabled),
                unattended=unattended,
                can_notify=can_notify,
                write_reach=write_reach,
                ungated_write_reach=ungated_reach,
                idle_days=float(b.get("idle_days") or 0.0),
                worst_cell=bool(ungated_reach > 0 and unattended and not can_notify),
            )
        )
    rows.sort(
        key=lambda r: (
            not r.worst_cell,
            not r.unattended,
            r.can_notify,
            r.idle_days,
            r.name,
        )
    )
    return rows


# ---------------------------------------------------------------------------------------------
# Approval posture
# ---------------------------------------------------------------------------------------------
def approval_posture(root: pathlib.Path) -> Dict[str, Any]:
    """The account's auto-review rules, out of the newest `inventory/` artifact.

    ABSENT IS NOT EMPTY. With no inventory the rules list is empty, coverage falls to zero, and
    every write-shaped tool is reported as named by NO rule. That is the fail-loud direction:
    an unmeasured gate must never render as a closed one.
    """
    rows = dated_children(root / "inventory", ".json")
    for path in reversed(rows):
        doc = load(path)
        if not isinstance(doc, dict):
            continue
        rules = [str(r) for r in (doc.get("auto_review_rules") or []) if str(r).strip()]
        if not rules:
            continue
        return {
            "available": True,
            "source": str(path),
            "provenance": str(doc.get("source") or ""),
            "enabled": bool(doc.get("auto_review_enabled")),
            "scope": str(doc.get("auto_review_rules_scope") or "unknown"),
            "rules": rules,
            "signal": RULE_SIGNAL,
        }
    return {
        "available": False,
        "source": None,
        "provenance": "",
        "enabled": False,
        "scope": "unknown",
        "rules": [],
        "signal": RULE_SIGNAL,
        "note": "no inventory artifact carried auto-review rules; coverage is reported as ZERO, "
        "not total — an unmeasured gate is an open gate",
    }


def charter_text_probe(root: pathlib.Path) -> Dict[str, Any]:
    """Is charter TEXT readable anywhere, for the approval-language question the brief asks?

    The audit and the inventory carry `description_chars` and no body, so the answer for live
    Bots is no. The POSITIVE CONTROL is `templates/*.json`, which carry a full `charter` string:
    if the control also came back empty the detector would be broken rather than the data
    missing, and this block would be worthless.
    """
    with_charter = 0
    with_boundary = 0
    for path in sorted((root / "templates").glob("*.json")):
        doc = load(path)
        if not isinstance(doc, dict):
            continue
        if str(doc.get("charter") or "").strip():
            with_charter += 1
        if str(doc.get("approval_boundary") or "").strip():
            with_boundary += 1
    return {
        "live_bot_charter_text_on_disk": False,
        "why": "audit `bots[]` and inventory rows carry description_chars and no body",
        "positive_control_templates_with_charter_text": with_charter,
        "positive_control_templates_with_approval_boundary": with_boundary,
        "control_held": with_charter > 0,
        "roadmap_row": "no-charter-text-in-corpus",
    }


# ---------------------------------------------------------------------------------------------
# The join
# ---------------------------------------------------------------------------------------------
def scope_block(audit: Dict[str, Any], bots: int, write_reach: int) -> Dict[str, Any]:
    """The account-level scope, stated as the amplifier it is."""
    mcp = audit.get("mcp") or {}
    return {
        "level": "account",
        "per_bot_scope": False,
        "note": str(mcp.get("scope_note") or ""),
        "effective_server_names": list(mcp.get("effective_server_names") or []),
        "amplifier": (
            "the effective MCP config is answered per ACCOUNT, so there is no lever that "
            "reduces one Bot's reach. Removing a connector removes it from all {b} Bots, "
            "including the ones that never run; leaving it installed grants it to all {b}, "
            "including the ones that run on a schedule. {w} write-shaped tool instances are "
            "granted {b} times over, once per Bot, by a single account-level decision."
        ).format(b=bots, w=write_reach),
    }


def build_from(
    audit: Dict[str, Any],
    approval: Dict[str, Any],
    charter: Dict[str, Any],
    audit_path: str = "",
) -> Dict[str, Any]:
    """The whole join, from an already-loaded audit. Split from `build` so the selftest can
    drive it with inline fixtures and never touch the disk."""
    mcp = audit.get("mcp") or {}
    servers = [s for s in (mcp.get("servers") or []) if isinstance(s, dict)]
    rules = [str(r) for r in approval.get("rules") or []]

    tools = tools_from(servers, rules)
    rows = connector_rows(tools, servers)
    caps = distinct_capabilities(tools)

    writes = [t for t in tools if t.kind == KIND_WRITE]
    ungated = [t for t in writes if not t.gated]
    connected = [s for s in servers if str(s.get("status") or "") == LIVE_STATUS]
    silent = [
        {
            "server_id": str(s.get("server_id") or ""),
            "status": str(s.get("status") or ""),
        }
        for s in servers
        if str(s.get("status") or "") != LIVE_STATUS
    ]

    bots = bot_rows(audit, len(writes), len(ungated))
    worst = [b for b in bots if b.worst_cell]

    # Two different ways a rule can fail to bite, and they are not the same fact. A rule the
    # LEXICON cannot expand is a limit of this file; a rule that expands fine and still names
    # nothing installed is a fact about the account — "Make a purchase" is a real rule guarding
    # a capability nobody here has. Collapsing them would blame the tool for the account.
    unexpandable = [r for r in rules if not rule_tokens(r)]
    unmatched = [r for r in rules if not any(r in t.named_by for t in tools)]

    return {
        "schema": SCHEMA,
        "audit": {
            "path": audit_path,
            "captured_at": str(audit.get("captured_at") or ""),
            "live_attempted": bool((audit.get("live") or {}).get("attempted")),
        },
        "signal": {
            "classification": TOOL_SIGNAL,
            "classification_is_a_heuristic": True,
            "classification_false_positive_direction": (
                "over-calls write on low-impact named acts; UNDER-states risk on the read side, "
                "because a read-shaped tool is an exfiltration path, not a safe one"
            ),
            "truthful_signal": "MCP tool annotations (readOnlyHint / destructiveHint)",
            "truthful_signal_available": False,
            "truthful_signal_blocker": (
                "bin/gb-deployment-audit.py keeps only tool name and count from "
                "DashboardService/ListSandMcpTools and discards annotations"
            ),
            "coverage": RULE_SIGNAL,
            "coverage_is_a_heuristic": True,
            "coverage_measures": "what the review rules SAY, never what the platform ENFORCES",
        },
        "tools": {
            "registered_servers": len(servers),
            "servers_answering_with_tools": len(connected),
            "silent_servers": silent,
            "tool_instances": len(tools),
            "distinct_capabilities": len(caps),
            "write_shaped": len(writes),
            "read_shaped": sum(1 for t in tools if t.kind == KIND_READ),
            "unclassified": sum(1 for t in tools if t.kind == KIND_UNKNOWN),
            "unclassified_names": sorted(
                {t.basename for t in tools if t.kind == KIND_UNKNOWN}
            ),
            "write_shaped_distinct": sum(1 for k in caps.values() if k == KIND_WRITE),
            "read_shaped_distinct": sum(1 for k in caps.values() if k == KIND_READ),
            "unclassified_distinct": sum(1 for k in caps.values() if k == KIND_UNKNOWN),
            "write_named_by_a_rule": len(writes) - len(ungated),
            "write_named_by_no_rule": len(ungated),
            "write_named_by_no_rule_distinct": sorted({t.basename for t in ungated}),
        },
        "connectors": [r.as_json() for r in rows],
        "scope": scope_block(audit, len(bots), len(writes)),
        "approval": dict(
            approval,
            rules_the_lexicon_cannot_expand=unexpandable,
            rules_matching_no_installed_tool=unmatched,
        ),
        "charter": charter,
        "bots": [b.as_json() for b in bots],
        "worst_cell": {
            "definition": (
                "reaches at least one write-shaped tool that NO review rule names, AND runs "
                "unattended on an enabled routine, AND cannot notify its operator"
            ),
            "count": len(worst),
            "denominator": len(bots),
            "members": [b.name for b in worst],
            "signal": RULE_SIGNAL,
        },
    }


def build(root: pathlib.Path) -> Optional[Dict[str, Any]]:
    """Read the newest audit that ATTEMPTED a live read and join it. None when there is none.

    `newest_audit` rather than a glob, on purpose: an OFFLINE audit is a valid artifact with
    empty server sections, and taking the newest file rather than the newest LIVE one is how a
    read-only run once blinded every reader in this repo at once.
    """
    path, audit = newest_audit(root)
    if path is None or not isinstance(audit, dict):
        return None
    if not (audit.get("mcp") or {}).get("servers"):
        return None
    return build_from(
        audit, approval_posture(root), charter_text_probe(root), audit_path=str(path)
    )


# ---------------------------------------------------------------------------------------------
# Render
# ---------------------------------------------------------------------------------------------
def render(doc: Dict[str, Any]) -> str:
    t = doc["tools"]
    scope = doc["scope"]
    approval = doc["approval"]
    worst = doc["worst_cell"]
    out: List[str] = []
    a = doc["audit"]
    out.append(
        "  audit {p}  captured {c}  live_read={l}".format(
            p=a["path"] or "(inline)", c=a["captured_at"] or "?", l=a["live_attempted"]
        )
    )
    out.append("")
    out.append(
        "  {i} registered tool instances across {s} servers, of which {c} answered with tools "
        "({z} silent)".format(
            i=t["tool_instances"],
            s=t["registered_servers"],
            c=t["servers_answering_with_tools"],
            z=len(t["silent_servers"]),
        )
    )
    out.append(
        "  {d} DISTINCT capabilities after folding repeat installs of the same connector".format(
            d=t["distinct_capabilities"]
        )
    )
    out.append(
        "  WRITE-shaped {w} of {i} instances ({wd} of {d} distinct) · read {r} · unclassified {u}"
        "   [HEURISTIC {sig}]".format(
            w=t["write_shaped"],
            i=t["tool_instances"],
            wd=t["write_shaped_distinct"],
            d=t["distinct_capabilities"],
            r=t["read_shaped"],
            u=t["unclassified"],
            sig=TOOL_SIGNAL,
        )
    )
    if t["unclassified_names"]:
        out.append(
            "  unclassified, counted as NEITHER read nor write: "
            + ", ".join(t["unclassified_names"])
        )
    out.append(
        "  read-shaped is NOT harmless: it is the exfiltration half. This ranks on write "
        "because write is the irreversible half."
    )
    out.append("")
    out.append(
        "  CONNECTOR            INST  TOOLS  WRITE   READ   ?   RULED  UNRULED  SHARE"
    )
    out.append("  " + "-" * 78)
    total_write = max(1, int(t["write_shaped"]))
    for c in doc["connectors"]:
        out.append(
            "  {n:<20} {i:>4} {t:>6} {w:>6} {r:>6} {u:>3} {g:>7} {x:>8} {s:>5.0f}%".format(
                n=c["connector"][:20],
                i=c["installs"],
                t=c["tool_instances"],
                w=c["write_shaped"],
                r=c["read_shaped"],
                u=c["unclassified"],
                g=c["write_named_by_a_rule"],
                x=c["write_named_by_no_rule"],
                s=100.0 * c["write_shaped"] / total_write,
            )
        )
    out.append("")
    out.append(
        "  APPROVAL POSTURE — {n} review rule(s), scope={s}, enabled={e}  [HEURISTIC {sig}]".format(
            n=len(approval["rules"]),
            s=approval["scope"],
            e=approval["enabled"],
            sig=RULE_SIGNAL,
        )
    )
    if not approval["available"]:
        out.append("    " + str(approval.get("note") or ""))
    for r in approval["rules"]:
        if r in approval["rules_the_lexicon_cannot_expand"]:
            mark = " (this file's lexicon cannot map this rule to any tool name)"
        elif r in approval["rules_matching_no_installed_tool"]:
            mark = " (names nothing installed on this account)"
        else:
            mark = ""
        out.append("    · {r}{m}".format(r=r, m=mark))
    out.append(
        "    {g} of {w} write-shaped instances are named by a rule; {x} are named by NONE: {n}".format(
            g=t["write_named_by_a_rule"],
            w=t["write_shaped"],
            x=t["write_named_by_no_rule"],
            n=", ".join(t["write_named_by_no_rule_distinct"]) or "(none)",
        )
    )
    out.append(
        "    this measures what the rules SAY. Nothing on disk binds a rule to a tool call, so "
        "ENFORCEMENT is not measured here."
    )
    ch = doc["charter"]
    out.append(
        "    charter TEXT for live Bots is not on disk ({w}); positive control held: {c} "
        "templates carry charter text, {b} carry an approval_boundary".format(
            w=ch["why"],
            c=ch["positive_control_templates_with_charter_text"],
            b=ch["positive_control_templates_with_approval_boundary"],
        )
    )
    out.append("")
    out.append(
        "  BOT                  ROUTINES  UNATTENDED  NOTIFIES  WRITE  UNRULED  CELL"
    )
    out.append("  " + "-" * 78)
    for b in doc["bots"]:
        out.append(
            "  {n:<20} {r:>8}  {u:<10}  {o:<8}  {w:>5}  {x:>7}  {c}".format(
                n=b["bot"][:20],
                r="{e}/{t}".format(e=b["enabled_routines"], t=b["routines"]),
                u="yes" if b["unattended"] else "no",
                o="yes" if b["can_notify"] else "NO",
                w=b["write_reach"],
                x=b["write_reach_named_by_no_rule"],
                c="WORST" if b["worst_cell"] else "",
            )
        )
    out.append("")
    out.append(
        "  WORST CELL: {c} of {d} Bots — {m}".format(
            c=worst["count"],
            d=worst["denominator"],
            m=", ".join(worst["members"]) or "(none)",
        )
    )
    out.append("    " + str(worst["definition"]))
    out.append("")
    out.append("  SCOPE: " + str(scope["note"] or "account-level"))
    out.append("    " + str(scope["amplifier"]))
    return "\n".join(out)


# ---------------------------------------------------------------------------------------------
# Selftest — inline fixtures only. Every leg names the defect it would catch.
# ---------------------------------------------------------------------------------------------
FIX_SERVERS: List[Dict[str, Any]] = [
    {
        "server_id": "user-Mail",
        "status": "connected",
        "tool_count": 4,
        "tool_names": [
            "user-Mail-send_message",
            "user-Mail-get_message",
            "user-Mail-create_label",
            "user-Mail-suggest_time",
        ],
    },
    # The SAME connector, installed a second time. Four capabilities, eight instances.
    {
        "server_id": "user-Mail--second",
        "status": "connected",
        "tool_count": 4,
        "tool_names": [
            "user-Mail--second-send_message",
            "user-Mail--second-get_message",
            "user-Mail--second-create_label",
            "user-Mail--second-suggest_time",
        ],
    },
    # KNOWN BAD: registered, reachable in the config, and contributing nothing.
    {
        "server_id": "user-Store",
        "status": "needsAuth",
        "tool_count": 0,
        "tool_names": [],
    },
]

FIX_AUDIT: Dict[str, Any] = {
    "captured_at": "2026-01-01T00:00:00+00:00",
    "live": {"attempted": True},
    "mcp": {
        "servers": FIX_SERVERS,
        "effective_server_names": ["Mail", "Store"],
        "scope_note": "account-level (no per-Bot scope in the returned config)",
    },
    "bots": [
        {
            "id": "u-cron",
            "name": "Cron",
            "notifications_enabled": False,
            "idle_days": 0.1,
        },
        {
            "id": "u-idle",
            "name": "Idle",
            "notifications_enabled": False,
            "idle_days": 9.0,
        },
        {
            "id": "u-loud",
            "name": "Loud",
            "notifications_enabled": True,
            "idle_days": 0.2,
        },
        {
            "id": "u-off",
            "name": "Off",
            "notifications_enabled": False,
            "idle_days": 3.0,
        },
    ],
    "routines": {
        "bots": [
            {
                "name": "Cron",
                "routines": [
                    {
                        "name": "daily",
                        "enabled": True,
                        "schedule": "CRON_TZ=UTC 0 9 * * *",
                        "trigger": "cron",
                    }
                ],
            },
            {"name": "Idle", "routines": []},
            {
                "name": "Loud",
                "routines": [
                    {
                        "name": "weekly",
                        "enabled": True,
                        "schedule": "0 9 * * 1",
                        "trigger": "cron",
                    }
                ],
            },
            # KNOWN BAD: a routine that exists and is switched OFF. Not unattended.
            {
                "name": "Off",
                "routines": [
                    {
                        "name": "paused",
                        "enabled": False,
                        "schedule": "0 9 * * 1",
                        "trigger": "cron",
                    }
                ],
            },
        ]
    },
}

FIX_RULES: List[str] = [
    "Send any external email or message",
    "Delete or overwrite any file or record",
    "Make a purchase, a payment, or use cards",
]

FIX_APPROVAL: Dict[str, Any] = {
    "available": True,
    "source": "(inline)",
    "provenance": "selftest fixture",
    "enabled": True,
    "scope": "account",
    "rules": FIX_RULES,
    "signal": RULE_SIGNAL,
}

FIX_CHARTER: Dict[str, Any] = {
    "live_bot_charter_text_on_disk": False,
    "why": "fixture",
    "positive_control_templates_with_charter_text": 2,
    "positive_control_templates_with_approval_boundary": 2,
    "control_held": True,
    "roadmap_row": "no-charter-text-in-corpus",
}


def selftest() -> int:
    # The banner carries the word `blast` on purpose: the roadmap probe for this row needs a
    # CONTROL token that is present whether the verb is wired or not, so an empty or
    # misdirected capture cannot read as a close. Unwired, `gb blast` answers
    # "invalid choice: 'blast'"; wired, it answers this line. Both contain the control.
    print("  gb-blast — blast radius properties")
    legs: List[Tuple[str, bool, str]] = []

    def leg(name: str, ok: bool, detail: str = "") -> None:
        legs.append((name, bool(ok), detail))

    doc = build_from(FIX_AUDIT, FIX_APPROVAL, FIX_CHARTER, audit_path="(inline)")
    t = doc["tools"]
    by_name = {b["bot"]: b for b in doc["bots"]}

    # 1 — the server prefix comes off exactly, and a name without it is left alone.
    leg(
        "server prefix stripped exactly, never by length",
        strip_server_prefix("user-Mail", "user-Mail-send_message") == "send_message"
        and strip_server_prefix("user-Mail", "send_message") == "send_message"
        and strip_server_prefix("user-Mail", "user-Mailbox-send")
        == "user-Mailbox-send",
        "known-bad: a length-trim would read 'box-send' as the tool",
    )

    # 2 — repeat installs fold to one connector; different connectors do not fold.
    leg(
        "install suffix folds, connector identity does not",
        family_of("user-Gmail--chiefzester") == "user-Gmail"
        and family_of("user-Gmail") == "user-Gmail"
        and family_of("user-Store") != family_of("user-Mail"),
    )

    # 3 — read verbs classify read, and a write verb does not sneak in.
    leg(
        "read verbs classify read",
        classify("get_message") == KIND_READ
        and classify("search_threads") == KIND_READ
        and classify("send_message") != KIND_READ,
    )

    # 4 — write verbs classify write, and a read verb does not.
    leg(
        "write verbs classify write",
        classify("send_message") == KIND_WRITE
        and classify("execute_sql") == KIND_WRITE
        and classify("reply") == KIND_WRITE
        and classify("get_message") != KIND_WRITE,
    )

    # 5 — FIRES ON KNOWN BAD. An unrecognised verb must be UNKNOWN, never silently read.
    #     Folding it into read is how a connector full of unseen verbs reports zero blast radius.
    leg(
        "unknown verbs are unknown, never folded into read",
        classify("suggest_time") == KIND_UNKNOWN
        and classify("confirm_cost") == KIND_UNKNOWN
        and t["unclassified"] == 2
        and t["unclassified_names"] == ["suggest_time"],
        "known-bad: suggest_time counted safe would understate reach",
    )

    # 6 — the census partitions. Every instance lands in exactly one bucket.
    leg(
        "read + write + unknown == tool instances",
        t["read_shaped"] + t["write_shaped"] + t["unclassified"] == t["tool_instances"]
        and t["tool_instances"] == 8,
    )

    # 7 — FIRES ON KNOWN BAD. Instances and capabilities are different numbers, and the fixture
    #     installs the same connector twice so a run that conflates them cannot pass.
    leg(
        "instances and distinct capabilities are counted separately",
        t["tool_instances"] == 8
        and t["distinct_capabilities"] == 4
        and t["write_shaped"] == 4
        and t["write_shaped_distinct"] == 2,
        "known-bad: reporting 4 for both is the conflation that produced the prior 112",
    )

    # 8 — a registered server that answered nothing is still registered, and named.
    leg(
        "silent servers counted as registered, not as reach",
        t["registered_servers"] == 3
        and t["servers_answering_with_tools"] == 2
        and [s["server_id"] for s in t["silent_servers"]] == ["user-Store"],
        "known-bad: 'tools across N servers' hides that N-2 of them are dark",
    )

    # 9 — the lexicon maps a rule to the acts it names, and NOT to acts it does not.
    leg(
        "rule lexicon maps what a rule says and no more",
        "send" in rule_tokens(FIX_RULES[0])
        and "message" in rule_tokens(FIX_RULES[0])
        and "label" not in rule_tokens(FIX_RULES[0])
        and "create" not in rule_tokens(FIX_RULES[1]),
        "known-bad: a lexicon wide enough to catch 'create' would report every write reviewed",
    )

    # 10 — FIRES ON KNOWN BAD. The two ways a rule fails to bite are reported separately: the
    #      payment rule expands fine and simply names nothing installed here, which is a fact
    #      about the ACCOUNT, not a limit of this file. Collapsing them blames the wrong party.
    leg(
        "a rule naming nothing installed is distinguished from one the lexicon cannot map",
        FIX_RULES[2] in doc["approval"]["rules_matching_no_installed_tool"]
        and FIX_RULES[2] not in doc["approval"]["rules_the_lexicon_cannot_expand"]
        and FIX_RULES[0] not in doc["approval"]["rules_matching_no_installed_tool"]
        and doc["approval"]["rules_the_lexicon_cannot_expand"] == [],
        "known-bad: one bucket would read 'the tool cannot see this rule' for a rule guarding "
        "a capability nobody installed",
    )

    # 11 — coverage picks out exactly the creation nobody wrote a rule for.
    leg(
        "write tools named by no rule are exactly the unruled ones",
        t["write_named_by_no_rule"] == 2
        and t["write_named_by_no_rule_distinct"] == ["create_label"]
        and t["write_named_by_a_rule"] == 2,
        "known-bad: send_message must be gated, create_label must not",
    )

    # 12 — FIRES ON KNOWN BAD. No rules at all means NO coverage, not total coverage.
    blind = build_from(
        FIX_AUDIT, dict(FIX_APPROVAL, available=False, rules=[]), FIX_CHARTER
    )
    leg(
        "no readable rules reports zero coverage, not total",
        blind["tools"]["write_named_by_no_rule"] == 4
        and blind["tools"]["write_named_by_a_rule"] == 0
        and blind["worst_cell"]["count"] >= 1,
        "known-bad: an unmeasured gate rendering as a closed gate",
    )

    # 13 — account-level scope: every Bot's reach is the census total, identically.
    leg(
        "account scope gives every Bot the same reach",
        {b["write_reach"] for b in doc["bots"]} == {t["write_shaped"]}
        and doc["scope"]["per_bot_scope"] is False
        and "reduces one Bot's reach" in doc["scope"]["amplifier"],
    )

    # 14 — FIRES ON KNOWN BAD. A DISABLED routine is not a schedule.
    leg(
        "a disabled routine is not unattended",
        by_name["Off"]["routines"] == 1
        and by_name["Off"]["enabled_routines"] == 0
        and by_name["Off"]["unattended"] is False
        and by_name["Cron"]["unattended"] is True,
        "known-bad: counting 'Off' would put a paused Bot in the unattended column",
    )

    # 15 — FIRES ON KNOWN BAD. A Bot that CAN interrupt you leaves the worst cell.
    leg(
        "notifications lift a Bot out of the worst cell",
        by_name["Loud"]["unattended"] is True
        and by_name["Loud"]["can_notify"] is True
        and by_name["Loud"]["worst_cell"] is False,
        "known-bad: ranking on write alone would flag Loud identically to Cron",
    )

    # 16 — FIRES ON KNOWN BAD. An idle Bot with the same reach is not the same risk.
    leg(
        "no routine lifts a Bot out of the worst cell",
        by_name["Idle"]["write_reach"] == by_name["Cron"]["write_reach"]
        and by_name["Idle"]["worst_cell"] is False,
        "known-bad: identical reach, different risk — the whole point of the verb",
    )

    # 17 — the worst cell is counted with its denominator and its members named.
    leg(
        "worst cell counted against its denominator",
        doc["worst_cell"]["count"] == 1
        and doc["worst_cell"]["denominator"] == 4
        and doc["worst_cell"]["members"] == ["Cron"],
    )

    # 18 — the ranking puts the worst cell first rather than sorting by name.
    leg(
        "ranking leads with the worst cell",
        doc["bots"][0]["bot"] == "Cron"
        and [b["bot"] for b in doc["bots"]][1] != "Idle",
        "known-bad: alphabetical order would lead with Cron by accident, so Loud/Idle order "
        "is checked too",
    )

    # 19 — both heuristics are DISCLOSED in the rendered text, not only in the JSON.
    text = render(doc)
    leg(
        "every heuristic is labelled in the rendered output",
        TOOL_SIGNAL in text
        and RULE_SIGNAL in text
        and "exfiltration" in text
        and "WORST CELL: 1 of 4" in text
        and all(b["bot"] in text for b in doc["bots"]),
        "known-bad: a heuristic disclosed only in a docstring is not disclosed",
    )

    # 20 — no audit, no answer. An empty deployment directory must refuse, not print zeros.
    leg(
        "a missing audit refuses rather than printing zeros",
        build(pathlib.Path("/nonexistent-gb-blast-root")) is None,
    )

    ok = sum(1 for _, good, _ in legs if good)
    for name, good, detail in legs:
        print(
            "  {m} {n}{d}".format(
                m="ok  " if good else "FAIL",
                n=name,
                d=(" — " + detail) if detail else "",
            )
        )
    print(
        "SELFTEST {v} - {o}/{n}".format(
            v="PASS" if ok == len(legs) else "FAIL", o=ok, n=len(legs)
        )
    )
    return 0 if ok == len(legs) else 1


# ---------------------------------------------------------------------------------------------
@dataclasses.dataclass(frozen=True)
class BlastArgs:
    """What your Bots can do unattended, and how bad it could get."""

    json: bool = gbargs.arg(default=False, help="machine-readable envelope on stdout")
    selftest: bool = gbargs.arg(default=False, help="prove this module's properties")


def body() -> int:
    args = gbargs.parse(BlastArgs, sys.argv[1:])
    if args.selftest:
        return selftest()
    doc = build(ROOT)
    if doc is None:
        sys.stderr.write(
            "ERROR no deployment audit with an MCP census on disk — run "
            "`gb deployment audit --live` (or bin/gb-deployment-audit.py) first. "
            "A clone has no audit, and blast radius is a property of YOUR account, "
            "not of this repository.\n"
        )
        return 3
    if args.json:
        print(json.dumps(doc, indent=1, sort_keys=False))
        return 0
    print(render(doc))
    return 0


if __name__ == "__main__":
    gbtypes.main(body)
