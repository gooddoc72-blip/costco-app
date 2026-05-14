'use client';
import Header from '@/components/Header';
import BottomNav from '@/components/BottomNav';
import Field from '@/components/Field';
import { Save, Truck, Key, MessageCircle } from 'lucide-react';
import { useSettings } from '@/hooks/useSettings';

export default function SettingsPage() {
  const s = useSettings();

  if (s.loading) return (
    <>
      <Header title="설정" />
      <main className="p-4">로딩 중...</main>
      <BottomNav />
    </>
  );

  return (
    <>
      <Header title="⚙️ 설정" subtitle="API 키 · 고정 비용 · 알림" />
      <main className="px-4 pt-4 pb-32 space-y-4">

        <Section icon={<Truck size={18} />} title="고정 비용">
          <div className="grid grid-cols-2 gap-3 text-sm">
            <Field label="택배비 (원)" type="number" step={100}
              value={s.data.shippingCost} onChange={v => s.update('shippingCost', Number(v))} />
            <Field label="박스비 (원)" type="number" step={50}
              value={s.data.boxCost} onChange={v => s.update('boxCost', Number(v))} />
            <Field label="배송비 수수료율 (%)" type="number" step={0.1}
              value={s.data.shippingCommissionRate}
              onChange={v => s.update('shippingCommissionRate', Number(v))}
              hint="네이버가 고객결제 배송비에 부과 (보통 3~5%)" />
            <Field label="목표 마진율 (%)" type="number" step={1}
              value={s.data.targetMargin} onChange={v => s.update('targetMargin', Number(v))} />
          </div>
        </Section>

        <Section icon={<Key size={18} />} title="네이버 커머스 API">
          <div className="space-y-2 text-sm">
            <Field label="Client ID"
              value={s.data.naverApiClientId} onChange={v => s.update('naverApiClientId', String(v))} />
            <Field label="Client Secret" type="password"
              value={s.data.naverApiClientSecret} onChange={v => s.update('naverApiClientSecret', String(v))} />
          </div>
        </Section>

        <Section icon={<Key size={18} />} title="쿠팡 Wing API">
          <div className="space-y-2 text-sm">
            <Field label="Access Key"
              value={s.data.coupangAccessKey} onChange={v => s.update('coupangAccessKey', String(v))} />
            <Field label="Secret Key" type="password"
              value={s.data.coupangSecretKey} onChange={v => s.update('coupangSecretKey', String(v))} />
            <Field label="Vendor ID"
              value={s.data.coupangVendorId} onChange={v => s.update('coupangVendorId', String(v))} />
          </div>
        </Section>

        <Section icon={<MessageCircle size={18} />} title="알림 (카톡 · 텔레그램)">
          <div className="space-y-2 text-sm">
            <Field label="카카오 REST API 키"
              value={s.data.kakaoApiKey} onChange={v => s.update('kakaoApiKey', String(v))} />
            <Field label="카카오 Access Token" type="password"
              value={s.data.kakaoAccessToken} onChange={v => s.update('kakaoAccessToken', String(v))} />
            <Field label="텔레그램 Bot Token" type="password"
              value={s.data.telegramToken} onChange={v => s.update('telegramToken', String(v))} />
            <Field label="텔레그램 Chat ID"
              value={s.data.telegramChatId} onChange={v => s.update('telegramChatId', String(v))} />
          </div>
        </Section>

        <div className="fixed bottom-16 left-0 right-0 p-3 bg-white border-t z-10">
          {s.msg && <div className="text-sm mb-2">{s.msg}</div>}
          <button onClick={s.save} disabled={s.saving}
            className="w-full bg-blue-600 text-white font-medium py-2.5 rounded-lg flex items-center justify-center gap-1 disabled:bg-gray-300">
            <Save size={16} /> {s.saving ? '저장 중...' : '전체 설정 저장'}
          </button>
        </div>
      </main>
      <BottomNav />
    </>
  );
}

function Section({ icon, title, children }: { icon: React.ReactNode; title: string; children: React.ReactNode }) {
  return (
    <section className="bg-white rounded-xl p-4 shadow-sm border">
      <div className="flex items-center gap-2 mb-3 font-semibold text-gray-900">
        {icon} {title}
      </div>
      {children}
    </section>
  );
}
