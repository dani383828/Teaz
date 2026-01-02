// main.js

// مستقیم از binding wrangler.toml
const TELEGRAM_API = `https://api.telegram.org/bot${BOT_TOKEN}`;
const CHANNEL = CHANNEL_USERNAME;
const ADMIN = parseInt(ADMIN_ID);
const TRON = TRON_ADDRESS;
const CARD = BANK_CARD;

addEventListener("fetch", event => {
  event.respondWith(handleRequest(event.request));
});

async function handleRequest(req) {
  if (req.method === "GET") {
    return new Response(`Bot is running on ${CHANNEL}`, { headers: { "Content-Type": "text/plain" } });
  }

  if (req.method === "POST") {
    const body = await req.json();
    console.log("Incoming update:", body);

    if (body.message && body.message.text) {
      const chatId = body.message.chat.id;
      const text = `پیام شما دریافت شد: ${body.message.text}`;

      await fetch(`${TELEGRAM_API}/sendMessage`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ chat_id: chatId, text }),
      });
    }

    return new Response("ok");
  }

  return new Response("Method not allowed", { status: 405 });
}
