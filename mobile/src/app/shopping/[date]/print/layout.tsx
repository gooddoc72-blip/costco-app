/** 인쇄 전용 레이아웃 — root layout의 max-w 컨테이너를 fixed로 무력화. */
export default function PrintLayout({ children }: { children: React.ReactNode }) {
  return (
    <div className="fixed inset-0 bg-white overflow-auto z-[9999] p-4">
      <style>{`
        @media print {
          body { background: white !important; }
          .no-print { display: none !important; }
          .print-shell { position: static !important; padding: 0 !important; }
        }
      `}</style>
      <div className="print-shell mx-auto max-w-[800px]">
        {children}
      </div>
    </div>
  );
}
