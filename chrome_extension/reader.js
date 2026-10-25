// 검색결과 페이지에서 상품 목록을 읽는다.
//
// 네이버쇼핑은 첫 화면 결과를 <script id="__NEXT_DATA__"> 에 통째로 담아 보낸다.
// 화면(HTML 카드)을 긁는 것보다 이쪽이 정확하다 — 순위를 우리가 세지 않고
// 네이버가 준 rank 를 그대로 쓰고, 상품번호·판매처명도 가공 없이 들어 있다.
//
// 이 함수는 chrome.scripting.executeScript 로 페이지에 주입되어 실행된다.
function readNaverSearchPage() {
  const out = { ok: false, page: null, products: [], reason: "" };
  const body = document.body ? (document.body.innerText || "") : "";
  for (const phrase of ["보안 확인을 완료", "일시적으로 제한", "자동 입력 방지", "비정상적인 검색"]) {
    if (body.indexOf(phrase) >= 0) { out.reason = phrase; return out; }
  }
  const el = document.getElementById("__NEXT_DATA__");
  if (!el) { out.reason = "__NEXT_DATA__ 없음"; return out; }
  let data;
  try { data = JSON.parse(el.textContent); }
  catch (e) { out.reason = "JSON 파싱 실패"; return out; }

  const pp = (data.props && data.props.pageProps) || {};
  const sp = pp.searchParam || {};
  out.page = Number(sp.pagingIndex || (data.query && data.query.pagingIndex) || 0) || null;

  let list = null;
  if (pp.compositeList && Array.isArray(pp.compositeList.list)) list = pp.compositeList.list;
  else if (pp.shoppingResult && Array.isArray(pp.shoppingResult.products)) list = pp.shoppingResult.products;
  if (!list) { out.reason = "상품 목록 경로 없음"; return out; }

  for (const entry of list) {
    const it = (entry && entry.item) ? entry.item : entry;
    if (!it || typeof it !== "object") continue;
    out.products.push({
      rank: it.rank != null ? Number(it.rank) : null,
      isAd: !!(it.adId || it.adcrUrl),
      mallProductId: it.mallProductId ? String(it.mallProductId) : "",
      nvMid: it.nvMid ? String(it.nvMid) : (it.id ? String(it.id) : ""),
      mallName: it.mallName || "",
      title: it.productTitle || it.productName || "",
      mallCount: it.mallCount != null ? Number(it.mallCount) : null,
      lowPrice: it.lowPrice != null ? Number(it.lowPrice) : null
    });
  }
  out.ok = out.products.length > 0;
  if (!out.ok) out.reason = "상품 0개";
  return out;
}

// executeScript({files:[...]}) 는 마지막 평가값을 돌려준다.
// 그래서 여기서 호출해 두면 주입 한 번으로 결과까지 받는다.
readNaverSearchPage();
