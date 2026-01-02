// main.js – Cloudflare Worker style

const TELEGRAM_API = `https://api.telegram.org/bot${BOT_TOKEN}`;
const CHANNEL_USERNAME = CHANNEL_USERNAME || "@teazvpn";
const ADMIN_ID = parseInt(ADMIN_ID || "5542927340");
const TRON_ADDRESS = TRON_ADDRESS || "TJ4xrwKzKjk6FgKfuuqwah3Az5Ur22kJb";
const BANK_CARD = BANK_CARD || "6037 9975 9717 2684";

addEventListener("fetch", event => {
  event.respondWith(handleRequest(event.request));
});

async function handleRequest(req) {
  // نمونه ساده پاسخ
  if (req.method === "GET") {
    return new Response(`Bot is running! Channel: ${CHANNEL_USERNAME}`, {
      headers: { "Content-Type": "text/plain" },
    });
  }

  // POST برای دریافت پیام‌ها از تلگرام (webhook)
  if (req.method === "POST") {
    const body = await req.json();
    console.log("Incoming update:", body);

    // نمونه: ارسال پیام پاسخ ساده به تلگرام
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
