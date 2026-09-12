import { compileTemplate, validateLocal } from "./compiler.js";
import { MISSIONS } from "./missions.js";

const stripePricingFingerprint = {
  url: "https://stripe.com/pricing",
  canonical: "https://stripe.com/pricing",
  title: "Stripe Pricing",
  host: "stripe.com",
  path: "/pricing",
  description: "Pricing for payments and financial infrastructure.",
  og_title: "Stripe Pricing",
  og_site_name: "Stripe",
  og_type: "website",
  page_kind: "pricing",
  headings: {
    h1: ["Pricing built for growth"],
    h2: ["Payments", "Billing"],
    h3: ["Standard pricing"],
  },
  jsonld: {
    Product: [],
    Organization: [{ "@type": "Organization", name: "Stripe", url: "https://stripe.com/" }],
    SoftwareApplication: [],
  },
  prices: ["2.9% + 30¢ per successful card charge"],
  sibling_links: {
    pricing: ["https://stripe.com/pricing/local-payment-methods"],
    docs: ["https://docs.stripe.com/"],
    changelog: ["https://docs.stripe.com/changelog"],
    careers: ["https://stripe.com/jobs"],
  },
  last_updated: "2026-09-12",
  selection: "Focus on pricing and packaging changes.",
  excerpt: "Stripe pricing for payments, billing, and related products.",
};

const expectedKeys = [
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

try {
  for (const mission of MISSIONS) {
    for (const cadence of ["weekly", "none"]) {
      const template = compileTemplate({
        fingerprint: stripePricingFingerprint,
        missionId: mission.id,
        intent: mission.hint,
        cadence,
      });
      const actualKeys = Object.keys(template);
      if (actualKeys.length !== expectedKeys.length) {
        throw new Error(`${mission.id}/${cadence}: expected 15 keys, got ${actualKeys.length}`);
      }
      for (const key of expectedKeys) {
        if (!actualKeys.includes(key)) {
          throw new Error(`${mission.id}/${cadence}: missing ${key}`);
        }
      }
      const errors = validateLocal(template);
      if (errors.length) {
        throw new Error(`${mission.id}/${cadence}: ${errors.join("; ")}`);
      }
      if (template.charter.length > 900) {
        throw new Error(`${mission.id}/${cadence}: charter too long (${template.charter.length})`);
      }
    }
  }
  console.log("extension self-test ok");
} catch (error) {
  console.error(error.message || error);
  process.exit(1);
}
