import { useEffect, useState } from 'react';
import { fetchSettings, saveSettings } from '@/lib/client/settings';

export interface SettingsForm {
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

const EMPTY: SettingsForm = {
  shippingCost: 1800, boxCost: 300, shippingCommissionRate: 4.0,
  targetMargin: 10, maxIncreasePct: 20,
  naverApiClientId: '', naverApiClientSecret: '',
  naverOpenApiClientId: '', naverOpenApiClientSecret: '',
  coupangAccessKey: '', coupangSecretKey: '', coupangVendorId: '',
  kakaoApiKey: '', kakaoAccessToken: '',
  telegramToken: '', telegramChatId: '',
};

export function useSettings() {
  const [data, setData] = useState<SettingsForm>(EMPTY);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);

  useEffect(() => {
    (async () => {
      try {
        const result = await fetchSettings();
        setData({ ...EMPTY, ...(result as SettingsForm) });
      } finally {
        setLoading(false);
      }
    })();
  }, []);

  const update = <K extends keyof SettingsForm>(k: K, v: SettingsForm[K]) =>
    setData(prev => ({ ...prev, [k]: v }));

  const save = async () => {
    setSaving(true); setMsg(null);
    try {
      await saveSettings(data);
      setMsg('✅ 저장 완료');
    } catch (e: any) {
      setMsg('❌ ' + (e.message || '저장 실패'));
    } finally {
      setSaving(false);
    }
  };

  return { data, loading, saving, msg, update, save };
}
