#!/usr/bin/env python3
"""gb-teach — what FORM a Grok Bot teaching post actually takes. Offline, from the corpus.

THE DISCRIMINATOR, stated before any number is printed, because every count below is a
consequence of it:

  A post TEACHES when the instruction is IN THE POST and transferable to a reader — a
  second-person operating instruction (`you can add Stripe's MCP as a custom connector`), an
  imperative procedure (`keep one orchestrator → give every specialist one narrow job`), an
  ordered step list, an explicit tip or rule, or a prompt/charter quoted verbatim for reuse.

  A post REPORTS when it states what the author's Bot does, however vividly, and leaves the
  reader no way to get there (`I gave a Grok Bot $300 and one order`).

  A post ADVERTISES when it points at instruction held somewhere else (`this free 1-hour
  workshop shows how to build …`, `new guide called Grok Bot 101`).

REPORT and ADVERTISEMENT are both non-teaching, and the advertisement shape is why a keyword
gate is not a teaching detector. Measured across all three artifacts: `how to <verb>` fires in
40 posts, 36 of which carry no accept-pattern at all — they announce SpaceXAI's own guide or
sell a workshop seat. The 4 that do reach the accept path reach it on a DIFFERENT pattern (an
imperative procedure), so `how to` contributed nothing to a single accepted row, and the census
reads 2 of those 4 as teaching. `here's how` fires ONCE in 3,026 posts, in a video ad for
cloning Grok Bot inside another product. So `how-to-lead` and `imperative-single` are recorded
per post as WEAK SIGNALS and are never sufficient to accept.

THE USE CLASSIFIER IS IMPORTED, NOT REBUILT. `gb-x-sweep.py` already decides what a use post is
(`use/4`) and who authored versus relayed it (`attrib/1`). This file imports that module and
reuses `PROD`, `classify_use`, `strip_quoted`, `RELAY_FRAME`, `RELAY_ROLE_LEAD` and
`DOMINATED_AT` verbatim. Teaching is an ORTHOGONAL axis — a teaching post need not be a use
claim at all (`you can add Stripe's MCP server` is instruction with no operator) — so the
patterns below are new, but nothing that the sweep already projects is projected twice.

PRECISION IS MEASURED BY CENSUS, NOT ESTIMATED. The detector accepts 77 of the 3,026 posts in
the three `x/` artifacts. All 77 were READ, one at a time, and the verdict for each is recorded
in `CENSUS` below with its handle and post id. 51 teach; 26 do not. That is 66.2% precision as a
census, not a sample — and the 26 are named with their class, so the number is falsifiable by
re-reading them rather than by argument.

  gb-teach.py forms                 # the ranked form table, author shares, dominance flags
  gb-teach.py absences              # forms that are MISSING, each with its live control
  gb-teach.py authors               # the top-20 teaching authors, with URLs
  gb-teach.py posts                 # one row per accepted post, with its census verdict
  gb-teach.py census                # the hand-read audit and the precision it measures
  gb-teach.py forms --write         # teach/<ISO>.json
  gb-teach.py --selftest            # every rule above fires on a known-bad fixture
"""

from __future__ import annotations

import argparse
import collections
import datetime
import importlib.util
import json
import pathlib
import re
import sys
from typing import Any, Callable, Dict, List, Optional, Sequence, Set, Tuple

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from gbtypes import atomic_write_text, main as gbmain, read_json_capped  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[1]
SCHEMA = "gb-teach/1"
# The ONLY field derived from the run clock. Every other value in the artifact is a function of
# the two files on disk, so two runs over the same artifacts differ in exactly one byte range.
VOLATILE_FIELDS = ["captured_at"]


# ---------------------------------------------------------------------------------------------
# gb-x-sweep.py, imported rather than copied
# ---------------------------------------------------------------------------------------------
def _load_sweep() -> Any:
    """Import `gb-x-sweep.py` as a module.

    The hyphen means the filename is not an identifier, so `import gb_x_sweep` cannot reach it —
    the same reason `gb-x-sweep.py:_load_gbx` exists, and the same mechanism. Importing rather
    than vendoring is the point: a SECOND projection of the same post payload is how two readers
    come to disagree about how many uses the corpus holds.

    `sys.modules[name] = mod` before `exec_module`, kept for the same reason the sweep keeps it.
    """
    path = pathlib.Path(__file__).resolve().parent / "gb-x-sweep.py"
    spec = importlib.util.spec_from_file_location("gb_x_sweep_producer", path)
    if (
        spec is None or spec.loader is None
    ):  # pragma: no cover - a missing sibling is fatal
        raise ImportError("cannot load %s" % path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


sw = _load_sweep()

CLASSIFIER_VERSION = "teach/1"
# Inherited verbatim from `gb-x-sweep.py:167`, which inherited it from `gb-demand.py:64`. One
# account holding half or more of a form's posts is speaking for it alone; one person is not a
# pattern. Same threshold, same reason, and deliberately not re-chosen here.
DOMINATED_AT = sw.DOMINATED_AT


# ---------------------------------------------------------------------------------------------
# the instruction vocabulary
# ---------------------------------------------------------------------------------------------
# Verbs that OPERATE a Bot. Assembled by reading the accepted set, not from a thesaurus: every
# entry is a verb somebody actually used to tell a reader what to do (`separate skills from
# routines`, `fund a throwaway wallet`, `scope every task tight`).
OPV = (
    r"set\s+up|setup|spin\s+up|stand\s+up|install|add|create|connect|enable|configure|schedule|"
    r"give|tell|ask|point|paste|open|click|use|try|run|put|keep|separate|start|build|make|"
    r"turn\s+on|let|hire|attach|upload|assign|name|define|wire|fix|avoid|ignore|stop|focus|"
    r"save|treat|brief|train|fund|write|watch|split|batch|steal|have"
)
# The things a Grok Bot instruction is ABOUT. An imperative with no mechanism in reach is prose
# (`install an update`, `make an exception for me`), which is what this set exists to exclude.
MECH = (
    r"bot|agent|routine|skill|connector|schedul|prompt|task|it\b|team|specialist|mcp|"
    r"instruction|chief\s+of\s+staff|workflow|charter|plugin"
)

# A DIRECT ADDRESS to the reader. The modal is REQUIRED: without it `diagnostics you run in
# Cursor` (@codedrai, a product pitch) reads as instruction, and it is a feature list. The
# 30-character object window is what separates `you can add Stripe's MCP server` from `hope you
# can make an exception for me so that i can use grok bot` (@Camilofimv) — both contain
# `you can <verb>`, and only one of them is aimed at a Bot.
SECOND_PERSON = re.compile(
    r"\byou\s+(?:can|could|should|just|need\s+to|have\s+to|must|want\s+to|only\s+have\s+to|"
    r"can\s+also|might\s+want\s+to|gotta)\s+(?:%s)\b" % OPV,
    re.I,
)
SECOND_PERSON_WINDOW = 30

# An imperative at a CLAUSE START. One is narrative (`So I set one up from scratch`); two or
# more with a mechanism in the post is a procedure. The clause boundaries are explicit rather
# than a proximity window, because the whitespace in these rows was collapsed by the harvester
# and a bullet is the only list structure that survived (see `CAVEATS`).
IMPERATIVE = re.compile(
    r"(?:^|(?<=[.;:!?])\s+|(?<=\u2192)\s*|(?<=\u2022)\s*|(?<=\u2014)\s*)"
    r"(?:then\s+|just\s+|also\s+|first\s+|next\s+|don'?t\s+|never\s+)?(?:%s)\s+"
    r"(?:a|an|the|my|your|it|its|up|one|two|other|all|each|every|multiple|new|\d|\w+)\b"
    % OPV,
    re.I,
)

# An explicit tip, rule or framework marker. `tip (steal this):` is @gac_long's verbatim shape
# and is why the marker is allowed up to 40 characters of slack before its colon — a first
# version required `tip:` adjacently and missed the best group-chat teaching post in the corpus.
TIP = re.compile(
    r"\b(?:pro\s*tip|tip|trick|lesson|rule\s+of\s+thumb|best\s+practice|playbook|cheat\s+sheet|"
    r"steal\s+this|worth\s+stealing|what\s+worked|the\s+key\s+is|the\s+move\s+is|the\s+order)\b"
    r"[^.!?]{0,40}?[:\u2014\u2192]|"
    r"\bhere'?s\s+(?:how|some\s+tips|the\s+(?:order|trick|setup|fix|rule|mistake))\b|"
    r"\bsteal\s+(?:this|the)\b",
    re.I,
)

# A prompt offered for REUSE, in the four shapes the corpus actually uses.
PROMPT_PASTE = re.compile(
    r"\bpaste\s+(?:this|that|it|the|into|before)\b|\bmaster\s+prompt\b", re.I
)
PROMPT_LABEL = re.compile(
    r"\b(?:prompts?|charter|instructions?)\s*[:=\u2014]|\bprompts?\s+worth\s+stealing\b|"
    r"\b(?:my|the)\s+prompt\s+(?:is|was|i\s+use)\b|\bsave\s+(?:that|this|it)\s+as\s+a\s+skill\b",
    re.I,
)
PROMPT_QUOTED = re.compile(
    r"\b(?:tell|told|ask(?:ed)?|say|said|give|gave|type|prompt)\s+"
    r"(?:it|him|her|them|the\s+bot|my\s+bot|your\s+bot|your\s+grok\s+bot|grok\s*bot)"
    r"\s*[:,]?\s*[\"\u201c\u00ab]",
    re.I,
)
# A pasteable CHARTER opens by naming the role. CASE-SENSITIVE ON PURPOSE, and this is the most
# expensive line in the file: under `re.I` the `[A-Z]` is inert — the exact defect
# `gb-x-sweep.py:660` records — and `you are NOT ready for what I just found` (@0xCristal),
# `unless you are on someone else's dime` (@chadalderson), `verify you are human`
# (@Patriotfinder) and `You are a bad operator if your morning still needs you` (@kenokki) all
# scored as shared charters. Five false positives from one missing case flag.
PROMPT_CHARTER = re.compile(
    r"(?:^|[.!?\u2026]\s+)You\s+are\s+(?:a\s+|an\s+|the\s+)?[A-Z]"
)
# …and a charter has STRUCTURE, not just an opening line. @dataguybobby's `## Job / ## Skill /
# ## Cadence / ## Stop` is the only real one in the corpus, and requiring the labels is what
# keeps the four false positives above rejected even when their capitalisation happens to line
# up.
PROMPT_STRUCT = re.compile(
    r"##\s+\w+|\b(?:Job|Cadence|Rules|Scope|Stop|Skill|Role)\s*[:\n]", re.I
)

# An ordered step. The guard is the whole rule: `Day 1: … Day 2: …` (@Tesla_FANtastic, an event
# agenda) and `Part 3/3` are ordinals with no procedure behind them, so a preceding label word
# disqualifies the marker. `08:00` is already excluded by the lookbehind, since `8` follows a
# word character.
ORDINAL = re.compile(r"(?<![\w/.$\-\u2014])([1-9])\s*[\.\)\u2014:]\s+(?=\S)")
ORDINAL_LABEL = frozenset(
    {
        "day",
        "part",
        "step",
        "week",
        "round",
        "q",
        "v",
        "no",
        "fig",
        "chapter",
        "phase",
        "sept",
        "september",
        "version",
        "ep",
        "episode",
        "vol",
        "top",
        "page",
    }
)
STEP_BODY = 90  # how far past a marker to look for the step's verb

# --- the rejects. Each one names the measured post that bought it. -------------------------
# A request is not an instruction: `Can we get a Grok Bot CLI` (@DomAtSiteSage), `Give me the
# prompt for my Grok Bot` (@finnious), `Any chance you can fix the routine problem`
# (@JackieRKLB). All three carry a second-person modal and none of them teaches.
REQUEST = re.compile(
    r"\bcan\s+(?:we|you|i)\s+get\b|\bany\s+chance\b|\bplease\s+(?:fix|add|make|send)|"
    r"\bgive\s+me\s+the\s+prompt\b|\bsend\s+me\b|\bhope\s+you\s+can\b|\bwould\s+love\s+a\b|"
    r"\bhow\s+do\s+(?:we|i)\s+\w+\?|\bcan\s+you\s+fix\b|\bshould\s+definitely\s+give\b",
    re.I,
)
# Instruction aimed at the VENDOR, not the reader: `Grok Bot next moves: 1. Connect to Grok
# Voice` (@beyrouti), `If I were on the Grok Bot team, these are the 7 things I'd obsess over`
# (@Michael_Fenech_), `What to improve: 1. Scope the pitch` (@Patriotfinder). Deliberately
# narrow: `7 things Grok Bot needs to know before it can perform inside your business` is the
# SAME author teaching, and a broader `needs to` would have taken it with the wishlist.
WISHLIST = re.compile(
    r"\bif\s+i\s+(?:were|was)\s+on\s+the\b|\bnext\s+moves\b|\bthings\s+i'?d\s+obsess\b|"
    r"\bwhat\s+to\s+improve\b|\bi'?(?:ll|d)\s+(?:immediately\s+)?pay\s+\$|\bfeature\s+request\b|"
    r"\bwish\s?list\b|\b(?:grok\s*bot|the\s+product|the\s+team)\s+(?:should|needs\s+to)\s+"
    r"(?:add|ship|build|support)\b|\bmy\s+grokbot\s+idea\b|"
    r"\b(?:my|planned)\s+(?:grok\s*bot\s+)?idea\s*[-\u2014:]",
    re.I,
)
# A machine-generated daily digest is not a person teaching: @arthur_win8's `GROK BOT HOW TO
# USE … Updated: 2026-09-11 … WHAT NEW TODAY` and @x_autonomy's `【AI热点】00:00-01:00` both
# enumerate other people's links on a clock.
DIGEST = re.compile(
    r"\bupdated\s*:\s*\d{4}-\d{2}-\d{2}|\d{2}:\d{2}\s*-\s*\d{2}:\d{2}|\bwhat\s+new\s+today\b",
    re.I,
)
# THE REJECTS RUN ON QUOTE-STRIPPED TEXT, and this is not a refinement. @aiedge_'s `5 Grok Bot
# prompts worth stealing` — the single best prompt-sharing post in the corpus — contains the
# words `Send me one brief` INSIDE one of the five prompts. Scanning the raw text made `REQUEST`
# fire on a quoted prompt and discarded the post. `strip_quoted` is imported from the sweep
# rather than rewritten, so a quote is defined identically on both axes.


def normalise(text: Optional[str]) -> str:
    # `sw` is dynamically loaded, so its members type as `Any`; `sw.normalise` already
    # returns `str` and `str(...)` makes that concrete without changing the value.
    return str(sw.normalise(text))


def ordinals(text: str) -> List[Tuple[int, int]]:
    """`(number, end_offset)` for every ordinal marker that is not a label like `Day 1:`."""
    out: List[Tuple[int, int]] = []
    for m in ORDINAL.finditer(text):
        prev = re.findall(r"([A-Za-z\u4e00-\u9fff]+)\s*$", text[: m.start()])
        if prev and prev[0].lower() in ORDINAL_LABEL:
            continue
        out.append((int(m.group(1)), m.end()))
    return out


def stepped(text: str) -> bool:
    """Two or more ordinals STARTING AT 1, with a verb or a mechanism inside a step body.

    Starting at 1 is what rejects `Sept 15-17 … 12:00 … 3.` style fragments; the step body test
    is what rejects a bare enumeration of names. Both are checked, because the news digests in
    the corpus satisfy one of them each.
    """
    marks = ordinals(text)
    nums = {n for n, _ in marks}
    if len(nums) < 2 or 1 not in nums:
        return False
    for _, end in marks:
        span = text[end : end + STEP_BODY]
        if re.search(r"\b(?:%s)\b" % OPV, span, re.I) or re.search(MECH, span, re.I):
            return True
    return False


def second_person(text: str) -> bool:
    """A reader-directed modal instruction whose OBJECT is a Bot or one of its mechanisms."""
    for m in SECOND_PERSON.finditer(text):
        window = text[m.end() : m.end() + SECOND_PERSON_WINDOW]
        if re.search(MECH, window, re.I) or re.search(sw.PROD, window, re.I):
            return True
    return False


def prompt_verbatim(text: str) -> bool:
    if (
        PROMPT_PASTE.search(text)
        or PROMPT_LABEL.search(text)
        or PROMPT_QUOTED.search(text)
    ):
        return True
    return bool(PROMPT_CHARTER.search(text) and PROMPT_STRUCT.search(text))


def imperatives(text: str) -> int:
    return len(IMPERATIVE.findall(text))


# Patterns that can carry a teaching verdict on their own.
ACCEPT_PATTERNS = (
    "second-person-modal",
    "imperative-procedure",
    "stepped-procedure",
    "tip-rule",
    "prompt-verbatim",
)
# Signals recorded but NEVER sufficient. Measured base rates are in the module docstring.
WEAK_PATTERNS = ("imperative-single", "how-to-lead")


def classify_teach(
    text: Optional[str], author: Optional[str] = None
) -> Optional[Dict[str, Any]]:
    """The teaching verdict for one post, or None when no instruction is present at all.

    Returns a dict even when a reject fires, because a rejected candidate is evidence about the
    corpus (11 of the 88 candidates are requests, wishlists, digests or relays) and dropping it
    silently would hide the shape that dominates the population.
    """
    t = normalise(text)
    if not re.search(sw.PROD, t, re.I):
        return None
    pats: List[str] = []
    weak: List[str] = []
    n_imp = imperatives(t)
    is_stepped = stepped(t)
    is_sp = second_person(t)
    if is_sp:
        pats.append("second-person-modal")
    if n_imp >= 2 and re.search(MECH, t, re.I):
        pats.append("imperative-procedure")
    if is_stepped:
        pats.append("stepped-procedure")
    if TIP.search(t) and (n_imp >= 1 or is_sp or is_stepped):
        pats.append("tip-rule")
    if prompt_verbatim(t):
        pats.append("prompt-verbatim")
    if n_imp == 1 and re.search(MECH, t, re.I):
        weak.append("imperative-single")
    if re.search(r"\bhow\s+to\s+(?:%s)\b" % OPV, t, re.I):
        weak.append("how-to-lead")
    if not pats:
        return None

    unquoted = sw.strip_quoted(t)
    rejects: List[str] = []
    if REQUEST.search(unquoted):
        rejects.append("request-not-instruction")
    if WISHLIST.search(unquoted):
        rejects.append("vendor-wishlist")
    if DIGEST.search(unquoted):
        rejects.append("automated-digest")
    if sw.RELAY_FRAME.search(unquoted):
        rejects.append("reported-speech-frame")
    lead = sw.RELAY_ROLE_LEAD.search(unquoted)
    if lead and not sw.FIRST_PERSON.search(lead.group("lead")):
        rejects.append("third-party-role-attribution-lead")
    return {
        "is_teaching_candidate": not rejects,
        "patterns": pats,
        "weak_signals": weak,
        "rejects": rejects,
        # The vendor's own account teaches — `You can add Stripe's official MCP server as a
        # custom connector` is instruction — but it is not a practitioner, so it is FLAGGED and
        # counted separately rather than dropped. `gb-x-sweep.py:596` drops it from USE counts
        # for the opposite and equally good reason: support answers are not operator claims.
        "vendor": (author or "").lower() in sw.EXCLUDED_AUTHORS,
        "classifier": CLASSIFIER_VERSION,
    }


# ---------------------------------------------------------------------------------------------
# THE CENSUS — every accepted post, read once, verdict recorded
# ---------------------------------------------------------------------------------------------
# A pattern detector cannot measure its own precision. At n=77 a SAMPLE would be a waste of a
# cheaper instrument, so all 77 accepted posts were read and each verdict is below with its
# handle and post id. Re-derive any row with:
#   python3 -c "import glob,json
#   [print(r['summary']) for f in glob.glob('x/*.json') for r in json.load(open(f))['rows']
#    if (r.get('signals') or {}).get('tweet_id')=='<ID>']"
#
# The false-positive classes, and what each one is:
#   use-report              the author's own Bot, described, with no path for the reader
#   advertisement           a product, service, template or video for sale
#   news-announcement       SpaceXAI shipped a thing; the post relays the announcement
#   commentary              analysis of the space, not instruction in it
#   comparison              a competing tool measured against Grok Bot
#   product-explainer       what a Bot IS, in the second person, with nothing to do
#   not-product-instruction real instruction about something that is not a Grok Bot
#   link-list-index         an index of other people's guides, numbered
#   idea-list               Bots the author WOULD build, enumerated; a plan, not a lesson
#   relay-nonenglish        a third-party attribution the English relay rule cannot see
CENSUS: Dict[str, Tuple[str, str]] = {
    # --- x/2026-09-12T0024.json adds 392 posts and 10 candidates; all ten read here. ---
    "2098520960743678357": ("akira_papa_IT", "teaching"),
    "2098525472535769364": ("EdwardJerjian", "commentary"),
    "2098476504053067807": ("grok", "teaching"),
    "2098519075294957975": ("grok", "teaching"),
    "2098523607743836405": ("grok", "teaching"),
    "2098534304305332356": ("grok", "teaching"),
    "2098499259196731520": ("jjjerrydupe", "teaching"),
    "2098525416399253911": ("oldguyinsweater", "idea-list"),
    "2098521563540963383": ("omneky", "advertisement"),
    "2098499433176682629": ("zenvnt", "teaching"),
    "2098515181395726474": ("0xRafy", "teaching"),
    "2098216287570645098": ("0xRafy", "teaching"),
    "2098144256657662135": ("0xtoriin", "teaching"),
    "2098176223335645381": ("0xWyzern", "teaching"),
    "2098341439671325021": ("8clerk8", "teaching"),
    "2098469812057874809": ("AbuSaud_Cyber", "news-announcement"),
    "2098260253263777830": ("aiedge_", "teaching"),
    "2098147743500022061": ("altory_y", "use-report"),
    "2097713476823306599": ("antgoldbloom", "teaching"),
    "2098425118816936035": ("ashnichrist", "commentary"),
    "2097935107835539858": ("axencryptoo", "advertisement"),
    "2098459254805962917": ("B_doong2daddy", "teaching"),
    "2097807834008506788": ("bonam", "comparison"),
    "2098410208342999162": ("BotDirectoryAI", "teaching"),
    "2098292885225394386": ("BotDirectoryAI", "not-product-instruction"),
    "2098143787335799246": ("compileinstyle", "teaching"),
    "2098508793994727766": ("CSkoda", "teaching"),
    "2098170174268686521": ("dataguybobby", "teaching"),
    "2098162406400962718": ("DeadSimpleEmail", "teaching"),
    "2098162407801794623": ("DeadSimpleEmail", "advertisement"),
    "2098464530779013546": ("DenisLabelle", "link-list-index"),
    "2098327170313593047": ("fionntobin", "teaching"),
    "2098350791333449970": ("gac_long", "teaching"),
    "2098401602474283092": ("getgrokbotjobs", "advertisement"),
    "2098321993879650327": ("grok", "teaching"),
    "2098259415673532624": ("grok", "teaching"),
    "2098086445000855754": ("grok", "teaching"),
    "2097839842054856805": ("grok", "teaching"),
    "2098135420210909357": ("GrokBotRadar", "teaching"),
    "2098505983714886105": ("GrokBotRadar", "teaching"),
    "2098351054543097988": ("huxlab", "teaching"),
    "2098364572742942872": ("itsabhisheksood", "not-product-instruction"),
    "2098461190972490053": ("JayaNayak21", "news-announcement"),
    "2098521249819263031": ("JimmyChen1979", "use-report"),
    "2098308301285138519": ("jscurek", "teaching"),
    "2098234649419919846": ("justsomeguy2365", "teaching"),
    "2098459442421620796": ("kaish3n", "use-report"),
    "2098041146027241781": ("kristal_ai", "commentary"),
    "2098456536293278188": ("KuzkaaKuzkaa888", "product-explainer"),
    "2098186322263388571": ("liam_fallen", "teaching"),
    "2098240113864237234": ("LinHYxd", "teaching"),
    "2097642008001269896": ("MaiYangAI", "relay-nonenglish"),
    "2098448734472597917": ("Michael_Fenech_", "teaching"),
    "2098499821028114718": ("Michael_Fenech_", "teaching"),
    "2098406456143827348": ("Michael_Fenech_", "teaching"),
    "2098301262953390297": ("Michael_Fenech_", "teaching"),
    "2097685992048148583": ("MitchellKeller_", "teaching"),
    "2098262178273124764": ("MMMExtra_", "use-report"),
    "2098239127959519595": ("Napoleon__Bon", "product-explainer"),
    "2097739310388699528": ("paulallendotuk", "teaching"),
    "2098170904501235894": ("QuietLabOps", "teaching"),
    "2098137296017232308": ("Rachitjoshi12", "teaching"),
    "2098536033331867746": ("real_bf4", "use-report"),
    "2098208146095292865": ("robotshelp", "teaching"),
    "2098549463103242269": ("rockernestor", "teaching"),
    "2098453862999089351": ("satriyop", "use-report"),
    "2098211142694764750": ("the_davey", "advertisement"),
    "2098558891755765981": ("thienepachon", "teaching"),
    "2098087818564182038": ("unicodef1wn", "teaching"),
    "2098069526743224668": ("Valstry", "teaching"),
    "2098066799258632222": ("Valstry", "teaching"),
    "2098065848544227390": ("Valstry", "teaching"),
    "2098065656717676769": ("Valstry", "teaching"),
    "2098065505336885398": ("Valstry", "teaching"),
    "2098064906159554772": ("Valstry", "teaching"),
    "2098483307105144911": ("vibecodeapp", "advertisement"),
    "2098307163403764181": ("XEthanai", "product-explainer"),
}
CENSUS_NOTES = {
    # A reject rule that removes @DenisLabelle's numbered index of other people's guides also
    # removes the VENDOR'S OWN five-step Chinese install tutorial (@grok, 2098259415673532624).
    # Measured, not assumed: a `link-list` rule that requires each step body to be longer than
    # 40 characters once its URL is stripped flags both, because a 30-character CJK step body is
    # a complete sentence and the threshold is Latin-biased. At n=77 the census is the cheaper
    # and safer instrument, so the false positive stays in the artifact labelled
    # `link-list-index` rather than being regexed away with a real tutorial.
    "link-list-index": "a reject for this class also killed the vendor's own CJK install guide",
    # @huxlab (Chinese) and @MaiYangAI (Chinese) both open by attributing the content to a named
    # SpaceXAI engineer or product lead — the exact shape `RELAY_FRAME` and `RELAY_ROLE_LEAD`
    # catch in English and are structurally blind to here. @MaiYangAI is a translation of
    # @lennysan's relay, which the English rule DOES reject; the translation passes.
    #
    # The two are NOT given the same verdict, and the difference is the point: @MaiYangAI is a
    # pure relay and is labelled `relay-nonenglish`, while @huxlab relays a talk AND hands over
    # numbered rules a reader can act on, so it stays `teaching`. So the authored/relayed split
    # is measured for English and unmeasured for everything else, and the honest number is two
    # frames SEEN, one of which changed a verdict.
    "relay-nonenglish": "the attribution rules are English-only; 2 CJK relay frames were found "
    "by hand in 77 rows, and 1 of them changed a verdict",
}
# The CJK relay frames the census found. Named rather than counted inline, because `2` in a
# format string is a claim with no handle attached to it.
NON_ENGLISH_RELAY_FRAMES = ("huxlab", "MaiYangAI")


def census_verdict(tweet_id: Optional[str]) -> Optional[str]:
    row = CENSUS.get(tweet_id or "")
    return row[1] if row else None


# ---------------------------------------------------------------------------------------------
# FORM detectors. Each one is asked the same three questions: how many teaching posts, how many
# DISTINCT AUTHORS, and how many hits anywhere in the corpus — the last being the LIVE CONTROL
# that proves the detector is not structurally dead before any absence is claimed.
# ---------------------------------------------------------------------------------------------
BULLET = re.compile(
    r"[\u2022\u2192\u25aa\u00b7\u279c\u2705\ud83d\udc49]|(?:^|\s)-\s+\w|(?:^|\s)>\s+\w"
)
NAMED_BOT = re.compile(
    r"\b(?:call\s?sign|callsign|named?|call(?:ed)?\s+(?:it|him|her)|name\s+it)\s+"
    r"[\"\u201c]?([A-Z][a-z]{2,12})\b"
    r"|\b([A-Z][a-z]{2,12})\s+(?:the\s+)?(?:chief\s+of\s+staff|my\s+bot)\b"
    r"|\bYou\s+are\s+([A-Z][a-z]{2,12})\b"
)
BEFORE_AFTER = re.compile(
    r"\bbefore\s*[:\u2014]\s*\S[\s\S]{0,400}?\b(?:after|now)\s*[:\u2014]", re.I
)
COMMAND = re.compile(
    r"(?:^|\s)(?:npm|npx|pip|pipx|uv|cargo|curl|git|sudo|brew|docker|bash|sh|python3?)\s+"
    r"[\w./-]+|`[^`]{3,}`|/workspace\b|\s--[a-z][\w-]{2,}\b|\$\s?\w+\s"
)
IMAGE_REF = re.compile(
    r"\bscreenshot|\bscreen\s?shot|\bsee\s+(?:the\s+)?(?:image|pic|photo|screenshot)|"
    r"\bpic\.twitter|\bimage\s+below|\bscreen\s+recording|\bvideo\s+below",
    re.I,
)
THREAD_MARK = re.compile(
    r"\U0001f9f5|\bthread\b|\b\d{1,2}\s?/\s?\d{1,2}\b|\bpart\s+\d\b", re.I
)
SCHEDULE = re.compile(
    r"\b(?:\d{1,2}\s?[:.]\s?\d{2}\s?(?:am|pm)?|\d{1,2}\s?(?:am|pm))\b|"
    r"\bevery\s+(?:morning|day|night|week|hour|weekday|monday|friday)\b|"
    r"\bdaily\b|\bweekly\b|\bhourly\b|\bcron\b|\bon\s+a\s+schedule\b",
    re.I,
)
CONNECTOR = re.compile(
    r"\bconnector|\bmcp\b|\bplugin|\bintegrat|\bconnect\s+(?:it|the|your|my|to)\b|"
    r"\bapi\s+key\b|\boauth\b",
    re.I,
)
ROUTINE = re.compile(r"\broutine|\bschedul|\btrigger|\bcron\b|\brecurring\b", re.I)
SKILL = re.compile(r"\bskills?\b", re.I)
ARTIFACT = re.compile(
    r"github\.com|gist\.|\bgithub\b|\brepo\b|\bmarkdown\s+file\b|\btemplate\b", re.I
)
FAILURE = re.compile(
    r"\bmistake|\bwent\s+wrong|\bbroke\b|\bfailed\b|"
    r"\bdon'?t\s+(?:do|let|just|automate|upgrade|buy|hire)|\bnever\s+\w+|\bstop\s+\w+ing\b|"
    r"\bwasted?\b|\bburn(?:ed|ing)?\s+(?:tokens|\$)",
    re.I,
)
CHAPTERS = re.compile(r"\b\d{1,2}:\d{2}\b[^.]{0,40}\b\d{1,2}:\d{2}\b")
COST_USE = re.compile(
    r"\btokens?\b|\busage\s+limit|\bweekly\s+(?:limit|usage)|\brate\s+limit|\bcredits?\b|"
    r"\bplan\b.{0,20}\$|\$\d+\s?/?\s?(?:mo|month)?",
    re.I,
)

# (name, detector, what the form IS, the fixture the selftest holds it live against)
FORMS: Tuple[Tuple[str, Callable[[str], bool], str, str], ...] = (
    (
        "numbered-list",
        stepped,
        "an ordered procedure, 1. 2. 3.",
        "1. Connect Grok to GitHub 2. Tell your Grok Bot to give orders",
    ),
    (
        "bulleted-list",
        lambda t: len(BULLET.findall(t)) >= 2,
        "an unordered instruction list",
        "\u2022 Put 2 Bots in one group \u2022 One owner per stage, grok bot",
    ),
    (
        "connector-named",
        lambda t: bool(CONNECTOR.search(t)),
        "names a connector, plugin or MCP server",
        "you can add Stripe's official MCP server as a custom connector to your grok bot",
    ),
    (
        "routine-named",
        lambda t: bool(ROUTINE.search(t)),
        "names a routine, schedule or trigger",
        "set a daily routine on your grok bot",
    ),
    (
        "skill-named",
        lambda t: bool(SKILL.search(t)),
        "names a skill",
        "save that as a skill on the grok bot",
    ),
    (
        "explicit-schedule",
        lambda t: bool(SCHEDULE.search(t)),
        "states WHEN the work runs",
        "grok bot: Daily \u2192 08:00 \u2192 paste this",
    ),
    (
        "prompt-verbatim",
        prompt_verbatim,
        "quotes a reusable prompt or charter",
        "grok bot prompts worth stealing: 1. Chief of Staff",
    ),
    (
        "failure-framed",
        lambda t: bool(FAILURE.search(t)),
        "teaches from a mistake or a cost",
        "Don't upgrade just because your grok bot keeps hitting the weekly limit",
    ),
    (
        "cost-or-usage",
        lambda t: bool(COST_USE.search(t)),
        "quantifies tokens, limits or spend",
        "grok bot meters agent steps and tokens, not messages",
    ),
    (
        "artifact-link",
        lambda t: bool(ARTIFACT.search(t)),
        "hands over a repo, gist or template",
        "found this grok bot desk on github.com, paste into Grok Bot to build",
    ),
    (
        "named-bot",
        lambda t: bool(NAMED_BOT.search(t)),
        "gives the Bot a proper name",
        "my grok bot: name it Signal and save that as a skill",
    ),
    (
        "command-or-path",
        lambda t: bool(COMMAND.search(t)),
        "shows a command or a filesystem path",
        "keep the grok bot work in /workspace",
    ),
    (
        "thread-marker",
        lambda t: bool(THREAD_MARK.search(t)),
        "declares itself part of a thread",
        "grok bot setup, here's the order 1/7 \U0001f9f5",
    ),
    (
        "image-reference",
        lambda t: bool(IMAGE_REF.search(t)),
        "points at a screenshot or a recording",
        "the grok bot screenshot below shows the routine",
    ),
    (
        "video-chapters",
        lambda t: bool(CHAPTERS.search(t)),
        "indexes a video by timestamp",
        "grok bot walkthrough 00:00 Intro 01:28 The build",
    ),
    (
        "before-after",
        lambda t: bool(BEFORE_AFTER.search(t)),
        "shows the same prompt badly and well",
        "grok bot brief. Before: Go online and research. Now: Your objective is",
    ),
)
FORM_NAMES = tuple(name for name, _, _, _ in FORMS)
# The three concepts the repo's own templates encode. Measured together because a template that
# ships all three is only useful if practitioners talk about all three.
TEMPLATE_CONCEPTS = ("connector-named", "routine-named", "skill-named")
# Under this many teaching posts a form is reported as an ABSENCE rather than a share, and the
# absence is only printed when the detector has a live control (see `absence_rows`). Two is the
# floor at which a "share of authors" stops being one person's habit.
ABSENT_AT = 2


def forms_of(text: str) -> List[str]:
    return [name for name, fn, _, _ in FORMS if fn(text)]


# ---------------------------------------------------------------------------------------------
# Row helper. Every row in this artifact answers the same four questions.
# ---------------------------------------------------------------------------------------------
def row(
    row_id: str, evidence: str, source: str, reproduce: Optional[str], **extra: Any
) -> Dict[str, Any]:
    out: Dict[str, Any] = {
        "id": row_id,
        "evidence": evidence,
        "source": source,
        "reproduce": reproduce,
    }
    out.update(extra)
    return out


SELF = "python3 bin/gb-teach.py"


# ---------------------------------------------------------------------------------------------
# Corpus loading — every x/ artifact, merged on tweet id
# ---------------------------------------------------------------------------------------------
def corpus_paths(root: pathlib.Path) -> List[pathlib.Path]:
    """Every `x/` artifact, sweep or not, and deliberately not a named pair.

    Neither file is a superset of another: `gb-x-sweep.py:counts.seen_in_first_pass` says 230 of
    its 1,508 rows were already in the first pass, and the third artifact
    (`x/2026-09-12T0024.json`) adds 392 posts and 10 teaching candidates the other two do not
    hold — four of them the vendor's own step-by-step setups. Reading a named pair and silently
    ignoring a third file on disk would have cost 7 of the 51 teaching posts.
    """
    return sorted(p for p in (root / "x").glob("*.json") if p.name[:10].count("-") == 2)


def load_corpus(root: pathlib.Path) -> Tuple[List[Dict[str, Any]], List[str]]:
    """`(posts, sources)`. Deduplicated on `signals.tweet_id`, first artifact wins.

    Returns `([], [])` when nothing is readable rather than raising, so a missing snapshot is a
    refusal in `build` and not a traceback here.
    """
    seen: Dict[str, Dict[str, Any]] = {}
    used: List[str] = []
    for path in corpus_paths(root):
        doc = read_json_capped(path)
        if not isinstance(doc, dict):
            continue
        rows = doc.get("rows")
        if not isinstance(rows, list):
            continue
        used.append(str(path.relative_to(root)))
        for r in rows:
            if not isinstance(r, dict) or r.get("kind") != "x-post":
                continue
            sig = r.get("signals") or {}
            tid = str(sig.get("tweet_id") or r.get("id") or "")
            if tid and tid not in seen:
                seen[tid] = r
    return list(seen.values()), used


def post_text(r: Dict[str, Any]) -> str:
    return normalise(r.get("summary") or r.get("title") or "")


def post_author(r: Dict[str, Any]) -> str:
    return str((r.get("signals") or {}).get("author") or "")


def post_url(r: Dict[str, Any]) -> str:
    return str(r.get("url") or "")


# ---------------------------------------------------------------------------------------------
# Measurement
# ---------------------------------------------------------------------------------------------
def quantile(values: Sequence[int], p: float) -> int:
    if not values:
        return 0
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(len(ordered) * p))]


def teaching_rows(posts: Sequence[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
    """Accepted, rejected and weak-only candidates, each as an artifact row."""
    accepted: List[Dict[str, Any]] = []
    rejected: List[Dict[str, Any]] = []
    for r in posts:
        text = post_text(r)
        author = post_author(r)
        verdict = classify_teach(text, author)
        if verdict is None:
            continue
        sig = r.get("signals") or {}
        tid = str(sig.get("tweet_id") or "")
        # `use` is the SWEEP's verdict, imported, never recomputed here. Most teaching posts are
        # not use claims at all (`you can add Stripe's MCP` has no operator), so `None` is the
        # common and correct answer, and it is why the relay split below leans on the frame
        # rules rather than on the quote-strip leg, which needs a use claim to remove.
        use = sw.classify_use(text, author)
        entry = row(
            "teach-%s" % tid,
            "%s | patterns=%s" % (text[:180], ",".join(verdict["patterns"])),
            post_url(r),
            "%s posts --json" % SELF,
            author=author,
            tweet_id=tid,
            chars=len(text),
            char_capped=len(text) >= 700,
            patterns=verdict["patterns"],
            weak_signals=verdict["weak_signals"],
            rejects=verdict["rejects"],
            vendor=verdict["vendor"],
            forms=forms_of(text),
            use_tier=(use or {}).get("tier"),
            use_relay=(use or {}).get("relay"),
            audit=census_verdict(tid),
            classifier=CLASSIFIER_VERSION,
        )
        (accepted if verdict["is_teaching_candidate"] else rejected).append(entry)
    accepted.sort(key=lambda e: (e["author"].lower(), e["tweet_id"]))
    rejected.sort(key=lambda e: (e["author"].lower(), e["tweet_id"]))
    return {"accepted": accepted, "rejected": rejected}


def audited(rows: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """The rows a human read and confirmed. `audit is None` means UNREAD, not confirmed — a post
    harvested after the census was taken lands here and is excluded from the headline rather
    than quietly counted."""
    return [e for e in rows if e.get("audit") == "teaching"]


def authors_of(rows: Sequence[Dict[str, Any]]) -> "collections.Counter[str]":
    return collections.Counter(e["author"].lower() for e in rows)


def form_rows(
    teach: Sequence[Dict[str, Any]], corpus_texts: Sequence[str]
) -> List[Dict[str, Any]]:
    """One row per form, ranked by DISTINCT AUTHORS.

    Ranked by authors and not by posts, because @Valstry posted the same Chinese install
    tutorial six times and @Michael_Fenech_ four different lessons: counting posts would make
    one repeated post outrank four separate ones. The post count is reported beside it, and
    `dominated` fires when one author holds at least half a form's posts.
    """
    out: List[Dict[str, Any]] = []
    n_authors = len(authors_of(teach))
    for name, fn, what, fixture in FORMS:
        hits = [e for e in teach if name in e["forms"]]
        by_author = authors_of(hits)
        top, top_n = (by_author.most_common(1) or [("", 0)])[0]
        share = (top_n / len(hits)) if hits else 0.0
        control = sum(1 for t in corpus_texts if fn(t))
        out.append(
            row(
                "form-%s" % name,
                "%d post(s) by %d author(s); detector fires %d time(s) across the whole corpus"
                % (len(hits), len(by_author), control),
                "x/*.json rows (kind=x-post), summary field",
                "%s forms --json" % SELF,
                form=name,
                what=what,
                posts=len(hits),
                authors=len(by_author),
                author_share=round(len(by_author) / n_authors, 3) if n_authors else 0.0,
                top_author=top or None,
                top_author_posts=top_n,
                top_author_share=round(share, 3),
                dominated=bool(hits) and share >= DOMINATED_AT,
                corpus_control=control,
                control_fixture=fixture,
                detector_live=control > 0,
            )
        )
    out.sort(key=lambda e: (-e["authors"], -e["posts"], e["form"]))
    return out


def absence_rows(forms: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """The forms NOBODY is publishing, each with the control that makes the claim sayable.

    An empty result is not evidence of absence. Every row therefore carries `corpus_control` —
    how many times the SAME detector fires across every post in the corpus — and `verdict` is:

      absent-with-live-control   0 teaching hits, and the detector demonstrably fires elsewhere
      rare-with-live-control     1 or 2 teaching hits, detector alive
      absent-control-too-weak    0 teaching hits and 0 corpus hits: NOT a measurement. The
                                 selftest holds a fixture against every detector precisely so
                                 this verdict can never be silently read as an absence.
    """
    out: List[Dict[str, Any]] = []
    for f in forms:
        if f["posts"] >= ABSENT_AT + 1:
            continue
        if f["posts"] == 0:
            verdict = (
                "absent-with-live-control"
                if f["corpus_control"]
                else "absent-control-too-weak"
            )
        else:
            verdict = (
                "rare-with-live-control"
                if f["corpus_control"] > f["posts"]
                else "rare-control-is-itself"
            )
        out.append(
            row(
                "absence-%s" % f["form"],
                "%d teaching post(s); the same detector fires %d time(s) across the corpus, so "
                "a zero here is a property of teaching posts and not of the detector"
                % (f["posts"], f["corpus_control"]),
                "x/*.json rows (kind=x-post), summary field",
                "%s absences --json" % SELF,
                form=f["form"],
                what=f["what"],
                teaching_posts=f["posts"],
                teaching_authors=f["authors"],
                corpus_control=f["corpus_control"],
                control_fixture=f["control_fixture"],
                verdict=verdict,
            )
        )
    out.sort(key=lambda e: (e["teaching_posts"], -e["corpus_control"], e["form"]))
    return out


def length_rows(
    teach: Sequence[Dict[str, Any]], posts: Sequence[Dict[str, Any]]
) -> List[Dict[str, Any]]:
    """Teaching length against everything else, with the censoring stated.

    `gb-x.py:640` writes `summary = " ".join(text.split())[:700]`, which does two things this
    row has to declare: it strips every newline (so a list that used line breaks as its only
    marker is INVISIBLE to `stepped`) and it truncates at 700 characters. p90 for teaching posts
    lands ON the cap, so it is a FLOOR, not a value.
    """
    tids = {e["tweet_id"] for e in teach}
    t_len = [e["chars"] for e in teach]
    o_len = [
        len(post_text(r))
        for r in posts
        if str((r.get("signals") or {}).get("tweet_id")) not in tids
    ]
    out = []
    for label, vals in (("teaching", t_len), ("non-teaching", o_len)):
        capped = sum(1 for v in vals if v >= 700)
        out.append(
            row(
                "length-%s" % label,
                "n=%d median=%d p90=%d; %d row(s) hit the 700-char harvest cap, so p90 is a "
                "floor for this group"
                % (len(vals), quantile(vals, 0.5), quantile(vals, 0.9), capped),
                "gb-x.py:640 caps summary at 700 chars and collapses whitespace",
                "%s forms --json" % SELF,
                group=label,
                n=len(vals),
                median=quantile(vals, 0.5),
                p90=quantile(vals, 0.9),
                p90_censored=quantile(vals, 0.9) >= 700,
                capped_rows=capped,
                capped_share=round(capped / len(vals), 3) if vals else 0.0,
            )
        )
    return out


def author_rows(
    teach: Sequence[Dict[str, Any]], limit: int = 20
) -> List[Dict[str, Any]]:
    """Top teaching authors by DISTINCT post count, with a URL each and their form mix."""
    grouped: Dict[str, List[Dict[str, Any]]] = collections.defaultdict(list)
    for e in teach:
        grouped[e["author"].lower()].append(e)
    ranked = sorted(grouped.items(), key=lambda kv: (-len(kv[1]), kv[0]))[:limit]
    out = []
    for handle, entries in ranked:
        forms = sorted({f for e in entries for f in e["forms"]})
        out.append(
            row(
                "author-%s" % handle,
                "%d teaching post(s); forms=%s"
                % (len(entries), ",".join(forms) or "none"),
                entries[0]["source"],
                "%s authors --json" % SELF,
                author=entries[0]["author"],
                posts=len(entries),
                urls=[e["source"] for e in entries],
                forms=forms,
                vendor=any(e["vendor"] for e in entries),
                near_duplicate=len({e["evidence"][:60] for e in entries})
                < len(entries),
            )
        )
    return out


def thread_rows(
    teach: Sequence[Dict[str, Any]], posts: Sequence[Dict[str, Any]]
) -> Dict[str, Any]:
    """The STRUCTURAL thread question, which the text markers cannot answer.

    `thread-marker` is textual. This is the conversation graph: an author with two or more of
    their own posts inside one `conversation_id`. It is reported separately because the harvest
    is search-shaped — a thread's continuations may simply never have matched a query — so a low
    number here is a floor on threading, not a measurement of it.
    """
    own: Dict[Tuple[str, str], Set[str]] = collections.defaultdict(set)
    root_author: Dict[str, str] = {}
    for r in posts:
        sig = r.get("signals") or {}
        cid = str(sig.get("conversation_id") or "")
        tid = str(sig.get("tweet_id") or "")
        au = post_author(r).lower()
        own[(cid, au)].add(tid)
        if cid and cid == tid:
            root_author[cid] = au
    multi = 0
    roots = 0
    for e in teach:
        # A distinct name from the `r` above: reusing it pins this lookup to the
        # non-optional element type, and the `None` default is the whole point here.
        src: Optional[Dict[str, Any]] = next(
            (
                p
                for p in posts
                if str((p.get("signals") or {}).get("tweet_id")) == e["tweet_id"]
            ),
            None,
        )
        if src is None:
            continue
        sig = src.get("signals") or {}
        cid = str(sig.get("conversation_id") or "")
        if len(own[(cid, e["author"].lower())]) >= 2:
            multi += 1
        if cid == e["tweet_id"]:
            roots += 1
    return {
        "posts_with_two_or_more_own_posts_in_one_conversation": multi,
        "posts_that_are_their_own_conversation_root": roots,
        "of": len(teach),
        "floor_not_measurement": "the harvest is search-shaped; a thread's continuations need "
        "not have matched any query, so this is a lower bound on threading",
    }


CAVEATS = (
    "Attachments are NOT in this corpus. `gb-x.py:476` drops `.../status/N/photo/1` sublinks as "
    "`drop-media` and keeps only the count, so whether a teaching post carried a screenshot is "
    "UNMEASURABLE here — `image-reference` measures the TEXT saying so, which is a different and "
    "much rarer thing.",
    "Newlines are gone. `gb-x.py:640` collapses whitespace, so a numbered list that used line "
    "breaks as its only marker reads as one line and `stepped` can only see inline `1. 2.` "
    "markers. Every list count in this file is therefore a floor.",
    "Length is right-censored at 700 characters. Teaching posts hit the cap far more often than "
    "the rest of the corpus, so their p90 is reported as a floor, not a value.",
    "The product anchor is `gb-x-sweep.PROD` (`grok bot|grokbot`). Instruction about `the bot` or "
    "`@bot` without the product name is NOT counted — @mattyp's `just ask the bot to make a "
    "diagram or write math` is real instruction and is outside the population, deliberately, so "
    "this file's population is comparable with the sweep's use population.",
    "The authored/relayed rules are English-only. Two of the 77 accepted rows (@huxlab, "
    "@MaiYangAI) are CJK attributions of a SpaceXAI engineer's talk that `RELAY_FRAME` and "
    "`RELAY_ROLE_LEAD` are structurally blind to; the census found both, a rule found neither. "
    "They get DIFFERENT verdicts: @MaiYangAI is a pure relay and is excluded, @huxlab relays a "
    "talk but hands over numbered rules a reader can act on and stays counted. So `authored: "
    "51` is warranted by the frame rules for English and by hand for these two, and by nothing "
    "at all for any other language.",
    "Precision is 51/77 by CENSUS, not by sample. The 26 non-teaching rows are named in "
    "`CENSUS` with their class, so the number can be disputed by re-reading them.",
    "Three artifacts, two days. Every count is a snapshot of 2026-09-11/12 and nothing here "
    "measures a trend.",
)


def build(root: pathlib.Path) -> Optional[Dict[str, Any]]:
    posts, sources = load_corpus(root)
    if not posts:
        return None
    texts = [post_text(r) for r in posts]
    split = teaching_rows(posts)
    accepted = split["accepted"]
    confirmed = audited(accepted)
    forms = form_rows(confirmed, texts)
    by_author = authors_of(confirmed)
    vendor_posts = [e for e in confirmed if e["vendor"]]
    concept_hits = {
        c: len({e["author"].lower() for e in confirmed if c in e["forms"]})
        for c in TEMPLATE_CONCEPTS
    }
    all_three = [
        e for e in confirmed if all(c in e["forms"] for c in TEMPLATE_CONCEPTS)
    ]
    census_classes = collections.Counter(v for _, v in CENSUS.values())
    return {
        "schema": SCHEMA,
        "captured_at": datetime.datetime.now(datetime.timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z"),
        "volatile_fields": VOLATILE_FIELDS,
        "classifier": CLASSIFIER_VERSION,
        "imported": {
            "module": "gb-x-sweep.py",
            "use_classifier": sw.CLASSIFIER_VERSION,
            "attribution": sw.ATTRIBUTION_VERSION,
            "reused": [
                "PROD",
                "classify_use",
                "strip_quoted",
                "RELAY_FRAME",
                "RELAY_ROLE_LEAD",
                "DOMINATED_AT",
            ],
        },
        "corpus": {
            "artifacts": sources,
            "posts": len(posts),
            "authors": len({post_author(r).lower() for r in posts}),
        },
        "counts": {
            "candidates": len(accepted) + len(split["rejected"]),
            "accepted_by_detector": len(accepted),
            "rejected_with_reason": len(split["rejected"]),
            "audited_teaching": len(confirmed),
            "audited_not_teaching": len(accepted)
            - len(confirmed)
            - len([e for e in accepted if e["audit"] is None]),
            "unread_by_census": len([e for e in accepted if e["audit"] is None]),
            "teaching_authors": len(by_author),
            "teaching_authors_excluding_vendor": len(by_author)
            - len({e["author"].lower() for e in vendor_posts}),
            "vendor_posts": len(vendor_posts),
            # AUTHORED vs RELAYED, and exactly how far the measurement reaches. Every accepted
            # row cleared both of the sweep's FRAME legs by construction — a frame match is a
            # reject — so `authored` is real but its warrant is the frame rules alone. The
            # sweep's third leg (re-classify with quoted spans removed) needs a USE CLAIM to
            # remove, and most teaching posts are not use claims, so it could only have judged
            # `relay_quote_strip_applicable` of them. Reporting a bare `relayed: 0` would state
            # a measurement that two of these rows disprove (@huxlab, @MaiYangAI, both CJK).
            "authored": len([e for e in confirmed if not e["use_relay"]]),
            "relayed_by_frame_rule": len([e for e in confirmed if e["use_relay"]]),
            "relay_frame_rejects_removed": len(
                [
                    e
                    for e in split["rejected"]
                    if {"reported-speech-frame", "third-party-role-attribution-lead"}
                    & set(e["rejects"])
                ]
            ),
            "relay_quote_strip_applicable": len(
                [e for e in confirmed if e["use_tier"]]
            ),
            "relay_frames_non_english_found_by_hand": len(NON_ENGLISH_RELAY_FRAMES),
            "relay_non_english_excluded": len(
                [k for k, (_, v) in CENSUS.items() if v == "relay-nonenglish"]
            ),
            "precision_census": round(len(confirmed) / len(accepted), 3)
            if accepted
            else 0.0,
            "teaching_share_of_corpus": round(len(confirmed) / len(posts), 4)
            if posts
            else 0.0,
        },
        "rows": forms,
        "absences": absence_rows(forms),
        "lengths": length_rows(confirmed, posts),
        "top_authors": author_rows(confirmed),
        "teaching_posts": confirmed,
        "rejected_candidates": split["rejected"],
        "unconfirmed_candidates": [e for e in accepted if e["audit"] != "teaching"],
        "threads": thread_rows(confirmed, posts),
        "template_concepts": {
            "authors_per_concept": concept_hits,
            "posts_naming_all_three": len(all_three),
            "authors_naming_all_three": len({e["author"].lower() for e in all_three}),
        },
        "census": {
            "read": len(CENSUS),
            "classes": dict(
                sorted(census_classes.items(), key=lambda kv: (-kv[1], kv[0]))
            ),
            "notes": CENSUS_NOTES,
        },
        "caveats": list(CAVEATS),
    }


# ---------------------------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------------------------
def emit(text: str) -> None:
    """`print`, except that a reader who walked away is not a failed measurement."""
    try:
        sys.stdout.write(text + "\n")
    except BrokenPipeError:  # pragma: no cover - the reader closed the pipe
        try:
            sys.stdout.close()
        except BrokenPipeError:
            pass


def render_forms(doc: Dict[str, Any]) -> str:
    c = doc["counts"]
    out = [
        "teaching form - %d post(s) by %d DISTINCT AUTHOR(S) (%d excluding the vendor account)"
        % (
            c["audited_teaching"],
            c["teaching_authors"],
            c["teaching_authors_excluding_vendor"],
        ),
        "  corpus %d posts / %d authors from %s"
        % (
            doc["corpus"]["posts"],
            doc["corpus"]["authors"],
            ", ".join(doc["corpus"]["artifacts"]),
        ),
        "  detector accepted %d, census confirmed %d, precision %.0f%% (%d read, all of them)"
        % (
            c["accepted_by_detector"],
            c["audited_teaching"],
            100 * c["precision_census"],
            doc["census"]["read"],
        ),
        "  %.1f%% of the corpus teaches. %d candidate(s) rejected with a reason."
        % (100 * c["teaching_share_of_corpus"], c["rejected_with_reason"]),
        "  authored %d (all cleared both English frame rules; the quote-strip leg could only "
        "judge the %d that are also use claims; %d CJK relay frame(s) got past every rule and "
        "were found by hand, %d of which changed a verdict)"
        % (
            c["authored"],
            c["relay_quote_strip_applicable"],
            c["relay_frames_non_english_found_by_hand"],
            c["relay_non_english_excluded"],
        ),
        "",
        "%-18s %7s %7s %6s  %-22s %s"
        % ("form", "authors", "posts", "share", "top author", "flag"),
    ]
    for f in doc["rows"]:
        flag = "DOMINATED" if f["dominated"] else ("absent" if f["posts"] == 0 else "")
        out.append(
            "%-18s %7d %7d %5.0f%%  %-22s %s"
            % (
                f["form"],
                f["authors"],
                f["posts"],
                100 * f["author_share"],
                ("@%s x%d" % (f["top_author"], f["top_author_posts"]))
                if f["top_author"]
                else "-",
                flag,
            )
        )
    out.append("")
    for lr in doc["lengths"]:
        out.append(
            "length %-13s n=%-5d median=%-4d p90=%s  capped=%d (%.0f%%)"
            % (
                lr["group"],
                lr["n"],
                lr["median"],
                (">=%d" % lr["p90"]) if lr["p90_censored"] else str(lr["p90"]),
                lr["capped_rows"],
                100 * lr["capped_share"],
            )
        )
    tc = doc["template_concepts"]
    out.append("")
    out.append(
        "template concepts - connector %d author(s), routine %d, skill %d; all three in %d post(s) by %d author(s)"
        % (
            tc["authors_per_concept"]["connector-named"],
            tc["authors_per_concept"]["routine-named"],
            tc["authors_per_concept"]["skill-named"],
            tc["posts_naming_all_three"],
            tc["authors_naming_all_three"],
        )
    )
    th = doc["threads"]
    out.append(
        "threading - %d of %d teaching post(s) have 2+ of their author's posts in one "
        "conversation; %d are their own root (floor, not measurement)"
        % (
            th["posts_with_two_or_more_own_posts_in_one_conversation"],
            th["of"],
            th["posts_that_are_their_own_conversation_root"],
        )
    )
    return "\n".join(out)


def render_absences(doc: Dict[str, Any]) -> str:
    out = [
        "absent teaching forms - a zero is only sayable with a live control",
        "%-18s %8s %8s  %-26s %s" % ("form", "teaching", "corpus", "verdict", "what"),
    ]
    for a in doc["absences"]:
        out.append(
            "%-18s %8d %8d  %-26s %s"
            % (
                a["form"],
                a["teaching_posts"],
                a["corpus_control"],
                a["verdict"],
                a["what"],
            )
        )
    out.append("")
    out.append(
        "controls: every detector above is held live by a fixture in --selftest, so an"
    )
    out.append(
        "`absent-control-too-weak` verdict can never be read as an absence by accident."
    )
    return "\n".join(out)


def render_authors(doc: Dict[str, Any]) -> str:
    c = doc["counts"]
    singles = [a for a in doc["top_authors"] if a["posts"] == 1]
    out = [
        "top teaching authors by DISTINCT post count - %d author(s) total, %d shown"
        % (c["teaching_authors"], len(doc["top_authors"])),
    ]
    if singles:
        # Below the multi-post authors every row is a TIE at one post, and the order is
        # alphabetical. Saying so is the difference between a ranking and a list that looks like
        # one: nothing here says @huxlab out-teaches @LinHYxd.
        out.append(
            "  %d of the %d shown have exactly ONE post: that is a tie, ordered alphabetically, "
            "not a rank." % (len(singles), len(doc["top_authors"]))
        )
    for i, a in enumerate(doc["top_authors"], start=1):
        tag = " [vendor]" if a["vendor"] else ""
        tag += " [near-duplicate posts]" if a["near_duplicate"] else ""
        out.append("%2d. @%-18s %d post(s)%s" % (i, a["author"], a["posts"], tag))
        out.append("    %s" % a["urls"][0])
        out.append("    forms: %s" % (", ".join(a["forms"]) or "none"))
    return "\n".join(out)


def render_census(doc: Dict[str, Any]) -> str:
    c = doc["counts"]
    out = [
        "census - %d accepted post(s), all read, %d teach"
        % (doc["census"]["read"], c["audited_teaching"]),
        "precision %.1f%% as a census, not a sample" % (100 * c["precision_census"]),
        "",
    ]
    for cls, n in doc["census"]["classes"].items():
        out.append("  %-24s %3d" % (cls, n))
    out.append("")
    for cls, note in doc["census"]["notes"].items():
        out.append("  note %-20s %s" % (cls, note))
    out.append("")
    out.append("non-teaching rows, by handle:")
    for e in doc["unconfirmed_candidates"]:
        out.append(
            "  %-22s %-24s %s"
            % ("@" + e["author"], e["audit"] or "UNREAD", e["source"])
        )
    return "\n".join(out)


def render_posts(doc: Dict[str, Any]) -> str:
    out = []
    for e in doc["teaching_posts"]:
        out.append(
            "@%-18s %-40s %s"
            % (
                "%s%s" % (e["author"], "*" if e["vendor"] else ""),
                ",".join(e["patterns"]),
                e["source"],
            )
        )
        out.append("    forms: %s" % (", ".join(e["forms"]) or "none"))
    return "\n".join(out)


def write_artifact(root: pathlib.Path, doc: Dict[str, Any]) -> pathlib.Path:
    """The ONLY writer in this file."""
    out_dir = root / "teach"
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = doc["captured_at"].replace(":", "").replace("-", "")
    name = "%s-%s-%sT%s.json" % (stamp[0:4], stamp[4:6], stamp[6:8], stamp[9:13])
    path = out_dir / name
    atomic_write_text(path, json.dumps(doc, indent=1, ensure_ascii=False) + "\n")
    return path


# ---------------------------------------------------------------------------------------------
# Fixtures — real shapes, trimmed from the x/ artifacts
# ---------------------------------------------------------------------------------------------
def fx(
    tweet_id: str, author: str, text: str, *, conversation: Optional[str] = None
) -> Dict[str, Any]:
    return {
        "id": "fx-" + tweet_id,
        "url": "https://x.com/i/status/" + tweet_id,
        "kind": "x-post",
        "title": text[:80],
        "summary": text,
        "signals": {
            "tweet_id": tweet_id,
            "author": author,
            "conversation_id": conversation or tweet_id,
            "is_reply": False,
            "urls": [],
            "url_count": 0,
        },
    }


# POSITIVE CONTROL for the discriminator: instruction in the post, aimed at the reader, about
# the product's own mechanisms. Verbatim @CSkoda (2098508793994727766).
FX_TEACH = fx(
    "2098508793994727766",
    "CSkoda",
    "Grok Bot desk tip: separate skills from routines. A skill is how to do a task; a routine "
    "is when to check it. Put the schedule in the Chief of Staff, keep specialists draft-only, "
    "and let the desk stay silent when no rule fires. Fewer pings, better decisions.",
)
# NEGATIVE CONTROL: the ADVERTISEMENT shape, which is what `how to` overwhelmingly means in this
# corpus. Verbatim @RoundtableSpace. It must not reach the accept path at all.
FX_ADVERT = fx(
    "2098404942599905700",
    "RoundtableSpace",
    "THIS FREE 1-HOUR WORKSHOP SHOWS HOW TO TURN GROK BOT INTO A 24/7 AI TEAM. ROUTINES, AGENT "
    "HANDOFFS, APPROVAL GATES, ANALYTICS AND CLOUD AGENTS THAT KEEP WORKING ON AUTOPILOT. "
    "https://t.co/vLoWy3FFLJ",
)
# NEGATIVE CONTROL: a use REPORT. The sweep classifies it as a deployed use; this file must not
# classify it as teaching, because the reader is told nothing they can repeat.
FX_REPORT = fx(
    "2098470435012911573",
    "realsanb",
    "I use my Grok Bot for research and yt scripts every morning and it drafts the outline for me.",
)
# The stepped procedure, verbatim @B_doong2daddy — the form that carries most teaching here.
FX_STEPS = fx(
    "2098459254805962917",
    "B_doong2daddy",
    "Don't just let your Grok Bot work itself 1. Connect Grok to GitHub 2. Connect Cursor to "
    "GitHub 3. Tell your Grok Bot to give orders 4. Once Cursor is done building, let Grok Bot "
    "review and send it back to Cursor",
)
# KNOWN-BAD for the ordinal rule: an event agenda. `Day 1:`/`Day 2:` are ordinals with no
# procedure, and the label guard is the only thing between them and a "numbered list" count.
FX_AGENDA = fx(
    "2098300000000000001",
    "Tesla_FANtastic",
    "Grok Bot Galaxy lands next week and they are building a company LIVE with Grok Bot. Day 1: "
    "Engineering and PMs. Day 2: Sales and Support. Day 3: the demo.",
)
# KNOWN-BAD for the charter rule. Under `re.I` the `[A-Z]` in `PROMPT_CHARTER` is inert and this
# scores as a shared system prompt. Verbatim @kenokki.
FX_NOT_CHARTER = fx(
    "2098300000000000002",
    "kenokki",
    "You are a bad operator if your morning still needs you. Grok Bot already does the first "
    "hour. You just like feeling useful.",
)
# The real charter, verbatim @dataguybobby: opens by naming the role AND carries the structure.
FX_CHARTER = fx(
    "2098170174268686521",
    "dataguybobby",
    "You are Throttle (Token Officer) for the desk. ## Job Watch the Grok Bot fleet for token "
    "burn and wasteful loops. ## Skill Use Fleet token throttle. ## Cadence Own a weekday quiet "
    "fleet-burn digest routine. ## Stop Never auto-delete.",
)
# KNOWN-BAD for the reject path: a request, not an instruction. Verbatim @DomAtSiteSage.
FX_REQUEST = fx(
    "2098300000000000003",
    "DomAtSiteSage",
    "Can we get a Grok Bot CLI and/or MCP please? Maybe you can make it happen @poteto",
)
# KNOWN-BAD for the reject path: instruction aimed at the vendor. Verbatim @beyrouti.
FX_WISHLIST = fx(
    "2098300000000000004",
    "beyrouti",
    "Grok Bot next moves: 1. Connect to Grok Voice with its own phone number. 2. Grok Bot "
    "connector to Apple-native iOS. 3. Grok Bot ability to use your phone number.",
)
# THE QUOTE-STRIP LEG, and the post that paid for it. `Send me one brief` lives inside one of
# the five prompts; scanning raw text made REQUEST fire and discarded the best prompt-sharing
# post in the corpus. Trimmed @aiedge_ (2098260253263777830).
FX_QUOTED_PROMPT = fx(
    "2098260253263777830",
    "aiedge_",
    "5 Grok Bot prompts worth stealing: 1. Personal Chief of Staff \u201cEvery morning at 7am, "
    "scan my email, calendar and messages. Send me one brief: what needs a decision today.\u201d "
    "2. Competitor watch \u201cWatch the topic across news and X. Only alert me when something "
    "material changed.\u201d",
)
# DOMINANCE: four posts carrying one form, three authors, one of them holding half.
FX_DOM_A1 = fx(
    "2098300000000000010",
    "Repeater",
    "Grok Bot setup 1. Connect the connector 2. Save the skill 3. Set the routine",
)
FX_DOM_A2 = fx(
    "2098300000000000011",
    "Repeater",
    "Grok Bot setup again 1. Connect the connector 2. Save the skill 3. Set the routine",
)
FX_DOM_B1 = fx(
    "2098300000000000012",
    "Solo",
    "Grok Bot order that lasts 1. One chief of staff 2. Give every specialist one narrow job",
)
# The third handle exists because DOMINATED_AT is `>= 0.50` (inherited): with two posts by two
# authors EACH holds half and both are flagged, which is the rule working, not a bug. A control
# for "not dominated" therefore needs three separate authors, and getting that wrong is how a
# dominance flag gets quietly loosened to 0.6 to make a fixture pass.
FX_DOM_C1 = fx(
    "2098300000000000014",
    "Third",
    "Grok Bot rule 1. Connect the plugin first 2. Keep the routine quiet",
)
# ABSENCE CONTROL: a thread-declaring post that is NOT teaching. It proves `thread-marker` is
# alive while teaching posts score zero on it — which is exactly the shape an absence claim
# needs and cannot have from an empty result alone.
FX_THREAD_ONLY = fx(
    "2098300000000000013",
    "Threader",
    "grok bot is INSANE, the whole story 1/7 \U0001f9f5 stay tuned",
)

FIXTURES = [
    FX_TEACH,
    FX_ADVERT,
    FX_REPORT,
    FX_STEPS,
    FX_AGENDA,
    FX_NOT_CHARTER,
    FX_CHARTER,
    FX_REQUEST,
    FX_WISHLIST,
    FX_QUOTED_PROMPT,
    FX_DOM_A1,
    FX_DOM_A2,
    FX_DOM_B1,
    FX_DOM_C1,
    FX_THREAD_ONLY,
]


def selftest() -> int:
    legs: List[Tuple[str, bool, str]] = []

    def leg(name: str, good: bool, detail: str = "") -> None:
        legs.append((name, bool(good), detail))

    t_teach = post_text(FX_TEACH)
    t_advert = post_text(FX_ADVERT)
    t_report = post_text(FX_REPORT)

    # 1 — POSITIVE CONTROL. The discriminator accepts in-post reader-directed instruction.
    v = classify_teach(t_teach, "CSkoda")
    leg(
        "positive control: an in-post instruction is teaching",
        bool(v and v["is_teaching_candidate"] and "tip-rule" in v["patterns"]),
    )

    # 2 — NEGATIVE CONTROL. The advertisement shape never reaches the accept path. This is the
    #     leg that separates a teaching detector from a `how to` keyword grep.
    leg(
        "negative control: `workshop shows how to` is not teaching",
        classify_teach(t_advert, "RoundtableSpace") is None,
        "36 of the 40 `how to <verb>` hits carry no accept-pattern at all",
    )

    # 3 — NEGATIVE CONTROL. A use report is a use, not a lesson, and the sweep must still call
    #     it a use — proving the two axes are orthogonal rather than one relabelled.
    use = sw.classify_use(t_report, "realsanb")
    leg(
        "negative control: a use report is a use and not teaching",
        classify_teach(t_report, "realsanb") is None and bool(use and use["is_use"]),
    )

    # 4 — the stepped form, on the real post that carries it.
    v = classify_teach(post_text(FX_STEPS), "B_doong2daddy")
    leg(
        "a 1./2./3. procedure is a numbered list",
        bool(v and "stepped-procedure" in v["patterns"])
        and stepped(post_text(FX_STEPS)),
    )

    # 5 — FIRES ON KNOWN BAD. `Day 1:`/`Day 2:` is an agenda; without the label guard it counts
    #     as a procedure and an event announcement enters the teaching population.
    leg(
        "known-bad: Day 1/Day 2 is not a numbered procedure",
        not stepped(post_text(FX_AGENDA)),
        "the ORDINAL_LABEL guard; @Tesla_FANtastic's agenda",
    )

    # 6 — FIRES ON KNOWN BAD. The case-sensitivity defect, which cost five false positives.
    leg(
        "known-bad: `You are a bad operator if` is not a shared charter",
        not prompt_verbatim(post_text(FX_NOT_CHARTER)),
        "PROMPT_CHARTER is case-SENSITIVE; under re.I the [A-Z] is inert",
    )

    # 7 — and the real charter still passes, so leg 6 is a guard and not a mute.
    leg(
        "a named role plus ## structure IS a shared charter",
        prompt_verbatim(post_text(FX_CHARTER)),
    )

    # 8 — FIRES ON KNOWN BAD. A request carries a second-person modal and teaches nothing.
    v = classify_teach(post_text(FX_REQUEST), "DomAtSiteSage")
    leg(
        "known-bad: a feature request is rejected with a reason",
        bool(
            v
            and not v["is_teaching_candidate"]
            and "request-not-instruction" in v["rejects"]
        ),
    )

    # 9 — FIRES ON KNOWN BAD. Instruction aimed at the vendor is not instruction for a reader.
    v = classify_teach(post_text(FX_WISHLIST), "beyrouti")
    leg(
        "known-bad: a vendor wishlist is rejected with a reason",
        bool(
            v and not v["is_teaching_candidate"] and "vendor-wishlist" in v["rejects"]
        ),
    )

    # 10 — THE QUOTE-STRIP LEG. A reject must not fire on words the post is QUOTING.
    v = classify_teach(post_text(FX_QUOTED_PROMPT), "aiedge_")
    leg(
        "a reject inside a quoted prompt does not discard the post",
        bool(v and v["is_teaching_candidate"] and "prompt-verbatim" in v["patterns"]),
        "`Send me one brief` is inside prompt 1; raw-text scanning lost @aiedge_",
    )
    # 11 — and the same reject still fires when it is the AUTHOR asking, so leg 10 is scoped.
    leg(
        "the same reject still fires outside a quote",
        bool(REQUEST.search("send me a code please"))
        and not REQUEST.search(sw.strip_quoted('"send me a code"')),
    )

    # 12 — the second-person object window. Both posts say `you can <verb>`; one is about a Bot.
    leg(
        "second-person instruction needs a Bot as its object",
        second_person(
            "you can add Stripe's MCP server to your grok bot as a custom connector"
        )
        and not second_person(
            "hope you can make an exception for me so that i can eventually try something"
        ),
        "@Camilofimv's `hope you can make an exception` was a false positive",
    )

    # 13 — the weak signals are recorded and are never sufficient on their own.
    v = classify_teach(
        "Grok Bot 101 explains how to create a personal AI teammate in 10 minutes.",
        "cb_doge",
    )
    leg(
        "a `how to` announcement is never accepted on the weak signal alone",
        v is None or "how-to-lead" not in v["patterns"],
    )

    # 14 — DOMINANCE. One author holding half of a form's posts must be FLAGGED.
    dom_posts = [FX_DOM_A1, FX_DOM_A2, FX_DOM_B1, FX_DOM_C1]
    dom_rows = teaching_rows(dom_posts)["accepted"]
    for e in dom_rows:  # the census has not read fixtures; treat them as confirmed here
        e["audit"] = "teaching"
    dom_forms = form_rows(dom_rows, [post_text(p) for p in dom_posts])
    numbered = next(f for f in dom_forms if f["form"] == "numbered-list")
    leg(
        "an author holding >=50% of a form is flagged",
        numbered["posts"] == 4
        and numbered["authors"] == 3
        and numbered["top_author"] == "repeater"
        and numbered["top_author_share"] >= DOMINATED_AT
        and numbered["dominated"],
        "2 of 4 numbered-list posts are one handle",
    )
    # 15 — and a form spread over three authors is NOT flagged, so leg 14 is a measurement and
    #      not a constant that fires on everything.
    even = teaching_rows([FX_DOM_A1, FX_DOM_B1, FX_DOM_C1])["accepted"]
    for e in even:
        e["audit"] = "teaching"
    even_forms = form_rows(
        even, [post_text(p) for p in (FX_DOM_A1, FX_DOM_B1, FX_DOM_C1)]
    )
    even_numbered = next(f for f in even_forms if f["form"] == "numbered-list")
    leg(
        "a form spread over three authors is not flagged",
        even_numbered["posts"] == 3
        and even_numbered["authors"] == 3
        and not even_numbered["dominated"],
    )

    # 16 — ABSENCE, WITH ITS CONTROL. `thread-marker` scores zero on the teaching rows while the
    #      same detector fires on a thread-declaring post, which is what makes the zero sayable.
    all_texts = [post_text(p) for p in FIXTURES]
    abs_forms = form_rows(dom_rows, all_texts)
    thread = next(f for f in abs_forms if f["form"] == "thread-marker")
    rows_abs = absence_rows(abs_forms)
    thread_abs = next(a for a in rows_abs if a["form"] == "thread-marker")
    leg(
        "absence of thread-marker is reported WITH a live control",
        thread["posts"] == 0
        and thread["corpus_control"] >= 1
        and thread["detector_live"]
        and thread_abs["verdict"] == "absent-with-live-control",
        "@Threader's `1/7` proves the detector is alive; teaching rows still score 0",
    )

    # 17 — FIRES ON KNOWN BAD for the absence machinery itself: a detector with NO control must
    #      never be reported as an absence. Without this, a broken regex reads as a finding.
    dead = [
        row(
            "form-dead",
            "",
            "",
            None,
            form="dead-detector",
            what="a detector that matches nothing",
            posts=0,
            authors=0,
            author_share=0.0,
            top_author=None,
            top_author_posts=0,
            top_author_share=0.0,
            dominated=False,
            corpus_control=0,
            control_fixture="",
            detector_live=False,
        )
    ]
    leg(
        "a detector with no control is not an absence",
        absence_rows(dead)[0]["verdict"] == "absent-control-too-weak",
    )

    # 18 — every form detector is held LIVE by its own fixture. This is the leg that makes leg 16
    #      trustworthy for all sixteen forms and not just the one the corpus happens to exercise.
    dead_forms = [name for name, fn, _, fixture in FORMS if not fn(normalise(fixture))]
    leg(
        "every form detector matches its own control fixture",
        not dead_forms,
        ",".join(dead_forms),
    )

    # 19 — the census is internally consistent: no id read twice, classes cover every row.
    leg(
        "census ids are unique and every row carries a class",
        len(CENSUS) == len(set(CENSUS)) and all(v and a for a, v in CENSUS.values()),
    )

    # 20 — the census actually PARTITIONS: teaching plus named false-positive classes equals the
    #      number read, so precision cannot be inflated by an unlabelled row.
    classes = collections.Counter(v for _, v in CENSUS.values())
    leg(
        "census partitions: teaching + named classes == rows read",
        classes["teaching"] + sum(n for k, n in classes.items() if k != "teaching")
        == len(CENSUS)
        and classes["teaching"] == 51
        and len(CENSUS) == 77,
        "51 teach of 77 read",
    )

    # 21 — the imported classifier is the SWEEP's, not a copy. If `use/4` ever changes under us,
    #      this leg says so instead of letting two files disagree about what a use is.
    leg(
        "the use classifier and attribution rules are imported, not vendored",
        sw.CLASSIFIER_VERSION == "use/4"
        and sw.ATTRIBUTION_VERSION == "attrib/1"
        and DOMINATED_AT == sw.DOMINATED_AT,
    )

    # 21b — FIRES ON KNOWN BAD for the relay accounting. `relayed: 0` would be a measurement the
    #       census disproves, so the two non-English frames must be counted SEPARATELY from the
    #       one that changed a verdict, and every handle named in NON_ENGLISH_RELAY_FRAMES must
    #       actually be in the census. A renamed or dropped handle silently shrinks the claim.
    frame_handles = {h.lower() for h in NON_ENGLISH_RELAY_FRAMES}
    census_handles = {a.lower() for a, _ in CENSUS.values()}
    excluded = {a.lower() for a, v in CENSUS.values() if v == "relay-nonenglish"}
    leg(
        "the two CJK relay frames are named, present, and given different verdicts",
        frame_handles <= census_handles
        and len(frame_handles) == 2
        and excluded == {"maiyangai"}
        and CENSUS["2098351054543097988"][1] == "teaching",
        "@MaiYangAI excluded, @huxlab kept: a relay frame is not automatically not-teaching",
    )

    # 22 — every row in the contract carries its four fields, including a `reproduce` that may be
    #      null but must be PRESENT.
    doc = {
        "rows": form_rows(dom_rows, all_texts),
        "absences": rows_abs,
        "lengths": length_rows(dom_rows, dom_posts),
        "top_authors": author_rows(dom_rows),
        "teaching_posts": dom_rows,
    }
    missing = [
        (section, e.get("id"))
        for section, rows in doc.items()
        for e in rows
        if not all(k in e for k in ("id", "evidence", "source", "reproduce"))
    ]
    leg("every row carries id/evidence/source/reproduce", not missing, str(missing[:3]))

    # 23 — length censoring is DECLARED, not silently reported as a value. A group whose p90
    #      lands on the 700-char harvest cap must say so, because `p90=700` read as a value is a
    #      claim about how long people write and the harvester wrote that number, not the author.
    capped_rows = [
        row(
            "t-1",
            "",
            "",
            None,
            chars=700,
            tweet_id="a",
            author="A",
            vendor=False,
            forms=[],
        ),
        row(
            "t-2",
            "",
            "",
            None,
            chars=700,
            tweet_id="b",
            author="B",
            vendor=False,
            forms=[],
        ),
    ]
    lr = length_rows(capped_rows, [])
    teaching_len = next(x for x in lr if x["group"] == "teaching")
    short = length_rows(
        [
            row(
                "t-3",
                "",
                "",
                None,
                chars=120,
                tweet_id="c",
                author="C",
                vendor=False,
                forms=[],
            )
        ],
        [],
    )
    leg(
        "p90 on the 700-char cap is reported as censored, below it as a value",
        teaching_len["p90"] == 700
        and teaching_len["p90_censored"]
        and teaching_len["capped_rows"] == 2
        and not next(x for x in short if x["group"] == "teaching")["p90_censored"],
        "harvest caps at 700; see gb-x.py:640",
    )

    # 24 — no snapshots, no answer. An empty corpus refuses rather than printing zeros.
    leg(
        "a missing corpus refuses rather than printing zeros",
        build(pathlib.Path("/nonexistent-gb-teach-root")) is None,
    )

    # 25 — the renderers print what is measured and invent nothing.
    live = build(ROOT)
    if live is not None:
        text = render_forms(live)
        leg(
            "the form table renders every measured form",
            all(f["form"] in text for f in live["rows"]) and "DISTINCT AUTHOR" in text,
        )
        # 26 — the headline is the AUDITED count, never the detector's raw accept count.
        leg(
            "the headline count is the audited one, not the raw accept count",
            live["counts"]["audited_teaching"] <= live["counts"]["accepted_by_detector"]
            and live["counts"]["audited_teaching"] == 51
            and live["counts"]["accepted_by_detector"] == 77,
            "51 audited of 77 accepted",
        )
        # 27 — and every audited row really was read: no row reaches the headline unread.
        leg(
            "no unread row reaches the headline",
            all(e["audit"] == "teaching" for e in live["teaching_posts"]),
        )
    else:  # pragma: no cover - the repo always has x/ artifacts
        leg("live corpus readable", False, "x/*.json missing")
        leg("live headline audited", False, "x/*.json missing")
        leg("live rows audited", False, "x/*.json missing")

    ok = sum(1 for _, good, _ in legs if good)
    for name, good, detail in legs:
        mark = "ok  " if good else "FAIL"
        emit(
            "  {m} {n}{d}".format(m=mark, n=name, d=(" - " + detail) if detail else "")
        )
    emit(
        "SELFTEST {v} - {o}/{t}".format(
            v="PASS" if ok == len(legs) else "FAIL", o=ok, t=len(legs)
        )
    )
    return 0 if ok == len(legs) else 1


# ---------------------------------------------------------------------------------------------
def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        prog="gb-teach.py",
        description="What form a Grok Bot teaching post takes, measured offline from x/*.json.",
    )
    ap.add_argument(
        "verb",
        nargs="?",
        choices=("forms", "absences", "authors", "posts", "census"),
        default="forms",
    )
    ap.add_argument("--json", action="store_true", help="machine-readable envelope")
    ap.add_argument("--write", action="store_true", help="write teach/<ISO>.json")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args(argv)

    if args.selftest:
        return selftest()

    doc = build(ROOT)
    if doc is None:
        # NO x/ CORPUS. Fall back to the artifact this producer already wrote, because that is
        # exactly what it is for: the measurement TRAVELS even when the 3,026-post corpus does
        # not. Measured 2026-09-12 — a clean public clone ships teach/*.json and then refused
        # to read it, so a stranger held the verb, held the answer, and was told to go
        # collect 3,026 posts first. The fallback is labelled in the envelope so nobody
        # mistakes a replayed measurement for one taken on their own machine.
        prior = (
            sorted((ROOT / "teach").glob("*.json")) if (ROOT / "teach").is_dir() else []
        )
        if not prior:
            sys.stderr.write(
                "ERROR no x/ snapshot AND no teach/*.json to replay - run `gb x collect` "
                "to build a corpus, or fetch the shipped teach/ artifact\n"
            )
            return 2
        doc = json.loads(prior[-1].read_text())
        doc["replayed_from"] = prior[-1].name
        sys.stderr.write(
            f"note: no x/ corpus here - replaying the measurement recorded in "
            f"{prior[-1].name}. Numbers below describe THAT corpus, not this machine.\n"
        )

    if args.write:
        path = write_artifact(ROOT, doc)
        emit("wrote %s" % path.relative_to(ROOT))

    if args.json:
        emit(json.dumps(doc, indent=1, ensure_ascii=False))
        return 0

    renderers = {
        "forms": render_forms,
        "absences": render_absences,
        "authors": render_authors,
        "posts": render_posts,
        "census": render_census,
    }
    emit(renderers[args.verb](doc))
    return 0


if __name__ == "__main__":
    gbmain(main)
