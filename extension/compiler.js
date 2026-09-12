import { MISSIONS, categoryFor } from "./missions.js";

const EXACT_KEYS = [
  "schema",
  "id",
  "name",
  "tier",
  "category",
  "job",
  "charter",
  "charter_chars",
  "integrations",
  "routine",
  "approval_boundary",
  "memory",
  "verify",
  "why_this_shape",
  "replaces",
];

const ROUTINE_KEYS = ["cadence", "when", "prompt", "writes"];
const MISSION_BY_ID = Object.fromEntries(MISSIONS.map((mission) => [mission.id, mission]));

function cleanText(value) {
  return String(value || "").replace(/\s+/g, " ").trim();
}

function limit(value, max) {
  const text = cleanText(value);
  return text.length > max ? `${text.slice(0, max - 1).trimEnd()}…` : text;
}

function kebabCase(value) {
  const slug = cleanText(value)
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "");
  return slug || "page-bot";
}

function titleCase(value) {
  return cleanText(value)
    .split(/[\s/-]+/)
    .filter(Boolean)
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(" ");
}

function phrase(value) {
  return cleanText(value)
    .split(/[.!?](?:\s|$)/)[0]
    .replace(/[.!?]+$/g, "");
}

function pickSourceLabel(fingerprint) {
  const fromOg = cleanText(fingerprint?.og_site_name || fingerprint?.og_title);
  if (fromOg) return fromOg;
  const fromTitle = cleanText(fingerprint?.title);
  if (fromTitle) return fromTitle;
  const host = cleanText(fingerprint?.host).replace(/^www\./, "");
  if (host) return host;
  return "Page";
}

function deriveName({ fingerprint, mission, nameOverride }) {
  const explicit = cleanText(nameOverride);
  if (explicit) return limit(explicit, 80);
  const base = limit(pickSourceLabel(fingerprint), 48);
  const suffixByMission = {
    "watch-product": "Watch",
    "track-changelog": "Watch",
    "watch-hiring": "Hiring Watch",
    "analyze-compete": "Research Desk",
    "research-topic": "Research Desk",
    custom: "Desk",
  };
  return limit(`${base} ${suffixByMission[mission.id] || "Desk"}`, 80);
}

function deriveCategory(fingerprint, mission) {
  return categoryFor(fingerprint?.page_kind || "other", mission) === "Personal" ? "Personal" : "Ops";
}

function buildJob({ mission, fingerprint, intent, cadence }) {
  const target = limit(pickSourceLabel(fingerprint), 40);
  const focus = limit(phrase(intent || mission.hint), 88);
  if (mission.id === "watch-hiring") {
    return `Track ${target} roles, quote what changed, and keep the focus on ${focus.toLowerCase()}.`;
  }
  if (mission.id === "analyze-compete" || mission.id === "research-topic") {
    return cadence === "none"
      ? `Answer focused questions about ${target} with quotes, sources, and no invented claims.`
      : `Revisit ${target} on a schedule, answer the same question, and quote what changed.`;
  }
  return `Watch ${target}, quote changes, and stay focused on ${focus.toLowerCase()}.`;
}

function buildRoutine({ cadence, mission, intent }) {
  if (cadence === "none") {
    return {
      cadence: "none",
      when: "",
      prompt: "",
      writes: "",
    };
  }
  const when = cadence === "daily" ? "Daily 07:00 local" : mission.when || "Mon 07:00 local";
  const promptBase =
    mission.id === "analyze-compete" || mission.id === "research-topic"
      ? "Re-read the saved URLs, answer the same question, and post only what changed with quotes."
      : "Re-read the saved URLs and post only what changed, with quotes and one line if nothing moved.";
  const promptIntent = cleanText(intent) ? ` Focus: ${limit(intent, 140)}.` : "";
  return {
    cadence,
    when,
    prompt: `${promptBase}${promptIntent}`,
    writes:
      mission.id === "analyze-compete" || mission.id === "research-topic"
        ? "a dated answer in this thread with quoted evidence and what changed since the prior run"
        : "a dated diff note in this thread with quoted changes and a one-line no-change verdict when quiet",
  };
}

function buildCharter({ fingerprint, mission, intent, cadence }) {
  const target = limit(pickSourceLabel(fingerprint), 50);
  const focus = limit(intent || mission.hint, 160);
  const scheduledSentence =
    cadence === "none"
      ? ""
      : " On each run, compare against the last note so the output is a diff, not a fresh summary.";
  const base =
    mission.id === "analyze-compete" || mission.id === "research-topic"
      ? `You are the page research desk for ${target}.\n\nYour job: answer the operator's question about this page and its nearby source pages with quotes, not guesswork.\n\nHow you work: start from the URLs in memory, separate what the page says from what you concluded, and say "not found" when the page does not answer the question.${scheduledSentence}\n\nFocus for this desk: ${focus}.\n\nNever do: invent a feature, pretend a connector exists, or turn silence on the page into proof.`
      : `You are the page watch for ${target}.\n\nYour job: re-read the source URLs in memory and report only what changed, with quotes.\n\nHow you work: prefer the page itself over commentary, name a page that did not load instead of guessing, and keep last run's notes so the next run can diff against them.${scheduledSentence}\n\nFocus for this watch: ${focus}.\n\nNever do: invent pricing, infer hidden product behaviour, or confuse a missing page with no change.`;
  return limit(base, 900);
}

function collectUrls(fingerprint) {
  const urls = [fingerprint?.canonical, fingerprint?.url];
  const siblingLinks = fingerprint?.sibling_links || {};
  for (const key of ["pricing", "docs", "changelog", "careers"]) {
    urls.push(...(siblingLinks[key] || []));
  }
  return [...new Set(urls.filter(Boolean))].slice(0, 6);
}

function buildMemory(fingerprint) {
  const urls = collectUrls(fingerprint);
  const parts = [];
  if (urls.length) {
    parts.push(`Remember these source URLs: ${urls.join(", ")}.`);
  } else {
    parts.push("Remember the source URL list the operator pastes with the charter.");
  }
  if (fingerprint?.selection) {
    parts.push(`Keep this operator-selected focus in memory: "${limit(fingerprint.selection, 180)}".`);
  }
  parts.push("Keep last seen quotes or page text for comparison, not the whole excerpt pasted into the charter.");
  return limit(parts.join(" "), 700);
}

function buildVerify({ cadence, mission, fingerprint }) {
  const source = fingerprint?.canonical || fingerprint?.url || "the source page";
  if (cadence === "none") {
    return `The first useful reply names ${source}, quotes the page for at least one claim, and says "not found" when the page cannot answer.`;
  }
  if (mission.id === "watch-hiring") {
    return `This thread gains one dated note per run, and a changed run quotes role titles or counts from ${source}.`;
  }
  return `This thread gains one dated note per run, and a changed run quotes ${source} instead of paraphrasing it.`;
}

function buildWhyThisShape({ cadence }) {
  if (cadence === "none") {
    return "No schedulable routine is included because this mission is on demand: the extension only emits text, the human chooses the URLs, and the Bot should be asked when work is needed.";
  }
  return "This shape fits the playground boundary: the extension emits a paste-ready charter, the Bot reads remembered URLs, and a human can ask it to schedule the recurring prompt.";
}

export function compileTemplate({ fingerprint = {}, missionId = "custom", intent = "", cadence, nameOverride = "" }) {
  const mission = MISSION_BY_ID[missionId] || MISSION_BY_ID.custom || MISSIONS[0];
  const resolvedCadence = ["daily", "weekly", "none"].includes(cadence)
    ? cadence
    : mission.defaultCadence || "none";
  const resolvedIntent = limit(intent || mission.hint, 200);
  const name = deriveName({ fingerprint, mission, nameOverride });
  const charter = buildCharter({
    fingerprint,
    mission,
    intent: resolvedIntent,
    cadence: resolvedCadence,
  });
  return {
    schema: "gb-template/1",
    id: kebabCase(name),
    name,
    tier: "A",
    category: deriveCategory(fingerprint, mission),
    job: buildJob({
      mission,
      fingerprint,
      intent: resolvedIntent,
      cadence: resolvedCadence,
    }),
    charter,
    charter_chars: charter.length,
    integrations: [],
    routine: buildRoutine({
      cadence: resolvedCadence,
      mission,
      intent: resolvedIntent,
    }),
    approval_boundary: "",
    memory: buildMemory(fingerprint),
    verify: buildVerify({
      cadence: resolvedCadence,
      mission,
      fingerprint,
    }),
    why_this_shape: buildWhyThisShape({
      cadence: resolvedCadence,
      mission,
    }),
    replaces: null,
  };
}

export function validateLocal(template) {
  const errors = [];
  if (!template || typeof template !== "object") {
    return ["template must be an object"];
  }
  const keys = Object.keys(template);
  if (keys.length !== EXACT_KEYS.length) {
    errors.push(`expected ${EXACT_KEYS.length} keys, got ${keys.length}`);
  }
  for (const key of EXACT_KEYS) {
    if (!keys.includes(key)) errors.push(`missing key: ${key}`);
  }
  for (const key of keys) {
    if (!EXACT_KEYS.includes(key)) errors.push(`unexpected key: ${key}`);
  }
  if (template.schema !== "gb-template/1") errors.push("schema must be gb-template/1");
  if (!/^[a-z0-9]+(?:-[a-z0-9]+)*$/.test(template.id || "")) errors.push("id must be kebab-case");
  if (!cleanText(template.name)) errors.push("name must be non-empty");
  if (template.tier !== "A") errors.push("tier must be A");
  if (!["Personal", "Ops"].includes(template.category)) errors.push("category must be Personal or Ops");
  if (typeof template.job !== "string" || template.job.includes("\n")) errors.push("job must be one line");
  const sentenceCount = (template.job || "")
    .split(/[.!?](?:\s|$)/)
    .map((part) => part.trim())
    .filter(Boolean).length;
  if (sentenceCount !== 1) errors.push("job must be one sentence");
  if (typeof template.charter !== "string" || template.charter.length > 900) errors.push("charter must be <= 900 chars");
  if (template.charter_chars !== (template.charter || "").length) errors.push("charter_chars must equal charter.length");
  if (!Array.isArray(template.integrations) || template.integrations.length !== 0) errors.push("integrations must be []");
  if (template.approval_boundary !== "") errors.push("approval_boundary must be empty");
  if (template.replaces !== null) errors.push("replaces must be null");
  if (!template.memory) errors.push("memory must be non-empty");
  if (!template.verify) errors.push("verify must be non-empty");
  if (!template.why_this_shape) errors.push("why_this_shape must be non-empty");
  const routine = template.routine;
  if (!routine || typeof routine !== "object") {
    errors.push("routine must be an object");
  } else {
    const routineKeys = Object.keys(routine);
    for (const key of ROUTINE_KEYS) {
      if (!routineKeys.includes(key)) errors.push(`routine missing key: ${key}`);
    }
    for (const key of routineKeys) {
      if (!ROUTINE_KEYS.includes(key)) errors.push(`routine unexpected key: ${key}`);
    }
    if (!["daily", "weekly", "none"].includes(routine.cadence)) errors.push("routine.cadence must be daily|weekly|none");
    if (routine.cadence === "none") {
      if (routine.when !== "" || routine.prompt !== "" || routine.writes !== "") {
        errors.push("routine fields must be empty strings when cadence is none");
      }
      if (!String(template.why_this_shape).includes("schedul")) {
        errors.push("why_this_shape must mention schedul when cadence is none");
      }
    } else if (!cleanText(routine.when) || !cleanText(routine.prompt) || !cleanText(routine.writes)) {
      errors.push("scheduled routines need when, prompt, and writes");
    }
  }
  return errors;
}
