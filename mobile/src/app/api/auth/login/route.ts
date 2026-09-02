import { NextRequest, NextResponse } from 'next/server';
import bcrypt from 'bcrypt';
import crypto from 'crypto';
import { getAuthDb } from '@/lib/db';
import { createSession, SESSION_COOKIE } from '@/lib/session';

export async function POST(req: NextRequest) {
  const { username, password, remember = true } = await req.json();
  if (!username || !password) {
    return NextResponse.json({ error: '아이디/비밀번호를 입력하세요' }, { status: 400 });
  }

  const db = getAuthDb();
  // 아이디는 대소문자·앞뒤공백을 무시해서 찾는다 (Streamlit 쪽 check_login과 동일 규칙).
  // 정확히 일치하는 행이 있으면 그쪽을 우선한다.
  const uname = String(username).trim();
  const row = db
    .prepare(
      'SELECT username, password, display_name, is_admin, status FROM users ' +
        'WHERE username = ? COLLATE NOCASE ORDER BY (username = ?) DESC LIMIT 1'
    )
    .get(uname, uname) as
    | {
        username: string;
        password: string;
        display_name: string;
        is_admin: number;
        status: string;
      }
    | undefined;

  if (!row) {
    return NextResponse.json({ error: '로그인 실패' }, { status: 401 });
  }
  // 파이썬 쪽이 쓰는 status 값은 active / pending / rejected 뿐이다.
  // 예전엔 존재하지도 않는 'approved'와 비교해서 모든 계정이 403으로 막혀 있었다.
  const status = row.status || 'active';
  if (status !== 'active') {
    return NextResponse.json(
      {
        error:
          status === 'rejected'
            ? '가입이 거절된 계정입니다'
            : '관리자 승인 대기 중인 계정입니다',
      },
      { status: 403 }
    );
  }

  // bcrypt ($2a$ / $2b$) 또는 sha256 해시 호환
  let pwOk = false;
  if (row.password.startsWith('$2a$') || row.password.startsWith('$2b$')) {
    pwOk = await bcrypt.compare(password, row.password);
  } else {
    const sha = crypto.createHash('sha256').update(password).digest('hex');
    pwOk = sha === row.password;
  }
  if (!pwOk) {
    return NextResponse.json({ error: '로그인 실패' }, { status: 401 });
  }

  // 세션에는 반드시 DB에 저장된 원본 아이디를 넣는다.
  // 입력값(sueb)을 그대로 쓰면 이후 조회가 다른 사용자 DB를 가리킨다.
  const token = createSession(row.username, remember ? 30 : 1);

  const res = NextResponse.json({
    username: row.username,
    display_name: row.display_name,
    is_admin: !!row.is_admin,
  });
  res.cookies.set(SESSION_COOKIE, token, {
    httpOnly: true,
    secure: true,
    sameSite: 'lax',
    maxAge: (remember ? 30 : 1) * 86400,
    path: '/',
  });
  return res;
}
