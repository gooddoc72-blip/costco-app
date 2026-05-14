/**
 * Telegram bot API — sendMessage.
 */
export interface TelegramSendResult { ok: boolean; error?: string }

export async function sendTelegram(
  botToken: string, chatId: string, msg: string,
): Promise<TelegramSendResult> {
  try {
    const url = `https://api.telegram.org/bot${botToken}/sendMessage`;
    const res = await fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ chat_id: chatId, text: msg }),
    });
    if (!res.ok) {
      const t = await res.text();
      return { ok: false, error: `텔레그램 ${res.status}: ${t.slice(0, 200)}` };
    }
    return { ok: true };
  } catch (e: any) {
    return { ok: false, error: String(e?.message || e) };
  }
}
