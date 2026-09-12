export function extractPageFingerprint() {
  const textOf = (value) => String(value || "").replace(/\s+/g, " ").trim();
  const unique = (items) => [...new Set(items.filter(Boolean))];
  const absoluteUrl = (value) => {
    if (!value) return "";
    try {
      return new URL(value, window.location.href).href;
    } catch {
      return "";
    }
  };
  const metaContent = (...selectors) => {
    for (const selector of selectors) {
      const node = document.querySelector(selector);
      const content = textOf(node?.content || node?.getAttribute?.("content"));
      if (content) return content;
    }
    return "";
  };
  const textList = (selector, limit = 12) =>
    unique(
      Array.from(document.querySelectorAll(selector))
        .map((node) => textOf(node.textContent))
        .filter(Boolean)
        .slice(0, limit),
    );
  const trimField = (value, limit) => {
    const text = textOf(value);
    return text.length > limit ? `${text.slice(0, limit - 1).trimEnd()}…` : text;
  };
  const pickLastUpdated = (jsonldDates) => {
    const metaDate = metaContent(
      'meta[property="article:modified_time"]',
      'meta[name="last-modified"]',
      'meta[name="last_modified"]',
      'meta[itemprop="dateModified"]',
      'meta[property="og:updated_time"]',
    );
    if (metaDate) return metaDate;
    const timeDate = textOf(
      document.querySelector("time[datetime]")?.getAttribute("datetime") ||
        document.querySelector("relative-time[datetime]")?.getAttribute("datetime"),
    );
    if (timeDate) return timeDate;
    return jsonldDates.find(Boolean) || "";
  };
  const looksLikeArticle = () => {
    if (document.querySelector('article, meta[property="article:published_time"], meta[name="author"]')) {
      return true;
    }
    const paragraphs = document.querySelectorAll("main p, article p, p").length;
    return paragraphs >= 8;
  };
  const collectJsonLd = () => {
    const buckets = {
      Product: [],
      Organization: [],
      SoftwareApplication: [],
    };
    const modifiedDates = [];
    const keepKeys = [
      "@type",
      "name",
      "url",
      "description",
      "brand",
      "category",
      "applicationCategory",
      "dateModified",
      "datePublished",
      "price",
      "priceCurrency",
      "sku",
      "offers",
      "sameAs",
    ];
    const pushNode = (type, node) => {
      const picked = {};
      for (const key of keepKeys) {
        if (node[key] !== undefined) picked[key] = node[key];
      }
      buckets[type].push(picked);
      modifiedDates.push(textOf(node.dateModified));
    };
    const visit = (node) => {
      if (!node) return;
      if (Array.isArray(node)) {
        node.forEach(visit);
        return;
      }
      if (typeof node !== "object") return;
      if (Array.isArray(node["@graph"])) visit(node["@graph"]);
      const types = Array.isArray(node["@type"]) ? node["@type"] : [node["@type"]];
      for (const type of ["Product", "Organization", "SoftwareApplication"]) {
        if (types.includes(type)) pushNode(type, node);
      }
    };
    for (const script of document.querySelectorAll('script[type="application/ld+json"]')) {
      const raw = script.textContent?.trim();
      if (!raw) continue;
      try {
        visit(JSON.parse(raw));
      } catch {
        /* ignore malformed json-ld */
      }
    }
    return { buckets, modifiedDates };
  };
  const collectPrices = (jsonld) => {
    const values = [];
    for (const type of ["Product", "SoftwareApplication"]) {
      for (const item of jsonld[type] || []) {
        if (item.price && item.priceCurrency) {
          values.push(`${item.priceCurrency} ${item.price}`);
        } else if (item.offers) {
          const offers = Array.isArray(item.offers) ? item.offers : [item.offers];
          for (const offer of offers) {
            const currency = textOf(offer?.priceCurrency);
            const price = textOf(offer?.price);
            if (currency && price) values.push(`${currency} ${price}`);
          }
        }
      }
    }
    const bodyText = textOf(document.body?.innerText || "");
    const matches =
      bodyText.match(
        /(?:[$€£]\s?\d[\d,]*(?:\.\d{2})?|\bUSD\s?\d[\d,]*(?:\.\d{2})?\b|\bEUR\s?\d[\d,]*(?:\.\d{2})?\b|\bper month\b|\bper year\b)/gi,
      ) || [];
    values.push(...matches);
    return unique(values.map((value) => trimField(value, 80))).slice(0, 16);
  };
  const collectSiblingLinks = () => {
    const buckets = {
      pricing: [],
      docs: [],
      changelog: [],
      careers: [],
    };
    const classify = (pathname) => {
      if (/(?:^|\/)(pricing|prices|plans|billing)(?:\/|$)/.test(pathname)) return "pricing";
      if (/(?:^|\/)(docs|doc|help|guide|reference|manual|kb)(?:\/|$)/.test(pathname)) return "docs";
      if (/(?:^|\/)(changelog|release|releases|updates|whats-new|what-s-new)(?:\/|$)/.test(pathname)) {
        return "changelog";
      }
      if (/(?:^|\/)(careers|career|jobs|hiring|openings)(?:\/|$)/.test(pathname)) return "careers";
      return "";
    };
    for (const anchor of document.querySelectorAll("a[href]")) {
      const href = absoluteUrl(anchor.getAttribute("href"));
      if (!href) continue;
      let parsed;
      try {
        parsed = new URL(href);
      } catch {
        continue;
      }
      if (parsed.origin !== window.location.origin) continue;
      if (parsed.href === window.location.href) continue;
      const bucket = classify(parsed.pathname.toLowerCase());
      if (!bucket) continue;
      buckets[bucket].push(parsed.href);
    }
    return Object.fromEntries(
      Object.entries(buckets).map(([key, value]) => [key, unique(value).slice(0, 10)]),
    );
  };
  const pickExcerpt = () => {
    const source =
      document.querySelector("main") ||
      document.querySelector('article, [role="main"]') ||
      document.body;
    const clone = source?.cloneNode(true);
    if (!clone) return "";
    clone.querySelectorAll("nav, footer, script, style, noscript, svg, canvas").forEach((node) => node.remove());
    return trimField(clone.textContent || "", 4000);
  };
  const classifyPageKind = ({ path, ogType, jsonld }) => {
    const host = window.location.hostname.toLowerCase();
    const pathText = path.toLowerCase();
    if (host === "github.com" || host.endsWith(".github.com")) return "github";
    if (/(?:^|\/)(pricing|prices|plans|billing)(?:\/|$)/.test(pathText)) return "pricing";
    if (/(?:^|\/)(changelog|release|releases|updates|whats-new|what-s-new)(?:\/|$)/.test(pathText)) {
      return "changelog";
    }
    if (/(?:^|\/)(docs|doc|help|guide|reference|manual|kb)(?:\/|$)/.test(pathText)) return "docs";
    if (/(?:^|\/)(careers|career|jobs|hiring|openings)(?:\/|$)/.test(pathText)) return "careers";
    if (jsonld.Product.length || jsonld.SoftwareApplication.length || /product/i.test(ogType)) return "product";
    if (looksLikeArticle()) return "article";
    return "other";
  };

  const title = textOf(document.title);
  const canonical = absoluteUrl(document.querySelector('link[rel="canonical"]')?.href);
  const description = metaContent('meta[name="description"]', 'meta[property="og:description"]');
  const ogTitle = metaContent('meta[property="og:title"]');
  const ogSiteName = metaContent('meta[property="og:site_name"]');
  const ogType = metaContent('meta[property="og:type"]');
  const headings = {
    h1: textList("h1", 8),
    h2: textList("h2", 12),
    h3: textList("h3", 16),
  };
  const { buckets: jsonld, modifiedDates } = collectJsonLd();
  const sibling_links = collectSiblingLinks();
  const page_kind = classifyPageKind({
    path: window.location.pathname,
    ogType,
    jsonld,
  });

  return {
    url: window.location.href,
    canonical,
    title,
    host: window.location.hostname,
    path: window.location.pathname,
    description,
    og_title: ogTitle,
    og_site_name: ogSiteName,
    og_type: ogType,
    page_kind,
    headings,
    jsonld,
    prices: collectPrices(jsonld),
    sibling_links,
    last_updated: pickLastUpdated(modifiedDates),
    selection: trimField(window.getSelection?.()?.toString() || "", 1200),
    excerpt: pickExcerpt(),
  };
}
