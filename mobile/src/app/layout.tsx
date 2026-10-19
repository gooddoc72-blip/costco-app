import type { Metadata, Viewport } from 'next';
import { withBase } from '@/lib/basePath';
import AppShell from '@/components/AppShell';
import './globals.css';

export const metadata: Metadata = {
  title: 'costcobiz',
  description: '코스트코 주문 수익 관리',
  manifest: withBase('/manifest.json'),
  appleWebApp: {
    capable: true,
    statusBarStyle: 'default',
    title: 'costcobiz',
  },
};

export const viewport: Viewport = {
  themeColor: '#E31837',
  width: 'device-width',
  initialScale: 1,
  viewportFit: 'cover',
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="ko">
      <head>
        <link rel="icon" href={withBase("/icons/icon-192.png")} />
        <link rel="apple-touch-icon" href={withBase("/icons/icon-192.png")} />
      </head>
      <body className="min-h-screen bg-gray-100 safe-top safe-bottom">
        <AppShell>{children}</AppShell>
      </body>
    </html>
  );
}
