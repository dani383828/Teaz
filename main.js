// Cloudflare Worker bindings
const TELEGRAM_API = `https://api.telegram.org/bot${BOT_TOKEN}`;
const CHANNEL = CHANNEL_USERNAME;
const ADMIN = parseInt(ADMIN_ID);
const TRON = TRON_ADDRESS;
const CARD = BANK_CARD;

// Event listener برای Worker
addEventListener('fetch', event => {
  event.respondWith(handleRequest(event.request))
})

async function handleRequest(request) {
  const url = new URL(request.url);
  
  // اگر وبهوک تلگرام اینجا بخواد کار کنه
  if (url.pathname === "/webhook" && request.method === "POST") {
    const body = await request.json();
    // نمونه پاسخ: فقط نام کاربری بات و ادمین
    console.log("Update received:", body);

    // می‌تونی اینجا پیام به کانال یا ادمین بفرستی
    await fetch(`${TELEGRAM_API}/sendMessage`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        chat_id: ADMIN,
        text: `New update received:\n${JSON.stringify(body)}`,
      }),
    });

    return new Response("OK", { status: 200 });
  }

  // پاسخ پیشفرض
  return new Response(`Bot username: ${BOT_USERNAME}, Admin: ${ADMIN}`, {
    headers: { 'Content-Type': 'text/plain' },
  });
}
