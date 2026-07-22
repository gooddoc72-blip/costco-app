'use client';

import { useCallback, useEffect, useMemo, useState } from 'react';
import {
  getProfit, getMe, deleteRows, saveProfit, rowProfit, toSaveRow, won,
  type ProfitResponse, type ProfitRow,
} from '../lib/api';

function yesterday(): string {
  const d = new Date();
  d.setDate(d.getDate() - 1);
  const p = (n: number) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`;
}

type EditField = 'cost_price' | 'delivery_cost' | 'box_cost';

export default function Page() {
  const [date, setDate] = useState(yesterday());
  const [meta, setMeta] = useState<Omit<ProfitResponse, 'rows'> | null>(null);
  const [rows, setRows] = useState<ProfitRow[]>([]);
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [err, setErr] = useState('');
  const [msg, setMsg] = useState('');
  const [me, setMe] = useState('');
  const [sel, setSel] = useState<Record<string, boolean>>({});
  const [dirty, setDirty] = useState(false);

  const load = useCallback(async (d: string) => {
    setLoading(true); setErr(''); setMsg(''); setSel({}); setDirty(false);
    try {
      const res = await getProfit(d);
      const { rows: r, ...rest } = res;
      setRows(r.map((x) => ({ ...x })));
      setMeta(rest);
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
      setRows([]); setMeta(null);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    getMe().then((m) => setMe(m.username)).catch(() => setMe(''));
    load(yesterday());
  }, [load]);

  const selected = useMemo(() => Object.keys(sel).filter((k) => sel[k]), [sel]);

  const summary = useMemo(() => {
    const s = { settlement: 0, settled_shipping: 0, cost: 0, delivery: 0, box: 0, profit: 0 };
    for (const r of rows) {
      s.settlement += r.settlement_amount || 0;
      s.settled_shipping += r.settled_shipping || 0;
      s.cost += r.cost_price || 0;
      s.delivery += r.delivery_cost || 0;
      s.box += r.box_cost || 0;
      s.profit += rowProfit(r);
    }
    return s;
  }, [rows]);

  const onEdit = (orderNo: string, field: EditField, value: string) => {
    const n = value === '' ? 0 : Math.max(0, Math.round(Number(value) || 0));
    setRows((prev) => prev.map((r) => (r.order_no === orderNo ? { ...r, [field]: n } : r)));
    setDirty(true);
  };

  const onSave = async () => {
    if (!rows.length) return;
    setSaving(true); setErr(''); setMsg('');
    try {
      const r = await saveProfit(date, rows.map(toSaveRow));
      setMsg(`💾 정산저장 완료 — ${r.saved}건`);
      setDirty(false);
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setSaving(false);
    }
  };

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

  const profitPos = summary.profit >= 0;
  const busy = loading || saving;

  return (
    <div className="wrap">
      <h1>💰 수익 계산</h1>
      <div className="muted">
        {me ? `${me}님` : '로그인 세션(sid) 필요'} · React + FastAPI (Phase3)
      </div>

      <div className="toolbar">
        <input type="date" value={date} onChange={(e) => setDate(e.target.value)} />
        <button className="primary" onClick={() => load(date)} disabled={busy}>
          {loading ? '조회 중…' : '📋 조회'}
        </button>
        <button className="primary" onClick={onSave} disabled={busy || !rows.length || !dirty}>
          {saving ? '저장 중…' : dirty ? '💾 정산저장 *' : '💾 정산저장'}
        </button>
        <button className="danger" onClick={onDelete} disabled={busy || !selected.length}>
          🗑 선택 삭제 ({selected.length})
        </button>
        {meta?.source && <span className="tag">{meta.source}</span>}
        {meta?.saved && <span className="tag">정산저장 기준</span>}
        {dirty && <span className="tag" style={{ color: '#b45309' }}>편집됨 — 미저장</span>}
      </div>

      {err && <div className="err">⚠️ {err}</div>}
      {msg && <div className="ok">{msg}</div>}

      {rows.length > 0 && (
        <div className="cards">
          <div className="card"><div className="t">정산예정</div><div className="v">{won(summary.settlement)}</div></div>
          <div className="card"><div className="t">실정산배송비</div><div className="v">{won(summary.settled_shipping)}</div></div>
          <div className="card"><div className="t">구입가</div><div className="v">{won(summary.cost)}</div></div>
          <div className="card"><div className="t">택배+박스</div><div className="v">{won(summary.delivery + summary.box)}</div></div>
          <div className={`card profit ${profitPos ? 'pos' : 'neg'}`}>
            <div className="t">수입 (합계)</div>
            <div className={`v ${profitPos ? 'pos' : 'neg'}`}>{won(summary.profit)}</div>
          </div>
        </div>
      )}

      {meta && rows.length === 0 && !loading && (
        <div className="muted">해당 날짜 데이터가 없습니다.</div>
      )}

      {rows.length > 0 && (
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
                <th>구입가 ✏️</th>
                <th>택배 ✏️</th>
                <th>박스 ✏️</th>
                <th>수입</th>
                <th className="l">매칭</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r: ProfitRow) => {
                const p = rowProfit(r);
                return (
                  <tr key={r.order_no}>
                    <td>
                      <input
                        type="checkbox"
                        checked={!!sel[r.order_no]}
                        onChange={(e) => setSel((prev) => ({ ...prev, [r.order_no]: e.target.checked }))}
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
                    <td className="num">
                      <input className="edit" type="number" min={0} value={r.cost_price}
                        onChange={(e) => onEdit(r.order_no, 'cost_price', e.target.value)} />
                    </td>
                    <td className="num">
                      <input className="edit" type="number" min={0} value={r.delivery_cost}
                        onChange={(e) => onEdit(r.order_no, 'delivery_cost', e.target.value)} />
                    </td>
                    <td className="num">
                      <input className="edit" type="number" min={0} value={r.box_cost}
                        onChange={(e) => onEdit(r.order_no, 'box_cost', e.target.value)} />
                    </td>
                    <td className={`num ${p >= 0 ? 'pos' : 'neg'}`}>{p.toLocaleString('ko-KR')}</td>
                    <td className="l"><span className="tag">{r.match_source}</span></td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}

      {meta?.note && <div className="muted" style={{ marginTop: 12 }}>ℹ️ {meta.note}</div>}
    </div>
  );
}
