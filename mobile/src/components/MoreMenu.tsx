'use client';
import Link from 'next/link';
import { X, Package, CreditCard, TrendingUp, Settings, Crown, Receipt } from 'lucide-react';

interface Props { open: boolean; onClose: () => void }

const sections: Array<{
  title: string;
  links: Array<{ href: string; label: string; icon: any; desc?: string }>;
}> = [
  {
    title: '데이터',
    links: [
      { href: '/products', label: '제품 DB', icon: Package, desc: '단가/분할수량 관리' },
      { href: '/settlement', label: '정산 매칭', icon: CreditCard, desc: 'CSV 업로드 + 매칭' },
      { href: '/rank', label: '키워드 순위', icon: TrendingUp, desc: '검색 노출 추적' },
      { href: '/receipt', label: '영수증', icon: Receipt, desc: 'PDF 매입가 업데이트' },
    ],
  },
  {
    title: '시스템',
    links: [
      { href: '/settings', label: '설정', icon: Settings },
      { href: '/admin', label: '관리자', icon: Crown, desc: '장보기 제출 + DB 정리' },
    ],
  },
];

export default function MoreMenu({ open, onClose }: Props) {
  if (!open) return null;
  return (
    <div className="fixed inset-0 z-50 flex items-end" onClick={onClose}>
      <div className="absolute inset-0 bg-black/40" />
      <div className="relative w-full max-w-[480px] mx-auto bg-white rounded-t-2xl p-4 max-h-[80vh] overflow-y-auto safe-bottom"
        onClick={e => e.stopPropagation()}>
        <div className="flex items-center justify-between mb-3">
          <div className="font-semibold text-gray-900">더보기</div>
          <button onClick={onClose}><X size={20} className="text-gray-400" /></button>
        </div>
        {sections.map(sec => (
          <div key={sec.title} className="mb-4">
            <div className="text-[11px] font-semibold text-gray-500 mb-1">{sec.title}</div>
            <ul className="grid grid-cols-2 gap-2">
              {sec.links.map(l => (
                <li key={l.href}>
                  <Link href={l.href} onClick={onClose}
                    className="block border border-gray-200 rounded-lg p-3 hover:bg-gray-50">
                    <div className="flex items-center gap-2">
                      <l.icon size={16} className="text-blue-600" />
                      <span className="text-sm font-medium">{l.label}</span>
                    </div>
                    {l.desc && <p className="text-[10px] text-gray-500 mt-1">{l.desc}</p>}
                  </Link>
                </li>
              ))}
            </ul>
          </div>
        ))}
      </div>
    </div>
  );
}
