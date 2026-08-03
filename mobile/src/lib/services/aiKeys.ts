/**
 * AI 키 해석 — 파이썬 ai_service.get_ai_keys()와 같은 우선순위.
 *   ① auth.db app_settings (관리자 공용키)  ② 사용자 settings
 * 공용키가 있으면 그걸 쓰고, 없을 때만 본인 키로 폴백한다.
 */
import { getAuthDb } from '@/lib/db';
import { getAllSettings } from '@/lib/repositories/settings';

export interface AiKeys {
  anthropicKey: string;
  geminiKey: string;
}

function globalSetting(key: string): string {
  try {
    const row = getAuthDb()
      .prepare('SELECT value FROM app_settings WHERE key = ?')
      .get(key) as { value?: string } | undefined;
    return (row?.value || '').trim();
  } catch {
    return '';
  }
}

export function getAiKeys(username: string): AiKeys {
  let userSettings: Record<string, string> = {};
  try {
    userSettings = getAllSettings(username);
  } catch {
    userSettings = {};
  }
  const pick = (key: string) =>
    globalSetting(key) || (userSettings[key] || '').trim();
  return {
    anthropicKey: pick('anthropic_api_key'),
    geminiKey: pick('gemini_api_key'),
  };
}
