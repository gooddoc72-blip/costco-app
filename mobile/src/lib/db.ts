/**
 * SQLite 접근 layer — 기존 Streamlit 앱의 DB를 직접 read
 * /opt/costco-app/data/auth.db, admin.db, {username}.db
 */
import Database from 'better-sqlite3';
import path from 'path';

const DATA_DIR = process.env.COSTCO_DATA_DIR || '/opt/costco-app/data';

let _authDb: Database.Database | null = null;

export function getAuthDb(): Database.Database {
  if (!_authDb) {
    _authDb = new Database(path.join(DATA_DIR, 'auth.db'), { readonly: false, fileMustExist: true });
    _authDb.pragma('journal_mode = WAL');
  }
  return _authDb;
}

export function getUserDb(username: string): Database.Database {
  const db = new Database(path.join(DATA_DIR, `${username}.db`), { readonly: false, fileMustExist: true });
  db.pragma('journal_mode = WAL');
  return db;
}

export function getAdminDb(): Database.Database {
  const db = new Database(path.join(DATA_DIR, 'admin.db'), { readonly: false, fileMustExist: true });
  db.pragma('journal_mode = WAL');
  return db;
}
