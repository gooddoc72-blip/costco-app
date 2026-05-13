'use client';
import { useEffect, useState } from 'react';
import Header from '@/components/Header';
import BottomNav from '@/components/BottomNav';
import { Save, Settings as SettingsIcon, Truck, Key, MessageCircle } from 'lucide-react';

interface Settings {
  shippingCost: number;
  boxCost: number;
  shippingCommissionRate: number;
  targetMargin: number;
  maxIncreasePct: number;
  naverApiClientId: string;
  naverApiClientSecret: string;
  naverOpenApiClientId: string;
  naverOpenApiClientSecret: string;
  coupangAccessKey: string;
  coupangSecretKey: string;
  coupangVendorId: string;
  kakaoApiKey: string;
  kakaoAccessToken: string;
  telegramToken: string;
  telegramChatId: string;
}

const empty: Settings = {
  shippingCost: 1800, boxCost: 300, shippingCommissionRate: 4.0,
  targetMargin: 10, maxIncreasePct: 20,
  naverApiClientId: '', naverApiClientSecret: '',
  naverOpenApiClientId: '', naverOpenApiClientSecret: '',
  coupangAccessKey: '', coupangSecretKey: '', coupangVendorId: '',
  kakaoApiKey: '', kakaoAccessToken: '',
  telegramToken: '', telegramChatId: '',
};

export default function SettingsPage() {
  const [data, setData] = useState<Settings>(empty);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);

  useEffect(() => {
    (async () => {
      try {
        const res = await fetch('/api/settings');
        if (res.ok) setData({ ...empty, ...(await res.json()) });
      } finally { setLoading(false); }
    })();
  }, []);

  const update = <K extends keyof Settings>(k: K, v: Settings[K]) =>
    setData(prev => ({ ...prev, [k]: v }));

  const save = async () => {
    setSaving(true); setMsg(null);
    try {
      const res = await fetch('/api/settings', {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(data),
      });
      if (!res.ok) throw new Error(await res.text());
      setMsg('✅ 저장 완료');
    } catch (e: any) {
      setMsg('❌ ' + (e.message || '저장 실패'));
    } finally { setSaving(false); }
  };

  if (loading) return (<><Header title="설정" /><main className="p-4">로딩 중...</main><BottomNav /></>);

  return (
    <>
      <Header title="⚙️ 설정" subtitle="API 키 · 고정 비용 · 알림" />
      <main className="px-4 pt-4 pb-32 space-y-4">

        {/* 고정 비용 */}
        <section className="bg-white rounded-xl p-4 shadow-sm border">
          <div className="flex items-center gap-2 mb-3 font-semibold text-gray-900">
            <Truck size={18} /> 고정 비용
          </div>
          <div className="grid grid-cols-2 gap-3 text-sm">
            <Field label="택배비 (원)" type="number" step={100}
              value={data.shippingCost} onChange={v => update('shippingCost', Number(v))} />
            <Field label="박스비 (원)" type="number" step={50}
              value={data.boxCost} onChange={v => update('boxCost', Number(v))} />
            <Field label="배송비 수수료율 (%)" type="number" step={0.1}
              value={data.shippingCommissionRate}
              onChange={v => update('shippingCommissionRate', Number(v))}
              hint="네이버가 고객결제 배송비에 부과 (보통 3~5%)" />
            <Field label="목표 마진율 (%)" type="number" step={1}
              value={data.targetMargin} onChange={v => update('targetMargin', Number(v))} />
          </div>
        </section>

        {/* 네이버 커머스 API */}
        <section className="bg-white rounded-xl p-4 shadow-sm border">
          <div className="flex items-center gap-2 mb-3 font-semibold text-gray-900">
            <Key size={18} /> 네이버 커머스 API
          </div>
          <div className="space-y-2 text-sm">
            <Field label="Client ID"
              value={data.naverApiClientId}
              onChange={v => update('naverApiClientId', String(v))} />
            <Field label="Client Secret" type="password"
              value={data.naverApiClientSecret}
              onChange={v => update('naverApiClientSecret', String(v))} />
          </div>
        </section>

        {/* 쿠팡 Wing */}
        <section className="bg-white rounded-xl p-4 shadow-sm border">
          <div className="flex items-center gap-2 mb-3 font-semibold text-gray-900">
            <Key size={18} /> 쿠팡 Wing API
          </div>
          <div className="space-y-2 text-sm">
            <Field label="Access Key"
              value={data.coupangAccessKey}
              onChange={v => update('coupangAccessKey', String(v))} />
            <Field label="Secret Key" type="password"
              value={data.coupangSecretKey}
              onChange={v => update('coupangSecretKey', String(v))} />
            <Field label="Vendor ID"
              value={data.coupangVendorId}
              onChange={v => update('coupangVendorId', String(v))} />
          </div>
        </section>

        {/* 알림 */}
        <section className="bg-white rounded-xl p-4 shadow-sm border">
          <div className="flex items-center gap-2 mb-3 font-semibold text-gray-900">
            <MessageCircle size={18} /> 알림 (카톡 · 텔레그램)
          </div>
          <div className="space-y-2 text-sm">
            <Field label="카카오 REST API 키"
              value={data.kakaoApiKey}
              onChange={v => update('kakaoApiKey', String(v))} />
            <Field label="카카오 Access Token" type="password"
              value={data.kakaoAccessToken}
              onChange={v => update('kakaoAccessToken', String(v))} />
            <Field label="텔레그램 Bot Token" type="password"
              value={data.telegramToken}
              onChange={v => update('telegramToken', String(v))} />
            <Field label="텔레그램 Chat ID"
              value={data.telegramChatId}
              onChange={v => update('telegramChatId', String(v))} />
          </div>
        </section>

        {/* 저장 버튼 (고정) */}
        <div className="fixed bottom-16 left-0 right-0 p-3 bg-white border-t z-10">
          {msg && <div className="text-sm mb-2">{msg}</div>}
          <button
            onClick={save}
            disabled={saving}
            className="w-full bg-blue-600 text-white font-medium py-2.5 rounded-lg flex items-center justify-center gap-1 disabled:bg-gray-300"
          >
            <Save size={16} /> {saving ? '저장 중...' : '전체 설정 저장'}
          </button>
        </div>
      </main>
      <BottomNav />
    </>
  );
}

function Field(props: {
  label: string;
  value: string | number;
  onChange: (v: string | number) => void;
  type?: string;
  step?: number;
  hint?: string;
}) {
  return (
    <div>
      <label className="block text-xs text-gray-600 mb-1">{props.label}</label>
      <input
        type={props.type || 'text'}
        step={props.step}
        value={props.value}
        onChange={(e) => {
          const v = props.type === 'number' ? parseFloat(e.target.value) || 0 : e.target.value;
          props.onChange(v);
        }}
        className="w-full px-3 py-2 border border-gray-200 rounded-lg text-sm"
      />
      {props.hint && <p className="text-[10px] text-gray-400 mt-1">{props.hint}</p>}
    </div>
  );
}
