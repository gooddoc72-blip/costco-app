// 코코비즈 화면에 '순위 측정' 버튼을 띄운다.
//
// 코코비즈의 기존 [전체 순위조회]는 서버가 직접 크롤링하는 방식인데, 서버 IP로는
// 네이버가 막는다. 그래서 측정은 이 브라우저에서 하고 결과만 코코비즈에 저장한다.
// 기존 버튼을 가로채지 않고 별도 버튼을 띄우는 이유: 화면이 Streamlit이라
// 다시 그려질 때마다 DOM이 갈아엎혀 가로채기가 깨진다.

const PANEL_ID = "cocobiz-rank-ext-panel";
const NL_CHAR = String.fromCharCode(10);
const DEFAULT_PAGES = 10;   // 10페이지 = 400위

// 이 스크립트가 실제로 붙었는지 콘솔에서 확인할 수 있게 한다.
// 패널이 안 보일 때 "주입 자체가 안 된 것"인지 "패널만 안 그려진 것"인지 갈린다.
try {
  console.log("[코코비즈 순위추적] content script 로드됨 v" +
              chrome.runtime.getManifest().version +
              " / frame=" + (window.top === window ? "top" : "iframe") +
              " / " + location.href.slice(0, 80));
} catch (e) {}

function el(tag, style, text) {
  const n = document.createElement(tag);
  if (style) n.setAttribute("style", style);
  if (text != null) n.textContent = text;
  return n;
}

// 패널 상태: "open"(펼침) | "min"(제목줄만) | "hidden"(오른쪽 아래 작은 버튼만).
// 코코비즈 모든 화면에 뜨므로 표를 가리지 않게 줄일 수 있어야 한다.
// 다른 메뉴로 가도 유지되도록 저장해 둔다.
let _panelState = "open";
let _running = false;
const STATE_KEY = "panelState";

function applyPanelState() {
  const box = document.getElementById(PANEL_ID);
  if (!box) return;
  const card = box.querySelector("[data-cbx=card]");
  const body = box.querySelector("[data-cbx=body]");
  const pill = box.querySelector("[data-cbx=pill]");
  const minBtn = box.querySelector("[data-cbx=min]");
  card.style.display = _panelState === "hidden" ? "none" : "block";
  body.style.display = _panelState === "open" ? "block" : "none";
  pill.style.display = _panelState === "hidden" ? "inline-block" : "none";
  minBtn.textContent = _panelState === "min" ? "▢" : "–";
  minBtn.title = _panelState === "min" ? "펼치기" : "줄이기";
  pill.textContent = _running ? "순위 측정 중…" : "순위 측정";
  const title = box.querySelector("[data-cbx=title]");
  title.textContent = _running ? "순위 측정 중…" : "확장프로그램 순위 측정";
}

function setPanelState(v) {
  _panelState = v;
  applyPanelState();
  try { chrome.storage.local.set({ [STATE_KEY]: v }); } catch (e) {}
}

function setRunning(v, stopping) {
  _running = v;
  const btn = document.getElementById("cbx-run");
  if (btn) { btn.disabled = v; btn.textContent = v ? "측정 중…" : "등록된 키워드 순위 측정"; }
  const stopBtn = document.getElementById("cbx-stop");
  if (stopBtn) {
    stopBtn.style.display = v ? "block" : "none";
    stopBtn.disabled = !!(v && stopping);
    stopBtn.textContent = v && stopping ? "중지하는 중… (보던 페이지를 마치는 중)"
                                        : "⏹ 중지 (측정한 것까지 저장)";
  }
  applyPanelState();
}

function pagesHint(n) { return `(${n} = ${n * 40}위까지)`; }

function ensurePanel() {
  if (document.getElementById(PANEL_ID)) return;
  const box = el("div", `position:fixed; right:16px; bottom:16px; z-index:2147483647;
    font:13px/1.5 "Malgun Gothic",system-ui,sans-serif;`);
  box.id = PANEL_ID;

  const card = el("div", `width:340px; background:#fff; border:1px solid #d0d0d0; border-radius:8px;
    box-shadow:0 4px 16px rgba(0,0,0,.18);`);
  card.dataset.cbx = "card";

  const head = el("div", `display:flex; align-items:center; gap:4px; padding:8px 8px 8px 12px;
    border-bottom:1px solid #eee; font-weight:700;`);
  const title = el("span", "flex:1;", "확장프로그램 순위 측정");
  title.dataset.cbx = "title";
  const iconBtn = `width:26px; height:26px; padding:0; cursor:pointer; background:#fff;
    border:1px solid #ddd; border-radius:4px; font-size:14px; line-height:1; color:#555;`;
  const minBtn = el("button", iconBtn, "–");
  minBtn.dataset.cbx = "min";
  const hideBtn = el("button", iconBtn, "×");
  hideBtn.title = "숨기기 (오른쪽 아래 작은 버튼으로 다시 열 수 있습니다)";
  head.appendChild(title); head.appendChild(minBtn); head.appendChild(hideBtn);

  const body = el("div", "padding:10px 12px;");
  body.dataset.cbx = "body";
  const btn = el("button", `width:100%; padding:8px; cursor:pointer; font-size:13px;
    background:#e8342e; color:#fff; border:0; border-radius:5px; font-weight:700;`,
    "등록된 키워드 순위 측정");
  btn.id = "cbx-run";
  const opt = el("div", "margin-top:8px; color:#555;");
  opt.appendChild(document.createTextNode("확인 페이지 수 "));
  const pages = el("input", "width:56px; padding:2px 4px;");
  Object.assign(pages, { id: "cbx-pages", type: "number", min: 1, max: 20, value: DEFAULT_PAGES });
  const hint = el("span", "color:#888; margin-left:4px;", pagesHint(DEFAULT_PAGES));
  pages.addEventListener("input", () => { hint.textContent = pagesHint(Number(pages.value) || 0); });
  opt.appendChild(pages); opt.appendChild(hint);
  const log = el("div", `margin-top:10px; padding:8px; background:#f7f7f7; border:1px solid #e3e3e3;
    border-radius:4px; height:150px; overflow-y:auto; white-space:pre-wrap;
    font-family:Consolas,monospace; font-size:11.5px;`, "대기 중…");
  log.id = "cbx-log";
  const stopBtn = el("button", `display:none; width:100%; margin-top:6px; padding:7px; cursor:pointer;
    font-size:13px; background:#555; color:#fff; border:0; border-radius:5px; font-weight:700;`,
    "⏹ 중지 (측정한 것까지 저장)");
  stopBtn.id = "cbx-stop";
  stopBtn.addEventListener("click", async () => {
    stopBtn.disabled = true; stopBtn.textContent = "중지하는 중…";
    try { await chrome.runtime.sendMessage({ type: "stop" }); } catch (e) {}
  });
  body.appendChild(btn); body.appendChild(stopBtn); body.appendChild(opt); body.appendChild(log);
  card.appendChild(head); card.appendChild(body);

  const pill = el("button", `display:none; padding:6px 12px; cursor:pointer; font-size:12px;
    font-weight:700; color:#fff; background:#e8342e; border:0; border-radius:16px;
    box-shadow:0 2px 8px rgba(0,0,0,.2);`, "순위 측정");
  pill.dataset.cbx = "pill";
  pill.title = "순위 측정 창 열기";

  box.appendChild(card); box.appendChild(pill);
  document.body.appendChild(box);

  btn.addEventListener("click", () => run(btn, log));
  minBtn.addEventListener("click", () => setPanelState(_panelState === "min" ? "open" : "min"));
  hideBtn.addEventListener("click", () => setPanelState("hidden"));
  pill.addEventListener("click", () => setPanelState("open"));
  applyPanelState();
}

function write(log, line) {
  log.textContent = (log.textContent === "대기 중…" ? "" : log.textContent + "\n") + line;
  log.scrollTop = log.scrollHeight;
}

// 코코비즈는 로그인을 주소의 ?sid= 로 유지한다(쿠키가 아니다).
// 그래서 같은 출처에서 부르더라도 sid 를 직접 실어야 한다.
let _sid = null;
// 이 화면 주소에 박힌 sid 만 — 없으면 "" (확장이 기억한 다른 계정 sid 를 섞지 않는다)
function pageSid() {
  const m = /[?&]sid=([^&#]+)/.exec(location.search) ||
            /[?&]sid=([^&#]+)/.exec(document.referrer || "");
  return m ? decodeURIComponent(m[1]) : "";
}
async function getSid() {
  if (_sid !== null) return _sid;
  const m = /[?&]sid=([^&#]+)/.exec(location.search) ||
            /[?&]sid=([^&#]+)/.exec(document.referrer || "");
  if (m) {
    _sid = decodeURIComponent(m[1]);
    // 한 번 본 sid 는 저장해 둔다.
    // Streamlit 은 하위 페이지로 이동할 때 주소에서 쿼리를 떨군다
    // (/app/?sid=... → /app/run_rank_check/). 그래서 순위체크 화면에서는
    // 주소만 봐서는 sid 를 알 수 없다(실측).
    try { chrome.runtime.sendMessage({ type: "setSid", sid: _sid }); } catch (e) {}
    return _sid;
  }
  try {
    const res = await chrome.runtime.sendMessage({ type: "getSid" });
    _sid = (res && res.sid) || "";
  } catch (e) { _sid = ""; }
  return _sid;
}

async function api(path, init) {
  const sid = await getSid();
  const headers = Object.assign({ "Accept": "application/json" }, (init && init.headers) || {});
  if (sid) headers["Authorization"] = "Bearer " + sid;
  const res = await fetch(path, Object.assign({}, init || {}, { credentials: "include", headers }));
  if (res.status === 401) {
    throw new Error(sid
      ? "코코비즈 세션이 만료됐습니다. 다시 로그인해 주세요."
      : "sid를 찾지 못했습니다. 코코비즈 주소에 ?sid= 가 있는지 확인해 주세요.");
  }
  if (!res.ok) throw new Error("HTTP " + res.status);
  return res.json();
}

async function run(btn, log) {
  // 루프는 서비스 워커에서 돈다. 여기서 돌리면 다른 메뉴로 이동하는 순간
  // 중단되고 결과 저장까지 못 간다.
  const maxPage = Number(document.getElementById("cbx-pages").value) || DEFAULT_PAGES;
  if (_panelState !== "open") setPanelState("open");
  setRunning(true);
  log.textContent = `등록된 키워드를 ${maxPage}페이지(${maxPage * 40}위)까지 측정합니다.`;
  write(log, "다른 메뉴로 이동해도 계속 진행됩니다.");
  try {
    const res = await chrome.runtime.sendMessage({ type: "runAll", maxPage, sid: pageSid() });
    if (res && !res.ok) { write(log, "실패: " + res.error); setRunning(false); }
  } catch (e) {
    write(log, "실행 오류: " + e.message);
    setRunning(false);
  }
}

// 다른 메뉴로 갔다 와도 진행 상황이 이어 보이게 한다.
async function restoreState() {
  try {
    const st = await chrome.runtime.sendMessage({ type: "status" });
    if (!st || !st.ok) return;
    const log = document.getElementById("cbx-log");
    if (!log) return;
    if (st.lines && st.lines.length) log.textContent = st.lines.join(NL_CHAR);
    setRunning(!!st.running);
  } catch (e) {}
}

chrome.runtime.onMessage.addListener((msg) => {
  if (!msg || msg.type !== "log") return;
  const log = document.getElementById("cbx-log");
  if (msg.line === "__DONE__") { setRunning(false); return; }
  if (log) write(log, msg.line);
});

// 진행 로그·끝남 신호는 확장 팝업에만 바로 전달되고 이 화면(패널)에는 오지 않는다.
// 그래서 패널이 '측정 중… / 중지하는 중…'에 멈춰 있었다. 2초마다 실제 상태를
// 직접 물어 로그와 버튼을 맞춘다.
let _lastLineCount = -1;
setInterval(async () => {
  if (!document.getElementById(PANEL_ID)) return;
  try {
    const st = await chrome.runtime.sendMessage({ type: "status" });
    if (!st || !st.ok) return;
    const log = document.getElementById("cbx-log");
    const lines = st.lines || [];
    if (log && lines.length && lines.length !== _lastLineCount) {
      log.textContent = lines.join(NL_CHAR);
      log.scrollTop = log.scrollHeight;
      _lastLineCount = lines.length;
    }
    if (!!st.running !== _running || st.running) setRunning(!!st.running, !!st.stopping);
  } catch (e) { /* 확장이 새로 고쳐지는 중 — 다음 번에 다시 */ }
}, 2000);


function nowStamp() {
  const d = new Date(), p = n => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
}

// 코코비즈의 기존 [전체 순위조회]를 가로챈다.
//
// 그 버튼은 서버가 직접 네이버를 크롤링하게 하는데, 서버 IP는 막혀 있어
// "자동 수집 차단"만 돌아온다. 사용자는 그 버튼을 누르는 게 자연스러우므로
// 서버로 요청이 가기 전에 잡아서 이 브라우저로 측정한다.
//
// 리스너를 document 에 캡처 단계로 단다: Streamlit 이 화면을 다시 그려도
// 버튼에 직접 건 리스너와 달리 살아남는다.
const INTERCEPT_LABELS = ["전체 순위조회", "선택 체크"];

function labelOf(node) {
  const btn = node.closest ? node.closest("button") : null;
  if (!btn) return null;
  const t = (btn.textContent || "").replace(/\s+/g, " ").trim();
  for (const lab of INTERCEPT_LABELS) {
    if (t.indexOf(lab) >= 0) return lab;
  }
  return null;
}

document.addEventListener("click", (ev) => {
  const lab = labelOf(ev.target);
  if (!lab) return;
  ev.preventDefault();
  ev.stopPropagation();
  ev.stopImmediatePropagation();
  ensurePanel();
  const log = document.getElementById("cbx-log");
  const btn = document.getElementById("cbx-run");
  write(log, `[${lab}] 를 확장프로그램이 대신 처리합니다 (서버 크롤링은 막혀 있습니다).`);
  run(btn, log);
}, true);   // 캡처 단계 — Streamlit 핸들러보다 먼저 받는다

// Streamlit 은 화면을 자주 다시 그린다. 패널이 사라지면 다시 붙인다.
// 저장된 접힘 상태를 먼저 읽고 나서 그린다(펼쳤다 접히는 깜빡임 방지).
chrome.storage.local.get(STATE_KEY).then(got => {
  if (got && ["open", "min", "hidden"].indexOf(got[STATE_KEY]) >= 0) _panelState = got[STATE_KEY];
}).catch(() => {}).finally(() => {
  ensurePanel();
  restoreState();
  setInterval(() => { ensurePanel(); }, 2000);
});
// 다른 탭에서 접거나 펼치면 여기도 따라간다.
chrome.storage.onChanged.addListener((ch, area) => {
  if (area === "local" && ch[STATE_KEY] && ch[STATE_KEY].newValue !== _panelState) {
    _panelState = ch[STATE_KEY].newValue;
    applyPanelState();
  }
});
