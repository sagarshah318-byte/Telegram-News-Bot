// Cloudflare Worker: Telegram webhook -> GitHub repository_dispatch.
// Configure Worker secrets: TELEGRAM_BOT_TOKEN, TELEGRAM_WEBHOOK_SECRET, GITHUB_TOKEN.
// Configure vars: GITHUB_OWNER=sagarshah318-byte, GITHUB_REPO=Telegram-News-Bot, GITHUB_REF=main.
export default {
  async fetch(request, env) {
    if (request.method !== "POST") return new Response("OK");
    const supplied = request.headers.get("X-Telegram-Bot-Api-Secret-Token") || "";
    if (!constantTimeEqual(supplied, env.TELEGRAM_WEBHOOK_SECRET || "")) {
      return new Response("Unauthorized", { status: 401 });
    }
    const update = await request.json();
    const msg = update.message || update.edited_message;
    if (!msg || !msg.text || !msg.chat) return new Response("Ignored");
    const text = msg.text.trim();
    const isGroup = msg.chat.type === "group" || msg.chat.type === "supergroup";
    const botUsername = (env.BOT_USERNAME || "").toLowerCase();
    let query = text;
    if (isGroup) {
      if (/^\/mf(?:@\w+)?\s+/i.test(query)) query = query.replace(/^\/mf(?:@\w+)?\s+/i, "");
      else if (botUsername && query.toLowerCase().includes("@" + botUsername)) query = query.replace(new RegExp("@" + botUsername, "ig"), "").trim();
      else return new Response("Not addressed to bot");
    } else if (/^\/start|^\/help/i.test(query)) {
      await telegramSend(env, msg.chat.id, "Send a mutual fund scheme name. I search Regular Plan – Growth only. Example: HDFC Mid Cap Fund");
      return new Response("OK");
    }
    query = query.replace(/^\/mf\s+/i, "").trim();
    if (!query || query.length > 180) return new Response("Invalid query", { status: 400 });
    const owner = env.GITHUB_OWNER || "sagarshah318-byte";
    const repo = env.GITHUB_REPO || "Telegram-News-Bot";
    const response = await fetch(`https://api.github.com/repos/${owner}/${repo}/dispatches`, {
      method: "POST",
      headers: { "Authorization": `Bearer ${env.GITHUB_TOKEN}`, "Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28", "Content-Type": "application/json" },
      body: JSON.stringify({ event_type: "mf_lookup", client_payload: { scheme: query, chat_id: String(msg.chat.id), request_id: String(update.update_id) } })
    });
    if (!response.ok) {
      const detail = await response.text();
      console.error("MF GitHub dispatch rejected", response.status, detail.slice(0, 800));
      await telegramSend(env, msg.chat.id, "Sorry, I couldn't start the fund lookup. GitHub returned an error (" + response.status + "). Please try again shortly.");
      // Acknowledge Telegram's webhook delivery so it does not retry the same failed update.
      return new Response("Dispatch failed; user notified", { status: 200 });
    }
    return new Response("Accepted", { status: 202 });
  }
};

async function telegramSend(env, chatId, text) {
  await fetch(`https://api.telegram.org/bot${env.TELEGRAM_BOT_TOKEN}/sendMessage`, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ chat_id: chatId, text })
  });
}
function constantTimeEqual(a, b) {
  if (a.length !== b.length) return false;
  let out = 0;
  for (let i = 0; i < a.length; i++) out |= a.charCodeAt(i) ^ b.charCodeAt(i);
  return out === 0;
}
