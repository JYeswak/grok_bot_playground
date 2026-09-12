#!/usr/bin/env bash
#
# install.sh — one-touch install of `gb`, the Grok Bot playground CLI.
#
#   curl -fsSL https://raw.githubusercontent.com/JYeswak/grok_bot_playground/main/install.sh | bash
#
# WHAT THIS IS, AND WHAT IT DELIBERATELY IS NOT.
#
# `pip install git+https://github.com/JYeswak/grok_bot_playground` already works and was measured
# working on 2026-09-11 in a clean venv: zero dependencies resolved, a 226 KB wheel, and
# `gb capabilities --json` answering `gb 1.0.0`. So this script is NOT a second installation
# mechanism — a second mechanism is a second thing to keep correct, and it drifts. It is a
# WRAPPER around exactly that pip invocation, and everything it adds is something pip does not do:
#
#   * refuses early, in prose, when python3 is missing or older than 3.9 — instead of a traceback
#   * isolates the install in its own venv, so it cannot collide with your system python
#   * puts `gb` and `gb-walk` on your PATH, and tells you when the PATH directory is not on it
#   * is idempotent, and says which of the two things it did
#   * VERIFIES itself by running the tool and printing what it actually answered
#   * uninstalls cleanly, removing only what it created
#
# WHAT IT CANNOT DO, said up front because the alternative is a user waiting for something that
# is never coming: it does not touch your Grok Bot account. It cannot create a Bot, cannot log
# in, cannot read your Bots, and needs no credentials of any kind. `gb` MEASURES a deployment
# from artifacts on your own disk and PROPOSES Bots as text you paste in by hand.
#
# Exit codes match the tool's own table: 0 OK, 2 USAGE, 3 ENVIRONMENT.

set -euo pipefail

REPO_URL_DEFAULT="git+https://github.com/JYeswak/grok_bot_playground"
PREFIX_DEFAULT="${HOME}/.local/share/grok-bot-playground"
BIN_DIR_DEFAULT="${HOME}/.local/bin"
MIN_MAJOR=3
MIN_MINOR=9

SOURCE="${GB_INSTALL_SOURCE:-$REPO_URL_DEFAULT}"
PREFIX="${GB_INSTALL_PREFIX:-$PREFIX_DEFAULT}"
BIN_DIR="${GB_INSTALL_BIN_DIR:-$BIN_DIR_DEFAULT}"
DRY_RUN=0
UNINSTALL=0
QUIET=0

# ---------------------------------------------------------------------------------------------
# Output. Colour only for a human at a terminal; NO_COLOR and CI each veto it, because this
# script's output ends up in logs and in other agents' context at least as often as on a screen.
# ---------------------------------------------------------------------------------------------
if [ -t 1 ] && [ -z "${NO_COLOR:-}" ] && [ -z "${CI:-}" ]; then
  B=$'\033[1m'; D=$'\033[2m'; R=$'\033[0m'
else
  B=''; D=''; R=''
fi

say()  { [ "$QUIET" -eq 1 ] || printf '%s\n' "$*"; }
head_() { [ "$QUIET" -eq 1 ] || printf '%s%s%s\n' "$B" "$*" "$R"; }
dim()  { [ "$QUIET" -eq 1 ] || printf '%s%s%s\n' "$D" "$*" "$R"; }
warn() { printf '%s\n' "$*" >&2; }
# `die MESSAGE [CODE]`: only the first argument is the message, so the exit code never leaks
# into the prose the user reads. (It did, once.)
die()  { printf '\n%s\n' "$1" >&2; exit "${2:-3}"; }
step() { if [ "$DRY_RUN" -eq 1 ]; then say "  would: $*"; else say "  $*"; fi; }

usage() {
  cat <<'EOF'
install.sh — install `gb`, the Grok Bot playground CLI, into its own venv.

  install.sh                     install or upgrade
  install.sh --dry-run           print the exact plan, touch nothing
  install.sh --uninstall         remove only what this script created
  install.sh --help

options
  --prefix DIR     where the venv lives      (default: ~/.local/share/grok-bot-playground)
  --bin-dir DIR    where `gb` is linked      (default: ~/.local/bin)
  --source SPEC    any pip-installable spec  (default: the public GitHub repo)
  --quiet          errors only

environment
  GB_INSTALL_PREFIX, GB_INSTALL_BIN_DIR, GB_INSTALL_SOURCE — the same three, for a pipe:
    curl -fsSL .../install.sh | GB_INSTALL_PREFIX=/opt/gb bash

exit codes
  0  installed and verified
  2  bad usage
  3  this machine cannot run it (no python3, python3 too old, no venv module, verify failed)
EOF
}

while [ $# -gt 0 ]; do
  case "$1" in
    --dry-run|-n) DRY_RUN=1 ;;
    --uninstall)  UNINSTALL=1 ;;
    --quiet|-q)   QUIET=1 ;;
    # `[ $# -ge 2 ]` BEFORE the shift, not `shift ||` after it: `shift` on the LAST argument
    # still succeeds (it consumes the flag itself), and then `$1` is unbound, which under
    # `set -u` is a bash error message instead of our usage error. Measured: `--prefix` with no
    # value printed "line 92: $1: unbound variable" and exited 1, not 2.
    --prefix)     [ $# -ge 2 ] || die "--prefix needs a directory" 2; PREFIX="$2"; shift ;;
    --prefix=*)   PREFIX="${1#*=}" ;;
    --bin-dir)    [ $# -ge 2 ] || die "--bin-dir needs a directory" 2; BIN_DIR="$2"; shift ;;
    --bin-dir=*)  BIN_DIR="${1#*=}" ;;
    --source)     [ $# -ge 2 ] || die "--source needs a pip spec" 2; SOURCE="$2"; shift ;;
    --source=*)   SOURCE="${1#*=}" ;;
    --help|-h)    usage; exit 0 ;;
    *)            usage >&2; die "unknown option: $1" 2 ;;
  esac
  shift
done

VENV="${PREFIX}/venv"
GB_LINK="${BIN_DIR}/gb"
WALK_LINK="${BIN_DIR}/gb-walk"

# ---------------------------------------------------------------------------------------------
# Uninstall. Removes the venv, the prefix if this script is the only thing in it, and the two
# launchers — but ONLY when they point into our prefix. A `gb` you installed some other way is
# not ours to delete, and a `--uninstall` that removed it would be a bug with no undo.
# ---------------------------------------------------------------------------------------------
uninstall() {
  head_ "uninstalling the Grok Bot playground"
  local found=0
  for link in "$GB_LINK" "$WALK_LINK"; do
    if [ -L "$link" ] || [ -f "$link" ]; then
      # Ours iff it resolves into, or mentions, our prefix. Anything else we leave and say so.
      local target=""
      if [ -L "$link" ]; then
        target="$(readlink "$link" || true)"
      else
        target="$(grep -o "${PREFIX}[^\"' ]*" "$link" 2>/dev/null | head -n1 || true)"
      fi
      case "$target" in
        "${PREFIX}"*) step "rm ${link}"; [ "$DRY_RUN" -eq 1 ] || rm -f "$link"; found=1 ;;
        *) warn "leaving ${link}: it does not point into ${PREFIX} (it points at '${target:-unknown}') — not ours to remove" ;;
      esac
    fi
  done
  if [ -d "$VENV" ]; then
    step "rm -r ${VENV}"
    [ "$DRY_RUN" -eq 1 ] || rm -rf "$VENV"
    found=1
  fi
  if [ -d "$PREFIX" ]; then
    step "rmdir ${PREFIX} (only if now empty)"
    [ "$DRY_RUN" -eq 1 ] || rmdir "$PREFIX" 2>/dev/null || true
  fi
  # The bin directory is usually ~/.local/bin and usually shared, so `rmdir` — which refuses a
  # non-empty directory — is exactly the right instrument: it reclaims a throwaway --bin-dir we
  # created and cannot touch one that holds anything else.
  if [ -d "$BIN_DIR" ]; then
    step "rmdir ${BIN_DIR} (only if now empty)"
    [ "$DRY_RUN" -eq 1 ] || rmdir "$BIN_DIR" 2>/dev/null || true
  fi
  if [ "$found" -eq 0 ]; then
    say ""
    say "nothing to remove: no venv at ${VENV} and no launcher of ours in ${BIN_DIR}."
    say "that is a successful uninstall of an absent install, so this exits 0."
  else
    say ""
    if [ "$DRY_RUN" -eq 1 ]; then
      say "dry run — nothing was removed."
    else
      say "removed. ${B}pip${R} was never used outside ${PREFIX}, so nothing else on this machine changed."
    fi
  fi
  exit 0
}

# ---------------------------------------------------------------------------------------------
# Find a python3 that is actually new enough. Asking the interpreter beats parsing `--version`
# output: the interpreter is the authority on its own version.
#
# It sets the GLOBALS `PY` and `PY_REJECTED` rather than printing the answer. Measured: the first
# cut was `PY="$(find_python || true)"`, and command substitution runs the function in a SUBSHELL,
# so every rejected candidate it recorded was discarded on the way out. A machine with only
# python3.8 was told "no python3 >= 3.9 on this machine" and never told that the 3.8 it has is
# the reason — which is the one sentence that makes the error actionable.
# ---------------------------------------------------------------------------------------------
PY=""
PY_REJECTED=""
find_python() {
  local candidate version
  for candidate in python3 python3.13 python3.12 python3.11 python3.10 python3.9 python; do
    command -v "$candidate" >/dev/null 2>&1 || continue
    if "$candidate" -c "import sys; sys.exit(0 if sys.version_info[:2] >= ($MIN_MAJOR, $MIN_MINOR) else 1)" 2>/dev/null; then
      PY="$candidate"
      return 0
    fi
    version="$("$candidate" -c 'import sys; print("%d.%d.%d" % sys.version_info[:3])' 2>/dev/null || echo unknown)"
    PY_REJECTED="${PY_REJECTED}
  ${candidate} is ${version} — needs >= ${MIN_MAJOR}.${MIN_MINOR}"
  done
  return 1
}

# ---------------------------------------------------------------------------------------------
# Install
# ---------------------------------------------------------------------------------------------
[ "$UNINSTALL" -eq 1 ] && uninstall

head_ "Grok Bot playground — installer"
say ""
say "  This installs ${B}gb${R}, a CLI that measures a Grok Bot deployment from artifacts on"
say "  your own disk and proposes Bot templates you paste in by hand."
say ""
say "  It does NOT touch your Grok Bot account. It cannot create a Bot, cannot sign in,"
say "  cannot read your Bots, and asks for no credentials. Nothing here needs a network"
say "  connection after the install itself."
say ""

if ! find_python; then
  die "no python3 >= ${MIN_MAJOR}.${MIN_MINOR} on this machine.${PY_REJECTED:-}

remediation:
  macOS     brew install python@3.12
            (or install Xcode command line tools: xcode-select --install)
  Debian    sudo apt-get install -y python3 python3-venv
  Fedora    sudo dnf install -y python3
  Windows   https://www.python.org/downloads/  (then run this in Git Bash or WSL)

then run this script again. Nothing was installed."
fi
PY_VERSION="$("$PY" -c 'import sys; print("%d.%d.%d" % sys.version_info[:3])')"
say "  python    ${PY} (${PY_VERSION})  ->  $(command -v "$PY")"

if ! "$PY" -c 'import venv' >/dev/null 2>&1; then
  die "python3 is ${PY_VERSION} but the \`venv\` module is missing, so an isolated install is
impossible. This is the single most common Debian/Ubuntu failure here.

remediation:
  Debian/Ubuntu   sudo apt-get install -y python3-venv
  other           reinstall python3 from your package manager, not from source

Nothing was installed."
fi

# Is something already here? Decided BEFORE any mutation so the closing line can honestly say
# whether this run installed or upgraded.
PREEXISTING=0
if [ -x "${VENV}/bin/gb" ]; then
  PREEXISTING=1
fi

say "  source    ${SOURCE}"
say "  venv      ${VENV}"
say "  launchers ${GB_LINK}"
say "            ${WALK_LINK}"
if [ "$PREEXISTING" -eq 1 ]; then
  dim "  state     already installed — this run UPGRADES in place and is safe to repeat"
else
  dim "  state     not installed here yet"
fi
say ""

head_ "plan"
step "mkdir -p ${PREFIX} ${BIN_DIR}"
step "${PY} -m venv ${VENV}"
step "${VENV}/bin/python -m pip install --upgrade --quiet pip"
step "${VENV}/bin/python -m pip install --upgrade '${SOURCE}'"
step "link ${GB_LINK} -> ${VENV}/bin/gb"
step "write ${WALK_LINK} (a 3-line launcher for the guided tour)"
step "verify by running: gb capabilities --json"
say ""

if [ "$DRY_RUN" -eq 1 ]; then
  head_ "dry run — nothing above was done"
  say ""
  say "  re-run without --dry-run to do it:   bash install.sh"
  say "  or into somewhere disposable:        bash install.sh --prefix /tmp/gb --bin-dir /tmp/gb/bin"
  exit 0
fi

head_ "installing"
mkdir -p "$PREFIX" "$BIN_DIR"

# `python -m venv` on an existing venv is itself idempotent and cheap, so re-running the whole
# script does not require tearing anything down.
if ! "$PY" -m venv "$VENV" >/dev/null 2>&1; then
  die "could not create a venv at ${VENV}.

remediation:
  * check the directory is writable:  ls -ld $(dirname "$VENV")
  * try somewhere else:               bash install.sh --prefix \"\$HOME/gbplay\"
Nothing was linked."
fi
say "  venv ready"

# pip itself: quiet, and NOT fatal. An old pip that can still build a PEP 517 wheel is fine, and
# a sandbox with no network to PyPI must not fail here before we have even tried the real install.
"${VENV}/bin/python" -m pip install --upgrade --quiet pip >/dev/null 2>&1 || \
  dim "  (could not upgrade pip; continuing — the install below is the real test)"

PIP_LOG="$(mktemp -t gb-install-pip.XXXXXX)"
if ! "${VENV}/bin/python" -m pip install --upgrade "$SOURCE" >"$PIP_LOG" 2>&1; then
  warn ""
  warn "pip failed. Its last 20 lines:"
  tail -n 20 "$PIP_LOG" >&2
  warn ""
  rm -f "$PIP_LOG"
  die "install failed at the pip step, which is the step this script is a wrapper around.

remediation:
  * no network?   the source above is a GitHub URL and needs one, once
  * no git?       pip clones it — install git, or download a release tarball and use
                  --source /path/to/that/directory
  * behind a proxy? export https_proxy=... and re-run
The venv at ${VENV} was created but has no working \`gb\` in it; --uninstall removes it."
fi
rm -f "$PIP_LOG"
say "  package installed"

if [ ! -x "${VENV}/bin/gb" ]; then
  die "pip reported success but there is no executable at ${VENV}/bin/gb.

That means the package installed without its console script, which is a packaging defect rather
than anything you did wrong. Please report it with this line:
  ${SOURCE}
  $("${VENV}/bin/python" -V 2>&1)"
fi

ln -sfn "${VENV}/bin/gb" "$GB_LINK"
say "  linked gb"

# THE GUIDED TOUR. `gb-walk.py` is a producer inside the package, not a console script, so it
# gets a 3-line launcher rather than a symlink: the launcher pins the venv's interpreter, which
# is the one whose sibling modules (`gbtypes`) the producer imports.
#
# WHICH COPY IT POINTS AT MATTERS, and this is the whole reason the block is this long. The wheel
# carries the TOOL; `templates/` — the Bots the `bots` track walks — travels with the
# REPOSITORY, because putting a .json/.md-only directory inside a wheel would mean making it an
# importable package or keeping a second copy of the same bytes. So:
#
#   run from a clone  ->  point `gb-walk` at THE CLONE's gb-walk.py, whose `templates/` sibling
#                         is right there. `git clone && bash install.sh` is then one touch for
#                         both tracks, with no duplicated content anywhere.
#   run via curl|bash ->  point at the wheel's copy. `gb-walk cli` is fully live; `gb-walk bots`
#                         names the clone as its remediation instead of rendering an empty tour.
#
# `BASH_SOURCE[0]` is the discriminator: under `curl ... | bash` there is no script file, so the
# `-f` test fails and the clone branch cannot be taken by accident.
SCRIPT_DIR=""
if [ -n "${BASH_SOURCE[0]:-}" ] && [ -f "${BASH_SOURCE[0]}" ]; then
  SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
fi

WALK_PY=""
WALK_FROM="the installed package"
if [ -n "$SCRIPT_DIR" ] && [ -f "${SCRIPT_DIR}/bin/gb-walk.py" ] && [ -d "${SCRIPT_DIR}/templates" ]; then
  WALK_PY="${SCRIPT_DIR}/bin/gb-walk.py"
  WALK_FROM="this clone — so the Bot templates in ${SCRIPT_DIR}/templates are walkable too"
else
  WALK_PY="$("${VENV}/bin/python" - <<'PYEOF' 2>/dev/null || true
import pathlib
try:
    import grok_bot_ops
except Exception:
    raise SystemExit(0)
p = pathlib.Path(grok_bot_ops.__file__).resolve().parent / "bin" / "gb-walk.py"
print(p if p.exists() else "")
PYEOF
)"
fi
if [ -n "${WALK_PY:-}" ]; then
  cat >"$WALK_LINK" <<EOF
#!/bin/sh
# Installed by the Grok Bot playground installer. Runs the guided tour on the venv's python.
exec "${VENV}/bin/python" "${WALK_PY}" "\$@"
EOF
  chmod +x "$WALK_LINK"
  say "  linked gb-walk  (from ${WALK_FROM})"
  WALK_AVAILABLE=1
  if [ "$WALK_FROM" = "the installed package" ]; then
    WALK_HAS_TEMPLATES=0
  else
    WALK_HAS_TEMPLATES=1
  fi
else
  WALK_AVAILABLE=0
  WALK_HAS_TEMPLATES=0
  dim "  no gb-walk.py in this build of the package — the guided tour is not installed"
fi
say ""

# ---------------------------------------------------------------------------------------------
# Verification. This runs the tool and prints what it ANSWERED. `gb --version` does not exist —
# measured 2026-09-11, it exits 2 with "unrecognized arguments: --version" — so the check is
# `capabilities --json`, which is the tool's own declared machine contract and therefore the
# right thing to assert against anyway.
# ---------------------------------------------------------------------------------------------
head_ "verifying — this is a real run of the tool, not a claim"
VERIFY="$("${VENV}/bin/python" - "$GB_LINK" <<'PYEOF' 2>&1 || true
import json, subprocess, sys
gb = sys.argv[1]
try:
    p = subprocess.run([gb, "capabilities", "--json"], capture_output=True, timeout=60)
except Exception as e:  # noqa: BLE001 - any failure here is a failed verification, reported as one
    print("FAIL could not execute %s: %s" % (gb, e)); raise SystemExit(0)
if p.returncode != 0:
    print("FAIL `gb capabilities --json` exited %d: %s" % (p.returncode, p.stderr.decode()[:400]))
    raise SystemExit(0)
try:
    d = json.loads(p.stdout)
except ValueError as e:
    print("FAIL `gb capabilities --json` did not emit JSON: %s" % e); raise SystemExit(0)
missing = [k for k in ("tool", "version", "commands", "exit_codes", "subsystems") if k not in d]
if missing:
    print("FAIL capabilities is missing %s" % ", ".join(missing)); raise SystemExit(0)
print("OK %s %s · %d commands · %d subsystems · %d exit codes" % (
    d["tool"], d["version"], len(d["commands"]), len(d["subsystems"]), len(d["exit_codes"])))
PYEOF
)"
case "$VERIFY" in
  OK*)
    say "  ${VERIFY}"
    say "  $("$GB_LINK" platform 2>/dev/null | head -n1 || echo 'platform: could not read')"
    ;;
  *)
    warn "  ${VERIFY}"
    die "install did NOT verify.

The files are on disk but the tool does not answer, so this exits non-zero rather than
congratulating you.

remediation:
  1. run it directly and read the error:   ${VENV}/bin/gb capabilities --json
  2. confirm the interpreter:              ${VENV}/bin/python -V
  3. start over cleanly:                   bash install.sh --uninstall && bash install.sh
  4. still broken? open an issue with the output of step 1."
    ;;
esac
say ""

# ---------------------------------------------------------------------------------------------
# PATH, honestly. A launcher in a directory nobody searches is not installed.
# ---------------------------------------------------------------------------------------------
case ":${PATH}:" in
  *":${BIN_DIR}:"*) ON_PATH=1 ;;
  *) ON_PATH=0 ;;
esac

if [ "$PREEXISTING" -eq 1 ]; then
  head_ "upgraded — it was already installed, and re-running this was safe"
else
  head_ "installed"
fi
say ""
if [ "$ON_PATH" -eq 0 ]; then
  say "  ${B}${BIN_DIR} is not on your PATH${R}, so \`gb\` will not be found by name yet."
  say ""
  say "  add it — pick the line for your shell, then open a new terminal:"
  say "    zsh   echo 'export PATH=\"${BIN_DIR}:\$PATH\"' >> ~/.zshrc"
  say "    bash  echo 'export PATH=\"${BIN_DIR}:\$PATH\"' >> ~/.bashrc"
  say "    fish  fish_add_path ${BIN_DIR}"
  say ""
  say "  until then, the full path works:"
  say "    ${GB_LINK} triage"
  say ""
else
  say "  ${BIN_DIR} is on your PATH, so these work right now:"
  say ""
fi

say "  ${B}gb triage${R}                  what is wrong right now (start here)"
if [ "${WALK_AVAILABLE:-0}" -eq 1 ]; then
  say "  ${B}gb-walk cli${R}                a guided tour of the tool, running the read-only verbs live"
  if [ "${WALK_HAS_TEMPLATES:-0}" -eq 1 ]; then
    say "  ${B}gb-walk bots${R}               the Bot templates this repo proposes, and where to paste them"
    say "  ${B}gb-walk bots --paste <id>${R}  just the charter, for \`| pbcopy\`"
  else
    say "  ${B}gb-walk bots${R}               needs the repository's templates/ directory, which does not"
    say "                             ship inside the wheel. It will tell you so and name the fix:"
    say "                               git clone https://github.com/JYeswak/grok_bot_playground"
    say "                               cd grok_bot_playground && bash install.sh"
    say "                             (re-running it from a clone repoints gb-walk at the clone)"
  fi
else
  say "  ${B}git clone https://github.com/JYeswak/grok_bot_playground${R}"
  say "                             then \`bash install.sh\` from inside it — the guided tour and the"
  say "                             Bot templates ship in the repository, not in this build of the wheel"
fi
say "  ${B}gb capabilities --json${R}      the machine contract, if you are an agent"
say ""
dim "  uninstall:  bash install.sh --uninstall"
exit 0
