'use client';

import { useCallback, useEffect, useMemo, useState } from 'react';
import {
  getProfit, getMe, deleteRows, won,
  type ProfitResponse, type ProfitRow,
} from '../lib/api';

function yesterday(): string {
  const d = new Date();
  d.setDate(d.getDate() - 1);
  const p = (n: number) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`;
}

export default function Page() {
  const [date, setDate] = useState(yesterday());
  const [data, setData] = useState<ProfitResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState('');
  const [msg, setMsg] = useState('');
  const [me, setMe] = useState<string>('');
  const [sel, setSel] = useState<Record<string, boolean>>({});

  const load = useCallback(async (d: string) => {
    setLoading(true); setErr(''); setMsg(''); setSel({});
    try {
      const res = await getProfit(d);
      setData(res);
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
      setData(null);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    getMe().then((m) => setMe(m.username)).catch(() => setMe(''));
    load(yesterday());
  }, [load]);

  const selected = useMemo(
    () => Object.keys(sel).filter((k) => sel[k]),
    [sel],
  );

  const onDelete = async () => {
    if (!selected.length) return;
    if (!confirm(`${selected.length}건을 영구 삭제할까요? (dispatch_log·주문이력·정산저장·일별주문)`)) return;
    setLoading(true); setErr(''); setMsg('');
    try {
      const r = await deleteRows(date, selected);
      setMsg(`🗑 ${r.deleted}건 삭제 완료`);
      await load(date);
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  };

  const s = data?.summary;
  const profitPos = (s?.profit ?? 0) >= 0;

  return (
    <div className="wrap">
      <h1>💰 수익 계산</h1>
      <div className="muted">
        {me ? `${me}님` : '로그인 세션(sid) 필요'} · React + FastAPI (Phase3)
      </div>

      <div className="toolbar">
        <input type="date" value={date} onChange={(e) => setDate(e.target.value)} />
        <button className="primary" onClick={() => load(date)} disabled={loading}>
          {loading ? '조회 중…' : '📋 조회'}
        </button>
        <button className="danger" onClick={onDelete} disabled={loading || !selected.length}>
          🗑 선택 삭제 ({selected.length})
        </button>
        {data?.source && <span className="tag">{data.source}</span>}
        {data?.saved && <span className="tag">정산저장 기준</span>}
      </div>

      {err && <div className="err">⚠️ {err}</div>}
      {msg && <div className="ok">{msg}</div>}

      {s && (
        <div className="cards">
          <div className="card"><div className="t">정산예정</div><div className="v">{won(s.settlement)}</div></div>
          <div className="card"><div className="t">실정산배송비</div><div className="v">{won(s.settled_shipping)}</div></div>
          <div className="card"><div className="t">구입가</div><div className="v">{won(s.cost)}</div></div>
          <div className="card"><div className="t">택배+박스</div><div className="v">{won(s.delivery + s.box)}</div></div>
          <div className={`card profit ${profitPos ? 'pos' : 'neg'}`}>
            <div className="t">수입 (합계)</div>
            <div className={`v ${profitPos ? 'pos' : 'neg'}`}>{won(s.profit)}</div>
          </div>
        </div>
      )}

      {data && data.count === 0 && !loading && (
        <div className="muted">해당 날짜 데이터가 없습니다.</div>
      )}

      {data && data.count > 0 && (
        <div className="tablewrap">
          <table>
            <thead>
              <tr>
                <th></th>
                <th className="l">상품명</th>
                <th className="l">수취인</th>
                <th>수량</th>
                <th>정산예정</th>
                <th>배송비</th>
                <th>구입가</th>
                <th>택배</th>
                <th>박스</th>
                <th>수입</th>
                <th className="l">매칭</th>
              </tr>
            </thead>
            <tbody>
              {data.rows.map((r: ProfitRow) => (
                <tr key={r.order_no}>
                  <td>
                    <input
                      type="checkbox"
                      checked={!!sel[r.order_no]}
                      onChange={(e) => setSel((p) => ({ ...p, [r.order_no]: e.target.checked }))}
                    />
                  </td>
                  <td className="l" title={r.matched_name}>
                    {r.product_name}
                    {r.split_qty > 1 && <span className="tag" style={{ marginLeft: 6 }}>소분÷{r.split_qty}</span>}
                  </td>
                  <td className="l">{r.recipient}</td>
                  <td className="num">{r.qty}</td>
                  <td className="num">{r.settlement_amount.toLocaleString('ko-KR')}</td>
                  <td className="num">{r.shipping_fee.toLocaleString('ko-KR')}</td>
                  <td className="num">{r.cost_price.toLocaleString('ko-KR')}</td>
                  <td className="num">{r.delivery_cost.toLocaleString('ko-KR')}</td>
                  <td className="num">{r.box_cost.toLocaleString('ko-KR')}</td>
                  <td className={`num ${r.profit >= 0 ? 'pos' : 'neg'}`}>
                    {r.profit.toLocaleString('ko-KR')}
                  </td>
                  <td className="l"><span className="tag">{r.match_source}</span></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {data?.note && <div className="muted" style={{ marginTop: 12 }}>ℹ️ {data.note}</div>}
    </div>
  );
}
