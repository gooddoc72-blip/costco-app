export default function HomePage() {
  return (
    <main className="flex flex-col items-center justify-center min-h-screen p-6">
      <div className="text-6xl mb-4">📦</div>
      <h1 className="text-2xl font-bold text-primary mb-2">costcobiz</h1>
      <p className="text-sm text-gray-500 mb-8">코스트코 주문 수익 관리 모바일</p>
      <div className="w-full max-w-sm space-y-3">
        <a
          href="/login"
          className="block w-full py-3 text-center text-white bg-primary hover:bg-primary-dark rounded-lg font-medium transition"
        >
          로그인
        </a>
        <a
          href="/dashboard"
          className="block w-full py-3 text-center text-primary border border-primary rounded-lg font-medium"
        >
          대시보드 (개발 중)
        </a>
      </div>
      <p className="mt-12 text-xs text-gray-400">PC 버전: costcobiz.shop (자동 분기 예정)</p>
    </main>
  );
}
