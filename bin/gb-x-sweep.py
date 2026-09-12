#!/usr/bin/env python3
"""gb-x-sweep — the SECOND PASS over X, and the use-case classifier that reads it.

`gb-x.py` is the daily read. It ran once, at 2026-09-11T17:53Z, spent all twenty of its search
calls and produced `x/2026-09-11T1753.json`: 1,356 posts, 459 outbound links, 137 hosts. Asked
for "the top 100 uses people are getting from Grok Bot", that corpus answered with fewer than a
hundred. This file is why, and this file is the fix.

WHY A SECOND PASS IS NOT THE SAME PASS AGAIN (measured, from the first artifact):

    $ python3 -c "... for s in doc['sources']: print(s['items'], s['has_more'])"
    16 of the 20 queries returned items == 100 AND has_more == True.

  Sixteen queries hit the per-call ceiling with a next page waiting. So the corpus is not the
  population — it is the newest 100 posts of each of twenty questions. And the page cannot be
  taken: `x_cli/api.py:103` accepts `query` and `max_results` and NOTHING ELSE. No `next_token`,
  no `since_id`, no `until_id`. Pagination is unreachable from this instrument.

  Therefore the ONLY lever that reaches deeper into the population is THE QUERY STRING. Running
  `"grok bot" -is:retweet` again returns the same newest 100 posts. Running a NARROWER query
  returns a DIFFERENT newest 100 — the tail the broad query truncated. That is the whole
  mechanic this file exploits, and it is why every query below is disjoint from the first pass by
  construction rather than by hope.

THE TWO VEINS THE FIRST PASS ONLY SCRATCHED (both found by reading the corpus, not by taste):

  (1) PROMPT THREADS. `collections.Counter` over `signals.conversation_id` in the first artifact:
      one conversation holds 175 of the 1,356 posts (2098441494478889314, a "favourite Grok Bot
      use case" call-and-response hosted by @MatthewBerman) and a second holds 60
      (2098149586183270842, a credits giveaway). Those 235 posts arrived INCIDENTALLY, as spill
      from `vendor-mentions` and the practitioner batches, and both threads were truncated. A
      thread where hundreds of people volunteer a use case, unprompted, is the densest use-case
      surface on the platform, and `conversation_id:` addresses it directly.

  (2) USE LANGUAGE WITHOUT PRODUCT-NAME CEREMONY. The first matrix asked about the PRODUCT
      (`"grok bot" mcp`, `"grok bot" template`). People describing a use write `my grok bot
      checks ...`, `I set up a routine that ...`, `it drafts my ...`. `"my grok bot"` as a
      phrase was never queried. Nor was any connector name except the three vendor hosts.

WHAT THIS FILE CLAIMS, AND WHAT IT DOES NOT. The classifier here is a high-precision,
MODERATE-RECALL detector, and both numbers were measured by hand on the first-pass corpus rather
than asserted:

    precision  28/30 correct on a random sample of its hits          (0.93)
    recall     ~6.5/22 of the posts it REJECTED were really uses     (=> recall ~0.5)

  The two false positives and the named misses are recorded in `classifier` in every artifact,
  with the author handles, so the next reader can re-audit the same rows instead of trusting this
  docstring. Consequences, stated plainly:

    * A count from this file is a FLOOR, never a census. `uses_detected` undercounts.
    * The blind spots are NAMED: non-English posts (the classifier is English-only; `gsirginray`
      and `Minsi_AI` describe six- and forty-bot deployments in Chinese and are both missed),
      second-person instructions (`srt54558`, "send grok the template and give access to ur
      school logins"), and possessive-without-verb (`liam_fallen`, "my Grok Bot Sales
      Intelligence System"). Fixing these needs a different instrument, not a looser regex: the
      patterns that would catch them cost more precision than they buy recall, which was measured
      (an earlier revision reached 183 hits at 0.80 precision — strictly worse evidence).

RANKING IS BY DISTINCT AUTHORS. Never by citations, never by engagement. `gb-demand.py:60-64`
paid for this rule already: of the 52 integrations named by 3+ Bots in the local corpus, 26 have
ONE account holding half or more of the citations, and a `cites/builders` density flagged the
WRONG ones. So a use category here carries `distinct_authors` as its rank key and a
`top_author_share` flag at >= 0.50 (`DOMINATED_AT`, the same constant and the same threshold).
Likes and reposts are recorded because they are facts, and are excluded from the sort because
they are a popularity measurement of a post, not evidence that a use exists.

AUTHORED vs RELAYED, WHICH THE FIRST VERSION OF THIS FILE DID NOT SEPARATE. Ranking by distinct
authors is not enough, because a quote inside somebody's post is a RELAY and the classifier's
first-person patterns fire happily on text INSIDE the quotation marks. The live run measured the
consequence: FIVE accounts (@kaorixbt, @arle0x, @s4yonnara, @0xRafy, @sheemamoto) posted the same
SpaceXAI engineer's `I run 10-20 GrokBot agents, a Chief of Staff manages them` claim, and all
five counted as independent builders. `top_author_share` cannot see that, because the five
handles really are five distinct accounts.

  So every use verdict now carries `relay` and `relay_reason` (`attrib/1`, versioned separately
  because it FLAGS a use and never rejects one, leaving `use/4`'s audited precision intact). The
  decisive leg is mechanical, not stylistic: re-classify the post with every quoted span removed
  and ask whether the use claim survives without somebody else's words. Censused on all 37
  quote-bearing use rows, 6 fail that test and all 6 are relays. A relay is FLAGGED, never
  dropped - it is evidence that a claim circulates, and it is not evidence of an operator - so
  each category reports `distinct_authors` beside `authored_authors`, and `examples` holds
  authored posts only while `relayed_examples` holds the rest.

READ-ONLY, AND MORE STRUCTURALLY THAN THE FIRST PASS. This file BUILDS NO ARGV. It calls
`gb-x.py`'s `x_search`, which is the single frozen `("tweet", "search")` shape, and `selftest`
asserts that this module contains no x-cli argv construction of its own AND fires `gb-x.py`'s
`mutating_verbs` detector on known-bad source so the guard is proven live, not promised.

QUOTA. Unchanged and unchangeable: X exposes no rate-limit endpoint, `x_cli/api.py:46` turns 429
into a RuntimeError, so the quota is invisible until it is hit. The local counter is the binding
guard; it increments BEFORE the request; a 429 records `rate_limited` and stops the run with
everything collected so far intact.

  gb-x-sweep.py collect             # write x/<stamp>.sweep.json, print the summary
  gb-x-sweep.py collect --json      # the document on stdout
  gb-x-sweep.py collect --dry-run   # spend the quota, write nothing
  gb-x-sweep.py matrix              # the query design and the deferred candidates, no network
  gb-x-sweep.py reclassify          # re-read the artifacts on disk, 0 search calls, no write
  gb-x-sweep.py --selftest          # inline fixtures, no network
"""

from __future__ import annotations

import argparse
import ast
import collections
import datetime as dt
import importlib.util
import json
import os
import pathlib
import re
import sys
import time
from typing import Callable, Optional

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from gbtypes import Cancelled, atomic_write_json, main, read_json_capped  # noqa: E402


# -------------------------------------------------------------------------------------------
# gb-x.py, imported rather than copied
# -------------------------------------------------------------------------------------------
def _load_gbx() -> object:
    """Import `gb-x.py` as a module.

    The filename is not an identifier, so `import gb_x` cannot reach it. It is imported instead
    of vendored because the ROW CONTRACT must be identical, not similar: `post_row`, `content_id`
    and `harvest_urls` are the reason a sweep row and a daily row collide on the same id in the
    content-addressed store. A copied helper is a contract that drifts on the first edit to
    either side.

    `sys.modules[name] = mod` happens BEFORE `exec_module` — required on this interpreter
    (3.9.6) for any module whose body defines a `@dataclass`, because the dataclass machinery
    resolves the module by name while the module is still executing. `gb-x.py` defines none
    today; the ordering is kept anyway, since the failure it prevents is an import-time
    `KeyError` that would read as "gb-x.py is broken".
    """
    path = pathlib.Path(__file__).resolve().parent / "gb-x.py"
    spec = importlib.util.spec_from_file_location("gb_x_producer", path)
    if (
        spec is None or spec.loader is None
    ):  # pragma: no cover - a missing sibling is fatal
        raise ImportError("cannot load %s" % path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


gbx = _load_gbx()

SCHEMA = "gb-x-sweep/1"
# Inherited verbatim: the rows are `gb-x` rows, so they carry `gb-x`'s one volatile field and no
# other. Anything this file adds to `signals` is derived from the post TEXT or from a file on
# disk, never from the run clock.
VOLATILE_FIELDS = list(gbx.VOLATILE_FIELDS)

MAX_SEARCH_CALLS = gbx.MAX_SEARCH_CALLS  # 20
MAX_RESULTS = gbx.MAX_RESULTS  # 100
MIN_SLEEP_S = gbx.MIN_SLEEP_S  # 2.0

# One account holding half or more of a category's posts is speaking for it alone. Same constant,
# same threshold and same reason as `gb-demand.py:64`, which measured that 26 of 52 integrations
# clear this bar and that a citations/builders density flagged the wrong ones.
DOMINATED_AT = 0.50

# A query that returned nothing new, or nothing usable, is not a query worth a slot next run. The
# verdict is computed from the run's own numbers (`query_yield[].verdict`) rather than chosen.
PRUNE_MIN_NEW = 5  # fewer than this many posts nobody had seen -> the slot is wasted
PRUNE_MIN_USES = 1  # zero use-describing posts -> the question was about something else


# -------------------------------------------------------------------------------------------
# the query matrix — twenty slots, disjoint from the first pass BY CONSTRUCTION
# -------------------------------------------------------------------------------------------
# Two controls (kept from `gb-x.py`, because a zero here would otherwise be unreadable) and
# EIGHTEEN discovery queries. Every entry names the first-pass query it is disjoint FROM, because
# "different" is a claim and the first artifact is right there to check it against.
#
# `-is:retweet` everywhere: a retweet carries its parent's text, so it would inflate the author
# count with people who pressed a button rather than described a use.
PROMPT_THREAD_BERMAN = (
    "2098441494478889314"  # 175 posts in the first artifact, truncated
)
PROMPT_THREAD_CREDITS = (
    "2098149586183270842"  # 60 posts in the first artifact, truncated
)

QUERIES: tuple = (
    {
        "label": "control-positive",
        "q": "grok -is:retweet",
        "max": 10,
        "role": "control-positive",
        "why": "broadest possible term; zero here means the search path is broken, not quiet",
    },
    {
        "label": "control-no-links",
        "q": '("grok bot" OR grokbot) -has:links -is:retweet',
        "max": 10,
        "role": "control-negative",
        "why": "posts X says carry no links; the shared extractor must harvest ZERO outbound urls",
    },
    # -- use LANGUAGE, not product ceremony ---------------------------------------------------
    {
        "label": "mine-possessive",
        "q": '("my grok bot" OR "my grokbot" OR "our grok bot") -is:retweet',
        "max": MAX_RESULTS,
        "role": "use-language",
        "why": "the single highest-precision use signal in the corpus; never queried in pass 1",
    },
    {
        "label": "first-person-verb",
        "q": (
            '("grok bot" OR grokbot) ("i use" OR "i built" OR "i set up" OR "im using" '
            'OR "i have been using") -is:retweet'
        ),
        "max": MAX_RESULTS,
        "role": "use-language",
        "why": "operator-claims an operator actually types; pass 1 asked about the product only",
    },
    {
        "label": "use-case-phrase",
        "q": (
            '("grok bot use case" OR "grokbot use case" OR "use case for grok bot" '
            'OR "favorite grok bot") -is:retweet'
        ),
        "max": MAX_RESULTS,
        "role": "use-language",
        "why": 'pass 1 queried `"use case"` beside `template`; the bare phrase is a wider slice',
    },
    {
        "label": "cadence",
        "q": (
            '("grok bot" OR grokbot) ("every morning" OR "every day" OR overnight '
            'OR "while i sleep" OR "each week") -is:retweet'
        ),
        "max": MAX_RESULTS,
        "role": "use-language",
        "why": "unattended cadence: how a routine is described when the word routine is not used",
    },
    {
        "label": "built-setup",
        "q": (
            '("grok bot" OR grokbot) ("built a" OR "built my" OR "spun up" OR "set up my" '
            'OR "wired up") -is:retweet'
        ),
        "max": MAX_RESULTS,
        "role": "use-language",
        "why": "construction verbs; disjoint from pass 1's `skill`/`template` noun queries",
    },
    {
        "label": "workflow-stack",
        "q": '("grok bot" OR grokbot) (workflow OR workflows OR "my stack" OR pipeline) -is:retweet',
        "max": MAX_RESULTS,
        "role": "use-language",
        "why": "the word practitioners use for a multi-step use; absent from the pass-1 matrix",
    },
    {
        "label": "agents-manage",
        "q": (
            '("grok bot" OR grokbot) (agents OR subagents) (manage OR manages OR managing '
            "OR orchestrate OR orchestrating OR delegates) -is:retweet"
        ),
        "max": MAX_RESULTS,
        "role": "use-language",
        "why": "bot-manages-bots, the pattern behind the largest deployments in the corpus",
    },
    {
        "label": "role-hire",
        "q": (
            '("grok bot" OR grokbot) ("chief of staff" OR "my assistant" OR hired '
            'OR "my analyst" OR "my researcher") -is:retweet'
        ),
        "max": MAX_RESULTS,
        "role": "use-language",
        "why": "uses are named as JOB TITLES far more often than as tasks; a distinct vocabulary",
    },
    # -- connectors: the demand axis gb-demand.py ranks --------------------------------------
    {
        "label": "conn-google",
        "q": '("grok bot" OR grokbot) (gmail OR "google calendar" OR sheets OR inbox) -is:retweet',
        "max": MAX_RESULTS,
        "role": "connector",
        "why": "Gmail is the corpus's most-cited connector; pass 1 named only x.ai/github/cursor",
    },
    {
        "label": "conn-work",
        "q": '("grok bot" OR grokbot) (slack OR notion OR linear OR asana OR airtable) -is:retweet',
        "max": MAX_RESULTS,
        "role": "connector",
        "why": "the workplace connector set, each one a named integration in the Bot corpus",
    },
    {
        "label": "conn-dev",
        "q": (
            '("grok bot" OR grokbot) (github OR vercel OR supabase OR railway '
            'OR "pull request") -is:retweet'
        ),
        "max": MAX_RESULTS,
        "role": "connector",
        "why": "pass 1's `github.com` query returned 68 with has_more FALSE — this widens it",
    },
    {
        "label": "conn-gtm",
        "q": (
            '("grok bot" OR grokbot) (salesforce OR hubspot OR gong OR clay OR granola) '
            "-is:retweet"
        ),
        "max": MAX_RESULTS,
        "role": "connector",
        "why": "the five GTM names the vendor announced; 26/52 corpus integrations are dominated",
    },
    {
        "label": "conn-comm",
        "q": (
            '("grok bot" OR grokbot) (whatsapp OR telegram OR discord OR twilio OR calendly) '
            "-is:retweet"
        ),
        "max": MAX_RESULTS,
        "role": "connector",
        "why": "messaging/booking surfaces: where a Bot touches a human who is not its operator",
    },
    # -- prompt threads: the densest use-case surface measured ---------------------------------
    {
        "label": "thread-berman",
        "q": "conversation_id:%s -is:retweet" % PROMPT_THREAD_BERMAN,
        "max": MAX_RESULTS,
        "role": "prompt-thread",
        "why": "175 posts of this thread arrived as spill in pass 1 and it was truncated",
    },
    {
        "label": "thread-credits",
        "q": "conversation_id:%s -is:retweet" % PROMPT_THREAD_CREDITS,
        "max": MAX_RESULTS,
        "role": "prompt-thread",
        "why": "60 posts in pass 1, truncated; a credits giveaway draws stated intended uses",
    },
    {
        "label": "to-berman",
        "q": 'to:MatthewBerman ("grok bot" OR grokbot) -is:retweet',
        "max": MAX_RESULTS,
        "role": "prompt-thread",
        "why": "the same host's OTHER threads: `to:` reaches conversations we have no id for",
    },
    {
        "label": "to-vendor",
        "q": '(to:bot OR to:SpaceXAI) ("grok bot" OR grokbot OR routine) -is:retweet',
        "max": MAX_RESULTS,
        "role": "prompt-thread",
        "why": "replies TO the product account; pass 1's @-mention query capped with has_more",
    },
    # -- the announcement currently drawing uses out of lurkers -------------------------------
    {
        "label": "galaxy",
        "q": (
            '("grok bot" OR grokbot) (galaxy OR livestream OR "sept 15" OR "september 15" '
            'OR "sep 15") -is:retweet'
        ),
        "max": MAX_RESULTS,
        "role": "announcement",
        "why": "Galaxy is Sep 15-17, announced this week; the replies are `here is how I use it`",
    },
)

# Designs that lost the budget contest. Kept executable-shaped and in the artifact so the next
# run argues with a recorded reason instead of re-deriving the same twenty ideas.
DEFERRED: tuple = (
    {
        "label": "non-english",
        "q": '("grok bot" OR grokbot) lang:zh -is:retweet',
        "why_deferred": (
            "the classifier is English-only, so this would add POSTS and no USES; it becomes "
            "the top candidate the moment a non-English pattern set exists"
        ),
    },
    {
        "label": "quote-of-vendor",
        "q": "url:x.ai/bot -is:retweet",
        "why_deferred": "`url:` matches the link, and pass 1's link harvest already owns that axis",
    },
    {
        "label": "thread-tail-partition",
        "q": "conversation_id:%s has:media -is:retweet" % PROMPT_THREAD_BERMAN,
        "why_deferred": (
            "a second partition of a thread already sampled twice this run; worth a slot only "
            "if `thread-berman` comes back at the 100 ceiling with has_more"
        ),
    },
    {
        "label": "competitor-contrast",
        "q": '("grok bot" OR grokbot) (openclaw OR hermes OR "claude code") -is:retweet',
        "why_deferred": (
            "yields comparison discourse, which is opinion about products rather than a use; "
            "measured incidentally in pass 1 and it read as noise"
        ),
    },
)


# -------------------------------------------------------------------------------------------
# the classifier — patterns, not a model, so every verdict is inspectable
# -------------------------------------------------------------------------------------------
CLASSIFIER_VERSION = "use/4"
# Versioned SEPARATELY from the accept path, and deliberately: the attribution rule added below
# flags a use, it never rejects one, so `use/4`'s audited precision and recall were measured on
# exactly the accept path that still ships. Folding the two into one string would imply those
# numbers were re-measured when only this orthogonal rule was.
ATTRIBUTION_VERSION = "attrib/1"

PROD = r"grok[\s\-]?bots?|grokbots?"

# Task verbs. Deliberately long: a use is "the bot DOES something", and the something is stated
# with an ordinary verb. Assembled from reading the first-pass corpus, not from a thesaurus.
ACT = (
    r"us(?:e|es|ed|ing)|built|build(?:s|ing)?|set[\s\-]?up|setup|spun[\s\-]?up|deploy(?:ed|s|ing)?|"
    r"run(?:s|ning)?|automat(?:e|es|ed|ing)|manag(?:e|es|ed|ing)|draft(?:s|ed|ing)?|"
    r"monitor(?:s|ed|ing)?|check(?:s|ed|ing)?|watch(?:es|ed|ing)?|schedul(?:e|es|ed|ing)|"
    r"handl(?:e|es|ed|ing)|track(?:s|ed|ing)?|summar(?:ise|ize|ises|izes|ised|ized|ising|izing)|"
    r"review(?:s|ed|ing)?|research(?:es|ed|ing)?|send(?:s|ing)?|writ(?:e|es|ing)|wrote|"
    r"generat(?:e|es|ed|ing)|audit(?:s|ed|ing)?|find(?:s|ing)?|flag(?:s|ged|ging)?|"
    r"organi[sz](?:e|es|ed|ing)|plan(?:s|ned|ning)?|analy[sz](?:e|es|ed|ing)|scrap(?:e|es|ing)|"
    r"sync(?:s|ed|ing)?|triag(?:e|es|ed|ing)|ship(?:s|ped|ping)?|answer(?:s|ed|ing)?|"
    r"repl(?:y|ies)|post(?:s|ed|ing)?|sort(?:s|ed|ing)?|pull(?:s|ed|ing)?|fetch(?:es|ed|ing)?|"
    r"compil(?:e|es|ed|ing)|wir(?:e|es|ed|ing)|connect(?:s|ed|ing)?|integrat(?:e|es|ed|ing)|"
    r"prep(?:s|ped|ping)?|remind(?:s)?|coordinat(?:e|es|ed|ing)|do|does|doing|help(?:s|ed|ing)?|"
    r"cold[\s\-]?call(?:s|ing)?|outreach|bookkeep(?:s|ing)?|invoic(?:e|es|ing)|scan(?:s|ned|ning)?|"
    r"understand(?:s|ing)?|learn(?:s|ing)?|suggest(?:s|ing)?|ingest(?:s|ing)?|"
    r"simplif(?:y|ies|ying)|patrol(?:s|ling)?|mine|min(?:es|ing)|spot(?:s|ting)?|"
    r"catch(?:es|ing)?|updat(?:e|es|ing)|book(?:s|ing)?|negotiat(?:e|es|ing)|"
    r"catalog(?:s|ue|ues|uing)?|log(?:s|ging)?|price|pric(?:es|ing)|quote(?:s)?|"
    r"call(?:s|ing)?|email(?:s|ing)?|text(?:s|ing)?|translat(?:e|es|ing)|edit(?:s|ing)?|"
    r"curat(?:e|es|ing)|filter(?:s|ing)?|rank(?:s|ing)?|score(?:s)?|scor(?:es|ing)|"
    r"teach(?:es|ing)?|tutor(?:s|ing)?|trade(?:s)?|trad(?:es|ing)|invest(?:s|ing)?|"
    r"forecast(?:s|ing)?|alert(?:s|ing)?|notif(?:y|ies|ying)|escalat(?:e|es|ing)|"
    r"assist(?:s|ing)?|support(?:s|ing)?|serve(?:s)?|act(?:s|ing)?"
)

# The verbs that put a Bot in an operator's hands. `have`/`had` are in here deliberately AND are
# defused by `DET` below: `I have a grok bot` is a use claim, `I have to admit that Grok Bot ...`
# is an opinion, and only the determiner rule separates them.
I_VERB = (
    r"us(?:e|ed|ing)|built|build|set\s?up|setup|run(?:ning)?|wired|made|make|got|have|had|"
    r"deployed|spun\s?up|created|configured|added|put|gave|told|asked|turn(?:ed)?|point(?:ed)?|"
    r"train(?:ed)?|hooked|connect(?:ed)?|let|launch(?:ed)?|start(?:ed)?|spin"
)
I_LEAD = (
    r"i(?:'ve|'m|'d|'ll|\s+have|\s+am|\s+just|\s+would|\s+will|\s+plan\s+to|\s+want\s+to"
    r"|\s+wanna|\s+hope\s+to|\s+intend\s+to|\s+need\s+to|\s+plan\s+on|\s+plan)?"
)
# The product must be the verb's DIRECT OBJECT, with at most a determiner in between. This one
# rule killed three of the six false positives a proximity window produced: `I got my worst first
# try with the Grok bot`, `I have this week ... grok bot` and `I have to admit that Grok Bot`.
DET = r"(?:up\s+)?(?:a|an|my|our|the|this|that|another|\d+|some|new|his|her|their)?\s*"

PATTERNS = {
    "possessive": re.compile(
        r"\b(?:my|our)\s+(?:\w+\s+){0,2}(?:%s)\b[^.!?\u2026]{0,130}?\b(?:%s)\b"
        % (PROD, ACT),
        re.I,
    ),
    "possessive-pre": re.compile(
        r"\b(?:%s)\b[^.!?\u2026]{0,60}?\b(?:my|our)\s+(?:\w+\s+){0,2}(?:%s)\b"
        % (ACT, PROD),
        re.I,
    ),
    "possessive-role": re.compile(
        r"\b(?:my|our)\s+(?:\w+\s+){0,2}(?:%s)\b\s+(?:is|are|acts?\s+as|serves?\s+as|=)\s+"
        % PROD,
        re.I,
    ),
    "first-person": re.compile(
        r"\b%s\s+(?:been\s+)?(?:%s)\s+%s(?:%s)\b" % (I_LEAD, I_VERB, DET, PROD), re.I
    ),
    "bot-subject": re.compile(
        r"(?:%s)\b\s+(?:can|could|will|would|should|now|also|just|daily)?\s*(?:\w+\s+){0,2}?"
        r"\b(?:%s)\b" % (PROD, ACT),
        re.I,
    ),
    "bot-to-verb": re.compile(
        r"(?:%s)\b[^.!?\u2026]{0,90}?\bto\s+(?:%s)\b" % (PROD, ACT), re.I
    ),
    "bot-as-role": re.compile(
        r"(?:%s)\b\s+(?:as|for)\s+(?:my|our|a|an|the)\s+\w+" % PROD, re.I
    ),
    "bot-relative": re.compile(
        r"(?:%s)\b\s+(?:that|which|who)\s+(?:%s)\b" % (PROD, ACT), re.I
    ),
    "make-bot": re.compile(
        r"\b(?:make|turn|made|promot(?:e|ed))\s+(?:my\s+|a\s+|the\s+)?(?:%s)\s+"
        r"(?:my|our|into|a|the)\b" % PROD,
        re.I,
    ),
    "usecase-decl": re.compile(
        r"\b(?:favou?rite|best|top|my|planned|main|current)\s+(?:%s)\s+use[\s\-]?case"
        r"|(?:%s)\s+use[\s\-]?case\s*[:\-\u2014=]" % (PROD, PROD),
        re.I,
    ),
    "delegation": re.compile(
        r"\b(?:hav(?:e|ing)|had|got|let|make|made|tell|told|ask(?:ed)?)\s+"
        r"(?:it|him|her|them|the\s+bot|my\s+bot)\s+(?:%s)\b" % ACT,
        re.I,
    ),
    # Bound to a task at BOTH ends, which is what separates `using grok bot to build apps` from
    # `I've slept on Grok Bot for too long` — the latter was a false positive until this pattern
    # stopped accepting a bare preposition.
    "gerund-use": re.compile(
        r"(?:\b(?:%s)\b[^.!?\u2026]{0,50}?\b(?:using|via|with|on|through)\s+"
        r"(?:my\s+|a\s+|the\s+)?(?:%s)\b"
        r"|\b(?:using|via|with|on|through)\s+(?:my\s+|a\s+|the\s+)?(?:%s)\b"
        r"[^.!?\u2026]{0,80}?\b(?:%s)\b)" % (ACT, PROD, PROD, ACT),
        re.I,
    ),
}
# The patterns that carry an operator on their own. A post held up ONLY by a weak pattern is
# rejected when it also smells of news or promotion; a post with a strong pattern is not, because
# "I use my Grok Bot to draft our newsletter, link in bio" is still a use.
STRONG = frozenset(
    {
        "possessive",
        "possessive-pre",
        "possessive-role",
        "first-person",
        "make-bot",
        "usecase-decl",
        "delegation",
    }
)

OPERATOR = re.compile(r"\b(i|i'm|i've|im|ive|my|we|we're|we've|our|me|mine)\b", re.I)
INTENT = re.compile(
    r"\b(would|will|gonna|going\s+to|plan\s+(?:to|on|is)|plans\s+to|want(?:s)?\s+to|wanna|"
    r"hope\s+to|i'd|id\s+like|thinking\s+of|about\s+to|next\s+up|dream|wish)\b",
    re.I,
)
# UNAMBIGUOUS evidence that the use already exists: a first-person use verb in the present or
# past, or the bot itself as the subject of one. Checked BEFORE `INTENT`.
#
# A cadence phrase is deliberately NOT in here, and the selftest leg
# `classifier-tiers-an-intention-as-intended` is why: `I would use my Grok Bot to draft
# educational resources every week` contains `every week` and is still an intention. Cadence
# describes the SHAPE of a use, not its existence, so it sits in `HELD` below where `INTENT`
# gets first refusal.
DEPLOYED = re.compile(
    r"\b(?:i|we)\s+(?:use|used|run|ran|built|made|set\s?up|wired)\b|\bi'?ve\s+been\s+us|"
    r"\bi'?m\s+using\b|\b(?:it|he|she|they)\s+(?:runs?|checks?|does|sends?|drafts?|watches|"
    r"handles?|keeps?|monitors?|tracks?|writes?|posts?)\b|"
    r"\balready\b|\bso\s+far\b|"
    r"\bis\s+(?:running|handling|managing|drafting|watching|helping)\b",
    re.I,
)
# Weaker "it exists" evidence, applied only after INTENT has had its chance.
HELD = re.compile(
    r"\b(?:has\s+been|have\s+been|currently|daily|last\s+(?:week|night|month))\b|"
    r"\bevery\s+(?:morning|day|night|week|hour)\b|"
    r"\b(?:my|our)\s+(?:\w+\s+){0,2}(?:%s)\b" % PROD,
    re.I,
)
# Explicit absence of a use. `I don't have a favourite grok bot use case yet` is not a use case.
NEGATED = re.compile(
    r"\b(?:don'?t|do\s+not|doesn'?t|haven'?t|have\s+not|hasn'?t|didn'?t|never|not\s+yet|no)\s+"
    r"(?:\w+\s+){0,3}?(?:have|use|used|using|tried|try|implemented|favou?rite|access|idea|"
    r"clue|figured)\b",
    re.I,
)
NEWS = re.compile(
    r"\b(breaking|announc(?:ed|es|ing|ement)|just\s+launched|is\s+live|now\s+live|unveil(?:ed|s)?|"
    r"press\s+release|report(?:s|ed|edly)|according\s+to|kicked\s+off|will\s+livestream|"
    r"shared\s+a|explains?\s+how|here'?s\s+how\s+to|thread:|guide|101\s+is|tutorial)\b",
    re.I,
)
PROMO = re.compile(
    r"\b(we\s+offer|dm\s+(?:me|for|us)|link\s+in\s+bio|sign\s+up|waitlist|promo\s+code|discount|"
    r"\$\d+\s*/\s*mo|my\s+(?:product|tool|app|saas|startup|platform|service)|"
    r"our\s+(?:product|tool|app|saas|platform|service)|check\s+out\s+my|try\s+(?:my|our)|"
    r"install\s+(?:my|our)|available\s+now|free\s+trial|giveaway|retweet\s+to|"
    r"follow\s+(?:me|us)\s+(?:and|for)|we\s+can\s+(?:fix|build|set|do|help|run)|hire\s+us|"
    r"our\s+service|book\s+a\s+call|for\s+you\b|leftover\s+subscription)\b",
    re.I,
)
# A giveaway entry is a stated use, and it is a WEAKER use than one somebody is already running.
# Flagged, never dropped, because the flag is what lets a reader discount it.
SOLICITED = re.compile(
    r"\b(send\s+me\s+a?\s*code|pick\s+me|hope\s+(?:you|i)\s+(?:send|win|pick)|"
    r"need\s+(?:a\s+)?code|would\s+love\s+a\s+code|credits?\s+(?:would|please)|\$200\s+credit)\b",
    re.I,
)
# Somebody else is the operator. A report of another person's use is evidence about THEM, and
# counting it would let one viral claim arrive as fifty authors.
REPORTED = re.compile(
    r"^\s*(?:@\S+\s+)*(?:[A-Z]\w+\s+){0,3}(?:said|says|told|shared|posted|revealed|disclosed)\b"
)
# The product's own account answering support questions is not a user describing a use.
EXCLUDED_AUTHORS = frozenset({"grok"})

# -------------------------------------------------------------------------------------------
# AUTHORED vs RELAYED — the attribution rule this run paid for
# -------------------------------------------------------------------------------------------
# `REPORTED` above only catches a third-party lead that OPENS with a speech verb (`Sam said
# ...`). The live second pass found the other shape, and found it five times: an attribution
# with a COLON and no verb, followed by somebody else's first-person words in quotation marks.
#
#   sheemamoto  'SpaceXAI engineer, Lauren Tan: "... Right now I have 10-20 GrokBot agents ..."'
#   0xRafy      'SpaceXAI engineer: "... my Grok Bot agent built a product and hired 100+ ..."'
#   s4yonnara   'SpaceXAI engineer (ex-Cursor): "... I built a system that does the delegation"'
#   kaorixbt    'SpaceXAI engineer and ex-Cursor: "At SpaceXAI, 85% of our code is already ..."'
#   arle0x      'SpaceXAI engineer and ex-Cursor: "I don\'t prompt my ~20 GrokBot agents ..."'
#
# FIVE accounts, ONE upstream engineer, and the classifier's own first-person patterns firing on
# text INSIDE the quotation marks. Counted as authored, that is one claim arriving as five
# independent builders — the precise error the `DOMINATED_AT` flag exists to prevent and cannot
# see, because the five handles really are five distinct handles.
#
# The rule is mechanical, not a judgement: classify the post a SECOND time with every quoted
# span removed. If the use evidence does not survive without somebody else's words, the use
# belongs to the person being quoted. Measured on this run's 400 use rows: 37 carry a quote, 6
# lose their verdict when the quotes go, and all 6 are relays on inspection - no false relay.
# The 31 that survive include `drsarah`'s bot speaking (`He says the hard thing "this plan is a
# waste of time"`) and `Scratcherrrr`'s own rule (`one stupidly simple rule: "never let the
# screen go stale"`), which are quotes INSIDE an authored use and must stay authored.
DQUOTE = re.compile(r"[\"\u201c\u201d]")


def strip_quoted(text: Optional[str]) -> str:
    """`text` with every double-quoted span removed, quote characters included.

    A toggle rather than a regex pair: an unbalanced closing quote is common in a 280-character
    post, and a regex requiring both delimiters would leave the most-truncated relays intact.
    An unterminated quote therefore swallows the remainder of the post, which is the correct
    reading - everything after `engineer: "` is the engineer talking.
    """
    out: list = []
    inside = False
    for ch in text or "":
        if DQUOTE.match(ch):
            inside = not inside
            continue
        if not inside:
            out.append(ch)
    return "".join(out)


# The relay that carries NO quotation marks: an explicit frame naming whose account of events
# this is. `lennysan`'s `My biggest takeaways from Grok @Bot product lead @RomanUgarte_: 1. ...`
# is the measured instance - a report of a product lead's talk.
#
# THE OBJECT MUST BE A PERSON, and that is not a refinement, it is the whole rule. A first draft
# read `(takeaways|notes|summary|recap) (from|of)` followed by `@|the|a|an|[A-Z]`, and running it
# over both artifacts produced SEVEN false relays, because `summary`/`notes` are what a Bot
# PRODUCES and the objects are things:
#
#   @xueyu1125     "Bot C ... generate a summary OF THE latest AI news and emails it to my Gmail"
#   @AnkitR45220   "giving me one short summary OF WHAT actually matters"  (x3 near-duplicates)
#   @AdamZmenak    "I'd send it commands and notes FROM THE ring"
#   @_austinsheehy "hospital bag notes FROM THE classes we've taken"
#   @jorgediazapps "create voice notes FROM THE mobile app"
#
# (`[A-Z]` was also inert under `re.I`, so it matched any letter - the same defect twice.) Every
# one of those is an authored use, and four are among the best uses in the corpus. So the object
# is now required to be an @handle or an explicit role noun, `PERSON` below, and nothing matches
# on the strength of a frame word alone.
PERSON = (
    r"(?:@\w+|(?:co-?)?founder|engineer|ceo|cto|coo|vp|product\s+lead|exec(?:utive)?|"
)
PERSON += r"employee|staffer|researcher|pm|head\s+of\s+\w+|author|host|guest|speaker)"
RELAY_FRAME = re.compile(
    # `takeaways from ... @somebody` / `recap of the product lead`
    r"\b(?:takeaways?|recap|notes|highlights|summary|writeup|write-up)\s+(?:from|of)\b"
    r"[^.!?\u2026]{0,40}?" + PERSON + r"|"
    r"\baccording\s+to\b[^.!?\u2026]{0,30}?" + PERSON + r"|"
    # Unambiguous credit markers: these exist only to attribute, so they need no object test.
    r"\bh/t\s+@\w+|\bhat\s+tip\s+@\w+",
    re.I,
)
# A role/title attribution lead: `SpaceXAI engineer (ex-Cursor):`. Only counts when no
# first-person pronoun appears BEFORE the colon, which is what separates it from an authored
# `My Grok Bot handles founder ops:` (`_danielakande`) and from `a GrokBot build I'm working on
# with a fintech CEO right now:` (`coreyganim`) - both authored, both measured in this run.
#
# THE LEAD IS CAPTURED AND TESTED IN CODE, because the first version put the first-person guard
# in an unbounded lookahead - `(?!.*\b(?:i|my|we|our)\b)` - which scanned the WHOLE post. Since
# `classify_use` REQUIRES an operator pronoun somewhere to accept a use at all, that lookahead
# could never be satisfied by any post this rule is asked about: the leg was structurally dead,
# exactly like the unsubstituted `%s` that killed `bot-as-role`. An unfired rule is not a safe
# rule, it is an untested one, so the window is now explicit and `relay-role-lead-fires-on-an-
# unquoted-attribution` holds it live.
RELAY_ROLE_LEAD = re.compile(
    r"^\s*(?:@\S+\s+)*(?P<lead>[^:]{0,80}?\b(?:engineer|founder|co-?founder|ceo|cto|coo|"
    r"product\s+lead|lead|exec|executive|employee|staffer|researcher|pm|vp)\b[^:]{0,40}?):",
    re.I,
)
FIRST_PERSON = re.compile(r"\b(?:i|i'm|im|i've|ive|my|we|we're|we've|our)\b", re.I)


def relay_verdict(
    text: Optional[str], author: Optional[str], classified: bool
) -> tuple:
    """`(relay, reason)` for a post ALREADY classified as a use.

    `classified` is the caller's verdict on the full text, so this never re-runs the accept path
    on a post that was rejected anyway. Quote-strip is checked first because it is the
    evidence-based leg: it does not ask what the post looks like, it asks whether the use claim
    survives the removal of somebody else's words.
    """
    t = normalise(text)
    if not classified:
        return False, ""
    if DQUOTE.search(t) and classify_use(strip_quoted(t), author, _relay=False) is None:
        return True, "use-claim-only-inside-quoted-span"
    lead = RELAY_ROLE_LEAD.search(t)
    if lead and not FIRST_PERSON.search(lead.group("lead")):
        return True, "third-party-role-attribution-lead"
    if RELAY_FRAME.search(t):
        return True, "reported-speech-frame"
    return False, ""


def normalise(text: Optional[str]) -> str:
    return " ".join((text or "").split())


def classify_use(
    text: Optional[str], author: Optional[str] = None, *, _relay: bool = True
) -> Optional[dict]:
    """The verdict for one post, or None when it does not describe a use.

    Order matters and each gate is here because a measured false positive walked through the
    previous version: anchor, operator, explicit negation, then pattern, then the news/promo
    rejects that only apply to posts held up by a weak pattern.

    `_relay=False` is the re-entrant call `relay_verdict` makes on the quote-stripped text. It
    suppresses ONLY the attribution step, so the recursion is exactly one level deep and the
    accept path it probes is the same one every caller gets.
    """
    t = normalise(text)
    if author and author.lower() in EXCLUDED_AUTHORS:
        return None
    if not re.search(PROD, t, re.I):
        return None
    if not OPERATOR.search(t):
        return None
    if NEGATED.search(t):
        return None
    pats = sorted(name for name, rx in PATTERNS.items() if rx.search(t))
    if not pats:
        return None
    # A role/declaration with no verb anywhere is a label, not a use.
    if set(pats) <= {"bot-as-role", "usecase-decl"} and not re.search(ACT, t, re.I):
        return None
    if REPORTED.search(t):
        return None
    strong = [p for p in pats if p in STRONG]
    if not strong and (PROMO.search(t) or NEWS.search(t)):
        return None
    if DEPLOYED.search(t):
        tier = "deployed"
    elif INTENT.search(t):
        tier = "intended"
    elif HELD.search(t):
        tier = "deployed"
    else:
        tier = "unclear"
    relay, why = relay_verdict(t, author, True) if _relay else (False, "")
    return {
        "is_use": True,
        "tier": tier,
        "patterns": pats,
        "strong": bool(strong),
        "solicited": bool(SOLICITED.search(t)),
        # AUTHORED vs RELAYED. Flagged, never dropped: a relay is real evidence that the claim
        # is circulating, and it is NOT evidence of an independent operator. Both readings stay
        # available because the flag is what lets a reader pick one.
        "relay": relay,
        "relay_reason": why,
        "attribution": ATTRIBUTION_VERSION,
        "categories": categorise(t),
        "classifier": CLASSIFIER_VERSION,
    }


# -------------------------------------------------------------------------------------------
# the taxonomy — the RANKED UNIT, because a post has exactly one author
# -------------------------------------------------------------------------------------------
# "Rank uses by distinct authors" requires a unit that many authors can share. A post cannot be
# that unit (one post, one author, every rank tied at 1), so the unit is a use CATEGORY and the
# taxonomy below is what maps a post onto it. Keyword sets, drawn from the first-pass corpus; a
# post can land in several categories or in none, and `uncategorised` is reported rather than
# hidden, because a category list that silently absorbs everything is unfalsifiable.
TAXONOMY: tuple = (
    ("inbox-and-email", r"inbox|email|e-mail|gmail|unread|newsletter\s+triage|mail"),
    (
        "calendar-and-scheduling",
        r"calendar|schedul|appointment|booking|meeting|agenda|reminder",
    ),
    (
        "research-and-monitoring",
        r"research|monitor|watch(?:es|ing)?\s+for|track(?:s|ing)?\s+"
        r"(?:news|papers|prices)|digest|brief|papers|arxiv|competitor|alerts?",
    ),
    (
        "trading-and-crypto",
        r"trad(?:e|es|ing)|crypto|wallet|portfolio|stocks?|ticker|solana|"
        r"\bsol\b|btc|eth|market\s+(?:scan|maker)|prediction\s+market",
    ),
    (
        "content-and-social",
        r"content|post(?:s|ing)?\s+(?:to|on)|tweet|thread|youtube|script|"
        r"blog|video|caption|reels?|shorts?|podcast",
    ),
    (
        "sales-and-crm",
        r"\bcrm\b|salesforce|hubspot|gong|clay\b|lead|prospect|outreach|"
        r"cold\s+(?:call|email)|pipeline|\bsdr\b|quota|deal",
    ),
    (
        "coding-and-repos",
        r"github|repo|pull\s+request|\bpr\b|codebase|refactor|bug|deploy|"
        r"\bci\b|test\s+suite|docs\s+for|vercel|supabase|railway|cli\b",
    ),
    (
        "customer-support",
        r"support\s+(?:ticket|queue|inbox)|helpdesk|zendesk|intercom|"
        r"customer\s+(?:service|question)|first\s+reply|escalat",
    ),
    (
        "finance-and-bookkeeping",
        r"bookkeep|invoice|expense|accounting|quickbooks|payroll|"
        r"budget|receipt|finances|debt|tax",
    ),
    (
        "ops-and-logistics",
        r"warehouse|inventory|supply\s+chain|logistics|shipment|truck|"
        r"dispatch|shift|roster|field|fleet",
    ),
    (
        "personal-and-home",
        r"personal\s+assistant|life\s+admin|chores|grocery|household|family|"
        r"kids|home|errand|meal|recipe",
    ),
    (
        "health-and-medical",
        r"patient|medical|health|clinic|symptom|fitness|workout|nutrition|"
        r"therapy|doctor",
    ),
    (
        "legal-and-compliance",
        r"contract|legal|compliance|policy\s+review|\bnda\b|regulat|"
        r"lawyer|clause|filing",
    ),
    (
        "hiring-and-jobs",
        r"hiring|recruit|resume|cv\b|candidate|job\s+(?:board|search|post)|"
        r"applicant|interview",
    ),
    (
        "marketing-and-seo",
        r"\bseo\b|serp|keyword|ad\s+(?:copy|spend)|ads\b|campaign|brand|"
        r"marketing|landing\s+page|copywriting",
    ),
    (
        "data-and-reporting",
        r"dashboard|report(?:ing)?|spreadsheet|sheets|analytics|\bsql\b|"
        r"database|metrics|\bkpi\b|chart",
    ),
    (
        "education-and-learning",
        r"homework|student|school|course|teach|tutor|lesson|study|"
        r"learn(?:ing)?\s+plan|exam",
    ),
    (
        "travel-and-local",
        r"travel|trip|flight|hotel|itinerary|vacation|route|tide|weather|"
        r"restaurant|local\s+events?",
    ),
    (
        "agent-orchestration",
        r"sub-?agents?|orchestrat|delegat|multi-?agent|agent\s+mesh|"
        r"bot\s+team|swarm|manager\s+bot|chief\s+of\s+staff|coordinator",
    ),
    (
        "approval-and-governance",
        r"approv|human\s+in\s+the\s+loop|guardrail|gate\b|sign-?off|"
        r"before\s+(?:it\s+)?sends?|review\s+before|permission|audit\s+trail",
    ),
)
TAXONOMY_RX: tuple = tuple((name, re.compile(rx, re.I)) for name, rx in TAXONOMY)


def categorise(text: Optional[str]) -> list:
    """Every use category this post's text supports. Empty is a legitimate answer."""
    t = normalise(text)
    return [name for name, rx in TAXONOMY_RX if rx.search(t)]


# -------------------------------------------------------------------------------------------
# ranking — distinct authors, with the dominance flag gb-demand.py paid for
# -------------------------------------------------------------------------------------------
def rank_uses(use_rows: "list") -> list:
    """Use categories ranked by DISTINCT AUTHORS.

    The sort key is `(-distinct_authors, -posts, category)`. Engagement is carried and is NOT in
    the key: `gb-demand.py:60-64` measured that 26 of 52 integrations with 3+ citations have one
    account holding half of them, so a count of MENTIONS ranks one loud account above a real
    market. `top_author_share >= DOMINATED_AT` is the flag that makes such a row visible instead
    of merely wrong.

    A post with no author (X omitted the `includes.users` expansion) still counts as a POST and
    contributes NOTHING to `distinct_authors` — an unknown author must not be silently fused with
    every other unknown into one phantom builder, nor invented as a new one.

    `authored_authors` is the number to cite when INDEPENDENCE is the claim: it excludes authors
    whose only post in the category relays somebody else's use. `distinct_authors` stays the sort
    key and the headline, because a relay is still a distinct account describing a use — but the
    two numbers must be separable, and `examples` is authored-only so a relayed post can never be
    quoted as a primary source.
    """
    per: dict = collections.defaultdict(
        lambda: {
            "posts": 0,
            "authors": set(),
            "authored_authors": set(),
            "by_author": collections.Counter(),
            "anonymous_posts": 0,
            "relayed_posts": 0,
            "tiers": collections.Counter(),
            "solicited": 0,
            "likes": 0,
            "reposts": 0,
            "examples": [],
            "relayed_examples": [],
        }
    )
    for row in use_rows:
        sig = row["signals"]
        use = sig["use"]
        author = sig.get("author")
        relay = bool(use.get("relay"))
        for cat in use["categories"] or ["uncategorised"]:
            bucket = per[cat]
            bucket["posts"] += 1
            if author:
                bucket["authors"].add(author)
                bucket["by_author"][author] += 1
                if not relay:
                    bucket["authored_authors"].add(author)
            else:
                bucket["anonymous_posts"] += 1
            bucket["relayed_posts"] += 1 if relay else 0
            bucket["tiers"][use["tier"]] += 1
            bucket["solicited"] += 1 if use["solicited"] else 0
            bucket["likes"] += int(sig.get("likes") or 0)
            bucket["reposts"] += int(sig.get("reposts") or 0)
            # Two separate lists, not one list with a flag: a reader reaching for an example
            # wants a primary source, and the cheapest way to guarantee that is for the field
            # named `examples` to contain nothing else.
            into = "relayed_examples" if relay else "examples"
            if len(bucket[into]) < 3:
                bucket[into].append(
                    {
                        "id": row["id"],
                        "author": author,
                        "url": row["url"],
                        "relay_reason": use.get("relay_reason") or "",
                    }
                )

    out = []
    for cat, b in per.items():
        top_author, top_n = (b["by_author"].most_common(1) or [(None, 0)])[0]
        share = (top_n / b["posts"]) if b["posts"] else 0.0
        out.append(
            {
                "category": cat,
                "posts": b["posts"],
                "distinct_authors": len(b["authors"]),
                "authored_authors": len(b["authored_authors"]),
                "relayed_posts": b["relayed_posts"],
                "anonymous_posts": b["anonymous_posts"],
                "top_author": top_author,
                "top_author_posts": top_n,
                "top_author_share": round(share, 3),
                "dominated": share >= DOMINATED_AT,
                "tiers": dict(sorted(b["tiers"].items())),
                "solicited_posts": b["solicited"],
                "engagement": {"likes": b["likes"], "reposts": b["reposts"]},
                "examples": b["examples"],
                "relayed_examples": b["relayed_examples"],
            }
        )
    out.sort(key=lambda r: (-r["distinct_authors"], -r["posts"], r["category"]))
    return out


# -------------------------------------------------------------------------------------------
# the first pass, as a set of ids and a set of classified uses
# -------------------------------------------------------------------------------------------
def first_pass_path(root: pathlib.Path) -> Optional[pathlib.Path]:
    """The newest `gb-x` artifact that is not itself a sweep. Sweeps are excluded by name so a
    second sweep compares against the DAILY pass rather than against yesterday's sweep."""
    x = root / "x"
    if not x.is_dir():
        return None
    rows = sorted(p for p in x.glob("*.json") if not p.name.endswith(".sweep.json"))
    return rows[-1] if rows else None


def read_first_pass(path: Optional[pathlib.Path]) -> dict:
    """`{artifact, readable, captured_at, posts, post_ids, uses, note}` for the pass we are
    trying to get past. An absent or unreadable artifact yields an EMPTY set and says so, because
    "nothing was new" and "we could not tell what was old" must not look alike.

    Each use row carries its `relay` flag, so a relayed first-pass post cannot re-enter the
    combined ranking as an authored one."""
    empty = {
        "artifact": str(path) if path else None,
        "readable": False,
        "captured_at": None,
        "posts": 0,
        "post_ids": set(),
        "uses": [],
        "note": "no first-pass artifact found" if path is None else "unreadable",
    }
    if path is None:
        return empty
    doc = read_json_capped(path)
    if not isinstance(doc, dict) or not isinstance(doc.get("rows"), list):
        return empty
    ids = set()
    uses = []
    posts = 0
    for row in doc["rows"]:
        if not isinstance(row, dict) or row.get("kind") != "x-post":
            continue
        posts += 1
        ids.add(row.get("id"))
        verdict = classify_use(
            row.get("summary"), (row.get("signals") or {}).get("author")
        )
        if verdict:
            uses.append(
                {
                    "id": row.get("id"),
                    # The REAL permalink, carried so a ranked example is clickable. The synthetic
                    # row shape used to fabricate `status/0` here, which in `reclassify` - where
                    # every row is synthetic - would have made every example a dead link.
                    "url": row.get("url"),
                    "author": (row.get("signals") or {}).get("author"),
                    "tier": verdict["tier"],
                    "categories": verdict["categories"],
                    "solicited": verdict["solicited"],
                    "relay": verdict["relay"],
                    "relay_reason": verdict["relay_reason"],
                }
            )
    return {
        "artifact": path.name,
        "readable": True,
        "captured_at": doc.get("captured_at"),
        "posts": posts,
        "post_ids": ids,
        "uses": uses,
        "note": "",
    }


# -------------------------------------------------------------------------------------------
# collect — pure, takes no root, CANNOT write
# -------------------------------------------------------------------------------------------
def collect(
    *,
    fetch: "Callable" = None,
    now: Optional[dt.datetime] = None,
    queries: tuple = QUERIES,
    max_calls: int = MAX_SEARCH_CALLS,
    sleep_s: float = MIN_SLEEP_S,
    sleep: Callable[[float], None] = time.sleep,
    first_pass: Optional[dict] = None,
) -> dict:
    """The whole second pass. Always returns a document; never raises on a dead source.

    `calls` increments BEFORE the request, so a fetch that hangs and is killed still consumed its
    slot — the counter is the binding guard and a guard that only counts successes is not one.
    """
    fetch = fetch or gbx.x_search
    now = (now or dt.datetime.now(dt.timezone.utc)).astimezone(dt.timezone.utc)
    prior = (
        first_pass
        if first_pass is not None
        else {"post_ids": set(), "uses": [], "readable": False}
    )
    prior_ids = prior.get("post_ids") or set()

    posts: dict = {}
    sources: list = []
    yields: list = []
    calls = 0
    rate_limited = False
    reset_at: Optional[str] = None
    control_positive: Optional[int] = None
    control_urls: Optional[int] = None
    urls_seen = urls_outbound = 0

    for spec in queries:
        label, q = spec["label"], spec["q"]
        if rate_limited or calls >= max_calls:
            sources.append(
                {
                    "id": label,
                    "query": q,
                    "role": spec["role"],
                    "why": spec["why"],
                    "status": None,
                    "items": 0,
                    "rows": 0,
                    "note": "skipped - %s"
                    % (
                        "run stopped after rate limit"
                        if rate_limited
                        else "local call cap reached"
                    ),
                }
            )
            continue
        if calls and sleep_s:
            sleep(sleep_s)
        calls += 1
        envelope, error, status = fetch(q, int(spec["max"]))
        row = {
            "id": label,
            "query": q,
            "role": spec["role"],
            "why": spec["why"],
            "status": status,
            "items": 0,
            "rows": 0,
            "requested_max": int(spec["max"]),
            "new_vs_first_pass": 0,
            "seen_in_first_pass": 0,
            "uses": 0,
            "uses_new": 0,
            "urls_found": 0,
            "urls_outbound": 0,
            "has_more": False,
            "note": "",
        }
        if error or envelope is None:
            row["note"] = error or "no envelope"
            if status == 429:
                rate_limited = True
                m = re.search(r"Resets at (\d+)", row["note"])
                if m:
                    reset_at = gbx.now_z(
                        dt.datetime.fromtimestamp(int(m.group(1)), dt.timezone.utc)
                    )
            sources.append(row)
            yields.append(query_yield(row))
            continue

        data = envelope.get("data") or []
        meta = envelope.get("meta") or {}
        by_id = gbx.handles(envelope)
        returned = meta.get("result_count")
        row["items"] = int(returned) if isinstance(returned, int) else len(data)
        row["has_more"] = bool(meta.get("next_token"))
        if not isinstance(data, list):
            row["note"] = "unexpected data shape: %s" % type(data).__name__
            sources.append(row)
            yields.append(query_yield(row))
            continue
        if row["items"] == 0 and (
            "from:" in q or "to:" in q or "conversation_id:" in q
        ):
            # A zero from an ADDRESSED query is not an absent account or an absent thread. X's
            # recent-search window is ~7 days: a real handle that went quiet and a real thread
            # that aged out both answer zero, and so does a typo. The note carries the read that
            # makes the distinction so the next reader does not re-derive it.
            row["note"] = (
                "zero rows - NOT evidence of absence. X's recent-search window is ~7 days, so a "
                "quiet account and an aged-out thread both answer zero. Disambiguate an account "
                "with `x-cli-infisical -j user get <handle>`; a conversation older than the "
                "window is unreachable from recent search at any budget."
            )

        for post in data:
            if not isinstance(post, dict):
                continue
            candidate = gbx.post_row(
                post, by_id.get(str(post.get("author_id") or "")), now
            )
            if candidate is None:
                continue
            link_meta = candidate.pop("_link_meta")
            pid = candidate["id"]
            if pid in posts:
                existing = posts[pid]
                if candidate["signals"]["author"] and not existing["signals"]["author"]:
                    existing["signals"]["author"] = candidate["signals"]["author"]
                    refresh_use(existing)
            else:
                sig = candidate["signals"]
                sig["seen_in_first_pass"] = pid in prior_ids
                sig["sweep_only"] = pid not in prior_ids
                refresh_use(candidate)
                posts[pid] = candidate
                row["rows"] += 1
                if sig["seen_in_first_pass"]:
                    row["seen_in_first_pass"] += 1
                else:
                    row["new_vs_first_pass"] += 1
                if sig["use"]["is_use"]:
                    row["uses"] += 1
                    if sig["sweep_only"]:
                        row["uses_new"] += 1
            gbx.note_query(posts[pid], label)

            for canon in candidate["signals"]["urls"]:
                kind = (link_meta.get(canon) or {}).get("link_kind") or "web"
                urls_seen += 1
                row["urls_found"] += 1
                if kind != "x-status":
                    urls_outbound += 1
                    row["urls_outbound"] += 1

        if spec["role"] == "control-positive":
            control_positive = row["items"]
        elif spec["role"] == "control-negative":
            # OUTBOUND only, for the reason gb-x.py:1026-1038 recorded: X's `has:links` does not
            # count a quote-tweet's structural backlink, and counting it made a healthy run read
            # UNRELIABLE. The predicate is aligned with the vendor semantics it controls against.
            control_urls = row["urls_outbound"]
        sources.append(row)
        yields.append(query_yield(row))

    rows = sorted(
        posts.values(), key=lambda r: (r["published_at"] or "", r["id"]), reverse=True
    )
    use_rows = [r for r in rows if r["signals"]["use"]["is_use"]]
    new_rows = [r for r in rows if r["signals"]["sweep_only"]]
    new_use_rows = [r for r in use_rows if r["signals"]["sweep_only"]]

    prior_uses = prior.get("uses") or []
    prior_use_ids = {u["id"] for u in prior_uses}
    prior_use_authors = {u["author"] for u in prior_uses if u.get("author")}
    sweep_use_authors = {
        r["signals"]["author"] for r in use_rows if r["signals"]["author"]
    }
    # AUTHORED-only author sets, so the combined total can be read both ways. A handle counts as
    # authored if ANY of its use posts is authored: one relay does not disqualify an operator who
    # also described their own Bot.
    prior_authored = {
        u["author"] for u in prior_uses if u.get("author") and not u.get("relay")
    }
    sweep_authored = {
        r["signals"]["author"]
        for r in use_rows
        if r["signals"]["author"] and not r["signals"]["use"].get("relay")
    }
    relayed_use_rows = [r for r in use_rows if r["signals"]["use"].get("relay")]

    failed = [s for s in sources if s["status"] not in (200, None)]
    skipped = [s for s in sources if s["status"] is None]
    reliable = bool(control_positive) and control_urls == 0 and not failed

    sources.insert(
        0,
        {
            "id": "quota",
            "role": "quota-observation",
            "status": 429 if rate_limited else 200,
            "items": calls,
            "rows": 0,
            "search_calls": calls,
            "local_cap": max_calls,
            "min_sleep_s": sleep_s,
            "rate_limited": rate_limited,
            "reset_at": reset_at,
            "note": (
                "X exposes no rate-limit endpoint and x-cli surfaces no headers "
                "(x_cli/api.py:46 turns 429 into a RuntimeError), so the quota is invisible "
                "until it is hit. This local counter is the binding guard - no vendor number is "
                "trusted. %d/%d call(s) spent%s."
                % (calls, max_calls, "; RATE LIMITED" if rate_limited else "")
            ),
        },
    )

    return {
        "schema": SCHEMA,
        "captured_at": gbx.now_z(now),
        "volatile_fields": list(VOLATILE_FIELDS),
        "reliable": reliable,
        "pass": 2,
        "first_pass": {
            "artifact": prior.get("artifact"),
            "readable": bool(prior.get("readable")),
            "captured_at": prior.get("captured_at"),
            "posts": prior.get("posts", 0),
            "ids_loaded": len(prior_ids),
            "uses_detected": len(prior_uses),
            "use_authors": len(prior_use_authors),
            "use_authors_authored": len(prior_authored),
            "uses_relayed": sum(1 for u in prior_uses if u.get("relay")),
            "note": prior.get("note") or "",
        },
        "controls": {
            "positive_items": control_positive,
            "negative_urls": control_urls,
            "positive_passed": bool(control_positive),
            "negative_passed": control_urls == 0,
        },
        "classifier": CLASSIFIER_AUDIT,
        "counts": {
            "rows": len(rows),
            "posts": len(posts),
            "by_kind": {"x-post": len(posts)},
            "new_vs_first_pass": len(new_rows),
            "seen_in_first_pass": len(rows) - len(new_rows),
            "uses_detected": len(use_rows),
            "uses_new": len(new_use_rows),
            "use_authors": len(sweep_use_authors),
            "use_authors_authored": len(sweep_authored),
            # The five-accounts-one-engineer count. A number that stays at 0 across runs means
            # the attribution rule is asleep, not that nobody is relaying.
            "uses_relayed": len(relayed_use_rows),
            "uses_relayed_reasons": dict(
                sorted(
                    collections.Counter(
                        r["signals"]["use"]["relay_reason"] for r in relayed_use_rows
                    ).items()
                )
            ),
            "use_tiers": dict(
                sorted(
                    collections.Counter(
                        r["signals"]["use"]["tier"] for r in use_rows
                    ).items()
                )
            ),
            "uses_solicited": sum(
                1 for r in use_rows if r["signals"]["use"]["solicited"]
            ),
            "url_mentions": urls_seen,
            "url_mentions_outbound": urls_outbound,
            "search_calls": calls,
            "queries_failed": len(failed),
            "queries_skipped": len(skipped),
        },
        # The number the brief actually asked for, across BOTH passes, computed with ONE
        # classifier over both corpora so the two halves are commensurable.
        "combined": {
            "first_pass_uses": len(prior_uses),
            "sweep_uses": len(use_rows),
            "sweep_uses_not_in_first_pass": len(
                [r for r in use_rows if r["id"] not in prior_use_ids]
            ),
            "total_uses": len(prior_use_ids | {r["id"] for r in use_rows}),
            "total_use_authors": len(prior_use_authors | sweep_use_authors),
            "total_use_authors_authored": len(prior_authored | sweep_authored),
            "total_relayed": len(
                {u["id"] for u in prior_uses if u.get("relay")}
                | {r["id"] for r in relayed_use_rows}
            ),
            "first_pass_only_authors": len(prior_use_authors - sweep_use_authors),
            "sweep_only_authors": len(sweep_use_authors - prior_use_authors),
        },
        "uses_ranked": rank_uses(use_rows),
        "uses_ranked_combined": rank_uses(
            use_rows + synthetic_rows(prior_uses, use_rows)
        ),
        "query_yield": yields,
        "deferred_candidates": [dict(d) for d in DEFERRED],
        "sources": sources,
        "rows": rows,
    }


def refresh_use(row: dict) -> None:
    """(Re)compute the use verdict for a row in place. Called again when a later query supplies
    the author a previous one lacked, because `EXCLUDED_AUTHORS` cannot be applied to a handle we
    had not seen yet."""
    verdict = classify_use(row.get("summary"), row["signals"].get("author"))
    row["signals"]["use"] = verdict or {
        "is_use": False,
        "tier": None,
        "patterns": [],
        "strong": False,
        "solicited": False,
        "relay": False,
        "relay_reason": "",
        "attribution": ATTRIBUTION_VERSION,
        "categories": [],
        "classifier": CLASSIFIER_VERSION,
    }


def synthetic_rows(prior_uses: list, exclude_rows: list) -> list:
    """Use rows from an artifact, in the minimal row shape `rank_uses` needs, EXCLUDING any post
    already present in `exclude_rows`. Used for every combined ranking, so a post and an author
    are counted exactly once across two passes.

    Takes the rows to exclude rather than their ids: the caller always has the rows, and a
    separate id-set argument was carried unused through both call sites."""
    seen = {r["id"] for r in exclude_rows}
    out = []
    for u in prior_uses:
        if u["id"] in seen:
            continue
        out.append(
            {
                "id": u["id"],
                # The artifact's own permalink when it carried one; `status/0` only when an
                # older artifact predates the field, so a dead link is visibly a dead link
                # rather than a plausible wrong one.
                "url": u.get("url") or gbx.permalink("0"),
                "signals": {
                    "author": u.get("author"),
                    "likes": 0,
                    "reposts": 0,
                    "use": {
                        "is_use": True,
                        "tier": u["tier"],
                        "patterns": [],
                        "strong": False,
                        "solicited": u["solicited"],
                        # Carried, not recomputed: `read_first_pass` already classified this
                        # post's text, and the synthetic row has no text to re-read.
                        "relay": bool(u.get("relay")),
                        "relay_reason": u.get("relay_reason") or "",
                        "attribution": ATTRIBUTION_VERSION,
                        "categories": u["categories"],
                        "classifier": CLASSIFIER_VERSION,
                    },
                },
            }
        )
    return out


def reclassify(first: dict, sweep: dict) -> dict:
    """Re-derive the counts, the AUTHORED/RELAYED split and the combined ranking from artifacts
    already on disk. No network, no quota, no write.

    This exists because a classifier change is worthless if verifying it costs twenty search
    calls. `attrib/1` was written after a live run had already spent its budget, and re-reading
    the two artifacts is the only honest way to state what the new rule does to the numbers that
    run reported. Takes the dicts `read_first_pass` returns, not paths, so it is testable without
    touching a disk.
    """
    passes = []
    seen_ids: set = set()
    seen_authors: set = set()
    authored_authors: set = set()
    relayed_ids: set = set()
    rank_rows: list = []
    # Newest first: the sweep owns a post both artifacts hold, because its row is the one whose
    # text was classified most recently.
    for doc in (sweep, first):
        uses = doc.get("uses") or []
        fresh = [u for u in uses if u["id"] not in seen_ids]
        authors = {u["author"] for u in uses if u.get("author")}
        mine_authored = {
            u["author"] for u in uses if u.get("author") and not u.get("relay")
        }
        passes.append(
            {
                "artifact": doc.get("artifact"),
                "readable": bool(doc.get("readable")),
                "captured_at": doc.get("captured_at"),
                "posts": doc.get("posts", 0),
                "uses": len(uses),
                "uses_not_already_counted": len(fresh),
                "use_authors": len(authors),
                "use_authors_authored": len(mine_authored),
                "uses_relayed": sum(1 for u in uses if u.get("relay")),
                "note": doc.get("note") or "",
            }
        )
        rank_rows += synthetic_rows(fresh, rank_rows)
        seen_ids |= {u["id"] for u in uses}
        seen_authors |= authors
        authored_authors |= mine_authored
        relayed_ids |= {u["id"] for u in uses if u.get("relay")}
    return {
        "schema": SCHEMA,
        "mode": "reclassify",
        "classifier": CLASSIFIER_VERSION,
        "attribution": ATTRIBUTION_VERSION,
        "passes": passes,
        "combined": {
            "total_uses": len(seen_ids),
            "total_use_authors": len(seen_authors),
            "total_use_authors_authored": len(authored_authors),
            "total_relayed": len(relayed_ids),
            "tiers": dict(
                sorted(
                    collections.Counter(
                        r["signals"]["use"]["tier"] for r in rank_rows
                    ).items()
                )
            ),
        },
        "uses_ranked_combined": rank_uses(rank_rows),
    }


def query_yield(source_row: dict) -> dict:
    """One query's measured yield, with the KEEP/PRUNE verdict computed from it.

    The verdict is arithmetic, not taste: a query that found fewer than `PRUNE_MIN_NEW` posts
    nobody had seen, or zero use-describing posts, spent a slot out of twenty for nothing. It is
    recorded per run so the next matrix is chosen from evidence.
    """
    status = source_row.get("status")
    items = source_row.get("items", 0)
    new = source_row.get("new_vs_first_pass", 0)
    uses = source_row.get("uses", 0)
    if status is None:
        verdict, reason = "unrun", source_row.get("note", "")[:60]
    elif status != 200:
        verdict, reason = "failed", source_row.get("note", "")[:60]
    elif source_row.get("role") in ("control-positive", "control-negative"):
        verdict, reason = "control", "kept for reliability, not for yield"
    elif new < PRUNE_MIN_NEW:
        verdict, reason = "prune", "only %d post(s) new vs the first pass" % new
    elif uses < PRUNE_MIN_USES:
        verdict, reason = "prune", "%d new post(s) but no use-describing post" % new
    else:
        verdict, reason = "keep", "%d new post(s), %d use(s)" % (new, uses)
    return {
        "label": source_row["id"],
        "role": source_row.get("role"),
        "query": source_row.get("query"),
        "items": items,
        "new": new,
        "already_seen": source_row.get("seen_in_first_pass", 0),
        "uses": uses,
        "uses_new": source_row.get("uses_new", 0),
        "has_more": source_row.get("has_more", False),
        "verdict": verdict,
        "reason": reason,
    }


# -------------------------------------------------------------------------------------------
# the hand audit — the only honest source of a precision number
# -------------------------------------------------------------------------------------------
# A pattern classifier cannot measure its own precision, so these numbers came from READING
# rows — every sample drawn from the first-pass corpus (1,356 posts) against classifier `use/4`
# exactly as shipped. The handles are recorded so the audit can be repeated on the same rows
# rather than re-sampled and re-argued.
#
# The precision audit is in TWO PARTS and the second part is the interesting one. The prototype
# these numbers were first measured on had `bot-as-role` DEAD — an unsubstituted `%s` left the
# literal two characters in the pattern, so it matched nothing and the 30-row sample could not
# have covered it. Fixing the substitution added exactly three hits, so all three were read
# rather than resampled: a census, not an estimate. That is why `sampled` is 33 and not 30.
CLASSIFIER_AUDIT = {
    "version": CLASSIFIER_VERSION,
    "attribution_version": ATTRIBUTION_VERSION,
    "corpus": "x/2026-09-11T1753.json (1356 x-post rows)",
    "hits_on_that_corpus": 160,
    "precision": {
        "sampled": 33,
        "correct": 31,
        "value": 0.939,
        "method": (
            "30 rows drawn with random.seed(4242) from the classifier's hits and read one by "
            "one, PLUS a full 3-row census of the hits the repaired `bot-as-role` pattern adds "
            "(Vanessa89195261, Visionsphere_AT, vibecodejordan - 3/3 genuine uses)"
        ),
        "false_positives": [
            {
                "author": "leegmoore",
                "why": "commentary on plan sizing, no use described",
            },
            {
                "author": "ThomasColasanti",
                "why": "support request that references a workflow",
            },
        ],
    },
    "recall": {
        "sampled": 22,
        "missed_uses": 6,
        "value_estimate": 0.5,
        "method": (
            "random.seed(99) sample of the 524 product-mentioning posts the classifier REJECTS "
            "on that corpus (684 mention the product, 160 are accepted); 6 of 22 were really "
            "uses, so ~143 of that population are missed and the true count is ~2x the reported"
        ),
        "named_misses": [
            {
                "author": "gsirginray",
                "why": "Chinese: six bots producing a 14-page report",
            },
            {"author": "Minsi_AI", "why": "Chinese: 40-agent company, 50 routines"},
            {
                "author": "srt54558",
                "why": "second-person instructions, no first-person operator",
            },
            {
                "author": "liam_fallen",
                "why": "possessive with no verb: 'my Grok Bot Sales ... System'",
            },
            {
                "author": "westBfieldLa",
                "why": "'Been doing this with grok bot' - subjectless",
            },
            {
                "author": "babygreenrebel",
                "why": "bot referred to by its own name, not the product",
            },
        ],
    },
    # The attribution rule, audited on the two artifacts that motivated it. A CENSUS, not a
    # sample: every row either artifact classifies as a use was re-run through the shipped rule
    # and every flagged row was read.
    "attribution": {
        "corpus": (
            "x/2026-09-12T0047.sweep.json (1508 posts, 400 uses) + x/2026-09-11T1753.json "
            "(1356 posts, 160 uses); 487 unique uses after dedupe"
        ),
        "method": (
            "every use row in both artifacts re-classified through attrib/1 and every flagged "
            "row read individually: 7 flagged, 7 relays, 0 false. The quote-strip leg was "
            "separately censused on all 37 quote-bearing use rows of the sweep - 6 lost their "
            "verdict without the quotes and all 6 are relays, so the other 31 (including a "
            "bot quoted by its own operator) stay authored"
        ),
        "relays_found": 7,
        "false_relays": 0,
        "named_relays": [
            {
                "author": "kaorixbt",
                "relays": "SpaceXAI engineer (ex-Cursor), 85%-of-our-code claim",
            },
            {"author": "arle0x", "relays": "same engineer, ~20-agents claim"},
            {"author": "s4yonnara", "relays": "same engineer, 950K-leads claim"},
            {
                "author": "0xRafy",
                "relays": "a SpaceXAI engineer, hired-100+-agents claim",
            },
            {
                "author": "sheemamoto",
                "relays": "SpaceXAI engineer Lauren Tan, 10-20 agents",
            },
            {
                "author": "paulrodturner",
                "relays": "the genre: quotes other people's 'trades while I sleep' posts",
            },
            {"author": "lennysan", "relays": "Grok Bot product lead's talk"},
        ],
        "why_it_matters": (
            "FIVE of the seven relay one upstream SpaceXAI engineer. Counted as authored, that "
            "is one claim arriving as five independent builders, and `top_author_share` cannot "
            "see it because the five handles really are five distinct accounts"
        ),
        "rejected_first_draft": {
            "rule": "frame word + from/of, with no test on the object",
            "false_relays": 7,
            "why_rejected": (
                "`summary` and `notes` are what a Bot PRODUCES, so the object is a thing: "
                "xueyu1125 'a summary of the latest AI news', AnkitR45220 x3 'summary of what "
                "actually matters', AdamZmenak 'notes from the ring', _austinsheehy 'notes from "
                "the classes', jorgediazapps 'voice notes from the mobile app' - 7 authored "
                "uses called relays, 4 of them among the strongest in the corpus. `[A-Z]` in "
                "that draft was also inert under re.I, so the object test it did have matched "
                "any letter. Both shapes are now pinned as known-bad fixtures"
            ),
        },
        # Which leg actually found what, on that census. Recorded because a rule with three legs
        # and one shared assertion can ship two dead ones - `third-party-role-attribution-lead`
        # did exactly that, its first-person guard scanning the whole post when `classify_use`
        # guarantees a first-person pronoun is in it. It is reported as 0 rather than hidden: it
        # is a guard against the unquoted variant of a shape that occurred five times, held live
        # by its own fixture and its own selftest leg, not a detector with a measured yield.
        "legs": {
            "use-claim-only-inside-quoted-span": 6,
            "reported-speech-frame": 1,
            "third-party-role-attribution-lead": 0,
        },
        "blind_spot": (
            "a relay that neither quotes nor names its source is indistinguishable from an "
            "authored use by text alone; X's `referenced_tweets` expansion would settle those, "
            "and gb-x does not request it"
        ),
    },
    "rejected_alternative": {
        "version": "use/3",
        "hits": 183,
        "precision": 0.80,
        "why_rejected": (
            "26 more hits for 13 points of precision; a proximity window accepted 'I have to "
            "admit that Grok Bot is nice' and 'I've slept on Grok Bot for too long'"
        ),
    },
    "blind_spots": [
        "English only",
        "first-person operator required, so third-party reports of a use are dropped by design",
        "one post = one author, so a use category is the only unit many authors can share",
    ],
}


# -------------------------------------------------------------------------------------------
# the single writer
# -------------------------------------------------------------------------------------------
def write_artifact(root: pathlib.Path, doc: dict) -> pathlib.Path:
    """The ONLY writer in this file. `.sweep.json` so `first_pass_path` can tell a sweep from a
    daily pass by name and never compare a sweep against itself."""
    when = gbx.parse_created(doc["captured_at"]) or dt.datetime.now(dt.timezone.utc)
    out = root / "x" / ("%s.sweep.json" % gbx.stamp(when))
    out.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(out, doc)
    return out


def emit(text: str) -> None:
    """`print`, except that a reader who walked away is not a failed measurement."""
    try:
        print(text)
    except BrokenPipeError:
        try:
            os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
        except OSError:
            pass


def report(doc: dict, artifact: Optional[pathlib.Path] = None) -> None:
    c = doc["counts"]
    k = doc["combined"]
    fp = doc["first_pass"]
    art = artifact.name if artifact else "(not written)"
    flag = "" if doc["reliable"] else "  ** UNRELIABLE: see controls **"
    emit(
        "x-sweep %s: %d query(s) run, %d post(s) found, %d NEW vs %s, %d use-describing "
        "(%d new), %d distinct use author(s)%s"
        % (
            art,
            c["search_calls"],
            c["posts"],
            c["new_vs_first_pass"],
            fp.get("artifact") or "(no first pass)",
            c["uses_detected"],
            c["uses_new"],
            c["use_authors"],
            flag,
        )
    )
    emit(
        "  tiers=%s solicited=%d urls=%d (outbound %d) failed=%d skipped=%d"
        % (
            c["use_tiers"],
            c["uses_solicited"],
            c["url_mentions"],
            c["url_mentions_outbound"],
            c["queries_failed"],
            c["queries_skipped"],
        )
    )
    emit(
        "  COMBINED both passes: %d use-describing post(s), %d distinct author(s) "
        "(first pass %d/%d, sweep adds %d post(s) and %d author(s))"
        % (
            k["total_uses"],
            k["total_use_authors"],
            k["first_pass_uses"],
            fp.get("use_authors", 0),
            k["sweep_uses_not_in_first_pass"],
            k["sweep_only_authors"],
        )
    )
    emit(
        "  ATTRIBUTION: %d of those post(s) RELAY somebody else's use - %d AUTHORED author(s) "
        "is the independence number%s"
        % (
            k["total_relayed"],
            k["total_use_authors_authored"],
            (" (sweep reasons: %s)" % c["uses_relayed_reasons"])
            if c.get("uses_relayed_reasons")
            else "",
        )
    )
    emit("  query yield (verdict is arithmetic: new posts and uses, not taste):")
    for y in doc["query_yield"]:
        emit(
            "   %-6s %-18s items=%-4s new=%-4s uses=%-4s more=%-5s %s"
            % (
                y["verdict"],
                y["label"],
                y["items"],
                y["new"],
                y["uses"],
                y["has_more"],
                y["reason"][:48],
            )
        )
    for line in ranked_lines(doc["uses_ranked_combined"]):
        emit(line)
    a = doc["classifier"]
    emit(
        "  classifier %s: precision %d/%d on a hand-read sample, recall ~%.0f%% "
        "(%d of %d rejected rows were really uses) - every count here is a FLOOR"
        % (
            a["version"],
            a["precision"]["correct"],
            a["precision"]["sampled"],
            100 * a["recall"]["value_estimate"],
            a["recall"]["missed_uses"],
            a["recall"]["sampled"],
        )
    )
    if not doc["reliable"]:
        emit(
            "  refusal recorded - do NOT read any zero in this artifact as absence "
            "(AGENTS.md positive-control rule)"
        )


def ranked_lines(ranked: list, limit: int = 15) -> list:
    """The ranked table, shared by both reporters so a reader cannot be shown two different
    shapes of the same measurement."""
    top = ranked[:limit]
    if not top:
        return []
    out = [
        "  use categories, ranked by DISTINCT AUTHORS (both passes); "
        "auth=AUTHORED-only author(s):"
    ]
    for r in top:
        out.append(
            "   %3d author(s) (auth %3d) %4d post(s)  %-26s top=%-16s %.0f%%%s%s"
            % (
                r["distinct_authors"],
                r["authored_authors"],
                r["posts"],
                r["category"],
                (r["top_author"] or "-")[:16],
                100 * r["top_author_share"],
                "  DOMINATED" if r["dominated"] else "",
                "  relay=%d" % r["relayed_posts"] if r["relayed_posts"] else "",
            )
        )
    return out


def report_reclassify(doc: dict) -> None:
    k = doc["combined"]
    emit(
        "x-sweep reclassify (%s + %s, offline - 0 search call(s)): %d use-describing post(s), "
        "%d distinct author(s), %d AUTHORED author(s), %d RELAYED post(s)"
        % (
            doc["classifier"],
            doc["attribution"],
            k["total_uses"],
            k["total_use_authors"],
            k["total_use_authors_authored"],
            k["total_relayed"],
        )
    )
    emit("  tiers=%s" % k["tiers"])
    for p in doc["passes"]:
        emit(
            "   %-28s %s posts=%-5s uses=%-4s new=%-4s authors=%-4s auth=%-4s relay=%s%s"
            % (
                p["artifact"] or "(absent)",
                "ok " if p["readable"] else "DEAD",
                p["posts"],
                p["uses"],
                p["uses_not_already_counted"],
                p["use_authors"],
                p["use_authors_authored"],
                p["uses_relayed"],
                "  %s" % p["note"] if p["note"] else "",
            )
        )
    for line in ranked_lines(doc["uses_ranked_combined"]):
        emit(line)


def render_matrix() -> str:
    out = ["query matrix - %d slot(s), cap %d" % (len(QUERIES), MAX_SEARCH_CALLS)]
    for spec in QUERIES:
        out.append(
            "  %-18s %-16s max=%-4d %s"
            % (spec["label"], spec["role"], spec["max"], spec["q"])
        )
        out.append("      why: %s" % spec["why"])
    out.append("deferred candidates (lost the budget contest, with the reason):")
    for d in DEFERRED:
        out.append("  %-18s %s" % (d["label"], d["q"]))
        out.append("      deferred: %s" % d["why_deferred"])
    return "\n".join(out)


# -------------------------------------------------------------------------------------------
# fixtures — real shapes, trimmed from x/2026-09-11T1753.json
# -------------------------------------------------------------------------------------------
FX_DEPLOYED = {
    "id": "2098500000000000001",
    "text": "@MatthewBerman @bot @SpaceXAI My Grok bot checks the local tide times, surfers "
    "against sewage water quality and the weather and then lets me know it is the perfect "
    "time to have a surf.",
    "author_id": "11",
    "conversation_id": "2098441494478889314",
    "created_at": "2026-09-11T12:00:00.000Z",
    "lang": "en",
    "public_metrics": {"like_count": 12, "retweet_count": 1, "reply_count": 0},
}
FX_INTENDED = {
    "id": "2098500000000000002",
    "text": "@MatthewBerman @bot @SpaceXAI I would use my Grok Bot to draft educational "
    "resources for my students every week. Hope you send me a code, thanks Matthew.",
    "author_id": "12",
    "conversation_id": "2098441494478889314",
    "created_at": "2026-09-11T12:01:00.000Z",
    "lang": "en",
    "public_metrics": {"like_count": 3, "retweet_count": 0, "reply_count": 0},
}
FX_SECOND_AUTHOR = {
    "id": "2098500000000000003",
    "text": "my grok bot runs my inbox: it reads every email each morning, drafts the replies "
    "and waits for my approval before it sends anything.",
    "author_id": "13",
    "conversation_id": "2098500000000000003",
    "created_at": "2026-09-11T12:02:00.000Z",
    "lang": "en",
    "public_metrics": {"like_count": 40, "retweet_count": 4, "reply_count": 2},
}
# Same author as FX_SECOND_AUTHOR, so `distinct_authors` must NOT move while `posts` does.
FX_SAME_AUTHOR = {
    "id": "2098500000000000004",
    "text": "my grok bot also sorts my email into projects and flags the invoices it finds.",
    "author_id": "13",
    "conversation_id": "2098500000000000004",
    "created_at": "2026-09-11T12:03:00.000Z",
    "lang": "en",
    "public_metrics": {"like_count": 2, "retweet_count": 0, "reply_count": 0},
}
# KNOWN-BAD: opinion, not a use. The proximity classifier accepted this one.
FX_NOT_A_USE = {
    "id": "2098500000000000005",
    "text": "In my experience Grok is a little behind on executing plans but I have to admit "
    "that Grok Bot is pretty nice.",
    "author_id": "14",
    "conversation_id": "2098500000000000005",
    "created_at": "2026-09-11T12:04:00.000Z",
    "lang": "en",
    "public_metrics": {"like_count": 1, "retweet_count": 0, "reply_count": 0},
}
# KNOWN-BAD: a service advertisement held up only by a weak pattern.
FX_PROMO = {
    "id": "2098500000000000006",
    "text": "@aiedge_ Use-case lists are free. We can fix Grok Bot agent automation setup for "
    "you - using leftover subscription tokens.",
    "author_id": "15",
    "conversation_id": "2098500000000000006",
    "created_at": "2026-09-11T12:05:00.000Z",
    "lang": "en",
    "public_metrics": {"like_count": 0, "retweet_count": 0, "reply_count": 0},
}
# KNOWN-BAD: explicit absence of a use.
FX_NEGATED = {
    "id": "2098500000000000007",
    "text": "@MatthewBerman @bot @SpaceXAI I do not have any favorite grok bot usage yet but "
    "would love to discover one.",
    "author_id": "16",
    "conversation_id": "2098500000000000007",
    "created_at": "2026-09-11T12:06:00.000Z",
    "lang": "en",
    "public_metrics": {"like_count": 0, "retweet_count": 0, "reply_count": 0},
}
# A post with a real outbound url, for the negative control's extractor check.
FX_WITH_LINK = {
    "id": "2098500000000000008",
    "text": "my grok bot posts the weekly digest https://t.co/abc",
    "author_id": "17",
    "conversation_id": "2098500000000000008",
    "created_at": "2026-09-11T12:07:00.000Z",
    "lang": "en",
    "public_metrics": {"like_count": 0, "retweet_count": 0, "reply_count": 0},
    "entities": {
        "urls": [
            {
                "url": "https://t.co/abc",
                "expanded_url": "https://grokbotjobs.com/weekly?utm_source=x",
                "title": "Weekly digest",
            }
        ]
    },
}
# RELAY: the shape five separate accounts posted in this run. The first-person use language is
# inside the quotation marks and belongs to the engineer being quoted, not to the poster.
# Trimmed from x/2026-09-12T0047.sweep.json (@kaorixbt; @arle0x, @s4yonnara, @0xRafy and
# @sheemamoto posted the same frame around the same engineer). The trim keeps `GrokBot teams that
# do it for me` because that is the span `bot-subject` fires on: a shorter trim classifies as no
# use at all and would make this fixture prove nothing.
FX_RELAY_QUOTE = {
    "id": "2098500000000000009",
    "text": 'SpaceXAI engineer and ex-Cursor: "At SpaceXAI, 85% of our code is already written '
    "by GrokBot agents I'm not coding anymore I'm building GrokBot teams that do it for me "
    'A Chief of Staff agent manages the entire coding team"',
    "author_id": "18",
    "conversation_id": "2098500000000000009",
    "created_at": "2026-09-11T12:08:00.000Z",
    "lang": "en",
    "public_metrics": {"like_count": 900, "retweet_count": 120, "reply_count": 40},
}
# RELAY with NO quotation marks, so the quote-strip leg cannot see it: a report of somebody
# else's talk (@lennysan, verbatim opening). `RELAY_FRAME` is the leg that catches this one, and
# `made Grok Bot the success` is what makes it a use at all (pattern `make-bot`).
FX_RELAY_FRAME = {
    "id": "2098500000000000010",
    "text": "My biggest takeaways from Grok Bot product lead @RomanUgarte_: 1. Two key early "
    "product decisions made Grok Bot the success it has become. First, everything runs in the "
    "cloud, so bots can be kicked off from a phone and run while the user sleeps.",
    "author_id": "19",
    "conversation_id": "2098500000000000010",
    "created_at": "2026-09-11T12:09:00.000Z",
    "lang": "en",
    "public_metrics": {"like_count": 200, "retweet_count": 30, "reply_count": 10},
}
# KNOWN-BAD FOR THE RELAY RULE: an AUTHORED use that happens to quote something - here the
# operator's own bot talking (@drsarah's shape). The use claim stands OUTSIDE the quote, so
# stripping the quote must leave it standing. If this flips to relayed, the rule is over-firing
# and 31 of the 37 quote-bearing use rows in this run would be misattributed.
FX_AUTHORED_QUOTE = {
    "id": "2098500000000000011",
    "text": "Right now one of my favorite grok bot uses is my Elon bot. He is the bot that "
    "pushes things to execution. He says the hard thing \u201cthis plan is a waste of time, "
    "close the loop\u201d.",
    "author_id": "20",
    "conversation_id": "2098500000000000011",
    "created_at": "2026-09-11T12:10:00.000Z",
    "lang": "en",
    "public_metrics": {"like_count": 12, "retweet_count": 1, "reply_count": 2},
}
# KNOWN-BAD FOR THE ROLE-LEAD LEG: an authored use whose text contains a job title followed by a
# colon (@_danielakande's `My Grok Bot handles founder ops:`). The first-person guard is the only
# thing separating this from `SpaceXAI engineer:` and this fixture is what holds that guard.
FX_AUTHORED_ROLE_COLON = {
    "id": "2098500000000000012",
    "text": "@MatthewBerman My Grok Bot handles founder ops: it triages my Gmail and Slack every "
    "morning and auto-drafts follow-ups for stalled deals.",
    "author_id": "21",
    "conversation_id": "2098500000000000012",
    "created_at": "2026-09-11T12:11:00.000Z",
    "lang": "en",
    "public_metrics": {"like_count": 5, "retweet_count": 0, "reply_count": 1},
}
# KNOWN-BAD FOR THE FRAME LEG, and the most expensive pair in this file. A first draft of
# `RELAY_FRAME` matched a frame word plus `from`/`of` with no test on the object, and these two
# shapes are what it wrongly called relays - along with five more like them. `summary` and
# `notes` are what a Bot PRODUCES; the object is a thing, not a person. If either of these ever
# reports `relay: True` again, the rule has been re-widened and the authored/relayed split is
# wrong in the direction that discards real operators. Verbatim from @xueyu1125 and
# @_austinsheehy (x/2026-09-11T1753.json).
FX_AUTHORED_SUMMARY_OF = {
    "id": "2098500000000000013",
    "text": "Grok Bot A scrapes trending topics via the X API every hour; Bot C aggregates "
    "information from Bots A and B to generate a summary of the latest AI news and emails it "
    "to my Gmail.",
    "author_id": "22",
    "conversation_id": "2098500000000000013",
    "created_at": "2026-09-11T12:12:00.000Z",
    "lang": "en",
    "public_metrics": {"like_count": 8, "retweet_count": 1, "reply_count": 0},
}
FX_AUTHORED_NOTES_FROM = {
    "id": "2098500000000000014",
    "text": "Planning to lean on Grok Bot for admin tasks while I'm managing a screaming "
    "newborn: birth plan and hospital bag notes from the classes we've taken, and it drafts "
    "the updates for family.",
    "author_id": "23",
    "conversation_id": "2098500000000000014",
    "created_at": "2026-09-11T12:13:00.000Z",
    "lang": "en",
    "public_metrics": {"like_count": 30, "retweet_count": 2, "reply_count": 5},
}
# RELAY by ROLE LEAD, with no quotation marks anywhere: the same SpaceXAI-engineer attribution
# the five relayers used, paraphrased instead of quoted. The quote-strip leg is blind to this
# shape by construction, so this fixture is the ONLY thing proving `RELAY_ROLE_LEAD` is alive -
# and it was not alive when it was written: its first-person guard scanned the whole post, which
# `classify_use` guarantees contains a first-person pronoun, so the leg could never fire.
FX_RELAY_ROLE_LEAD = {
    "id": "2098500000000000015",
    "text": "SpaceXAI engineer and ex-Cursor: at SpaceXAI, 85% of our code is already written "
    "by GrokBot agents that do it for them, and a Chief of Staff agent manages the whole team.",
    "author_id": "24",
    "conversation_id": "2098500000000000015",
    "created_at": "2026-09-11T12:14:00.000Z",
    "lang": "en",
    "public_metrics": {"like_count": 60, "retweet_count": 9, "reply_count": 3},
}
FX_USERS = [
    {"id": "11", "username": "LenSeaside"},
    {"id": "12", "username": "pbeens"},
    {"id": "13", "username": "inboxops"},
    {"id": "14", "username": "AndrewCurioso"},
    {"id": "15", "username": "getgrokbotjobs"},
    {"id": "16", "username": "nousecase"},
    {"id": "17", "username": "digestbot"},
    {"id": "18", "username": "relaykaori"},
    {"id": "19", "username": "relaylenny"},
    {"id": "20", "username": "drsarah"},
    {"id": "21", "username": "danielakande"},
    {"id": "22", "username": "xueyu1125"},
    {"id": "24", "username": "relayrole"},
    {"id": "23", "username": "austinsheehy"},
]


def envelope(
    posts: list, *, users: Optional[list] = None, next_token: Optional[str] = None
) -> dict:
    env: dict = {
        "data": list(posts),
        "includes": {"users": list(users if users is not None else FX_USERS)},
        "meta": {"result_count": len(posts)},
    }
    if next_token:
        env["meta"]["next_token"] = next_token
    return env


def fake_x(
    by_query: dict,
    *,
    fail_after: Optional[int] = None,
    status: int = 429,
    # 1789153200 == 2026-09-11T19:00:00Z. Spelled as the epoch x-cli actually emits, because the
    # parse under test is `re.search(r"Resets at (\d+)")` against the CLI's own message.
    error: str = "rate-limited: Rate limited. Resets at 1789153200.",
) -> "tuple":
    """An `x_search` stand-in plus the call log, so the cap and the 429 STOP are provable without
    a network. Unknown queries answer an empty envelope, never an exception: an unlisted query in
    a fixture is a gap in the fixture, not a crash to debug."""
    log: list = []

    def _fetch(query: str, max_results: int):
        log.append((query, max_results))
        if fail_after is not None and len(log) > fail_after:
            return None, error, status
        return envelope(by_query.get(query, [])), None, 200

    return _fetch, log


# The two queries every selftest run must satisfy for the controls to pass.
CTRL = {
    "grok -is:retweet": [FX_DEPLOYED],
    '("grok bot" OR grokbot) -has:links -is:retweet': [FX_NOT_A_USE],
}


def emit_verdict(checks: list, passed: int, total: int) -> None:
    """Print the selftest result. Separated from `selftest` so the verdict is computed before any
    byte is written and a closed pipe cannot reach the `return`."""
    try:
        for name, ok, detail in checks:
            if not ok:
                print("FAIL %s %s" % (name, detail))
        print(
            "SELFTEST %s - %d/%d"
            % ("PASS" if passed == total else "FAIL", passed, total)
        )
        sys.stdout.flush()
    except BrokenPipeError:
        try:
            os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
        except OSError:
            pass


def selftest() -> int:
    checks: list = []

    def check(name: str, ok: bool, detail: str = "") -> None:
        checks.append((name, bool(ok), detail))

    NOW = dt.datetime(2026, 9, 11, 18, 0, 0, tzinfo=dt.timezone.utc)
    LATER = dt.datetime(2026, 9, 12, 6, 0, 0, tzinfo=dt.timezone.utc)

    # 1 — the classifier ACCEPTS the shapes it exists for.
    good = classify_use(FX_DEPLOYED["text"], "LenSeaside")
    check(
        "classifier-accepts-deployed-use",
        bool(good) and good["tier"] == "deployed" and "possessive" in good["patterns"],
        str(good),
    )
    intended = classify_use(FX_INTENDED["text"], "pbeens")
    check(
        "classifier-tiers-an-intention-as-intended",
        bool(intended) and intended["tier"] == "intended" and intended["solicited"],
        str(intended),
    )

    # 2 — FIRES ON KNOWN BAD. Three posts that a looser classifier accepted, each planting one
    #     defect: an opinion, an advertisement, and an explicit absence of a use. If any of these
    #     comes back as a use, every count downstream is inflated and this leg says so.
    bad = {
        "opinion": (FX_NOT_A_USE["text"], "AndrewCurioso"),
        "promo": (FX_PROMO["text"], "getgrokbotjobs"),
        "negated": (FX_NEGATED["text"], "nousecase"),
    }
    accepted = {k: classify_use(t, a) for k, (t, a) in bad.items()}
    check(
        "classifier-fires-on-known-bad",
        all(v is None for v in accepted.values()),
        str({k: v for k, v in accepted.items() if v}),
    )
    # 2b — and the known-bad leg is only meaningful if the detector can see those rows at all:
    #      all three DO mention the product and DO carry an operator pronoun, so they reach the
    #      rejects rather than falling out at the anchor.
    check(
        "known-bad-rows-reach-the-rejects",
        all(re.search(PROD, t, re.I) and OPERATOR.search(t) for t, _ in bad.values()),
        "a known-bad fixture that fails the anchor proves nothing",
    )
    # 2c — the product's own account is excluded, and only because of the handle.
    vendor_text = (
        "My Grok Bot support routine checks your account and drafts the reply."
    )
    check(
        "vendor-account-excluded-by-handle",
        classify_use(vendor_text, "grok") is None
        and classify_use(vendor_text, "somebody") is not None,
        "author exclusion must be the only difference",
    )

    # 2d — AUTHORED vs RELAYED. All THREE legs must fire, and each must fire for its OWN reason:
    #      a shared assertion would let one live leg cover for two dead ones, which is how
    #      `RELAY_ROLE_LEAD` shipped structurally unfireable in the first place. This is the leg
    #      that stops one upstream engineer's claim from arriving as five independent builders.
    relayed = {
        "quoted-span": (
            classify_use(FX_RELAY_QUOTE["text"], "kaorixbt"),
            "use-claim-only-inside-quoted-span",
        ),
        "role-lead": (
            classify_use(FX_RELAY_ROLE_LEAD["text"], "relayrole"),
            "third-party-role-attribution-lead",
        ),
        "reported-frame": (
            classify_use(FX_RELAY_FRAME["text"], "lennysan"),
            "reported-speech-frame",
        ),
    }
    check(
        "relay-flags-a-third-party-use-on-each-of-three-legs",
        all(
            v and v["relay"] and v["relay_reason"] == want
            for v, want in relayed.values()
        ),
        str({k: (v or {}).get("relay_reason") for k, (v, _w) in relayed.items()}),
    )
    # 2d2 — the role-lead leg carries NO quotation mark, so it cannot be the quote-strip leg
    #       wearing a different name. Proven, not assumed.
    check(
        "role-lead-relay-has-no-quote-to-strip",
        DQUOTE.search(FX_RELAY_ROLE_LEAD["text"]) is None
        and DQUOTE.search(FX_RELAY_FRAME["text"]) is None,
        "a quoted fixture would prove the wrong leg",
    )
    # 2e — FIRES ON KNOWN BAD for the relay rule itself. Four authored uses, each one a shape a
    #      draft of this rule got WRONG: a use that quotes its own bot, a job title before a
    #      colon, `a summary OF THE latest AI news`, and `notes FROM THE classes`. The last two
    #      were measured false relays, not hypotheticals - seven posts were misattributed before
    #      `RELAY_FRAME` required a named person, and four of them were among the best uses in
    #      the corpus. A rule that flags any of these discards real operators.
    authored = {
        "own-bot-quoted": classify_use(FX_AUTHORED_QUOTE["text"], "drsarah"),
        "role-colon": classify_use(FX_AUTHORED_ROLE_COLON["text"], "_danielakande"),
        "summary-of-a-thing": classify_use(FX_AUTHORED_SUMMARY_OF["text"], "xueyu1125"),
        "notes-from-a-thing": classify_use(
            FX_AUTHORED_NOTES_FROM["text"], "austinsheehy"
        ),
    }
    check(
        "relay-does-not-fire-on-authored-uses-that-quote-or-summarise",
        all(v and v["relay"] is False for v in authored.values()),
        str({k: (v or {}).get("relay_reason") for k, v in authored.items()}),
    )
    # 2e2 — and those four DO reach the attribution step: a known-bad that is not even a use
    #       proves nothing about the rule meant to classify uses.
    check(
        "relay-known-bad-rows-are-really-uses",
        all(v and v["is_use"] for v in authored.values()),
        str({k: bool(v) for k, v in authored.items()}),
    )
    # 2f — the mechanism, not just the verdict: the quoted-span leg must be the thing doing the
    #      work. Without the quote the relay has NO use left; the authored post still has one.
    check(
        "quote-strip-is-the-discriminator",
        classify_use(strip_quoted(FX_RELAY_QUOTE["text"]), "kaorixbt") is None
        and classify_use(strip_quoted(FX_AUTHORED_QUOTE["text"]), "drsarah") is not None
        and DQUOTE.search(FX_RELAY_QUOTE["text"]) is not None,
        "strip_quoted must remove the claim in one case and not the other",
    )

    # 3 — categories, and the fact that a use can have none.
    check(
        "categorise-finds-multiple-and-tolerates-none",
        set(categorise(FX_SECOND_AUTHOR["text"]))
        >= {"inbox-and-email", "approval-and-governance"}
        and categorise("my grok bot does the thing") == [],
        str(categorise(FX_SECOND_AUTHOR["text"])),
    )

    # 4 — RANK BY DISTINCT AUTHORS, not by posts and not by engagement. One author with two
    #     posts and 42 likes must rank BELOW two authors with one post each and 15 likes. This is
    #     the gb-demand.py rule; a rank that answered otherwise would reproduce the exact error
    #     that flagged Gmail as self-cited.
    def row_for(post, author, cats, likes):
        return {
            "id": post["id"],
            "url": gbx.permalink(post["id"]),
            "signals": {
                "author": author,
                "likes": likes,
                "reposts": 0,
                "use": {
                    "is_use": True,
                    "tier": "deployed",
                    "patterns": ["possessive"],
                    "strong": True,
                    "solicited": False,
                    "categories": cats,
                    "classifier": CLASSIFIER_VERSION,
                },
            },
        }

    ranked = rank_uses(
        [
            row_for(FX_SECOND_AUTHOR, "loud", ["loud-cat"], 42),
            row_for(FX_SAME_AUTHOR, "loud", ["loud-cat"], 0),
            row_for(FX_DEPLOYED, "a1", ["broad-cat"], 10),
            row_for(FX_INTENDED, "a2", ["broad-cat"], 5),
        ]
    )
    order = [r["category"] for r in ranked]
    loud = [r for r in ranked if r["category"] == "loud-cat"][0]
    broad = [r for r in ranked if r["category"] == "broad-cat"][0]
    check(
        "rank-is-by-distinct-authors-not-posts-or-engagement",
        order[0] == "broad-cat"
        and broad["distinct_authors"] == 2
        and loud["distinct_authors"] == 1
        and loud["posts"] == 2
        and loud["engagement"]["likes"] > broad["engagement"]["likes"],
        str(order),
    )
    check(
        "top-author-share-flags-domination-at-half",
        loud["top_author_share"] == 1.0
        and loud["dominated"] is True
        and broad["top_author_share"] == 0.5
        and broad["dominated"] is True
        and rank_uses(
            [
                row_for(FX_DEPLOYED, "a1", ["c"], 0),
                row_for(FX_INTENDED, "a2", ["c"], 0),
                row_for(FX_SECOND_AUTHOR, "a3", ["c"], 0),
            ]
        )[0]["dominated"]
        is False,
        "%s / %s" % (loud["top_author_share"], broad["top_author_share"]),
    )
    # 4b — an unknown author is a post with no builder, never a phantom one.
    anon = rank_uses(
        [row_for(FX_DEPLOYED, None, ["c"], 0), row_for(FX_INTENDED, None, ["c"], 0)]
    )[0]
    check(
        "unknown-authors-never-become-a-builder",
        anon["posts"] == 2
        and anon["distinct_authors"] == 0
        and anon["anonymous_posts"] == 2,
        str(anon),
    )

    # 5 — the row contract is gb-x's, byte for byte, including the id.
    fetch, _log = fake_x(dict(CTRL, **{QUERIES[2]["q"]: [FX_SECOND_AUTHOR]}))
    doc = collect(fetch=fetch, now=NOW, sleep_s=0, first_pass=read_first_pass(None))
    row = [
        r for r in doc["rows"] if r["signals"]["tweet_id"] == FX_SECOND_AUTHOR["id"]
    ][0]
    check(
        "row-id-is-gb-x-content-id-of-the-permalink",
        row["id"] == gbx.content_id(gbx.permalink(FX_SECOND_AUTHOR["id"]))
        and row["kind"] == "x-post"
        and row["source"] == gbx.SEARCH_ENDPOINT,
        row["id"],
    )
    check(
        "volatile-fields-declared-and-inherited",
        doc["volatile_fields"] == gbx.VOLATILE_FIELDS == ["signals.age_days"],
        str(doc["volatile_fields"]),
    )

    # 6 — NO CLOCK INSIDE A ROW. Two collects twelve hours apart must be byte-identical once the
    #     one declared volatile field is stripped. A sibling paid 204 blobs in a day for this.
    doc_later = collect(
        fetch=fake_x(dict(CTRL, **{QUERIES[2]["q"]: [FX_SECOND_AUTHOR]}))[0],
        now=LATER,
        sleep_s=0,
        first_pass=read_first_pass(None),
    )

    def strip(rows):
        out = []
        for r in rows:
            c = json.loads(json.dumps(r))
            c["signals"].pop("age_days", None)
            out.append(c)
        return out

    check(
        "rows-are-clock-independent",
        json.dumps(strip(doc["rows"]), sort_keys=True)
        == json.dumps(strip(doc_later["rows"]), sort_keys=True)
        and doc["captured_at"] != doc_later["captured_at"],
        "%s vs %s" % (doc["captured_at"], doc_later["captured_at"]),
    )

    # 7 — the local cap is the binding guard, and it cannot be exceeded.
    fetch, log = fake_x(CTRL)
    capped = collect(
        fetch=fetch, now=NOW, sleep_s=0, max_calls=3, first_pass=read_first_pass(None)
    )
    check(
        "local-call-cap-is-enforced",
        len(log) == 3
        and capped["counts"]["search_calls"] == 3
        and capped["counts"]["queries_skipped"] == len(QUERIES) - 3,
        "%d call(s)" % len(log),
    )

    # 8 — a 429 STOPS the run, records the reset, keeps what it had, and does not raise.
    fetch, log = fake_x(
        dict(CTRL, **{QUERIES[2]["q"]: [FX_SECOND_AUTHOR]}), fail_after=2
    )
    limited = collect(fetch=fetch, now=NOW, sleep_s=0, first_pass=read_first_pass(None))
    quota = limited["sources"][0]
    check(
        "rate-limit-stops-the-run-and-is-recorded",
        quota["rate_limited"] is True
        and quota["reset_at"] == "2026-09-11T19:00:00Z"
        # calls 1 and 2 are the two controls and they land; call 3 is the 429 and stops the run.
        # The two control posts SURVIVE — that is the property: a refused run keeps what it had.
        and len(log) == 3
        and limited["counts"]["posts"] == 2
        and limited["counts"]["queries_skipped"] == len(QUERIES) - 3
        and limited["reliable"] is False,
        "calls=%d posts=%d skipped=%d"
        % (
            len(log),
            limited["counts"]["posts"],
            limited["counts"]["queries_skipped"],
        ),
    )

    # 9 — the controls. Positive must return rows; negative must harvest ZERO outbound urls, and
    #     the leg is only meaningful if a real link WOULD fire it.
    check(
        "controls-pass-on-clean-fixtures",
        doc["controls"]["positive_passed"]
        and doc["controls"]["negative_passed"]
        and doc["reliable"],
        str(doc["controls"]),
    )
    bad_ctrl = dict(CTRL)
    bad_ctrl['("grok bot" OR grokbot) -has:links -is:retweet'] = [FX_WITH_LINK]
    broken = collect(
        fetch=fake_x(bad_ctrl)[0], now=NOW, sleep_s=0, first_pass=read_first_pass(None)
    )
    check(
        "negative-control-fires-on-a-real-outbound-link",
        broken["controls"]["negative_urls"] == 1
        and broken["controls"]["negative_passed"] is False
        and broken["reliable"] is False,
        str(broken["controls"]),
    )
    empty_pos = dict(CTRL)
    empty_pos["grok -is:retweet"] = []
    dead = collect(
        fetch=fake_x(empty_pos)[0], now=NOW, sleep_s=0, first_pass=read_first_pass(None)
    )
    check(
        "positive-control-failure-makes-the-run-unreliable",
        dead["controls"]["positive_passed"] is False and dead["reliable"] is False,
        str(dead["controls"]),
    )

    # 10 — DEDUPE AGAINST THE FIRST PASS. A post the prior artifact already holds is counted as
    #      seen, not as new, and the combined total counts it ONCE.
    prior_id = gbx.content_id(gbx.permalink(FX_DEPLOYED["id"]))
    prior = {
        "artifact": "fixture.json",
        "readable": True,
        "captured_at": "2026-09-11T17:53:50Z",
        "posts": 1,
        "post_ids": {prior_id},
        "uses": [
            {
                "id": prior_id,
                "author": "LenSeaside",
                "tier": "deployed",
                "categories": ["travel-and-local"],
                "solicited": False,
            }
        ],
        "note": "",
    }
    fetch, _ = fake_x(dict(CTRL, **{QUERIES[2]["q"]: [FX_DEPLOYED, FX_SECOND_AUTHOR]}))
    deduped = collect(fetch=fetch, now=NOW, sleep_s=0, first_pass=prior)
    c = deduped["counts"]
    k = deduped["combined"]
    seen_row = [r for r in deduped["rows"] if r["id"] == prior_id][0]
    check(
        "first-pass-posts-are-marked-seen-not-new",
        seen_row["signals"]["seen_in_first_pass"] is True
        and seen_row["signals"]["sweep_only"] is False
        and c["seen_in_first_pass"] == 1
        and c["new_vs_first_pass"] == c["posts"] - 1,
        "seen=%d new=%d posts=%d"
        % (c["seen_in_first_pass"], c["new_vs_first_pass"], c["posts"]),
    )
    check(
        "combined-total-counts-a-shared-post-once",
        k["first_pass_uses"] == 1
        and k["sweep_uses_not_in_first_pass"] == k["sweep_uses"] - 1
        and k["total_uses"] == 1 + k["sweep_uses_not_in_first_pass"]
        and k["total_use_authors"] >= 2,
        str(k),
    )
    # 10b — an absent first pass must NOT read as "everything is new" silently.
    check(
        "absent-first-pass-is-recorded-as-unreadable",
        doc["first_pass"]["readable"] is False
        and doc["first_pass"]["ids_loaded"] == 0
        and "no first-pass artifact" in (doc["first_pass"]["note"] or ""),
        str(doc["first_pass"]),
    )

    # 10c — the attribution flag must reach the ARTIFACT, not just the classifier. One relay and
    #       one authored post by the SAME kind of category: `uses_relayed` counts the relay,
    #       `use_authors_authored` excludes its author, and `examples` stays free of it while
    #       `relayed_examples` carries it. Without this leg the flag could be computed and then
    #       dropped on the floor by any of four call sites.
    attr_fetch, _ = fake_x(
        dict(CTRL, **{QUERIES[2]["q"]: [FX_RELAY_QUOTE, FX_AUTHORED_ROLE_COLON]})
    )
    attr = collect(
        fetch=attr_fetch, now=NOW, sleep_s=0, first_pass=read_first_pass(None)
    )
    ac, ak = attr["counts"], attr["combined"]
    relay_rows = [r for r in attr["rows"] if r["signals"]["use"].get("relay")]
    ranked_relay = [r for r in attr["uses_ranked"] if r["relayed_posts"]]
    # The control query contributes an authored use of its own, so the assertion is the
    # DIFFERENCE between the two author counts: exactly one handle is held out, and it is the
    # relay's. An absolute count here would break the moment a control fixture changed.
    check(
        "relay-flag-reaches-counts-and-ranking",
        ac["uses_relayed"] == 1
        and ac["use_authors"] - ac["use_authors_authored"] == 1
        and ak["total_relayed"] == 1
        and ak["total_use_authors"] - ak["total_use_authors_authored"] == 1
        and [r["signals"]["author"] for r in relay_rows] == ["relaykaori"]
        and ac["uses_relayed_reasons"] == {"use-claim-only-inside-quoted-span": 1}
        and bool(ranked_relay)
        and all(
            "relaykaori" not in [e["author"] for e in r["examples"]]
            and "relaykaori" in [e["author"] for e in r["relayed_examples"]]
            for r in ranked_relay
        ),
        "relayed=%s authors=%s authored=%s reasons=%s"
        % (
            ac["uses_relayed"],
            ac["use_authors"],
            ac["use_authors_authored"],
            ac["uses_relayed_reasons"],
        ),
    )

    # 10d — `reclassify`, the offline re-read. It takes NO fetch, so it cannot spend a search
    #       call by construction; what has to be proven is the arithmetic it replaces a live run
    #       with. A post held by BOTH artifacts is counted once, the author of a relay is held
    #       out of the authored total, and a real permalink survives into the ranked example
    #       instead of the `status/0` placeholder the synthetic row shape used to fabricate.
    shared = {
        "id": "shared-1",
        "url": gbx.permalink("2098500000000000020"),
        "author": "bothpasses",
        "tier": "deployed",
        "categories": ["inbox-and-email"],
        "solicited": False,
        "relay": False,
        "relay_reason": "",
    }
    only_relay = dict(
        shared,
        id="relay-1",
        url=gbx.permalink("2098500000000000021"),
        author="relayonly",
        relay=True,
        relay_reason="use-claim-only-inside-quoted-span",
    )
    only_first = dict(
        shared,
        id="first-1",
        url=gbx.permalink("2098500000000000022"),
        author="firstonly",
    )
    rc = reclassify(
        {
            "artifact": "first.json",
            "readable": True,
            "posts": 2,
            "uses": [shared, only_first],
        },
        {
            "artifact": "sweep.json",
            "readable": True,
            "posts": 2,
            "uses": [shared, only_relay],
        },
    )
    rk = rc["combined"]
    cat = [r for r in rc["uses_ranked_combined"] if r["category"] == "inbox-and-email"][
        0
    ]
    check(
        "reclassify-dedupes-and-keeps-attribution-offline",
        rk["total_uses"] == 3
        and rk["total_use_authors"] == 3
        and rk["total_use_authors_authored"] == 2
        and rk["total_relayed"] == 1
        and cat["posts"] == 3
        and cat["authored_authors"] == 2
        and cat["relayed_posts"] == 1
        and sorted(e["author"] for e in cat["examples"]) == ["bothpasses", "firstonly"]
        and [e["url"] for e in cat["relayed_examples"]]
        == [gbx.permalink("2098500000000000021")]
        and rc["passes"][0]["artifact"] == "sweep.json"
        and rc["passes"][1]["uses_not_already_counted"] == 1,
        json.dumps(rk),
    )
    # 10e — and an absent artifact is a REFUSAL, not a zero. `read_first_pass(None)` on both
    #       sides must produce empty numbers that say why, never a confident 0 uses.
    empty_rc = reclassify(read_first_pass(None), read_first_pass(None))
    check(
        "reclassify-reports-absence-as-unreadable",
        empty_rc["combined"]["total_uses"] == 0
        and all(p["readable"] is False for p in empty_rc["passes"])
        and all("no first-pass artifact" in p["note"] for p in empty_rc["passes"])
        and empty_rc["uses_ranked_combined"] == [],
        str(empty_rc["passes"]),
    )

    # 11 — the per-query yield verdict is ARITHMETIC, not taste. Both directions are asserted,
    #      because a rule that only ever says `prune` prunes the matrix to nothing.
    #
    #      `mine-possessive` returned two posts here and the first pass already held one of
    #      them, so ONE post was new: below `PRUNE_MIN_NEW` and therefore pruned. That is the
    #      honest verdict for a two-post fixture and it is asserted as such rather than dressed
    #      up — the `keep` arm is proven on explicit numbers just below it.
    ys = {y["label"]: y for y in deduped["query_yield"]}
    keeps = query_yield(
        {
            "id": "q",
            "role": "use-language",
            "query": "q",
            "status": 200,
            "items": 100,
            "new_vs_first_pass": 40,
            "uses": 3,
        }
    )
    check(
        "query-yield-prunes-below-the-new-post-floor-and-keeps-above-it",
        ys[QUERIES[2]["label"]]["new"] == 1
        and ys[QUERIES[2]["label"]]["verdict"] == "prune"
        and ys[QUERIES[3]["label"]]["new"] == 0
        and ys[QUERIES[3]["label"]]["verdict"] == "prune"
        and ys["control-positive"]["verdict"] == "control"
        and keeps["verdict"] == "keep"
        and PRUNE_MIN_NEW > 1,
        str({k2: (v["new"], v["verdict"]) for k2, v in ys.items()}),
    )
    check(
        "query-yield-prunes-a-query-with-posts-but-no-uses",
        query_yield(
            {
                "id": "q",
                "role": "use-language",
                "query": "q",
                "status": 200,
                "items": 40,
                "new_vs_first_pass": 40,
                "uses": 0,
            }
        )["verdict"]
        == "prune",
        "40 new posts and no use is a wasted slot",
    )

    # 12 — READ-ONLY, STRUCTURALLY. This module builds NO argv at all, and the detector that
    #      would catch one is proven live by firing it on known-bad source.
    src = pathlib.Path(__file__).resolve().read_text(encoding="utf-8")
    tree = ast.parse(src)
    check(
        "this-module-builds-no-x-cli-argv",
        gbx.mutating_verbs(tree) == set()
        and "ARGV_PREFIX"
        not in {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)},
        str(gbx.mutating_verbs(tree)),
    )
    known_bad = ast.parse(
        "import subprocess\n"
        "def bad(q):\n"
        "    return subprocess.run(['x-cli', '-j', 'tweet', 'post', q])\n"
    )
    known_bad_2 = ast.parse(
        "X_CLI = 'x-cli'\n"
        "def bad2(t):\n"
        "    argv = [X_CLI, 'tweet', 'reply', t]\n"
        "    return argv\n"
    )
    check(
        "argv-detector-fires-on-known-bad-source",
        gbx.mutating_verbs(known_bad) == {"post"}
        and gbx.mutating_verbs(known_bad_2) == {"reply"},
        "%s %s" % (gbx.mutating_verbs(known_bad), gbx.mutating_verbs(known_bad_2)),
    )
    writers = gbx.writer_functions(tree)
    check(
        "only-write-artifact-can-touch-the-filesystem",
        writers == {"write_artifact"},
        str(sorted(writers)),
    )
    check(
        "writer-detector-fires-on-known-bad-source",
        gbx.writer_functions(
            ast.parse(
                "import json\ndef sneaky(p, d):\n    json.dump(d, open(p, 'w'))\n"
            )
        )
        == {"sneaky"},
        "a detector that cannot see a raw open() proves nothing",
    )

    # 13 — the matrix IS the budget, and the queries really are disjoint from the first pass.
    labels = [q["label"] for q in QUERIES]
    check(
        "matrix-fills-the-budget-exactly-once",
        len(QUERIES) == MAX_SEARCH_CALLS and len(set(labels)) == len(labels),
        "%d slot(s), %d unique label(s)" % (len(QUERIES), len(set(labels))),
    )
    first_pass_queries = {q["q"] for q in gbx.QUERIES}
    overlap = {q["q"] for q in QUERIES} & first_pass_queries
    check(
        "only-the-controls-repeat-a-first-pass-query",
        overlap
        <= {
            "grok -is:retweet",
            '("grok bot" OR grokbot) -has:links -is:retweet',
        },
        str(sorted(overlap)),
    )
    check(
        "every-query-states-what-it-is-for",
        all(len(q["why"]) > 20 for q in QUERIES)
        and all(len(d["why_deferred"]) > 20 for d in DEFERRED),
        "a query nobody can justify is noise",
    )

    # 14 — argparse's own writes carry a verdict that a closed pipe would erase.
    check(
        "body-recovers-argparse-verdict-on-a-closed-pipe",
        gbx.guards_broken_pipe(tree, "body")
        and not gbx.unguarded_nonzero_returns(tree, "body"),
        str(sorted(gbx.unguarded_nonzero_returns(tree, "body"))),
    )

    # 15 — the audit block is the only honest source of the precision number, so it must be
    #      internally consistent rather than a round number somebody typed.
    a = CLASSIFIER_AUDIT
    check(
        "classifier-audit-numbers-are-self-consistent",
        a["version"] == CLASSIFIER_VERSION
        and abs(
            a["precision"]["correct"] / a["precision"]["sampled"]
            - a["precision"]["value"]
        )
        < 0.01
        and len(a["precision"]["false_positives"])
        == a["precision"]["sampled"] - a["precision"]["correct"]
        and len(a["recall"]["named_misses"]) == a["recall"]["missed_uses"],
        str(a["precision"]),
    )

    passed = sum(1 for _, ok, _ in checks if ok)
    verdict = 0 if passed == len(checks) else 1
    emit_verdict(checks, passed, len(checks))
    return verdict


# -------------------------------------------------------------------------------------------
def body() -> int:
    ap = argparse.ArgumentParser(
        prog="gb-x-sweep.py",
        description="second-pass X read: disjoint queries, use-case classification, "
        "rank by distinct authors",
    )
    ap.add_argument(
        "command",
        nargs="?",
        default="collect",
        choices=["collect", "matrix", "reclassify", "selftest"],
    )
    ap.add_argument(
        "--selftest", action="store_true", help="inline fixtures, no network"
    )
    ap.add_argument(
        "--json", action="store_true", help="print the document, not a summary"
    )
    ap.add_argument(
        "--dry-run", action="store_true", help="spend the quota, write nothing"
    )
    ap.add_argument(
        "--max-calls",
        type=int,
        default=MAX_SEARCH_CALLS,
        help="lower the local request cap for a cheap probe run (never raises it)",
    )
    ap.add_argument(
        "--first-pass",
        default=None,
        help="the artifact to dedupe against (default: newest non-sweep x/*.json)",
    )
    ap.add_argument(
        "--sweep",
        default=None,
        help="reclassify: the sweep artifact to re-read (default: newest x/*.sweep.json)",
    )
    # ARGPARSE WRITES ON OUR BEHALF and its write carries the verdict: on a bad flag it PRINTS
    # first and exits second, so on a closed pipe the BrokenPipeError escapes `parse_args` before
    # argparse's own `sys.exit(2)` runs and `gbtypes.main`'s EPIPE->0 arm would turn a typo into
    # success. Recovered from argv, because the library never reaches the SystemExit.
    try:
        args = ap.parse_args()
    except BrokenPipeError:
        try:
            os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stderr.fileno())
        except OSError:
            pass
        return 0 if {"-h", "--help"} & set(sys.argv[1:]) else 2

    if args.selftest or args.command == "selftest":
        return selftest()
    if args.command == "matrix":
        emit(render_matrix())
        return 0
    root = pathlib.Path(__file__).resolve().parents[1]
    if args.command == "reclassify":
        # Offline by construction: `reclassify` reaches no fetch, so a rule change is verifiable
        # for free. `--dry-run` is accepted and redundant here - nothing is written either way.
        first = read_first_pass(
            pathlib.Path(args.first_pass) if args.first_pass else first_pass_path(root)
        )
        sweeps = (
            sorted((root / "x").glob("*.sweep.json")) if (root / "x").is_dir() else []
        )
        sweep_path = (
            pathlib.Path(args.sweep) if args.sweep else (sweeps[-1] if sweeps else None)
        )
        doc = reclassify(first, read_first_pass(sweep_path))
        if args.json:
            emit(json.dumps(doc, indent=1, ensure_ascii=False, default=str))
            return 0
        report_reclassify(doc)
        return 0

    path = pathlib.Path(args.first_pass) if args.first_pass else first_pass_path(root)
    prior = read_first_pass(path)
    # The flag can only tighten the budget. A cap an argument could raise is not a cap.
    doc = collect(
        max_calls=max(1, min(args.max_calls, MAX_SEARCH_CALLS)), first_pass=prior
    )
    artifact = None if args.dry_run else write_artifact(root, doc)
    if args.json:
        emit(json.dumps(doc, indent=1, ensure_ascii=False, default=str))
        return 0
    report(doc, artifact)
    return 0


def recover_cancellation(exc: BaseException) -> Optional[int]:
    """The exit code a `BrokenPipeError` was ABOUT to report for a cancellation, or None.

    `gbtypes.main` reports a cancelled run by PRINTING to stderr inside `except Cancelled` and
    exits 130/143 only after; its `except BrokenPipeError` is a SIBLING arm, so on a closed pipe
    the print's EPIPE escapes the wrapper and the honest code is lost. Measured on `gb-x.py`
    (bin/gb-x.py:2441-2465) and inherited here unchanged.

    THE NEGATIVE CARRIES THE WEIGHT: an EPIPE with no `Cancelled` behind it is not a cancellation
    and must keep propagating, or this function would MANUFACTURE one.
    """
    ctx = getattr(exc, "__context__", None)
    if not isinstance(ctx, Cancelled):
        return None
    code = getattr(ctx, "exit_code", None)
    return code if isinstance(code, int) else None


if __name__ == "__main__":
    try:
        main(body)
    except BrokenPipeError as _exc:
        _code = recover_cancellation(_exc)
        if _code is None:
            raise
        for _stream in (sys.stdout, sys.stderr):
            try:
                os.dup2(os.open(os.devnull, os.O_WRONLY), _stream.fileno())
            except OSError:
                pass
        raise SystemExit(_code)
