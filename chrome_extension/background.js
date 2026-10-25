// 코코비즈 순위추적 — 배경 작업
//
// 흐름: 코코비즈에서 내 추적 목록을 받아온다 → 백그라운드 탭에서 네이버쇼핑을
// 검색해 순위를 읽는다 → 결과를 코코비즈로 보낸다.
//
// PC 한 대가 전체 사용자를 measure 하던 방식은 한 IP에 요청이 몰려 네이버가
// 내부 API를 418로 막았다(실측 2026-09-26: 대기·감속·계측제거 모두 무효).
// 각자 브라우저에서 자기 항목만 재면 요청이 분산되고 실제 사용자 세션이라
// 봇으로 몰릴 여지도 줄어든다.

const DEFAULTS = {
  base: "https://cocobiz.shop",
  sid: "",
  maxPage: 10,          // 10페이지 = 400위
  autoEnabled: false,   // 매일 자동 측정
  autoTime: "09:00",
  lastRunDate: "",      // 하루 두 번 도는 것을 막는다
  pageGapMs: 1800,      // 페이지 사이 간격 (공개 권장치 1~3초)
  kwGapMs: 3000,        // 키워드 사이 간격
  loadTimeoutMs: 25000
};

async function getConfig() {
  const got = await chrome.storage.local.get("config");
  return Object.assign({}, DEFAULTS, got.config || {});
}
export async function setConfig(patch) {
  const cur = await getConfig();
  const next = Object.assign({}, cur, patch);
  await chrome.storage.local.set({ config: next });
  return next;
}

// 코코비즈 sid 를 찾아온다.
//
// 확장프로그램에서 보내는 요청은 출처가 chrome-extension:// 이라, 코코비즈
// 로그인 쿠키가 SameSite 제한에 걸려 안 실린다(실측: 401). 그래서 sid 를
// 직접 찾아 Authorization 헤더로 보낸다. 찾는 순서는 손이 덜 가는 쪽부터:
//   ① 설정에 저장해 둔 값
//   ② 브라우저의 cocobiz.shop 쿠키
//   ③ 열려 있는 코코비즈 탭 주소의 ?sid=
async function sidCandidates(cfg) {
  const out = [];
  const add = v => { v = (v || "").trim(); if (v && out.indexOf(v) < 0) out.push(v); };

  // 열려 있는 코코비즈 탭들의 주소에 박힌 sid.
  // 탭이 여러 개면 예전 계정으로 열어둔 탭이 섞여 있을 수 있으므로 전부 모은다.
  try {
    const host = new URL(cfg.base).hostname;
    const tabs = await chrome.tabs.query({ url: `*://${host}/*` });
    for (const t of tabs) {
      const m = /[?&]sid=([^&#]+)/.exec(t.url || "");
      if (m) add(decodeURIComponent(m[1]));
    }
  } catch (e) {}

  try {
    const c = await chrome.cookies.get({ url: cfg.base, name: "sid" });
    if (c) add(c.value);
  } catch (e) {}

  add(cfg.sid);
  return out;
}

// 후보를 하나씩 /api/me 로 찔러 보고 실제로 통하는 것을 고른다.
//
// 주소만 보고 고르면 예전 계정 sid 를 집어 401 이 난다(실측: admin 으로 열어둔
// 탭이 남아 있어 costbusan 로그인인데도 계속 만료로 나왔다).
let _goodSid = null;
async function resolveSid(cfg, force) {
  if (_goodSid && !force) return _goodSid;
  const base = cfg.base.replace(/\/$/, "");
  for (const cand of await sidCandidates(cfg)) {
    try {
      const r = await fetch(base + "/api/me", {
        headers: { "Authorization": "Bearer " + cand, "Accept": "application/json" }
      });
      if (r.ok) {
        const me = await r.json().catch(() => ({}));
        _goodSid = cand;
        if (me && me.username) _lastUser = me.username;
        return cand;
      }
    } catch (e) {}
  }
  _goodSid = null;
  return "";
}
let _lastUser = "";
export function lastUser() { return _lastUser; }


function authHeaders(cfg) {
  const h = { "Accept": "application/json" };
  if (cfg.sid) h["Authorization"] = "Bearer " + cfg.sid;
  return h;
}

async function cocobiz(cfg, path, init, _retried) {
  const sid = await resolveSid(cfg, !!_retried);
  const headers = Object.assign({ "Accept": "application/json" },
                                (init && init.headers) || {});
  if (sid) headers["Authorization"] = "Bearer " + sid;
  const res = await fetch(cfg.base.replace(/\/$/, "") + path,
    Object.assign({}, init || {}, { credentials: "include", headers }));
  if (res.status === 401) {
    // 캐시해 둔 sid 가 그새 만료됐을 수 있다. 한 번은 다시 골라 본다.
    if (!_retried) {
      _goodSid = null;
      return cocobiz(cfg, path, init, true);
    }
    throw new Error("쓸 수 있는 코코비즈 sid가 없습니다. 코코비즈에 로그인한 뒤"
      + " 주소에 ?sid= 가 보이는 화면을 열어두고 다시 시도해 주세요.");
  }
  if (!res.ok) throw new Error("코코비즈 응답 오류 HTTP " + res.status);
  return res.json();
}

function searchUrl(keyword, page) {
  const q = encodeURIComponent(keyword);
  return "https://search.shopping.naver.com/search/all?query=" + q +
         "&pagingIndex=" + page + "&pagingSize=40&productSet=total&sort=rel";
}

// 판매처명 비교용 정규화: 공백·괄호·기호·대소문자 차이를 흡수한다.
function normStore(v) {
  return String(v || "").replace(/\s+/g, "").toLowerCase().replace(/[\[\]()\-_.·,]/g, "");
}

// 이 상품이 '내 상품'인가.
//
// 사용자가 넣는 번호는 세 가지인데 화면에서 구분이 안 되고 다들 'MID'라고 부른다.
//   채널상품번호  smartstore.naver.com/<스토어>/products/<이 번호>
//   상품 MID      검색결과 데이터의 id
//   카탈로그 MID  search.shopping.naver.com/catalog/<이 번호>
// 서로 다른 번호 체계라 겹칠 일이 없으므로 어느 것과 같아도 내 상품으로 본다.
//
// 광고 자리는 제외한다. 저장 칸이 rank_organic(자연순위)이라, 내 상품이 광고로도
// 떠 있으면 광고 위치가 순위로 기록되는 것을 막아야 한다.
function matches(target, p) {
  if (p.isAd) return false;
  if (target.no) {
    const want = String(target.no).trim();
    if (want && (want === p.mallProductId || want === p.nvMid)) return true;
  }
  if (target.store) {
    const w = normStore(target.store);
    if (w && w === normStore(p.mallName)) return true;
  }
  return false;
}

// 측정용 탭 하나를 만들어 계속 재사용한다.
//
// 페이지마다 탭을 새로 만들고 닫으면 그 비용이 매번 붙는다. 또 로딩이
// '완료'될 때까지 기다리면 이미지·광고·트래커까지 다 받고 나서야 읽는데,
// 우리가 필요한 데이터(__NEXT_DATA__)는 문서에 처음부터 들어 있다.
// 그래서 '요청한 페이지의 데이터가 보이는 순간' 바로 읽고 넘어간다.
let _tabId = null;

async function getTab() {
  if (_tabId != null) {
    try { await chrome.tabs.get(_tabId); return _tabId; }
    catch (e) { _tabId = null; }
  }
  const tab = await chrome.tabs.create({ url: "about:blank", active: false });
  _tabId = tab.id;
  return _tabId;
}

export async function closeTab() {
  if (_tabId == null) return;
  try { await chrome.tabs.remove(_tabId); } catch (e) {}
  _tabId = null;
}

async function readPage(keyword, page, cfg, log) {
  const tabId = await getTab();
  await chrome.tabs.update(tabId, { url: searchUrl(keyword, page) });

  // 준비될 때까지 짧게 반복해서 확인한다.
  // 데이터에 박힌 pagingIndex 가 요청한 페이지와 같아야 '그 페이지'다.
  const deadline = Date.now() + cfg.loadTimeoutMs;
  let last = null;
  while (Date.now() < deadline) {
    await sleep(250);
    let res;
    try {
      [res] = await chrome.scripting.executeScript({
        target: { tabId }, files: ["reader.js"]
      });
    } catch (e) { continue; }          // 아직 문서 교체 중
    const r = res && res.result;
    if (!r) continue;
    last = r;
    if (r.ok && (!r.page || r.page === page)) return r;   // 준비 완료
    if (r.reason && !/__NEXT_DATA__ 없음|상품 0개/.test(r.reason)) return r;  // 캡차·차단은 즉시 반환
  }
  return last;
}

function sleep(ms) { return new Promise(r => setTimeout(r, ms)); }

// ── 진행 상태 ───────────────────────────────────────────────────────
//
// 측정 루프를 페이지(content script) 안에서 돌리면 다른 메뉴로 이동하는 순간
// 루프가 죽어 결과 저장까지 못 간다. 그래서 루프는 여기서 돌리고, 팝업·패널은
// 시작 신호를 보내고 진행 상황만 받아 본다. 닫았다 열어도 이어서 보인다.
const STATE = { running: false, lines: [], startedAt: 0, stopRequested: false };

// ⏹ 중지 — 지금 보고 있는 페이지를 마치면 멈춘다. 그때까지 측정한 결과는 저장한다.
function stopRequested() { return !!STATE.stopRequested; }

function pushLine(line) {
  // "__DONE__" 은 UI에 끝났다고 알리는 신호일 뿐이라 기록에는 남기지 않는다.
  if (line === "__DONE__") {
    chrome.runtime.sendMessage({ type: "log", line }).catch(() => {});
    return;
  }
  STATE.lines.push(line);
  if (STATE.lines.length > 400) STATE.lines.splice(0, STATE.lines.length - 400);
  chrome.runtime.sendMessage({ type: "log", line }).catch(() => {});
}

function trim(s) { return String(s || "").slice(0, 28); }
function fmtSec(t) { return t >= 60 ? `${Math.floor(t / 60)}분 ${t % 60}초` : `${t}초`; }

function row(keyword, label, trackingId, rank, page) {
  const d = new Date(), p = x => String(x).padStart(2, "0");
  return {
    platform: "naver", search_keyword: keyword, product_keyword: label,
    tracking_id: trackingId, rank_organic: rank, page: page, device: "pc",
    checked_at: `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`
  };
}

// 측정이 끝나거나 실패하면 재사용하던 탭을 닫는다.
async function withTabCleanup(fn) {
  try { return await fn(); }
  finally { await closeTab(); }
}

// ── 진단: 이 브라우저에서 몇 페이지까지 읽히는지 ──────────────────────
export async function diagnose(keyword, maxPage, log, target) {
  log(`[확장 v${chrome.runtime.getManifest().version}]`);
  const cfg = await getConfig();
  const seen = [];
  let okUpTo = 0;
  let found = null;
  const hasTarget = !!(target && (target.no || target.store));

  for (let page = 1; page <= maxPage; page++) {
    if (stopRequested()) { log("⏹ 중지했습니다."); break; }
    if (page > 1) await sleep(cfg.pageGapMs);
    let r;
    try { r = await readPage(keyword, page, cfg, log); }
    catch (e) { log(`${page}페이지: 실패 — ${e.message}`); break; }
    if (!r || !r.ok) { log(`${page}페이지: 읽기 실패 — ${r ? r.reason : "응답 없음"}`); break; }

    const ids = r.products.map(p => p.mallProductId || p.nvMid).filter(Boolean);
    const prev = seen.length ? new Set(seen[seen.length - 1]) : null;
    const overlap = prev ? ids.filter(x => prev.has(x)).length : 0;
    const same = prev && ids.length && overlap >= ids.length * 0.7;

    // 광고도 rank 값을 갖지만 자연순위와 무관한 번호다(실측: 1페이지 광고 rank 42~116).
    // 섞으면 1페이지가 '1~54위'처럼 보여 2페이지와 겹쳐 보인다.
    const ranks = r.products.filter(p => !p.isAd).map(p => p.rank).filter(v => v != null);
    const ads = r.products.filter(p => p.isAd).length;
    const rankInfo = ranks.length
      ? ` · 순위 ${Math.min(...ranks)}~${Math.max(...ranks)}위`
      : " · ⚠️ 순위값 없음";
    log(`${page}페이지: 상품 ${r.products.length}개${rankInfo}` +
        (ads ? ` · 광고 ${ads}개` : "") +
        (prev ? ` · 겹침 ${overlap}/${ids.length}` : "") +
        (same ? "  ⚠️ 같은 페이지" : (prev ? "  ✅ 내용 바뀜" : "")));

    if (hasTarget && !found) {
      const idx = r.products.findIndex(pr => matches(target, pr));
      if (idx >= 0) {
        const hit = r.products[idx];
        found = { page, pos: idx + 1, hit };
        log(`  ✅ 내 상품을 찾았습니다 — ${page}페이지 ${idx + 1}번째` +
            (hit.rank != null ? ` · 네이버 순위 ${hit.rank}위` : "") +
            (hit.isAd ? " · 광고" : "") + (hit.mallName ? ` · ${hit.mallName}` : ""));
      }
    }
    if (same) break;
    seen.push(ids);
    okUpTo = page;
  }
  log("");
  log(`판정: ${okUpTo}페이지까지 실제로 확인됨 (상위 ${okUpTo * 40}위)`);
  if (hasTarget) {
    log(found
      ? `내 상품: ${found.page}페이지 ${found.pos}번째 (네이버 순위 ${found.hit.rank != null ? found.hit.rank + "위" : "표시 없음"})`
      : `내 상품: 확인한 범위(${okUpTo * 40}위)에서는 못 찾았습니다.`);
  }
  return okUpTo;
}

// ── 키워드 하나 측정 ────────────────────────────────────────────────
//
// 같은 키워드에 여러 상품이 걸려 있어도 검색은 한 번만 한다.
// 페이지가 실제로 넘어가지 않으면(내용이 그대로면) 거기서 멈춘다 —
// 못 본 페이지를 봤다고 기록하면 미노출이 거짓이 된다.
export async function measure(keyword, targets, maxPage, log) {
  const cfg = await getConfig();
  const pending = targets.slice();
  const found = [];
  let prevIds = null;
  let checkedTo = 0;
  let stop = "";

  for (let page = 1; page <= maxPage && pending.length; page++) {
    if (stopRequested()) { stop = "사용자 중지"; break; }
    if (page > 1) await sleep(cfg.pageGapMs);
    let r;
    try { r = await readPage(keyword, page, cfg, log); }
    catch (e) { stop = "로딩 실패(" + e.message + ")"; break; }
    if (!r || !r.ok) {
      const why = r ? r.reason : "응답 없음";
      stop = /보안 확인|자동 입력 방지/.test(why) ? "캡차"
           : /일시적으로 제한/.test(why) ? "접근 제한" : why;
      break;
    }
    if (r.page && r.page !== page) { stop = "페이지 불일치"; break; }

    const ids = r.products.map(p => p.mallProductId || p.nvMid).filter(Boolean);
    if (prevIds) {
      const prev = new Set(prevIds);
      const overlap = ids.filter(x => prev.has(x)).length;
      if (ids.length && overlap >= ids.length * 0.7) { stop = "페이지 미갱신"; break; }
    }
    prevIds = ids;
    checkedTo = page;

    for (let i = pending.length - 1; i >= 0; i--) {
      const t = pending[i];
      const idx = r.products.findIndex(p => matches(t, p));
      if (idx < 0) continue;
      const hit = r.products[idx];
      found.push({
        tracking_id: t.tracking_id, label: t.label,
        rank: hit.rank != null ? hit.rank : (page - 1) * 40 + idx + 1,
        page, isAd: hit.isAd, mallName: hit.mallName
      });
      pending.splice(i, 1);
    }
  }
  return {
    found,
    missing: pending.map(t => ({ tracking_id: t.tracking_id, label: t.label })),
    checkedTo, stop
  };
}

// ── 코코비즈 등록 목록 전체 측정 ────────────────────────────────────
export async function runAll(maxPage, log) {
  const cfg = await getConfig();
  log("코코비즈에서 추적 목록을 받아옵니다…");
  const data = await cocobiz(cfg, "/api/ranks/latest");
  if (lastUser()) log(`로그인 계정: ${lastUser()}`);
  const items = (data.items || []).filter(it => (it.platform || "naver") !== "coupang");
  if (!items.length) { log("등록된 네이버 추적 항목이 없습니다."); return { saved: 0 }; }

  const groups = new Map();
  for (const it of items) {
    const kw = (it.search_keyword || "").trim();
    if (!kw) continue;
    if (!groups.has(kw)) groups.set(kw, []);
    groups.get(kw).push(it);
  }
  log(`항목 ${items.length}개 · 검색어 ${groups.size}개 · 최대 ${maxPage}페이지(${maxPage * 40}위)`);

  const rows = [];
  let n = 0;
  const t0all = Date.now();
  const stops = {};
  const times = [];

  for (const [kw, targets] of groups) {
    if (stopRequested()) {
      log(`⏹ 중지 — ${n}/${groups.size} 검색어까지 측정했습니다. 측정한 것만 저장합니다.`);
      break;
    }
    n++;
    const t0 = Date.now();
    log(`[${n}/${groups.size}] ${kw}`);
    let res;
    try {
      res = await measure(kw, targets.map(t => ({
        tracking_id: t.id,
        no: String(t.naver_product_no || ""),
        store: t.store_name || "",
        label: t.product_keyword || kw
      })), maxPage, log);
    } catch (e) { log("   실패: " + e.message); continue; }

    times.push(Math.round((Date.now() - t0) / 1000));
    if (res.stop) {
      stops[res.stop] = (stops[res.stop] || 0) + 1;
      log(`   ⚠️ ${res.checkedTo}페이지에서 중단 — ${res.stop}`);
    }
    for (const f of res.found) {
      log(`   ✅ ${trim(f.label)} — ${f.rank}위`);
      rows.push(row(kw, f.label, f.tracking_id, f.rank, f.page));
    }
    // 순위가 없는 행의 page 에는 '몇 페이지까지 봤는가'를 담는다. 코코비즈는 이걸로
    // '400위 밖'처럼 표시한다. 한 페이지도 못 봤으면(1페이지부터 캡차 등) 미노출이
    // 아니라 측정 실패이므로 기록하지 않는다 — 기록하면 거짓 이탈 알림이 뜬다.
    for (const m2 of res.missing) {
      if (res.stop === "사용자 중지") {
        log(`   ⏭ ${trim(m2.label)} — 중지해서 끝까지 못 봄(기록 안 함)`);
        continue;
      }
      if (!res.checkedTo) {
        log(`   ⏭ ${trim(m2.label)} — 확인 못 함(기록 안 함)`);
        continue;
      }
      log(`   ❌ ${trim(m2.label)} — ${res.checkedTo * 40}위 밖`);
      rows.push(row(kw, m2.label, m2.tracking_id, null, res.checkedTo));
    }
    if (n < groups.size) await sleep(cfg.kwGapMs);
  }

  // 규모를 정하려면 실제 수치가 필요하다: 키워드당 시간, 캡차 빈도
  const total = Math.round((Date.now() - t0all) / 1000);
  const avg = times.length ? Math.round(times.reduce((a, b) => a + b, 0) / times.length) : 0;
  log("");
  log(`측정 요약 — 검색어 ${groups.size}개 / 총 ${fmtSec(total)} / 평균 ${avg}초`);
  const stopKeys = Object.keys(stops);
  log(stopKeys.length
    ? "중단 발생: " + stopKeys.map(k => `${k} ${stops[k]}회`).join(", ")
    : "중단 없이 전부 완주했습니다.");

  if (!rows.length) { log("저장할 결과가 없습니다."); return { saved: 0 }; }
  log(`코코비즈에 ${rows.length}건 저장 중…`);
  const saved = await cocobiz(cfg, "/api/ranks/ingest", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ rows, source: "chrome_extension" })
  });
  log(`저장 완료 — ${saved.saved || 0}건`);
  log("코코비즈 화면을 새로고침하면 표에 반영됩니다.");
  return saved;
}

// ── 매일 자동 측정 ──────────────────────────────────────────────────
//
// 크롬이 켜져 있어야 돈다. 꺼져 있던 시각은 건너뛰고, 다시 켜면 그때 한 번
// 실행된다. 하루에 두 번 돌지 않도록 마지막 실행 날짜를 남긴다.
const ALARM = "dailyRank";

function todayStr() {
  const d = new Date(), p = n => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`;
}

function nextAt(hhmm) {
  const [h, m] = String(hhmm || "09:00").split(":").map(Number);
  const t = new Date();
  t.setHours(h || 0, m || 0, 0, 0);
  if (t.getTime() <= Date.now()) t.setDate(t.getDate() + 1);
  return t.getTime();
}

export async function scheduleDaily(enabled, time, maxPage) {
  await setConfig({ autoEnabled: !!enabled, autoTime: time || "09:00", maxPage: maxPage || 10 });
  await chrome.alarms.clear(ALARM);
  if (enabled) await chrome.alarms.create(ALARM, { when: nextAt(time), periodInMinutes: 1440 });
  return nextRunInfo();
}

export async function nextRunInfo() {
  const cfg = await getConfig();
  if (!cfg.autoEnabled) return { enabled: false };
  const a = await chrome.alarms.get(ALARM);
  return { enabled: true, time: cfg.autoTime, when: a ? a.scheduledTime : null,
           lastRunDate: cfg.lastRunDate || "" };
}

chrome.alarms.onAlarm.addListener(async (a) => {
  if (a.name !== ALARM) return;
  const cfg = await getConfig();
  if (!cfg.autoEnabled || STATE.running) return;
  const today = todayStr();
  if (cfg.lastRunDate === today) return;

  STATE.running = true;
  STATE.stopRequested = false;
  STATE.lines = [];
  STATE.startedAt = Date.now();
  pushLine(`⏰ 예약 측정 시작 (${cfg.autoTime})`);
  try {
    await withTabCleanup(() => runAll(cfg.maxPage || 10, pushLine));
    await setConfig({ lastRunDate: today });
  } catch (e) {
    pushLine("자동 측정 오류: " + e.message);
  } finally {
    STATE.running = false;
  }
});

chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  if (msg && msg.type === "stop") {
    if (STATE.running && !STATE.stopRequested) {
      STATE.stopRequested = true;
      pushLine("⏹ 중지 요청 — 지금 보는 페이지를 마치고 멈춥니다…");
    }
    sendResponse({ ok: true, running: STATE.running });
    return false;
  }
  if (msg && msg.type === "getSid") {
    getConfig().then(resolveSid)
      .then(sid => sendResponse({ ok: true, sid }))
      .catch(e => sendResponse({ ok: false, error: e.message }));
    return true;
  }
  if (msg && msg.type === "setSid") {
    setConfig({ sid: (msg.sid || "").trim() })
      .then(() => sendResponse({ ok: true }))
      .catch(e => sendResponse({ ok: false, error: e.message }));
    return true;
  }
  if (msg && msg.type === "setDaily") {
    scheduleDaily(msg.enabled, msg.time, msg.maxPage)
      .then(info => sendResponse({ ok: true, info }))
      .catch(e => sendResponse({ ok: false, error: e.message }));
    return true;
  }
  if (msg && msg.type === "dailyInfo") {
    nextRunInfo().then(info => sendResponse({ ok: true, info }))
      .catch(e => sendResponse({ ok: false, error: e.message }));
    return true;
  }
  if (msg && msg.type === "status") {
    getConfig().then(cfg => sendResponse({
      ok: true, running: STATE.running, stopping: STATE.stopRequested, lines: STATE.lines,
      hasSid: !!cfg.sid, user: lastUser()
    })).catch(() => sendResponse({ ok: true, running: STATE.running, lines: STATE.lines }));
    return true;
  }
  if (msg && msg.type === "runAll") {
    if (STATE.running) {
      sendResponse({ ok: false, error: "이미 측정이 진행 중입니다." });
      return false;
    }
    STATE.running = true;
    STATE.stopRequested = false;
    STATE.lines = [];
    STATE.startedAt = Date.now();
    // 측정은 몇 분씩 걸린다. 응답 채널을 그동안 열어두면 팝업을 닫거나
    // 서비스 워커가 잠들 때 채널이 끊겨 "실행 오류"로 보인다(실측).
    // 그래서 시작만 알리고 바로 응답한다. 진행 상황은 log 메시지와
    // status 조회로 전달한다.
    withTabCleanup(() => runAll(msg.maxPage || 10, pushLine))
      .then(() => { setConfig({ lastRunDate: todayStr() }); })
      .catch(e => { pushLine("오류: " + e.message); })
      .finally(() => { STATE.running = false; pushLine("__DONE__"); });
    sendResponse({ ok: true, started: true });
    return false;
  }
  if (msg && msg.type === "measure") {
    withTabCleanup(() => measure(msg.keyword, msg.targets || [], msg.maxPage || 5, () => {}))
      .then(res => sendResponse(Object.assign({ ok: true }, res)))
      .catch(e => sendResponse({ ok: false, error: e.message }));
    return true;
  }
  if (msg && msg.type === "diagnose") {
    if (STATE.running) { sendResponse({ ok: false, error: "이미 진행 중입니다." }); return false; }
    STATE.running = true;
    STATE.stopRequested = false;
    STATE.lines = [];
    withTabCleanup(() => diagnose(msg.keyword, msg.maxPage || 5, pushLine, msg.target))
      .catch(e => { pushLine("오류: " + e.message); })
      .finally(() => { STATE.running = false; pushLine("__DONE__"); });
    sendResponse({ ok: true, started: true });
    return false;   // 비동기 응답
  }
});
