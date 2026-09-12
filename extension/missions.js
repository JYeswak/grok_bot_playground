// Mission chips. Each one compiles toward a known playground skeleton.
// Keep copy in the house style of vendor-watch / research-desk.

export const MISSIONS = [
  {
    id: "watch-product",
    label: "Watch this product",
    hint: "Re-read this page on a schedule. Report only what moved.",
    skeleton: "vendor-watch",
    category: "Ops",
    defaultCadence: "weekly",
    when: "Mon 07:00 local",
  },
  {
    id: "analyze-compete",
    label: "Analyze site & competitors",
    hint: "Compare positioning, pricing, and claims. Sources required.",
    skeleton: "research-desk",
    category: "Personal",
    defaultCadence: "none",
    when: "",
  },
  {
    id: "track-changelog",
    label: "Track changelog / docs / pricing",
    hint: "Diff vendor pages. Quiet week = one line.",
    skeleton: "vendor-watch",
    category: "Ops",
    defaultCadence: "weekly",
    when: "Mon 07:00 local",
  },
  {
    id: "watch-hiring",
    label: "Watch this hiring page",
    hint: "Quote new, closed, and retitled roles. Change nothing.",
    skeleton: "vendor-watch",
    category: "Ops",
    defaultCadence: "weekly",
    when: "Mon 08:00 local",
  },
  {
    id: "research-topic",
    label: "Research this topic",
    hint: "One hard question, answered with sources. No schedule.",
    skeleton: "research-desk",
    category: "Personal",
    defaultCadence: "none",
    when: "",
  },
  {
    id: "custom",
    label: "Custom",
    hint: "You write the job. Still compiled into gb-template/1.",
    skeleton: "vendor-watch",
    category: "Ops",
    defaultCadence: "weekly",
    when: "Mon 07:00 local",
  },
];

export function suggestedMissions(pageKind) {
  const order = {
    product: ["watch-product", "analyze-compete", "track-changelog", "custom"],
    pricing: ["track-changelog", "watch-product", "analyze-compete", "custom"],
    changelog: ["track-changelog", "watch-product", "custom"],
    docs: ["track-changelog", "research-topic", "watch-product", "custom"],
    careers: ["watch-hiring", "watch-product", "custom"],
    github: ["track-changelog", "watch-product", "research-topic", "custom"],
    article: ["research-topic", "analyze-compete", "watch-product", "custom"],
    other: ["watch-product", "analyze-compete", "research-topic", "track-changelog", "watch-hiring", "custom"],
  };
  const ids = order[pageKind] || order.other;
  const byId = Object.fromEntries(MISSIONS.map((m) => [m.id, m]));
  return ids.map((id) => byId[id]).filter(Boolean);
}

export function categoryFor(pageKind, mission) {
  if (mission?.category) return mission.category;
  if (pageKind === "article") return "Personal";
  return "Ops";
}
