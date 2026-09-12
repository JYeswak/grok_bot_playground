import { compileTemplate, validateLocal } from "./compiler.js";
import { extractPageFingerprint } from "./extract.js";
import { MISSIONS, suggestedMissions } from "./missions.js";

const state = {
  fingerprint: null,
  missionId: MISSIONS[0]?.id || "custom",
  template: null,
  lastAutoIntent: "",
};

const elements = {
  status: document.getElementById("status"),
  capture: document.getElementById("capture"),
  fingerprint: document.getElementById("fingerprint"),
  pageKind: document.getElementById("page-kind"),
  missions: document.getElementById("missions"),
  intent: document.getElementById("intent"),
  name: document.getElementById("name"),
  cadence: document.getElementById("cadence"),
  templateName: document.getElementById("template-name"),
  preview: document.getElementById("preview"),
  charterMeter: document.getElementById("charter-meter"),
  copyCharter: document.getElementById("copy-charter"),
  copyFirstMessage: document.getElementById("copy-first-message"),
  copySchedulePrompt: document.getElementById("copy-schedule-prompt"),
  downloadTemplate: document.getElementById("download-template"),
};

function escapeHtml(value) {
  return String(value ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function missionFor(id) {
  return MISSIONS.find((mission) => mission.id === id) || MISSIONS[0];
}

function setStatus(message, isError = false) {
  elements.status.textContent = message;
  elements.status.classList.toggle("error", Boolean(isError));
}

async function activeTabId() {
  const tabs = await chrome.tabs.query({ active: true, lastFocusedWindow: true });
  if (tabs[0]?.id) return tabs[0].id;
  const currentWindowTabs = await chrome.tabs.query({ active: true, currentWindow: true });
  return currentWindowTabs[0]?.id || null;
}

async function captureCurrentTab() {
  setStatus("Reading the active tab…");
  try {
    const tabId = await activeTabId();
    if (!tabId) throw new Error("No active tab is available.");
    const [injection] = await chrome.scripting.executeScript({
      target: { tabId },
      func: extractPageFingerprint,
    });
    if (!injection?.result) throw new Error("Could not read the active page.");
    state.fingerprint = injection.result;
    const mission = missionFor(state.missionId);
    if (!elements.intent.value || elements.intent.value === state.lastAutoIntent) {
      state.lastAutoIntent = mission?.hint || "";
      elements.intent.value = state.lastAutoIntent;
    }
    renderMissions();
    renderFingerprint();
    compileAndRender();
    setStatus(`Read ${state.fingerprint.host || "the page"} successfully.`);
  } catch (error) {
    setStatus(error?.message || "Could not read this tab.", true);
  }
}

function renderMissions() {
  const ordered = state.fingerprint ? suggestedMissions(state.fingerprint.page_kind) : MISSIONS;
  elements.missions.innerHTML = ordered
    .map(
      (mission) => `
        <button class="chip ${mission.id === state.missionId ? "active" : ""}" data-mission="${escapeHtml(mission.id)}" type="button">
          <span>${escapeHtml(mission.label)}</span>
          <small>${escapeHtml(mission.hint)}</small>
        </button>`,
    )
    .join("");
  for (const button of elements.missions.querySelectorAll("[data-mission]")) {
    button.addEventListener("click", () => {
      const missionId = button.getAttribute("data-mission");
      const mission = missionFor(missionId);
      state.missionId = missionId;
      elements.cadence.value = mission.defaultCadence;
      if (!elements.intent.value || elements.intent.value === state.lastAutoIntent) {
        state.lastAutoIntent = mission.hint;
        elements.intent.value = mission.hint;
      }
      renderMissions();
      compileAndRender();
    });
  }
}

function renderFingerprint() {
  const fingerprint = state.fingerprint;
  if (!fingerprint) {
    elements.fingerprint.className = "fingerprint empty";
    elements.fingerprint.textContent = "Open the panel from a page, then read the active tab.";
    elements.pageKind.textContent = "other";
    return;
  }
  elements.fingerprint.className = "fingerprint";
  elements.pageKind.textContent = fingerprint.page_kind || "other";
  const siblings = Object.entries(fingerprint.sibling_links || {})
    .filter(([, links]) => links?.length)
    .map(([kind, links]) => `${kind}: ${links.length}`)
    .join(" · ");
  const sections = [
    ["Title", fingerprint.title || fingerprint.og_title || "Untitled"],
    ["URL", fingerprint.canonical || fingerprint.url || ""],
    ["Description", fingerprint.description || "—"],
    ["Headings", [fingerprint.headings?.h1?.[0], fingerprint.headings?.h2?.[0], fingerprint.headings?.h3?.[0]].filter(Boolean).join(" · ") || "—"],
    ["Prices", (fingerprint.prices || []).slice(0, 4).join(" · ") || "—"],
    ["Sibling links", siblings || "—"],
    ["Last updated", fingerprint.last_updated || "—"],
    ["Selection", fingerprint.selection || "—"],
  ];
  elements.fingerprint.innerHTML = `<dl>${sections
    .map(
      ([label, value]) => `<div><dt>${escapeHtml(label)}</dt><dd>${escapeHtml(value)}</dd></div>`,
    )
    .join("")}</dl>`;
}

function buildFirstMessage(template, fingerprint) {
  const urls = [
    fingerprint?.canonical || fingerprint?.url,
    ...(fingerprint?.sibling_links?.pricing || []),
    ...(fingerprint?.sibling_links?.docs || []),
    ...(fingerprint?.sibling_links?.changelog || []),
    ...(fingerprint?.sibling_links?.careers || []),
  ].filter(Boolean);
  const lines = [
    "Use these page URLs as your starting list:",
    ...[...new Set(urls)].slice(0, 6).map((url) => `- ${url}`),
  ];
  if (fingerprint?.selection) {
    lines.push("", `Operator focus: ${fingerprint.selection}`);
  }
  lines.push("", `Please follow this charter: ${template.name}.`);
  return lines.join("\n");
}

function buildSchedulePrompt(template) {
  if (template.routine.cadence === "none") return "";
  return `Please create a ${template.routine.cadence} routine for yourself at ${template.routine.when}. Prompt: ${template.routine.prompt}`;
}

function compileAndRender() {
  const mission = missionFor(state.missionId);
  state.template = compileTemplate({
    fingerprint: state.fingerprint || {},
    missionId: state.missionId,
    intent: elements.intent.value,
    cadence: elements.cadence.value,
    nameOverride: elements.name.value,
  });
  const errors = validateLocal(state.template);
  elements.templateName.textContent = state.template.name;
  elements.preview.textContent = JSON.stringify(state.template, null, 2);
  elements.charterMeter.textContent = `${state.template.charter_chars} / 900`;
  elements.charterMeter.classList.toggle("over", state.template.charter_chars > 900);
  elements.copySchedulePrompt.disabled = state.template.routine.cadence === "none";
  if (errors.length) {
    setStatus(`Template validation: ${errors[0]}`, true);
  } else if (state.fingerprint) {
    setStatus(`Ready: ${mission.label}.`);
  }
}

async function copyText(text, okMessage) {
  try {
    await navigator.clipboard.writeText(text);
    setStatus(okMessage);
  } catch {
    setStatus("Clipboard write failed in this context.", true);
  }
}

function downloadTemplate() {
  const blob = new Blob([JSON.stringify(state.template, null, 2)], { type: "application/json" });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = `${state.template.id || "page-to-grok-bot"}.json`;
  anchor.click();
  URL.revokeObjectURL(url);
  setStatus("Downloaded gb-template/1.");
}

elements.capture.addEventListener("click", () => {
  captureCurrentTab();
});

elements.intent.addEventListener("input", () => {
  compileAndRender();
});

elements.name.addEventListener("input", () => {
  compileAndRender();
});

elements.cadence.addEventListener("change", () => {
  compileAndRender();
});

elements.copyCharter.addEventListener("click", () => {
  copyText(state.template?.charter || "", "Copied charter.");
});

elements.copyFirstMessage.addEventListener("click", () => {
  copyText(buildFirstMessage(state.template, state.fingerprint), "Copied first message.");
});

elements.copySchedulePrompt.addEventListener("click", () => {
  copyText(buildSchedulePrompt(state.template), "Copied schedule prompt.");
});

elements.downloadTemplate.addEventListener("click", () => {
  downloadTemplate();
});

renderMissions();
renderFingerprint();
compileAndRender();
captureCurrentTab();
