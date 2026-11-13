/**
 * 로그아웃 — /m 로그인 쿠키를 지우고, Streamlit에서 ?sid=로 넘어와 이 탭에 남겨 둔
 * 로그인(sessionStorage 'billing_sid', lib/client/billing.ts)도 지운다.
 * 쿠키만 지우면 sid로 들어온 사용자는 로그아웃해도 화면이 계속 로그인 상태였다.
 */
import { apiPost } from '@/lib/api';

export async function logout() {
  try { await apiPost('/api/auth/logout', {}); } catch { /* 이미 끊긴 세션이어도 아래는 지운다 */ }
  try { sessionStorage.removeItem('billing_sid'); } catch { /* 저장소를 못 써도 쿠키는 지웠다 */ }
}
