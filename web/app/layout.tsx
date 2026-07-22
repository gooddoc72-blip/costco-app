import type { Metadata } from 'next';
import './globals.css';

export const metadata: Metadata = {
  title: '수익 계산 — 코스트코',
  description: '일별 정산·수익 계산 (React)',
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="ko">
      <body>{children}</body>
    </html>
  );
}
