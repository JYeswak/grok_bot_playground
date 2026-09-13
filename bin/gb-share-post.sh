#!/usr/bin/env bash
# gb-share-post — post draft to clipboard + .txt on whatever machine runs it.
#
# WHY THIS EXISTS. The daily post is written where the repo lives (Studio);
# the operator reads it wherever he is (Studio, Brain, MacBook Pro). This
# script is the last ten centimeters: stdin or the newest post-drafts/<stamp>.md
# goes to the local clipboard AND optionally to a dated .txt, in one command.
# No network, no gate, no producers — output plumbing only. The draft schema
# (gb-post-draft/1: title, body, links[]) is emitted by gb-daily-proof; this
# wrapper copies newest post-drafts/<stamp>.md bytes. Never pipe JSON.
#
#   bin/gb-share-post.sh                       # newest post-drafts/*.md
#   bin/gb-share-post.sh --save ~/posts/        # clipboard + dated .txt there
#   bin/gb daily proof --share                 # this run's md, then this wrapper
#
# FAIL CLOSED. No clipboard tool, empty input, or unwritable dir each name
# the fix on stderr and exit nonzero. Never prints the draft to stdout
# (stdout is how the next pipe would leak it into a log).
set -euo pipefail

SAVE_DIR=""
if [[ "${1:-}" == "--save" ]]; then
  SAVE_DIR="${2:?--save needs a directory}"
elif [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  sed -n '2,16p' "$0"
  exit 0
elif [[ -n "${1:-}" ]]; then
  echo "gb-share-post: unknown flag '$1' (see --help)" >&2
  exit 2
fi

TMP="$(mktemp)"
trap 'rm -f "$TMP"' EXIT

if [[ ! -t 0 ]]; then
  cat > "$TMP"
else
  LATEST="$(ls -t post-drafts/*.md 2>/dev/null | head -1 || true)"
  if [[ -z "$LATEST" ]]; then
    echo "gb-share-post: no stdin and no post-drafts/*.md — pipe a draft or wait for gb daily" >&2
    exit 2
  fi
  cp "$LATEST" "$TMP"
  echo "gb-share-post: using $LATEST" >&2
fi

if [[ ! -s "$TMP" ]]; then
  echo "gb-share-post: empty draft — refusing to copy nothing" >&2
  exit 2
fi

OS="$(uname -s)"
if [[ "$OS" == "Darwin" ]]; then
  pbcopy < "$TMP"
elif command -v xclip >/dev/null 2>&1; then
  xclip -selection clipboard < "$TMP"
elif command -v xsel >/dev/null 2>&1; then
  xsel --clipboard --input < "$TMP"
else
  echo "gb-share-post: no clipboard tool (need xclip or xsel on $OS)" >&2
  exit 3
fi

CHARS="$(wc -c < "$TMP" | tr -d ' ')"
echo "gb-share-post: ${CHARS} chars on clipboard" >&2

if [[ -n "$SAVE_DIR" ]]; then
  mkdir -p "$SAVE_DIR"
  OUT="$SAVE_DIR/post-$(date -u +%Y-%m-%dT%H%M).txt"
  cp "$TMP" "$OUT"
  echo "gb-share-post: saved $OUT" >&2
fi
