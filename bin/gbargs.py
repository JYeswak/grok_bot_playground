"""gbargs — derive an argument parser FROM a typed dataclass, the way clap does.

WHY. `argparse.Namespace` is a bag of `Any`. After `args = ap.parse_args()`, every downstream
use — `args.device`, `args.dry_run` — is unchecked at author time and untypeable at check time:
a rename leaves a runtime `AttributeError` in whichever branch runs least often, and a checker
cannot see it. The parser and the program's idea of its own inputs drift apart silently.

The fix is the one clap made: the TYPE is the source of truth, and the parser is derived from it.
Declare a frozen dataclass, get a parser for free, and get a frozen typed instance back. A field
that does not exist cannot be read; a field whose type changes breaks the checker at the call
site, not the user at runtime.

    @dataclasses.dataclass(frozen=True)
    class Args:
        device: Tuple[str, ...] = arg(help="label(s) from desktops.json", required=True)
        dry_run: bool           = arg(help="print, do not write")
        limit: int              = arg(default=20, help="rows to show")

    args = parse(Args)          # -> Args(device=('studio',), dry_run=False, limit=20)

RUST TARGET. This is `#[derive(clap::Parser)]` with `#[arg(long, help = "...")]`. The mapping is
one-to-one and deliberate:

    Tuple[str, ...]   -> `Vec<String>`         (repeatable `--device a --device b`)
    bool              -> `bool`                (`#[arg(long)]`, presence = true)
    Optional[T]       -> `Option<T>`
    int / float / str -> `i64` / `f64` / `String`
    Path              -> `PathBuf`
    Enum subclass     -> a `#[derive(ValueEnum)]` enum (choices enforced by the parser)

Deliberately NOT a framework: no subcommand tree, no config-file merging, no plugin hooks. The
20 producers in this repo each take a handful of flags; anything more would be scaffolding for a
need nobody in this tree has.
"""

from __future__ import annotations

import argparse
import dataclasses
import enum
import pathlib
import sys
import typing
from typing import (
    Any,
    Callable,
    ClassVar,
    Dict,
    List,
    Optional,
    Protocol,
    Sequence,
    Tuple,
    Type,
    TypeVar,
)

__all__ = ["arg", "parse", "build_parser", "ArgSpec"]


class DataclassLike(Protocol):
    """What `parse`/`build_parser` actually require: a class the dataclass machinery recognises.

    Both mypy and pyright rejected a bare `TypeVar("T")` here, and they were right — nothing
    stopped `parse(int, ...)`. Rust states this as a trait bound (`T: clap::Parser`); the Python
    equivalent is a Protocol carrying the attribute the machinery keys on.
    """

    __dataclass_fields__: ClassVar[Dict[str, Any]]


T = TypeVar("T", bound=DataclassLike)

_MISSING = object()


@dataclasses.dataclass(frozen=True)
class ArgSpec:
    """The per-field metadata a derived flag needs. Carried in the dataclass field's `metadata`,
    so the declaration stays a plain dataclass and nothing here is magic."""

    help: str = ""
    required: bool = False
    short: str = ""
    metavar: str = ""
    choices: Tuple[str, ...] = ()
    positional: bool = False


def arg(
    *,
    default: Any = _MISSING,
    help: str = "",  # noqa: A002 - matches argparse's own parameter name on purpose
    required: bool = False,
    short: str = "",
    metavar: str = "",
    choices: Sequence[str] = (),
    positional: bool = False,
) -> Any:
    """Declare a field's CLI surface. Returns a `dataclasses.field`, so the class stays a
    dataclass and `dataclasses.replace`, `asdict`, and the checker all keep working."""
    spec = ArgSpec(
        help=help,
        required=required,
        short=short,
        metavar=metavar,
        choices=tuple(choices),
        positional=positional,
    )
    meta = {"gbargs": spec}
    if default is not _MISSING:
        return dataclasses.field(default=default, metadata=meta)
    return dataclasses.field(default=None, metadata=meta)


def _unwrap_optional(tp: Any) -> Tuple[Any, bool]:
    """`Optional[X]` -> (X, True); anything else -> (tp, False)."""
    origin = typing.get_origin(tp)
    if origin is typing.Union:
        inner = [a for a in typing.get_args(tp) if a is not type(None)]
        if len(inner) == 1:
            return inner[0], True
    return tp, False


def _flag_name(field_name: str) -> str:
    """`dry_run` -> `--dry-run`. The underscore form is the Python identifier; the hyphen form is
    the CLI spelling. One rule, applied everywhere, so no producer invents its own."""
    return "--" + field_name.replace("_", "-")


def _names_for(field_name: str, spec: ArgSpec) -> List[str]:
    """The argparse name(s) a field is addressed by: a positional keeps its identifier, a flag
    gets the hyphenated long form plus an optional short alias."""
    if spec.positional:
        return [field_name]
    names = [f"-{spec.short.lstrip('-')}"] if spec.short else []
    names.append(_flag_name(field_name))
    return names


def _kwargs_for(
    f: "dataclasses.Field[Any]", spec: ArgSpec, tp: Any, optional: bool
) -> Dict[str, Any]:
    """The `add_argument` keyword arguments for one field, chosen by its TYPE.

    Three shapes, because there are three: a bool is presence, a sequence is repeatable, and
    everything else is a single converted value. Split out of `build_parser` so each shape can
    be read on its own — `#[arg(...)]` attribute derivation in the Rust port is the same three
    branches, and a 39-complexity loop is not a spec anyone can port from.
    """
    kwargs: Dict[str, Any] = {"help": spec.help or None}
    if not spec.positional:
        kwargs["dest"] = f.name

    if tp is bool:
        kwargs.update(action="store_true", default=False)
        return kwargs

    if typing.get_origin(tp) in (tuple, list):
        (item_tp,) = [a for a in typing.get_args(tp) if a is not Ellipsis] or [str]
        kwargs.update(
            action="append",
            default=None,
            type=_converter(item_tp),
            metavar=spec.metavar or item_tp.__name__.upper(),
        )
        if spec.required:
            kwargs["required"] = True
        return kwargs

    kwargs.update(type=_converter(tp), metavar=spec.metavar or tp.__name__.upper())
    if spec.choices:
        kwargs["choices"] = list(spec.choices)
    elif isinstance(tp, type) and issubclass(tp, enum.Enum):
        kwargs["choices"] = [e.value for e in tp]
        kwargs["metavar"] = spec.metavar or "{" + ",".join(e.value for e in tp) + "}"
    if spec.required and not spec.positional:
        kwargs["required"] = True
    # A POSITIONAL WITH A DEFAULT MUST BE OPTIONAL, or it is not a default — it is a demand.
    # Measured 2026-09-11: `gb templates deploy <id>` was documented in commit messages and in
    # the hand-off, and could not be typed, because a second positional had no way to be
    # omitted; `id` was therefore a flag, and the documented shape errored with
    # "unrecognized arguments". argparse expresses this as nargs="?" and gbargs simply never
    # emitted it, so every derived CLI silently forced `verb thing` even where `verb` alone is
    # the common case.
    if spec.positional and (optional or f.default is not dataclasses.MISSING):
        kwargs["nargs"] = "?"
    if f.default is not dataclasses.MISSING and f.default is not None:
        kwargs["default"] = f.default
    elif optional or f.default is None:
        kwargs["default"] = None
    return kwargs


def build_parser(
    cls: Type[Any], *, prog: Optional[str] = None, description: str = ""
) -> argparse.ArgumentParser:
    """Derive an `ArgumentParser` from a dataclass's fields and type hints."""
    if not dataclasses.is_dataclass(cls):
        raise TypeError(
            f"{cls.__name__} is not a dataclass — gbargs derives from the type, so there must be one"
        )

    ap = argparse.ArgumentParser(
        prog=prog or pathlib.Path(sys.argv[0]).name,
        description=description or (cls.__doc__ or "").strip(),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    hints = typing.get_type_hints(cls)
    for f in dataclasses.fields(cls):
        spec: ArgSpec = f.metadata.get("gbargs", ArgSpec())
        tp, optional = _unwrap_optional(hints[f.name])
        kwargs = _kwargs_for(f, spec, tp, optional)
        ap.add_argument(
            *_names_for(f.name, spec),
            **{k: v for k, v in kwargs.items() if v is not None or k == "default"},
        )
    return ap


def _edit_distance(a: str, b: str) -> int:
    """Levenshtein. Small enough to inline; the alternative is a dependency for 12 lines."""
    if a == b:
        return 0
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def parse_sub(
    commands: "Dict[str, Type[Any]]",
    argv: Optional[Sequence[str]] = None,
    *,
    prog: str = "",
    description: str = "",
    epilog: str = "",
) -> "Tuple[str, Any]":
    """Derive a SUBCOMMAND tree from `{name: args-dataclass}` and return `(name, args)`.

    The dataclass-per-subcommand shape is clap's `#[derive(Subcommand)]` on an enum whose
    variants carry their own `#[derive(Args)]` struct — one variant per command, arguments
    owned by the variant. Keeping that shape here means the dispatcher ports as a match on an
    enum rather than as a rewritten parser.

    Each command's help text is its dataclass docstring, so the subcommand list a user sees and
    the type the code consumes cannot drift: there is one source for both.

    No subcommand at all prints help and exits 2 (usage), never 0 — an agent that types the bare
    tool name has made a usage error, and exiting 0 would tell it the empty invocation worked.
    """
    ap = argparse.ArgumentParser(
        prog=prog or pathlib.Path(sys.argv[0]).name,
        description=description,
        epilog=epilog,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    subs = ap.add_subparsers(dest="_command", metavar="<command>")
    for name, cls in commands.items():
        doc = (cls.__doc__ or "").strip().splitlines()
        sp = subs.add_parser(
            name,
            help=doc[0] if doc else "",
            description="\n".join(doc),
            formatter_class=argparse.RawDescriptionHelpFormatter,
        )
        hints = typing.get_type_hints(cls)
        for f in dataclasses.fields(cls):
            spec: ArgSpec = f.metadata.get("gbargs", ArgSpec())
            tp, optional = _unwrap_optional(hints[f.name])
            flag_kwargs = _kwargs_for(f, spec, tp, optional)
            sp.add_argument(
                *_names_for(f.name, spec),
                **{
                    k: v
                    for k, v in flag_kwargs.items()
                    if v is not None or k == "default"
                },
            )

    # Intent inference, BEFORE argparse gets to reject it. argparse's own message dumps the full
    # command list and names no correction — an agent that mistypes learns nothing and mistypes
    # again. A surgical "did you mean" teaches the spelling permanently (Levenshtein over the
    # command names, plus the glued-flag case `triage--json`, which is a real thing agents type).
    raw = list(argv) if argv is not None else sys.argv[1:]
    first = next((a for a in raw if not a.startswith("-")), None)
    if first is not None and first not in commands:
        glued = next((c for c in commands if first.startswith(c)), None)
        if glued:
            rest = first[len(glued) :]
            print(
                f"{ap.prog}: '{first}' runs together a command and a flag.\n"
                f"    did you mean:  {ap.prog} {glued} {rest}",
                file=sys.stderr,
            )
            raise SystemExit(2)
        near = sorted(commands, key=lambda c: _edit_distance(first, c))
        if near and _edit_distance(first, near[0]) <= max(2, len(near[0]) // 3):
            print(
                f"{ap.prog}: no command '{first}'.\n"
                f"    did you mean:  {ap.prog} {near[0]}\n"
                f"    list them:     {ap.prog} capabilities --json | jq -r '.commands|keys[]'",
                file=sys.stderr,
            )
            raise SystemExit(2)

    ns = ap.parse_args(list(argv) if argv is not None else None)
    chosen = getattr(ns, "_command", None)
    if not chosen:
        ap.print_help(sys.stderr)
        raise SystemExit(2)

    cls = commands[chosen]
    hints = typing.get_type_hints(cls)
    kwargs: Dict[str, Any] = {}
    for f in dataclasses.fields(cls):
        value = getattr(ns, f.name)
        tp, _optional = _unwrap_optional(hints[f.name])
        if typing.get_origin(tp) in (tuple, list):
            value = tuple(value or ())
        kwargs[f.name] = value
    return chosen, cls(**kwargs)


def _converter(tp: Any) -> Callable[[str], Any]:
    """The argparse `type=` callable for a field type. Enums convert by VALUE, so `--verdict RED`
    yields `Verdict.RED` and the program never handles the bare string.

    The enum converter is a named closure, not a lambda: argparse prints the callable's
    `__name__` in its error, and `invalid <lambda> value: 'MAUVE'` tells the reader nothing about
    what was expected. It raises `ArgumentTypeError` with the legal values spelled out.
    """
    if isinstance(tp, type) and issubclass(tp, enum.Enum):
        legal = ", ".join(e.value for e in tp)

        def convert_enum(raw: str) -> Any:
            try:
                return tp(raw)
            except ValueError:
                raise argparse.ArgumentTypeError(f"{raw!r} is not one of: {legal}")

        convert_enum.__name__ = tp.__name__
        return convert_enum
    if tp is pathlib.Path:
        return pathlib.Path
    if tp is int:
        return int
    if tp is float:
        return float
    return str


def parse(
    cls: Type[T], argv: Optional[Sequence[str]] = None, *, description: str = ""
) -> T:
    """Parse `argv` into a frozen instance of `cls`.

    Repeatable flags come back as tuples, not lists: the parsed arguments are a VALUE, and a
    mutable default that a later function can append to is a bug waiting for a long enough
    program. Frozen + tuple is the closest Python gets to passing an owned struct by value.
    """
    ap = build_parser(cls, description=description)
    ns = ap.parse_args(list(argv) if argv is not None else None)
    hints = typing.get_type_hints(cls)
    kwargs: Dict[str, Any] = {}
    for f in dataclasses.fields(cls):
        value = getattr(ns, f.name)
        tp, _optional = _unwrap_optional(hints[f.name])
        if typing.get_origin(tp) in (tuple, list):
            value = tuple(value or ())
        kwargs[f.name] = value
    return cls(**kwargs)
