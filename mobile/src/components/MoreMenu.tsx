'use client';
import Link from 'next/link';
import { useState } from 'react';
import { useRouter } from 'next/navigation';
import { X, Package, CreditCard, TrendingUp, Settings, Crown, Receipt, Tag, ScanLine, LogOut, User } from 'lucide-react';
import { logout } from '@/lib/logout';

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
      { href: '/scan', label: '바코드 스캔', icon: ScanLine, desc: '찍으면 상품·매입가·재고 — 매장 가격 확인' },
      { href: '/pricetag', label: '가격표 사진', icon: Tag, desc: '매장 가격표로 매입가 수정 (관리자)' },
    ],
  },
  {
    title: '시스템',
    links: [
      { href: '/me', label: '내 정보', icon: User, desc: '계정 확인·로그아웃' },
      { href: '/settings', label: '설정', icon: Settings },
      { href: '/admin', label: '관리자', icon: Crown, desc: '장보기 제출 + DB 정리' },
      { href: '/stock', label: '재고 조회', icon: Package, desc: '사용자별 재고·출고 내역·이상 점검' },
      { href: '/receipt-settle', label: '영수증 정산', icon: CreditCard, desc: '매칭 확인·정산 요청 (관리자)' },
      { href: '/trends', label: 'SNS 트렌드 TOP 30', icon: TrendingUp, desc: '뜨는 코스트코 상품·판매 제안 (관리자)' },
      { href: '/billing', label: '정산·청구', icon: CreditCard, desc: '청구·입금·예치금 (관리자)' },
    ],
  },
];

export default function MoreMenu({ open, onClose }: Props) {
  const router = useRouter();
  const [out, setOut] = useState(false);
  if (!open) return null;
  const doLogout = async () => {
    if (out || !confirm('로그아웃할까요?')) return;
    setOut(true);
    await logout();
    onClose();
    router.push('/login');
  };
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
        <button onClick={doLogout} disabled={out}
          className="w-full flex items-center justify-center gap-2 border border-red-200 text-red-600 rounded-lg py-3 disabled:opacity-50">
          <LogOut size={16} />
          <span className="text-sm font-medium">{out ? '로그아웃 중...' : '로그아웃'}</span>
        </button>
      </div>
    </div>
  );
}
