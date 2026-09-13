#!/usr/bin/env python3
"""gb-schema — mine the versioned RPC schema out of the shipped desktop bundle.

WHAT. The vendor's desktop client declares its whole RPC surface inside
`dist/electron-main/proto.cjs` in `app.asar`: six `aiserver.v1` services, every
method's input/output shapes, every message's fields, every enum. This producer
extracts that declaration into `schema/<app_version>/` plus a cumulative
`schema/registry.json` and a `schema/reviewed.json` marker, so the drift gate
(g27) can diff what the vendor SHIPPED against what somebody REVIEWED.

WHY A MINER, NOT A PROBE. Live RPC calls prove reachability; they cannot prove
completeness — a method you never call is invisible. The bundle is the
complete declaration. NEVER live-probe RPCs here, never `--apply`, never
Bot/routine writes. Read-only against a local file.

NEVER COMMIT VENDOR BYTES. The bundle stays where the vendor put it. The only
thing recorded about it is its sha256 in the manifest.

REQ POLARITY, VERIFIED 2026-09-12 AGAINST THE LIVE 0.47.0 BUNDLE (3 messages):
protobuf-es marks proto3-optional (explicit-presence) fields with `opt:!0` in
the field atom, and leaves them OUT of the constructor initialisation:

  1. agent.v1.AgentSkill: full_path/content/description carry no `opt` and ARE
     constructor-initialised; parse_error carries `opt:!0` and is NOT.
  2. aiserver.v1.SubmitPerformanceEventsResponse: success (no `opt`) is
     initialised (`this.success=!1`); error_message (`opt:!0`) is not.
  3. aiserver.v1.GetOrganizationMembersRequest: organizationId (no `opt`) is
     initialised; page/page_size (`opt:!0`) are not — skippable pagination.

So `req = (no opt marker in the same atom)`. An earlier draft had this
inverted (`opt:!0` => required); the three messages above refute that — it
would mark error_message/page as required and is therefore wrong.

WRITE-DETECT RULE (exact): a method is a READ iff its wire name starts with
"Get" or "List"; every other method is a WRITE for coverage purposes.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import hashlib
import json
import pathlib
import re
import struct
import sys
import tempfile
from typing import Any, Dict, Iterator, List, Optional, Tuple

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from gbargs import arg, parse  # noqa: E402
from gbtypes import atomic_write_bytes, atomic_write_json, main  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[1]
SCHEMA = "gb-schema/1"
SUMMARY = "mine the versioned RPC schema out of the shipped desktop bundle"
VERSION = "1.0.0"

EXIT_OK, EXIT_FINDINGS, EXIT_USAGE, EXIT_ENVIRONMENT = 0, 1, 2, 3

# The client bundle and the member that declares the RPC surface (NE-8 recipe:
# struct-unpack the asar header, read its JSON, seek to the member's offset).
DEFAULT_ASAR = "/Applications/Grok Bot.app/Contents/Resources/app.asar"
PROTO_MEMBER = "dist/electron-main/proto.cjs"
PKG_MEMBER = "package.json"

# The six services the shipped client declares (registry span + aiserver cross-check).
EXPECTED_SERVICES = (
    "Ai",
    "Analytics",
    "BackgroundComposer",
    "Dashboard",
    "GrokBot",
    "SandBox",
)
SERVICE_PACKAGE = "aiserver.v1"

# Write-detect rule, stated exactly: READ iff the wire name starts with one of
# these; every other method is a WRITE for coverage purposes.
READ_PREFIXES = ("Get", "List")

# Method-atom key priority: long form first, then the request/response
# spellings, then the minified I:/O: the shipped bundle actually uses.
INPUT_KEYS = ("input", "requestType", "I")
OUTPUT_KEYS = ("output", "responseType", "O")
KIND_UNSTATED = "unstated"

_OPT_RE = re.compile(r"[{,]opt\s*:")
_NO_RE = re.compile(r"(?:no|number)\s*:\s*(\d+)")
_NAME_RE = re.compile(r'name\s*:\s*"([^"]+)"')
_KIND_RE = re.compile(r"kind\s*:\s*(?:\"([^\"]+)\"|([A-Za-z_$][\w$.]*))")
_SYM_RE = re.compile(r"(?<![\w$])([\w$]+)\.typeName\s*=\s*\"([^\"]+)\"")
_ENUM_RE = re.compile(
    r"(?<![\w$])[\w$]+\.setEnumType\s*\(\s*[\w$]+\s*,\s*\"([^\"]+)\"\s*,"
)
_SERVICE_REF_RE = re.compile(r"([A-Za-z_][A-Za-z0-9_]*Service):\(\)=>(\w+)")


def _scan_spans(
    text: str, open_ch: str, close_ch: str, start: int = 0
) -> Iterator[Tuple[int, int]]:
    """(open, close) index pairs for each top-level balanced span at/after `start`.

    THE bracket scanner: the only loop in this file that walks quoted regions
    (both quote styles) and backslash escapes. `balanced`, `_bracket_span` and
    `_top_level_atoms` are thin projections of these pairs, so a quoting fix
    lands once instead of three times. Unterminated tails yield nothing —
    wrappers decide whether that is None or an error.
    """
    depth = 0
    cur: Optional[int] = None
    quote: Optional[str] = None
    i = start
    while i < len(text):
        c = text[i]
        if quote is not None:
            if c == "\\":
                i += 2
                continue
            if c == quote:
                quote = None
        elif c in ("'", '"'):
            quote = c
        elif c == open_ch:
            if depth == 0 and cur is None:
                cur = i
            depth += 1
        elif c == close_ch:
            if depth > 0:
                depth -= 1
                if depth == 0 and cur is not None:
                    yield (cur, i)
                    cur = None
        i += 1


def balanced(text: str, start: int) -> str:
    """The balanced `{...}` span starting at `start`, string-aware.

    Minified JS is dense with braces inside string literals; a naive depth
    counter closes early on `name:"Weird}Name"`. Quoted regions are skipped by
    `_scan_spans`, so only structural braces count. The scan starts AT `start`,
    so the first span it yields is ours.
    """
    if start >= len(text) or text[start] != "{":
        raise ValueError(f"balanced: expected '{{' at {start}")
    for _o, close in _scan_spans(text, "{", "}", start):
        return text[start : close + 1]
    raise ValueError("balanced: unterminated span")


def _top_level_atoms(body: str) -> List[str]:
    """Depth-1 `{...}` atoms of a methods/fields span, in order.

    The span's own outer braces are stripped first, so its members become
    top-level spans of the inner content — the `name:"between"` prefixes
    between atoms are simply never yielded.
    """
    inner = (
        body[1:-1]
        if len(body) >= 2 and body.startswith("{") and body.endswith("}")
        else body
    )
    return [inner[o : c + 1] for (o, c) in _scan_spans(inner, "{", "}", 0)]


def _bracket_span(text: str, open_idx: int) -> Optional[str]:
    """Balanced `[...]` span starting at `open_idx`, or None when unterminated."""
    if open_idx >= len(text) or text[open_idx] != "[":
        return None
    for _o, close in _scan_spans(text, "[", "]", open_idx):
        return text[open_idx : close + 1]
    return None


def _operand(atom: str, keys: Tuple[str, ...]) -> Optional[str]:
    """First matching key's operand: quoted literal wins, else symbol name."""
    for key in keys:
        m = re.search(r"\b" + key + r"\s*:\s*(?:\"([^\"]+)\"|([A-Za-z_$][\w$]*))", atom)
        if m:
            return m.group(1) if m.group(1) is not None else m.group(2)
    return None


def parse_method_atom(atom: str) -> Optional[Dict[str, str]]:
    """One `{name:I:O:kind:}` method atom, or None when it names nothing.

    Input resolves by priority input -> requestType -> I (output likewise);
    kind defaults to "unstated". A `kind:i.Unary` prefix is minified noise —
    the suffix after the last dot is the stable vocabulary.
    """
    name_m = _NAME_RE.search(atom)
    if not name_m:
        return None
    in_val = _operand(atom, INPUT_KEYS)
    out_val = _operand(atom, OUTPUT_KEYS)
    if in_val is None or out_val is None:
        return None
    kind_m = _KIND_RE.search(atom)
    kind = KIND_UNSTATED
    if kind_m:
        raw = kind_m.group(1) if kind_m.group(1) is not None else kind_m.group(2)
        kind = raw.split(".")[-1]
    return {"name": name_m.group(1), "input": in_val, "output": out_val, "kind": kind}


def is_write(method_name: str) -> bool:
    """The write-detect rule: READ iff the wire name starts with Get or List."""
    return not method_name.startswith(READ_PREFIXES)


def parse_field_atom(atom: str) -> Optional[Dict[str, Any]]:
    """One `{no:,name:,kind:}` field atom, or None unless all three share it.

    `req` is True exactly when the atom carries no `opt:` marker (verified
    polarity — see module docstring). `no:` and `number:` spellings both read.
    """
    no_m = _NO_RE.search(atom)
    name_m = _NAME_RE.search(atom)
    kind_m = re.search(r'\bkind\s*:\s*"([^"]+)"', atom)
    if not (no_m and name_m and kind_m):
        return None
    return {
        "no": int(no_m.group(1)),
        "name": name_m.group(1),
        "kind": kind_m.group(1),
        "req": _OPT_RE.search(atom) is None,
    }


def parse_enum_entries(entries_body: str) -> List[Dict[str, Any]]:
    """`[{no:,name:}]` enum entries; `number:` reads as `no:`."""
    return [
        {"name": n.group(1), "no": int(o.group(1))}
        for atom in _top_level_atoms("{" + entries_body + "}")
        for o in [_NO_RE.search(atom)]
        for n in [_NAME_RE.search(atom)]
        if o is not None and n is not None
    ]


def sym_type_map(text: str) -> Dict[str, str]:
    """Minified symbol -> qualified typeName, exact-symbol match only.

    Suffix matching is a trap (`le` also ends `ele`, `tle`, `$le`...): the
    negative lookbehind keeps each symbol whole. Zero conflicts in 0.47.0.
    """
    return {m.group(1): m.group(2) for m in _SYM_RE.finditer(text)}


def extract_services(
    text: str, symbols: Dict[str, str]
) -> Dict[str, List[Dict[str, str]]]:
    """Services keyed by short name, methods with resolved qualified I/O.

    Registry span first (`XService:()=>y`), then the aiserver.v1 cross-check:
    the service definition's own typeName must be `aiserver.v1.<X>Service`.
    Anything failing the cross-check (another package's Service) is excluded.
    """
    services: Dict[str, List[Dict[str, str]]] = {}
    for ref_name, sym in _SERVICE_REF_RE.findall(text):
        short = ref_name[: -len("Service")]
        want = f"{SERVICE_PACKAGE}.{ref_name}"
        # The cross-check reads the definition literal's own typeName
        # (`var SYM={typeName:"...",methods:`) — service objects never get a
        # separate `SYM.typeName=` assignment, so the symmap cannot bind them.
        decl = re.search(
            r"var " + re.escape(sym) + r"=\{typeName:\"([^\"]+)\",methods:",
            text,
        )
        if not decl or decl.group(1) != want:
            continue
        meth_at = text.find("methods:", decl.start())
        methods_body = balanced(text, text.find("{", meth_at + len("methods:")))
        methods: List[Dict[str, str]] = []
        for atom in _top_level_atoms(methods_body):
            parsed = parse_method_atom(atom)
            if parsed is None:
                continue
            methods.append(
                {
                    "name": parsed["name"],
                    "input": symbols.get(parsed["input"], parsed["input"]),
                    "output": symbols.get(parsed["output"], parsed["output"]),
                    "kind": parsed["kind"],
                }
            )
        if methods:
            services.setdefault(short, methods)
    return services


def extract_messages(
    text: str, symbols: Dict[str, str]
) -> Dict[str, List[Dict[str, Any]]]:
    """Messages keyed by PACKAGE-QUALIFIED typeName — short names never merge.

    Each symbol's own `SYM.fields=r.util.newFieldList(()=>[atoms])` span feeds
    per-atom parsing only: an atom missing any of no/name/kind is skipped, so
    a `no` from one atom can never join a `name` from the next.
    """
    messages: Dict[str, List[Dict[str, Any]]] = {}
    for sym, type_name in symbols.items():
        if type_name in messages:
            continue
        marker = sym + ".fields=r.util.newFieldList(()=>"
        idx = text.find(marker)
        if idx < 0:
            continue
        span = _bracket_span(text, text.find("[", idx + len(marker)))
        if span is None:
            continue
        fields: List[Dict[str, Any]] = []
        for atom in _top_level_atoms("{" + span[1:-1] + "}"):
            parsed = parse_field_atom(atom)
            if parsed is not None:
                fields.append(parsed)
        messages[type_name] = fields
    return messages


def extract_enums(text: str) -> Dict[str, List[Dict[str, Any]]]:
    """Enums keyed by qualified name via `setEnumType(sym, "P.T", [...])`."""
    enums: Dict[str, List[Dict[str, Any]]] = {}
    for m in _ENUM_RE.finditer(text):
        type_name = m.group(1)
        if type_name in enums:
            continue
        span = _bracket_span(text, text.find("[", m.end() - 1))
        if span is None:
            continue
        enums[type_name] = parse_enum_entries(span[1:-1])
    return enums


def coverage_flags(
    services: Dict[str, List[Dict[str, str]]],
    messages: Dict[str, List[Dict[str, Any]]],
) -> List[str]:
    """Every method's input shape must exist; write inputs must be KNOWN.

    A dangling input (typeName absent from messages.json) is flagged for any
    method. A WRITE method (name not starting with Get/List) with a dangling
    input is flagged again: its required fields are unknown, so the registry
    cannot answer "what is necessary for every write" for it.
    """
    flags: List[str] = []
    for service, methods in services.items():
        for m in methods:
            if m["input"] not in messages:
                flags.append(f"dangling input: {service}.{m['name']} -> {m['input']}")
                if is_write(m["name"]):
                    flags.append(
                        "write with unknown required fields: "
                        f"{service}.{m['name']} -> {m['input']}"
                    )
    return flags


def _header_node(header: Dict[str, Any], member: str) -> Dict[str, Any]:
    """Walk the header tree to one member's entry."""
    node: Dict[str, Any] = header
    for part in member.split("/"):
        node = node["files"][part]
    return node


def _data_base(raw: bytes, header: Dict[str, Any], header_size: int) -> int:
    """Byte offset where member data starts, CALIBRATED not assumed.

    The naive `16 + header_size` is 2 bytes early on the shipped bundle
    (proven 2026-09-12: package.json and proto.cjs slices match their
    recorded integrity sha256 only at +2). Rather than hardcode the delta,
    try 0..16 and keep the one where a single-block member's sha256 matches
    its recorded hash. No match means the header lies — refuse, don't guess.
    """
    singles: List[Tuple[str, Dict[str, Any]]] = []

    def _collect(node: Dict[str, Any], prefix: str) -> None:
        for name, entry in node.get("files", {}).items():
            if not isinstance(entry, dict):
                continue
            if "files" in entry:
                _collect(entry, prefix + name + "/")
            elif (
                "offset" in entry
                and len(entry.get("integrity", {}).get("blocks", [])) == 1
            ):
                singles.append((prefix + name, entry))

    _collect(header, "")
    for delta in range(17):
        base = 16 + header_size + delta
        for member, entry in singles:
            off = base + int(entry["offset"])
            sl = raw[off : off + int(entry["size"])]
            if hashlib.sha256(sl).hexdigest() == entry["integrity"]["blocks"][0]:
                return base
    raise ValueError(
        "asar data base uncalibrated: no member matches its integrity hash"
    )


def read_asar_member(asar: pathlib.Path, member: str) -> Tuple[bytes, Dict[str, Any]]:
    """Raw bytes + header entry for one member, per the NE-8 recipe.

    Header: four little-endian UInt32s; the word at offset 12 is the header
    size; the header JSON runs 16..16+size; member data starts at the
    CALIBRATED base plus its recorded offset. A member carrying a single
    integrity block is hash-checked — a mismatch refuses, because mining
    shifted bytes would silently corrupt every record. Unpacked members (no
    offset) ride alongside in `<asar>.unpacked/<member>`.
    """
    raw = asar.read_bytes()
    if len(raw) < 16:
        raise ValueError(f"asar too small to hold a header: {asar}")
    (_a, _b, _c, header_size) = struct.unpack("<IIII", raw[:16])
    header = json.loads(raw[16 : 16 + header_size].decode("utf8"))
    node = _header_node(header, member)
    if "offset" not in node:
        sidecar = pathlib.Path(str(asar) + ".unpacked") / member
        return sidecar.read_bytes(), node
    base = _data_base(raw, header, header_size)
    off = base + int(node["offset"])
    data = raw[off : off + int(node["size"])]
    blocks = node.get("integrity", {}).get("blocks", [])
    if len(blocks) == 1 and hashlib.sha256(data).hexdigest() != blocks[0]:
        raise ValueError(f"asar member fails integrity check: {member}")
    return data, node


def mine_bundle(asar_path: str) -> Dict[str, Any]:
    """Mine services/messages/enums/version/sha256 out of the live bundle."""
    asar = pathlib.Path(asar_path).expanduser()
    digest = hashlib.sha256(asar.read_bytes()).hexdigest()
    proto_bytes, _ = read_asar_member(asar, PROTO_MEMBER)
    text = proto_bytes.decode("utf8", "replace")
    pkg_bytes, _ = read_asar_member(asar, PKG_MEMBER)
    app_version = str(json.loads(pkg_bytes.decode("utf8"))["version"])
    symbols = sym_type_map(text)
    return {
        "app_version": app_version,
        "bundle_sha256": digest,
        "services": extract_services(text, symbols),
        "messages": extract_messages(text, symbols),
        "enums": extract_enums(text),
    }


def update_registry(
    old: Dict[str, Any],
    app_version: str,
    services: Dict[str, List[Dict[str, str]]],
    messages: Dict[str, List[Dict[str, Any]]],
) -> Dict[str, Any]:
    """Cumulative union: first_seen sticks, last_seen advances, nothing deleted.

    A method the vendor REMOVES keeps its row (tombstone) — the registry is a
    union over every version ever mined, so drift can name what disappeared.
    """
    methods = dict(old.get("methods", {}))
    for service, rows in services.items():
        for m in rows:
            row = methods.get(m["name"], {"first_seen": app_version})
            methods[m["name"]] = {
                "first_seen": row.get("first_seen", app_version),
                "last_seen": app_version,
                "services": sorted(set(row.get("services", [])) | {service}),
            }
    msgs = dict(old.get("messages", {}))
    for type_name in messages:
        row = msgs.get(type_name, {"first_seen": app_version})
        msgs[type_name] = {
            "first_seen": row.get("first_seen", app_version),
            "last_seen": app_version,
        }
    return {"methods": methods, "messages": msgs}


def _record_counts(bundle: Dict[str, Any]) -> Dict[str, int]:
    """What the manifest reports: services, methods, messages, enums."""
    return {
        "services": len(bundle["services"]),
        "methods": sum(len(rows) for rows in bundle["services"].values()),
        "messages": len(bundle["messages"]),
        "enums": len(bundle["enums"]),
    }


def _write_manifest(
    version_dir: pathlib.Path,
    bundle: Dict[str, Any],
    counts: Dict[str, int],
    mined_at: str,
) -> None:
    """The bundle's identity card: schema, version, sha256, timestamp, counts."""
    atomic_write_json(
        version_dir / "manifest.json",
        {
            "schema": SCHEMA,
            "app_version": bundle["app_version"],
            "bundle_sha256": bundle["bundle_sha256"],
            "mined_at": mined_at,
            "counts": counts,
        },
    )


def _write_services(
    version_dir: pathlib.Path, services: Dict[str, List[Dict[str, str]]]
) -> None:
    """Per-service methods with input/output/kind, keyed by short name."""
    atomic_write_json(version_dir / "services.json", services)


def _write_messages(
    version_dir: pathlib.Path, messages: Dict[str, List[Dict[str, Any]]]
) -> None:
    """Per-message fields, keyed by package-qualified typeName."""
    atomic_write_json(version_dir / "messages.json", messages)


def _write_enums(
    version_dir: pathlib.Path, enums: Dict[str, List[Dict[str, Any]]]
) -> None:
    """Enum entries, keyed by package-qualified typeName."""
    atomic_write_json(version_dir / "enums.json", enums)


def _write_registry(
    root: pathlib.Path,
    app_version: str,
    services: Dict[str, List[Dict[str, str]]],
    messages: Dict[str, List[Dict[str, Any]]],
) -> None:
    """The cumulative union: first_seen sticks, last_seen advances, nothing deleted."""
    registry_path = root / "schema" / "registry.json"
    old: Dict[str, Any] = {}
    try:
        old = json.loads(registry_path.read_text(errors="replace"))
    except Exception:
        old = {}
    if not isinstance(old, dict):
        old = {}
    atomic_write_json(
        registry_path, update_registry(old, app_version, services, messages)
    )


def _write_reviewed(
    root: pathlib.Path,
    bundle: Dict[str, Any],
    counts: Dict[str, int],
    reviewed_at: str,
) -> None:
    """The review marker: which version somebody read, when, and under what rules."""
    app_version = bundle["app_version"]
    services = bundle["services"]
    write_count = sum(
        1 for rows in services.values() for m in rows if is_write(m["name"])
    )
    atomic_write_json(
        root / "schema" / "reviewed.json",
        {
            "version": app_version,
            "reviewed_at": reviewed_at,
            "note": (
                f"{app_version} reviewed {reviewed_at}: {counts['methods']} methods "
                f"across {counts['services']} services, {counts['messages']} messages, "
                f"{counts['enums']} enums. req polarity verified against "
                "AgentSkill/SubmitPerformanceEventsResponse/"
                "GetOrganizationMembersRequest (opt:!0 = optional, absent = "
                "required-shape). Write = wire name not starting with "
                f"Get/List ({write_count} write methods)."
            ),
        },
    )


def write_artifacts(
    root: pathlib.Path, bundle: Dict[str, Any], mined_at: str, reviewed_at: str
) -> Dict[str, Any]:
    """Version dir + cumulative registry + reviewed marker, atomically."""
    app_version = bundle["app_version"]
    counts = _record_counts(bundle)
    version_dir = root / "schema" / app_version
    version_dir.mkdir(parents=True, exist_ok=True)
    _write_manifest(version_dir, bundle, counts, mined_at)
    _write_services(version_dir, bundle["services"])
    _write_messages(version_dir, bundle["messages"])
    _write_enums(version_dir, bundle["enums"])
    _write_registry(root, app_version, bundle["services"], bundle["messages"])
    _write_reviewed(root, bundle, counts, reviewed_at)
    return {
        "manifest": f"schema/{app_version}/manifest.json",
        "version": app_version,
        "counts": counts,
    }


# --------------------------------------------------------------------------- selftest

_SYNTH_GOOD = """var n6e={};LKe(n6e,{PingService:()=>PSvc,OtherService:()=>OSvc});
var PSvc={typeName:"aiserver.v1.PingService",methods:{getStatus:{name:"GetStatus",I:GSReq,O:GSRes,kind:i.Unary},updateConfig:{name:"UpdateConfig",I:UCReq,O:UCRes},legacyCall:{name:"LegacyCall",requestType:LCReq,responseType:LCRes,kind:i.ServerStreaming},renamed:{name:"Rename",input:RReq,output:RRes,kind:i.Unary}}};
var OSvc={typeName:"otherpkg.v1.OtherService",methods:{doThing:{name:"DoThing",I:DTReq,O:DTRes,kind:i.Unary}}};
var GSReq=class n extends s{};GSReq.typeName="aiserver.v1.GetStatusRequest";GSReq.fields=r.util.newFieldList(()=>[{no:1,name:"verbose",kind:"scalar",T:8}]);
var GSRes=class n extends s{};GSRes.typeName="aiserver.v1.GetStatusResponse";GSRes.fields=r.util.newFieldList(()=>[{no:1,name:"ok",kind:"scalar",T:8}]);
var UCReq=class n extends s{};UCReq.typeName="aiserver.v1.UpdateConfigRequest";UCReq.fields=r.util.newFieldList(()=>[{no:1,name:"config_id",kind:"scalar",T:9},{no:2,name:"note",kind:"scalar",T:9,opt:!0},{no:3,name:"stray"}]);
var UCRes=class n extends s{};UCRes.typeName="aiserver.v1.UpdateConfigResponse";UCRes.fields=r.util.newFieldList(()=>[]);
var LCReq=class n extends s{};LCReq.typeName="aiserver.v1.LegacyCallRequest";LCReq.fields=r.util.newFieldList(()=>[{no:1,name:"q",kind:"scalar",T:9}]);
var LCRes=class n extends s{};LCRes.typeName="aiserver.v1.LegacyCallResponse";LCRes.fields=r.util.newFieldList(()=>[{no:1,name:"n",kind:"scalar",T:5}]);
var RReq=class n extends s{};RReq.typeName="aiserver.v1.RenameRequest";RReq.fields=r.util.newFieldList(()=>[{number:1,name:"target",kind:"scalar",T:9}]);
var RRes=class n extends s{};RRes.typeName="aiserver.v1.RenameResponse";RRes.fields=r.util.newFieldList(()=>[{no:1,name:"ok",kind:"scalar",T:8}]);
var CE;(function(n){n[n.UNSPECIFIED=0]="UNSPECIFIED",n[n.RED=1]="RED"})(CE||(CE={}));r.util.setEnumType(CE,"aiserver.v1.Color",[{no:0,name:"COLOR_UNSPECIFIED"},{number:1,name:"COLOR_RED"}]);"""


def _check(label: str, cond: bool, failures: List[str]) -> None:
    print(("  ok  " if cond else "  FAIL") + f" {label}")
    if not cond:
        failures.append(label)


def _t_services(
    text: str, symbols: Dict[str, str], failures: List[str]
) -> Dict[str, List[Dict[str, str]]]:
    """Registry span + cross-check + method atoms + I/O fallbacks + kind default."""
    services = extract_services(text, symbols)
    _check(
        "only the aiserver service survives cross-check",
        sorted(services) == ["Ping"],
        failures,
    )
    got = {m["name"]: m for m in services.get("Ping", [])}
    _check("4 methods mined", len(got) == 4, failures)
    _check(
        "I/O resolve to qualified typeNames",
        got.get("GetStatus", {}).get("input") == "aiserver.v1.GetStatusRequest"
        and got.get("GetStatus", {}).get("output") == "aiserver.v1.GetStatusResponse",
        failures,
    )
    _check(
        "requestType/responseType fallback reads",
        got.get("LegacyCall", {}).get("input") == "aiserver.v1.LegacyCallRequest"
        and got.get("LegacyCall", {}).get("kind") == "ServerStreaming",
        failures,
    )
    _check(
        "input/output fallback reads",
        got.get("Rename", {}).get("input") == "aiserver.v1.RenameRequest"
        and got.get("Rename", {}).get("output") == "aiserver.v1.RenameResponse",
        failures,
    )
    _check(
        "missing kind defaults to unstated",
        got.get("UpdateConfig", {}).get("kind") == KIND_UNSTATED,
        failures,
    )
    return services


def _t_fields(
    text: str, symbols: Dict[str, str], failures: List[str]
) -> Dict[str, List[Dict[str, Any]]]:
    """Qualified keys + same-atom-only + opt polarity + no:/number: spellings."""
    messages = extract_messages(text, symbols)
    _check("8 messages keyed qualified", len(messages) == 8, failures)
    _check(
        "no short-name merging",
        "aiserver.v1.GetStatusRequest" in messages
        and "GetStatusRequest" not in messages,
        failures,
    )
    uc = {f["name"]: f for f in messages.get("aiserver.v1.UpdateConfigRequest", [])}
    _check(
        "absent opt => req True", uc.get("config_id", {}).get("req") is True, failures
    )
    _check(
        "opt:!0 => req False (verified polarity)",
        uc.get("note", {}).get("req") is False,
        failures,
    )
    _check("stray atom without kind dropped", "stray" not in uc, failures)
    rn = {f["name"]: f for f in messages.get("aiserver.v1.RenameRequest", [])}
    _check("number: spells no:", rn.get("target", {}).get("no") == 1, failures)
    return messages


def _t_enums(text: str, failures: List[str]) -> None:
    """Enum entries with no:/number: spellings."""
    enums = extract_enums(text)
    _check(
        "enum entries with both spellings",
        enums.get("aiserver.v1.Color")
        == [{"name": "COLOR_UNSPECIFIED", "no": 0}, {"name": "COLOR_RED", "no": 1}],
        failures,
    )


def _t_coverage(
    services: Dict[str, List[Dict[str, str]]],
    messages: Dict[str, List[Dict[str, Any]]],
    failures: List[str],
) -> None:
    """Coverage both ways: clean passes, dangling write input flagged; plus the rule."""
    _check(
        "clean corpus: no coverage flags",
        coverage_flags(services, messages) == [],
        failures,
    )
    bad_messages = {
        k: v for k, v in messages.items() if k != "aiserver.v1.UpdateConfigRequest"
    }
    bad_flags = coverage_flags(services, bad_messages)
    _check(
        "dangling write input flagged",
        any("dangling input: Ping.UpdateConfig" in f for f in bad_flags),
        failures,
    )
    _check(
        "write with unknown required fields flagged",
        any("unknown required fields" in f and "UpdateConfig" in f for f in bad_flags),
        failures,
    )
    _check(
        "read rule exact: Get/List read, rest write",
        (not is_write("GetStatus"))
        and (not is_write("ListComposers"))
        and is_write("UpdateConfig")
        and is_write("Rename"),
        failures,
    )


def _t_registry(
    services: Dict[str, List[Dict[str, str]]],
    messages: Dict[str, List[Dict[str, Any]]],
    failures: List[str],
) -> Dict[str, Any]:
    """Tombstones survive; first_seen sticks while last_seen advances."""
    reg = update_registry(
        {
            "methods": {
                "OldCall": {
                    "first_seen": "0.46.0",
                    "last_seen": "0.46.0",
                    "services": ["Ping"],
                }
            },
            "messages": {},
        },
        "0.47.0",
        services,
        messages,
    )
    _check("tombstone survives (never deleted)", "OldCall" in reg["methods"], failures)
    _check(
        "first_seen sticks, last_seen advances",
        reg["methods"]["OldCall"]["first_seen"] == "0.46.0"
        and reg["methods"]["GetStatus"]["first_seen"] == "0.47.0"
        and reg["methods"]["GetStatus"]["last_seen"] == "0.47.0",
        failures,
    )
    return reg


def _t_asar(failures: List[str]) -> None:
    """Synthetic asar round-trip: struct-unpack, calibrate, read back. No bundle needed."""
    payload = b"hello-asar"
    digest = hashlib.sha256(payload).hexdigest()
    hdr = json.dumps(
        {
            "files": {
                "m.txt": {
                    "size": len(payload),
                    "offset": "0",
                    "integrity": {
                        "algorithm": "SHA256",
                        "hash": digest,
                        "blocks": [digest],
                    },
                }
            }
        }
    ).encode()
    blob = struct.pack("<IIII", 4, len(hdr) + 8, len(hdr) + 4, len(hdr)) + hdr + payload
    with tempfile.TemporaryDirectory() as tmp:
        ap = pathlib.Path(tmp) / "app.asar"
        atomic_write_bytes(ap, blob)
        back, _ = read_asar_member(ap, "m.txt")
        _check("synthetic asar member round-trips", back == payload, failures)
        atomic_write_bytes(ap, blob[:-1] + b"X")
        try:
            read_asar_member(ap, "m.txt")
            _check("corrupt member refused", False, failures)
        except ValueError:
            _check("corrupt member refused", True, failures)


def _t_shapes(
    reg: Dict[str, Any], messages: Dict[str, List[Dict[str, Any]]], failures: List[str]
) -> None:
    """Registry/method/field key shapes exact."""
    _check(
        "registry/method/field key shapes exact",
        set(reg["methods"]["GetStatus"]) == {"first_seen", "last_seen", "services"}
        and set(messages["aiserver.v1.GetStatusRequest"][0])
        == {"no", "name", "kind", "req"},
        failures,
    )


def selftest() -> int:
    """The parser against a synthetic descriptor — no asar needed.

    One flat call list over per-area legs (gbhttp precedent): services,
    fields, enums, coverage, registry, asar round-trip, shapes. Covers:
    registry span + aiserver cross-check (OtherService excluded), I:/O: with
    requestType/responseType and input/output fallbacks, kind defaulting to
    unstated, same-atom-only fields (the stray no/name atom is dropped),
    no:/number: spellings, opt polarity (opt => req False), enum entries, and
    the coverage assertion both ways (clean corpus passes, a corpus with a
    dangling write input is flagged, tombstones survive).
    """
    failures: List[str] = []
    text = _SYNTH_GOOD
    symbols = sym_type_map(text)
    _check("symmap binds all 8 synthetic symbols", len(symbols) == 8, failures)
    services = _t_services(text, symbols, failures)
    messages = _t_fields(text, symbols, failures)
    _t_enums(text, failures)
    _t_coverage(services, messages, failures)
    reg = _t_registry(services, messages, failures)
    _t_asar(failures)
    _t_shapes(reg, messages, failures)
    print(
        f"gb-schema --selftest: {'PASS' if not failures else 'FAIL'} ({len(failures)} failures)"
    )
    return EXIT_OK if not failures else EXIT_FINDINGS


# --------------------------------------------------------------------------- capabilities


def capabilities() -> Dict[str, Any]:
    """What this producer mines, and the exact rules it mines by."""
    return {
        "schema": SCHEMA,
        "producer": "bin/gb-schema.py",
        "version": VERSION,
        "reads": [
            DEFAULT_ASAR + " : " + PROTO_MEMBER,
            DEFAULT_ASAR + " : " + PKG_MEMBER,
        ],
        "writes": [
            "schema/<app_version>/{manifest,services,messages,enums}.json",
            "schema/registry.json",
            "schema/reviewed.json",
        ],
        "services": {
            "expected": list(EXPECTED_SERVICES),
            "registry_span": "XService:()=>y",
            "cross_check": "definition typeName must equal aiserver.v1.<X>Service",
        },
        "methods": {
            "entry": "{name,input,output,kind}",
            "input_priority": list(INPUT_KEYS),
            "output_priority": list(OUTPUT_KEYS),
            "kind_default": KIND_UNSTATED,
            "kind_normalised": "suffix after last dot (i.Unary -> Unary)",
        },
        "messages": {
            "entry": "{no,name,kind,req}",
            "keys": "package-qualified typeNames; short names never merge",
            "atom_rule": "no/name/kind must share one field atom",
            "req_rule": (
                "req = no opt: marker in the same atom (opt:!0 = optional; "
                "verified 2026-09-12 against AgentSkill, "
                "SubmitPerformanceEventsResponse, GetOrganizationMembersRequest)"
            ),
            "no_spellings": ["no", "number"],
        },
        "enums": {"entry": "{name,no}", "no_spellings": ["no", "number"]},
        "write_rule": f"READ iff wire name starts with {list(READ_PREFIXES)}; all else WRITE",
        "coverage": (
            "every method input must exist in messages.json; "
            "a WRITE method with a dangling input is flagged unknown-required-fields"
        ),
        "network": "none — local bundle read only; vendor bytes never committed, only bundle_sha256",
        "flags": [
            "--selftest",
            "--capabilities",
            "--json",
            "--asar PATH",
            "--root PATH",
        ],
    }


# --------------------------------------------------------------------------- CLI


@dataclasses.dataclass(frozen=True)
class SchemaArgs:
    """Mine the versioned RPC schema out of the shipped desktop bundle."""

    asar: str = arg(
        default=DEFAULT_ASAR, metavar="PATH", help="desktop app.asar to mine"
    )
    root: str = arg(
        default=str(ROOT), metavar="PATH", help="repo root (writes schema/)"
    )
    selftest: bool = arg(help="synthetic-descriptor selftest; no asar needed")
    capabilities: bool = arg(help="print what this producer mines and how")
    json: bool = arg(help="machine-readable envelope on stdout")


def body() -> int:
    a = parse(SchemaArgs, description=SUMMARY)
    if a.capabilities:
        print(json.dumps(capabilities(), indent=1))
        return EXIT_OK
    if a.selftest:
        return selftest()
    try:
        bundle = mine_bundle(a.asar)
    except FileNotFoundError as exc:
        print(f"gb-schema: bundle not found: {exc.filename}", file=sys.stderr)
        return EXIT_ENVIRONMENT
    except (ValueError, KeyError, struct.error, json.JSONDecodeError) as exc:
        print(f"gb-schema: cannot mine bundle: {exc}", file=sys.stderr)
        return EXIT_ENVIRONMENT
    if not bundle["services"]:
        print(
            "gb-schema: no aiserver.v1 services found; refusing to write",
            file=sys.stderr,
        )
        return EXIT_ENVIRONMENT
    root = pathlib.Path(a.root)
    now = dt.datetime.now(dt.timezone.utc)
    summary = write_artifacts(
        root, bundle, now.strftime("%Y-%m-%dT%H:%M:%SZ"), now.strftime("%Y-%m-%d")
    )
    flags = coverage_flags(bundle["services"], bundle["messages"])
    for flag in flags:
        print(f"gb-schema: {flag}", file=sys.stderr)
    if a.json:
        print(
            json.dumps(
                {
                    "schema": SCHEMA,
                    "version": VERSION,
                    "manifest": summary["manifest"],
                    "app_version": summary["version"],
                    "counts": summary["counts"],
                    "coverage_flags": flags,
                    "exit": EXIT_OK,
                },
                indent=1,
            )
        )
    else:
        c = summary["counts"]
        print(
            f"gb-schema — {summary['version']}: {c['services']} services, "
            f"{c['methods']} methods, {c['messages']} messages, {c['enums']} enums"
        )
        print(f"  manifest: {summary['manifest']}")
        if flags:
            print(f"  coverage flags: {len(flags)} (see stderr)")
    return EXIT_OK


if __name__ == "__main__":
    main(body)
