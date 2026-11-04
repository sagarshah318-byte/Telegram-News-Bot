// REDBOXINDIA -> Telegram, independent of GitHub Actions.
// Deploy as a NEW Cloudflare Worker. Do not replace mf-bot/cloudflare-worker.js.
// Required bindings:
//   KV namespace: REDBOX_STATE
// Required secrets:
//   TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
// Cron trigger: * * * * * (once per minute)

const FEEDS = [
  "https://fxtwitter.com/REDBOXINDIA/feed.xml",
  "https://fixupx.com/REDBOXINDIA/feed.xml",
];
const STATE_KEY = "redbox_seen_ids";
const MAX_SEEN = 500;

export default {
  async scheduled(controller, env, ctx) {
    ctx.waitUntil(pollAndForward(env));
  },

  async fetch(request, env) {
    if (request.method !== "GET") {
      return new Response("Method not allowed", { status: 405 });
    }
    return new Response("REDBOXINDIA Telegram monitor is running.", {
      headers: { "content-type": "text/plain; charset=utf-8" },
    });
  },
};

async function pollAndForward(env) {
  if (!env.REDBOX_STATE || !env.TELEGRAM_BOT_TOKEN || !env.TELEGRAM_CHAT_ID) {
    throw new Error("Missing REDBOX_STATE KV binding or Telegram secrets.");
  }

  const entries = await fetchFeed();
  const stored = await env.REDBOX_STATE.get(STATE_KEY, "json");
  const seen = Array.isArray(stored) ? stored : null;

  // First run seeds the current feed so old posts are not blasted into Telegram.
  if (!seen) {
    const initial = entries.map((entry) => entry.id).filter(Boolean).slice(0, MAX_SEEN);
    await env.REDBOX_STATE.put(STATE_KEY, JSON.stringify(initial));
    console.log("Initial feed baseline saved:", initial.length, "posts");
    return;
  }

  const known = new Set(seen);
  const fresh = entries.filter((entry) => entry.id && !known.has(entry.id)).reverse();

  if (!fresh.length) {
    console.log("No new REDBOXINDIA posts.");
    return;
  }

  const updated = [...seen];
  for (const post of fresh) {
    const text = (post.text || "").trim();
    if (text) {
      await sendTelegram(env, text);
    }
    updated.push(post.id);
    // Persist after each successful send to prevent repeats after a later failure.
    await env.REDBOX_STATE.put(
      STATE_KEY,
      JSON.stringify([...new Set(updated)].slice(-MAX_SEEN))
    );
    console.log("Forwarded post:", post.id);
  }
}

async function fetchFeed() {
  const errors = [];
  for (const url of FEEDS) {
    try {
      const response = await fetch(url, {
        headers: {
          "User-Agent": "Mozilla/5.0 (compatible; RedboxTelegramBot/1.0)",
          "Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml",
        },
        cf: { cacheTtl: 0, cacheEverything: false },
      });
      if (!response.ok) throw new Error("HTTP " + response.status);
      const xml = await response.text();
      const entries = parseFeed(xml);
      if (!entries.length) throw new Error("Feed returned no entries");
      console.log("RSS source:", url, "entries:", entries.length);
      return entries;
    } catch (error) {
      errors.push(url + ": " + error.message);
      console.error("RSS source failed:", url, error.message);
    }
  }
  throw new Error("All RSS sources failed: " + errors.join(" | "));
}

function parseFeed(xml) {
  const blocks = [...xml.matchAll(/<(item|entry)\b[^>]*>([\s\S]*?)<\/\1>/gi)];
  return blocks.map((match) => {
    const block = match[2];
    const linkTag = block.match(/<link\b([^>]*)>([\s\S]*?)<\/link>/i);
    const atomHref = block.match(/<link\b[^>]*href=["']([^"']+)["'][^>]*\/?\s*>/i);
    const guid = tagText(block, "guid") || tagText(block, "id");
    const link = (linkTag && decodeXml(linkTag[2])) || (atomHref && decodeXml(atomHref[1])) || guid;
    const idMatch = String(link || guid || "").match(/\/status\/(\d+)/i);
    const id = idMatch ? "status:" + idMatch[1] : String(guid || link || "").trim();
    const description = tagText(block, "description") ||
      tagText(block, "content:encoded") ||
      tagText(block, "content") ||
      tagText(block, "summary") ||
      tagText(block, "title");
    return { id, text: cleanText(description), link };
  }).filter((entry) => entry.id);
}

function tagText(block, tag) {
  const escaped = tag.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const match = block.match(new RegExp("<" + escaped + "(?:\\s[^>]*)?>([\\s\\S]*?)<\\/" + escaped + ">", "i"));
  return match ? decodeXml(match[1]) : "";
}

function decodeXml(value) {
  return String(value || "")
    .replace(/<!\[CDATA\[([\s\S]*?)\]\]>/g, "$1")
    .replace(/&amp;/g, "&").replace(/&lt;/g, "<")
    .replace(/&gt;/g, ">").replace(/&quot;/g, '"')
    .replace(/&#39;|&apos;/g, "'").replace(/&#(\d+);/g, (_, n) => String.fromCodePoint(Number(n)))
    .replace(/&#x([\da-f]+);/gi, (_, n) => String.fromCodePoint(parseInt(n, 16)));
}

function cleanText(value) {
  return decodeXml(value)
    .replace(/<br\s*\/?\s*>/gi, "\n")
    .replace(/<\/(p|div|li|blockquote)>/gi, "\n")
    .replace(/<[^>]+>/g, "")
    .replace(/[\t\u00a0 ]+/g, " ")
    .replace(/\n\s*\n+/g, "\n\n")
    .trim();
}

async function sendTelegram(env, text) {
  const response = await fetch(
    "https://api.telegram.org/bot" + env.TELEGRAM_BOT_TOKEN + "/sendMessage",
    {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({
        chat_id: env.TELEGRAM_CHAT_ID,
        text: text.slice(0, 4000),
        disable_web_page_preview: true,
      }),
    }
  );
  const result = await response.json();
  if (!response.ok || !result.ok) {
    throw new Error("Telegram sendMessage failed: " + JSON.stringify(result));
  }
}
