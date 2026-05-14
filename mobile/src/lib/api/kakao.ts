/**
 * 카카오톡 메모 발송 (text 200자 청크 분할).
 *
 * - 200자 단위로 잘라서 순차 발송 (청크 사이 0.5초 sleep).
 * - 401이면 refresh_token으로 access_token 갱신 후 재시도.
 *
 * 호출자는 새 토큰을 받으면 settings에 다시 저장해야 함.
 */

const MEMO_URL = 'https://kapi.kakao.com/v2/api/talk/memo/default/send';
const TOKEN_URL = 'https://kauth.kakao.com/oauth/token';

export interface KakaoSendResult {
  ok: boolean;
  error?: string;
  /** 토큰이 갱신됐다면 새 값을 돌려준다. settings에 저장 필요. */
  refreshedAccessToken?: string;
  refreshedRefreshToken?: string;
  sentChunks: number;
  totalChunks: number;
}

const CHUNK_SIZE = 200;
const SLEEP_MS = 500;

async function sleep(ms: number) { return new Promise(r => setTimeout(r, ms)); }

async function refreshKakaoToken(restApiKey: string, refreshToken: string)
: Promise<{ accessToken?: string; refreshToken?: string; error?: string }> {
  try {
    const body = new URLSearchParams({
      grant_type: 'refresh_token',
      client_id: restApiKey,
      refresh_token: refreshToken,
    });
    const res = await fetch(TOKEN_URL, {
      method: 'POST',
      headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
      body,
    });
    if (!res.ok) {
      const t = await res.text();
      return { error: `갱신 실패 (${res.status}): ${t.slice(0, 200)}` };
    }
    const j = await res.json();
    return {
      accessToken: j.access_token,
      refreshToken: j.refresh_token || refreshToken,
    };
  } catch (e: any) {
    return { error: String(e?.message || e) };
  }
}

async function postOneChunk(accessToken: string, chunk: string): Promise<Response> {
  const template = {
    object_type: 'text',
    text: chunk,
    link: {
      web_url: 'https://sell.smartstore.naver.com',
      mobile_web_url: 'https://sell.smartstore.naver.com',
    },
    button_title: '스마트스토어 바로가기',
  };
  return fetch(MEMO_URL, {
    method: 'POST',
    headers: {
      Authorization: `Bearer ${accessToken}`,
      'Content-Type': 'application/x-www-form-urlencoded',
    },
    body: new URLSearchParams({ template_object: JSON.stringify(template) }),
  });
}

export async function sendKakao(
  accessToken: string,
  msg: string,
  opt: { restApiKey?: string; refreshToken?: string } = {},
): Promise<KakaoSendResult> {
  const chunks = msg.length === 0
    ? ['']
    : Array.from({ length: Math.ceil(msg.length / CHUNK_SIZE) },
        (_, i) => msg.slice(i * CHUNK_SIZE, (i + 1) * CHUNK_SIZE));
  let token = accessToken;
  let refreshedAccess: string | undefined;
  let refreshedRefresh: string | undefined;
  let sent = 0;

  for (let i = 0; i < chunks.length; i++) {
    if (i > 0) await sleep(SLEEP_MS);
    let res = await postOneChunk(token, chunks[i]);
    if (res.status === 401 && opt.refreshToken && opt.restApiKey) {
      const r = await refreshKakaoToken(opt.restApiKey, opt.refreshToken);
      if (r.error || !r.accessToken) {
        return {
          ok: false,
          error: `토큰 갱신 실패 (${i + 1}/${chunks.length} 발송 중): ${r.error || 'unknown'}`,
          sentChunks: sent, totalChunks: chunks.length,
        };
      }
      token = r.accessToken;
      refreshedAccess = r.accessToken;
      refreshedRefresh = r.refreshToken;
      res = await postOneChunk(token, chunks[i]);
    }
    if (!res.ok) {
      const body = await res.text();
      return {
        ok: false,
        error: `카카오 발송 실패 (성공 ${sent}/${chunks.length}, 청크 ${i + 1} 실패 ${res.status}): ${body.slice(0, 120)}`,
        refreshedAccessToken: refreshedAccess,
        refreshedRefreshToken: refreshedRefresh,
        sentChunks: sent, totalChunks: chunks.length,
      };
    }
    sent++;
  }

  return {
    ok: true,
    refreshedAccessToken: refreshedAccess,
    refreshedRefreshToken: refreshedRefresh,
    sentChunks: sent,
    totalChunks: chunks.length,
  };
}
