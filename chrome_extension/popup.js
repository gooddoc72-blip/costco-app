const NL_CHAR = String.fromCharCode(10);
const logEl = document.getElementById("log");
function write(line) {
  logEl.textContent += (logEl.textContent === "대기 중…" ? "" : "\n") + line;
  if (logEl.textContent.startsWith("대기 중…\n")) logEl.textContent = logEl.textContent.slice(7);
  logEl.scrollTop = logEl.scrollHeight;
}
chrome.runtime.onMessage.addListener((msg) => {
  if (!msg || msg.type !== "log") return;
  if (msg.line === "__DONE__") { setBusy(false); return; }
  write(msg.line);
});

// 측정은 서비스 워커에서 돈다. 팝업은 시작 신호만 보내고 로그를 받아 보므로,
// 버튼 상태는 실제 진행 여부(status)에 맞춰 둔다.
function setBusy(busy) {
  for (const id of ["runAll", "run"]) {
    const b = document.getElementById(id);
    if (!b) continue;
    b.disabled = busy;
  }
  const b = document.getElementById("runAll");
  if (b) b.textContent = busy ? "측정 중… (닫아도 계속됩니다)" : "전체 측정 시작";
  const s = document.getElementById("stop");
  if (s) s.style.display = busy ? "inline-block" : "none";
}

document.getElementById("stop").addEventListener("click", async () => {
  const s = document.getElementById("stop");
  s.disabled = true; s.textContent = "중지하는 중…";
  try { await chrome.runtime.sendMessage({ type: "stop" }); } catch (e) {}
});

// 팝업이 열려 있는 동안 주기적으로 실제 상태를 확인한다(로그를 놓쳤을 때 대비).
setInterval(async () => {
  try {
    const st = await chrome.runtime.sendMessage({ type: "status" });
    if (st && st.ok) {
      setBusy(!!st.running);
      const s = document.getElementById("stop");
      if (s && st.running) { s.disabled = !!st.stopping; s.textContent = st.stopping ? "중지하는 중…" : "⏹ 중지"; }
    }
  } catch (e) {}
}, 2000);
document.getElementById("run").addEventListener("click", async () => {
  const kw = document.getElementById("kw").value.trim();
  const maxPage = Number(document.getElementById("maxPage").value) || 10;
  const target = {
    no: document.getElementById("no").value.trim(),
    store: document.getElementById("store").value.trim()
  };
  if (!kw) { write("검색어를 입력해 주세요."); return; }
  logEl.textContent = `'${kw}' 로 1~${maxPage}페이지를 확인합니다. 백그라운드 탭이 잠깐씩 열립니다…`;
  try {
    const res = await chrome.runtime.sendMessage({ type: "diagnose", keyword: kw, maxPage, target });
    if (res && !res.ok) write("실패: " + res.error);
    else setBusy(true);
  } catch (e) {
    write("실행 오류: " + e.message);
  }
});

document.getElementById("runAll").addEventListener("click", async () => {
  const maxPage = Number(document.getElementById("maxPage").value) || 10;
  logEl.textContent = `코코비즈 등록 목록을 ${maxPage}페이지까지 측정합니다. 이 창을 닫지 마세요…`;
  try {
    const res = await chrome.runtime.sendMessage({ type: "runAll", maxPage });
    if (res && !res.ok) write("실패: " + res.error);
    else setBusy(true);
  } catch (e) {
    write("실행 오류: " + e.message);
  }
});

document.getElementById("saveSid").addEventListener("click", async () => {
  const sid = document.getElementById("sid").value.trim();
  try {
    const res = await chrome.runtime.sendMessage({ type: "setSid", sid });
    write(res && res.ok
      ? (sid ? "sid를 저장했습니다." : "sid를 비웠습니다 — 자동으로 찾습니다.")
      : "sid 저장 실패");
  } catch (e) { write("sid 저장 오류: " + e.message); }
});

// 팝업은 열 때마다 새로 그려진다. 측정은 서비스 워커에서 계속 돌고 있으므로
// 지금까지의 진행 상황을 받아 와 이어서 보여준다.
(async function restore() {
  try {
    const st = await chrome.runtime.sendMessage({ type: "status" });
    if (!st || !st.ok) return;
    if (st.lines && st.lines.length) {
      logEl.textContent = st.lines.join(NL_CHAR);
      logEl.scrollTop = logEl.scrollHeight;
    }
    setBusy(!!st.running);
    if (st.hasSid) {
      document.getElementById("sid").placeholder = "저장된 sid 있음 (바꾸려면 새로 입력)";
    }
    if (st.user) {
      document.getElementById("sid").placeholder = "로그인 확인됨: " + st.user;
    }
  } catch (e) {}
})();

function showNext(info) {
  const box = document.getElementById("nextRun");
  if (!info || !info.enabled) { box.textContent = "자동 실행: 꺼짐"; return; }
  const when = info.when ? new Date(info.when) : null;
  const p = n => String(n).padStart(2, "0");
  box.textContent = "자동 실행: 켜짐" + (when
    ? ` · 다음 ${when.getMonth() + 1}/${when.getDate()} ${p(when.getHours())}:${p(when.getMinutes())}`
    : "") + (info.lastRunDate ? ` · 마지막 ${info.lastRunDate}` : "");
}

document.getElementById("saveDaily").addEventListener("click", async () => {
  const enabled = document.getElementById("autoOn").checked;
  const time = document.getElementById("autoTime").value || "09:00";
  const maxPage = Number(document.getElementById("maxPage").value) || 10;
  try {
    const res = await chrome.runtime.sendMessage({ type: "setDaily", enabled, time, maxPage });
    if (res && res.ok) { showNext(res.info); write(enabled ? `매일 ${time}에 측정하도록 저장했습니다.` : "자동 실행을 껐습니다."); }
    else write("저장 실패: " + (res && res.error));
  } catch (e) { write("저장 오류: " + e.message); }
});

(async function loadDaily() {
  try {
    const res = await chrome.runtime.sendMessage({ type: "dailyInfo" });
    if (res && res.ok) {
      document.getElementById("autoOn").checked = !!res.info.enabled;
      if (res.info.time) document.getElementById("autoTime").value = res.info.time;
      showNext(res.info);
    }
  } catch (e) {}
})();
