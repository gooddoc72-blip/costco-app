'use client';
/**
 * 카메라 바코드 인식 + 직접 입력칸.
 *
 * - 카메라: @zxing/browser — 아이폰 사파리에는 브라우저 내장 BarcodeDetector가 없어서
 *   자바스크립트 인식기를 쓴다. 상품·가격표 바코드(EAN-13/8, UPC, CODE128/39, ITF)만 본다.
 * - 입력칸: 번호를 손으로 넣을 때, 그리고 나중에 USB·블루투스 스캐너를 쓸 때
 *   (스캐너는 키보드처럼 숫자를 치고 Enter를 누른다) 그대로 쓴다.
 * 같은 바코드가 카메라에 계속 비치면 2.5초에 한 번만 알린다.
 */
import { useEffect, useRef, useState } from 'react';

interface Props {
  onCode: (code: string) => void;
  /** 결과를 처리하는 동안 카메라 인식을 잠시 멈춘다 */
  paused?: boolean;
}

export default function BarcodeScanner({ onCode, paused }: Props) {
  const video = useRef<HTMLVideoElement>(null);
  const controls = useRef<{ stop: () => void } | null>(null);
  const last = useRef<{ code: string; at: number }>({ code: '', at: 0 });
  const pausedRef = useRef(!!paused);
  const cb = useRef(onCode);
  const [on, setOn] = useState(false);
  const [err, setErr] = useState('');
  const [typed, setTyped] = useState('');
  pausedRef.current = !!paused;
  cb.current = onCode;

  const stop = () => {
    controls.current?.stop();
    controls.current = null;
    setOn(false);
  };

  const start = async () => {
    setErr('');
    if (typeof navigator === 'undefined' || !navigator.mediaDevices?.getUserMedia) {
      setErr('이 브라우저는 카메라를 쓸 수 없습니다 — 아래 칸에 번호를 넣어 주세요.');
      return;
    }
    try {
      const [{ BrowserMultiFormatReader }, { BarcodeFormat, DecodeHintType }] = await Promise.all([
        import('@zxing/browser'), import('@zxing/library'),
      ]);
      const hints = new Map();
      hints.set(DecodeHintType.POSSIBLE_FORMATS, [
        BarcodeFormat.EAN_13, BarcodeFormat.EAN_8, BarcodeFormat.UPC_A, BarcodeFormat.UPC_E,
        BarcodeFormat.CODE_128, BarcodeFormat.CODE_39, BarcodeFormat.ITF,
      ]);
      const reader = new BrowserMultiFormatReader(hints, { delayBetweenScanAttempts: 150 });
      setOn(true);
      controls.current = await reader.decodeFromConstraints(
        { video: { facingMode: { ideal: 'environment' }, width: { ideal: 1280 }, height: { ideal: 720 } } },
        video.current!,
        (res) => {
          if (!res || pausedRef.current) return;
          const code = res.getText().replace(/\D/g, '');
          if (!code) return;
          const now = Date.now();
          if (code === last.current.code && now - last.current.at < 2500) return;
          last.current = { code, at: now };
          try { navigator.vibrate?.(80); } catch { /* 진동 없는 기기 */ }
          cb.current(code);
        },
      );
    } catch (e: any) {
      setOn(false);
      const m = String(e?.name || e?.message || e);
      setErr(/NotAllowed|Permission/i.test(m)
        ? '카메라 권한이 막혀 있습니다 — 브라우저 주소창의 🔒(설정)에서 카메라를 허용해 주세요.'
        : /NotFound|Overconstrained/i.test(m)
          ? '카메라를 찾지 못했습니다 — 아래 칸에 번호를 넣어 주세요.'
          : `카메라를 켜지 못했습니다: ${m}`);
    }
  };

  useEffect(() => () => controls.current?.stop(), []);

  const submit = () => {
    const c = typed.replace(/\D/g, '');
    if (!c) return;
    setTyped('');
    cb.current(c);
  };

  return (
    <div className="space-y-2">
      <div className={on ? 'relative rounded-xl overflow-hidden bg-black' : 'hidden'}>
        <video ref={video} className="w-full max-h-[42vh] object-cover" muted playsInline />
        {/* 가로 조준선 — 바코드를 이 줄에 맞추면 잘 읽힌다 */}
        <div className="pointer-events-none absolute inset-x-6 top-1/2 h-0.5 bg-red-500/80" />
        {paused && <div className="absolute inset-0 bg-black/40 flex items-center justify-center text-white text-sm">확인 중…</div>}
        <button onClick={stop} className="absolute top-2 right-2 bg-black/60 text-white text-xs rounded px-2 py-1">■ 카메라 끄기</button>
      </div>
      {!on && (
        <button onClick={start} className="w-full bg-blue-600 text-white text-sm font-medium rounded-lg py-3">
          📷 카메라로 바코드 찍기
        </button>
      )}
      {err && <div className="bg-amber-50 text-amber-800 p-2 rounded text-xs">⚠️ {err}</div>}
      <form onSubmit={e => { e.preventDefault(); submit(); }} className="flex gap-2">
        <input value={typed} onChange={e => setTyped(e.target.value)} inputMode="numeric"
          placeholder="바코드·상품번호 직접 입력 (스캐너도 여기로)"
          className="flex-1 border border-gray-300 rounded-lg px-3 py-2 text-sm" />
        <button type="submit" className="border border-gray-300 rounded-lg px-3 text-sm">찾기</button>
      </form>
    </div>
  );
}
