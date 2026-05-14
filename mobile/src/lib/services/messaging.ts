/**
 * Messaging Service — 카톡/텔레그램 자동 분기 발송.
 *
 * 정책:
 *  - msg.length > 2000 && 텔레그램 설정 있음 → 텔레그램 풀, 카톡엔 짧은 알림
 *  - 그 외 → 카톡 우선 (200자 청크 분할), 실패시 텔레그램 폴백
 *  - 카톡 토큰 갱신되면 settings에 자동 저장
 */
import { sendKakao } from '@/lib/api/kakao';
import { sendTelegram } from '@/lib/api/telegram';
import { loadUserSettings, saveUserSettings } from '@/lib/services/settings';

export interface SendResult {
  channel: 'kakao' | 'telegram' | null;
  ok: boolean;
  error?: string;
  detail?: string;
}

const LIMIT_FOR_TELEGRAM_PREFERENCE = 2000;

async function sendKakaoAndPersist(
  username: string,
  msg: string,
  s: Record<string, any>,
): Promise<SendResult> {
  const r = await sendKakao(s.kakaoAccessToken, msg, {
    restApiKey: s.kakaoApiKey,
    refreshToken: s.kakaoRefreshToken,
  });
  if (r.refreshedAccessToken) {
    saveUserSettings(username, {
      kakaoAccessToken: r.refreshedAccessToken,
      kakaoRefreshToken: r.refreshedRefreshToken ?? s.kakaoRefreshToken,
    });
  }
  return {
    channel: 'kakao',
    ok: r.ok,
    error: r.error,
    detail: `${r.sentChunks}/${r.totalChunks} 청크`,
  };
}

export async function sendShoppingMessage(username: string, msg: string, itemCount: number): Promise<SendResult> {
  const s = loadUserSettings(username);
  const hasKakao = !!s.kakaoAccessToken;
  const hasTelegram = !!s.telegramToken && !!s.telegramChatId;

  if (!hasKakao && !hasTelegram) {
    return { channel: null, ok: false, error: '카톡 또는 텔레그램이 설정되지 않았습니다.' };
  }

  // 긴 메시지 → 텔레그램으로 풀, 카톡엔 알림만
  if (msg.length > LIMIT_FOR_TELEGRAM_PREFERENCE && hasTelegram) {
    const tr = await sendTelegram(s.telegramToken, s.telegramChatId, msg);
    if (!tr.ok) return { channel: 'telegram', ok: false, error: tr.error };
    if (hasKakao) {
      const short = `🛒 코스트코 장보기 알림 발송됨\n총 ${itemCount}건 (${msg.length.toLocaleString()}자)\n자세한 내역은 텔레그램에서 확인하세요.`;
      const kr = await sendKakaoAndPersist(username, short, s);
      return {
        channel: 'telegram',
        ok: true,
        detail: `텔레그램 전체 + ${kr.ok ? '카톡 알림 OK' : `카톡 알림 실패: ${kr.error}`}`,
      };
    }
    return { channel: 'telegram', ok: true };
  }

  // 일반: 카톡 우선
  if (hasKakao) {
    const kr = await sendKakaoAndPersist(username, msg, s);
    if (kr.ok) return kr;
    if (hasTelegram) {
      const tr = await sendTelegram(s.telegramToken, s.telegramChatId, msg);
      return tr.ok
        ? { channel: 'telegram', ok: true, detail: `카톡 실패, 텔레그램 폴백 성공 (${kr.error})` }
        : { channel: 'telegram', ok: false, error: `카톡: ${kr.error} / 텔레그램: ${tr.error}` };
    }
    return kr;
  }

  // 카톡 미설정, 텔레그램만
  const tr = await sendTelegram(s.telegramToken, s.telegramChatId, msg);
  return { channel: 'telegram', ok: tr.ok, error: tr.error };
}
