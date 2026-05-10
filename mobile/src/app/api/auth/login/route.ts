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
  const row = db
    .prepare('SELECT password, display_name, is_admin, status FROM users WHERE username = ?')
    .get(username) as
    | { password: string; display_name: string; is_admin: number; status: string }
    | undefined;

  if (!row) {
    return NextResponse.json({ error: '로그인 실패' }, { status: 401 });
  }
  if (row.status && row.status !== 'approved') {
    return NextResponse.json({ error: '승인 대기 중인 계정입니다' }, { status: 403 });
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

  const token = createSession(username, remember ? 30 : 1);

  const res = NextResponse.json({
    username,
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
