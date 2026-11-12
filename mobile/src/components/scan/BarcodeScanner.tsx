'use client';
/**
 * 카메라 바코드 인식 + 사진으로 읽기 + 직접 입력칸.
 *
 * - 인식기: 브라우저 내장 BarcodeDetector(안드로이드 크롬 — 빠르고 정확)를 먼저 쓰고,
 *   없으면(아이폰 사파리 등) @zxing/browser를 'TRY_HARDER'로 쓴다.
 *   처음엔 zxing만 썼는데 화면에 띄운 가격표·작은 바코드를 못 읽었다(11/12).
 * - 카메라: 1920×1080을 요청하고, 기기가 지원하면 확대(줌) 버튼을 보여 준다
 *   — 작은 바코드는 가까이 대면 초점이 안 맞으므로 조금 떨어져서 확대해야 읽힌다.
 * - 📸 사진으로 읽기: 실시간 인식이 안 될 때 고해상도 사진 한 장으로 다시 읽는다
 *   (90° 돌려서도 한 번 더 본다).
 * - 입력칸: 번호를 손으로 넣을 때, 그리고 USB·블루투스 스캐너(키보드처럼 숫자+Enter).
 * 같은 바코드가 계속 비치면 2.5초에 한 번만 알린다.
 */
import { useEffect, useRef, useState } from 'react';

interface Props {
  onCode: (code: string) => void;
  /** 결과를 처리하는 동안 인식을 잠시 멈춘다 */
  paused?: boolean;
  /** 값이 바뀌면 카메라를 끈다 — 사진 촬영 직전에 꺼서 아이폰에서 카메라 두 개가 겹치지 않게 */
  stopSignal?: number;
  /** 카메라를 켤 때 — 잠가 둔 스캔을 푼다 */
  onStart?: () => void;
  /** 사진 바코드 판독이 모두 실패했을 때 AI로 숫자를 읽는다 → 코드(없으면 '') */
  onPhotoAi?: (f: File) => Promise<string>;
}

const NATIVE_FORMATS = ['ean_13', 'ean_8', 'upc_a', 'upc_e', 'code_128', 'code_39', 'code_93',
  'itf', 'codabar', 'qr_code', 'data_matrix'];

async function nativeDetector(): Promise<any | null> {
  const BD = typeof window !== 'undefined' ? (window as any).BarcodeDetector : undefined;
  if (!BD) return null;
  try {
    const sup: string[] = await BD.getSupportedFormats();
    const formats = NATIVE_FORMATS.filter(f => sup.includes(f));
    return formats.length ? new BD({ formats }) : null;
  } catch { return null; }
}

async function zxingReader() {
  const [{ BrowserMultiFormatReader }, { BarcodeFormat, DecodeHintType }] = await Promise.all([
    import('@zxing/browser'), import('@zxing/library'),
  ]);
  const hints = new Map();
  hints.set(DecodeHintType.POSSIBLE_FORMATS, [
    BarcodeFormat.EAN_13, BarcodeFormat.EAN_8, BarcodeFormat.UPC_A, BarcodeFormat.UPC_E,
    BarcodeFormat.CODE_128, BarcodeFormat.CODE_39, BarcodeFormat.CODE_93, BarcodeFormat.ITF,
    BarcodeFormat.CODABAR, BarcodeFormat.QR_CODE, BarcodeFormat.DATA_MATRIX,
  ]);
  hints.set(DecodeHintType.TRY_HARDER, true);
  return new BrowserMultiFormatReader(hints, { delayBetweenScanAttempts: 120 });
}

/** 바코드 값 → 숫자만. 숫자가 없으면(QR 글자 등) 원문을 그대로. */
const clean = (raw: string) => (raw.replace(/\D/g, '') || raw.trim());

export default function BarcodeScanner({ onCode, paused, stopSignal, onStart, onPhotoAi }: Props) {
  const video = useRef<HTMLVideoElement>(null);
  const stream = useRef<MediaStream | null>(null);
  const zx = useRef<{ stop: () => void } | null>(null);
  const timer = useRef<number | null>(null);
  const last = useRef<{ code: string; at: number }>({ code: '', at: 0 });
  const pausedRef = useRef(!!paused);
  const cb = useRef(onCode);
  const [on, setOn] = useState(false);
  const [engine, setEngine] = useState('');
  const [err, setErr] = useState('');
  const [typed, setTyped] = useState('');
  const [zoom, setZoom] = useState<{ min: number; max: number; cur: number } | null>(null);
  const [reading, setReading] = useState(false);
  pausedRef.current = !!paused;
  cb.current = onCode;

  const emit = (raw: string) => {
    const code = clean(raw);
    if (!code) return;
    const now = Date.now();
    if (code === last.current.code && now - last.current.at < 2500) return;
    last.current = { code, at: now };
    try { navigator.vibrate?.(80); } catch { /* 진동 없는 기기 */ }
    cb.current(code);
  };

  const stop = () => {
    if (timer.current) { window.clearInterval(timer.current); timer.current = null; }
    zx.current?.stop(); zx.current = null;
    stream.current?.getTracks().forEach(t => t.stop()); stream.current = null;
    setOn(false); setZoom(null);
  };

  const start = async () => {
    setErr('');
    onStart?.();
    if (typeof navigator === 'undefined' || !navigator.mediaDevices?.getUserMedia) {
      setErr('이 브라우저는 카메라를 쓸 수 없습니다 — 「📸 사진으로 읽기」나 아래 칸을 써 주세요.');
      return;
    }
    try {
      const s = await navigator.mediaDevices.getUserMedia({
        audio: false,
        video: { facingMode: { ideal: 'environment' }, width: { ideal: 1920 }, height: { ideal: 1080 } },
      });
      stream.current = s;
      const v = video.current!;
      v.srcObject = s;
      await v.play().catch(() => {});
      setOn(true);

      // 연속 초점 + 확대 지원 여부
      const track = s.getVideoTracks()[0];
      const caps: any = (track as any).getCapabilities?.() || {};
      try {
        if (Array.isArray(caps.focusMode) && caps.focusMode.includes('continuous')) {
          await track.applyConstraints({ advanced: [{ focusMode: 'continuous' } as any] });
        }
      } catch { /* 초점 설정 못 해도 계속 */ }
      if (caps.zoom && caps.zoom.max > caps.zoom.min) {
        const cur = Number((track.getSettings() as any).zoom || caps.zoom.min);
        setZoom({ min: caps.zoom.min, max: caps.zoom.max, cur });
      }

      const det = await nativeDetector();
      if (det) {
        setEngine('내장 인식기');
        let busy = false;
        timer.current = window.setInterval(async () => {
          if (busy || pausedRef.current || !video.current || video.current.readyState < 2) return;
          busy = true;
          try {
            const r = await det.detect(video.current);
            if (r && r.length) emit(String(r[0].rawValue || ''));
          } catch { /* 한 프레임 실패는 무시 */ }
          busy = false;
        }, 200);
      } else {
        setEngine('zxing');
        const reader = await zxingReader();
        zx.current = await reader.decodeFromStream(s, v, (res) => {
          if (res && !pausedRef.current) emit(res.getText());
        });
      }
    } catch (e: any) {
      stop();
      const m = String(e?.name || e?.message || e);
      setErr(/NotAllowed|Permission/i.test(m)
        ? '카메라 권한이 막혀 있습니다 — 브라우저 주소창의 🔒(설정)에서 카메라를 허용해 주세요.'
        : /NotFound|Overconstrained/i.test(m)
          ? '카메라를 찾지 못했습니다 — 「📸 사진으로 읽기」나 아래 칸을 써 주세요.'
          : `카메라를 켜지 못했습니다: ${m}`);
    }
  };

  const setZoomTo = async (z: number) => {
    const track = stream.current?.getVideoTracks()[0];
    if (!track || !zoom) return;
    const v = Math.min(zoom.max, Math.max(zoom.min, z));
    try {
      await track.applyConstraints({ advanced: [{ zoom: v } as any] });
      setZoom({ ...zoom, cur: v });
    } catch { /* 확대 못 하는 기기 */ }
  };

  /** 사진 한 장으로 읽기 — 내장 인식기 → zxing(원본·90° 회전) 순서 */
  const readPhoto = async (f: File | undefined) => {
    if (!f) return;
    setErr(''); setReading(true);
    const url = URL.createObjectURL(f);
    try {
      const img = await new Promise<HTMLImageElement>((res, rej) => {
        const i = new Image(); i.onload = () => res(i); i.onerror = () => rej(new Error('사진을 열 수 없습니다')); i.src = url;
      });
      const det = await nativeDetector();
      if (det) {
        try {
          const r = await det.detect(img);
          if (r && r.length) { emit(String(r[0].rawValue || '')); return; }
        } catch { /* zxing으로 */ }
      }
      // zxing — 여러 크기 × (전체·가운데 확대) × (0°·90°). 아이폰 원본(4000px+)을 한 크기로만
      // 줄이면 작은 바코드가 뭉개져 못 읽었다(11/12).
      const reader = await zxingReader();
      const W = img.naturalWidth, H = img.naturalHeight;
      const tries: { crop: number; edge: number }[] = [
        { crop: 1, edge: 1600 }, { crop: 0.6, edge: 1600 }, { crop: 1, edge: 2600 },
        { crop: 0.4, edge: 1400 }, { crop: 1, edge: 1000 },
      ];
      for (const t of tries) {
        const cw = Math.round(W * t.crop), ch = Math.round(H * t.crop);
        const sx = Math.round((W - cw) / 2), sy = Math.round((H - ch) / 2);
        const k = Math.min(1, t.edge / Math.max(cw, ch));
        const w = Math.round(cw * k), h = Math.round(ch * k);
        for (const rot of [0, 90]) {
          const c = document.createElement('canvas');
          c.width = rot ? h : w; c.height = rot ? w : h;
          const g = c.getContext('2d')!;
          if (rot) { g.translate(h, 0); g.rotate(Math.PI / 2); }
          g.drawImage(img, sx, sy, cw, ch, 0, 0, w, h);
          try { const r = reader.decodeFromCanvas(c); emit(r.getText()); return; } catch { /* 다음 */ }
        }
      }
      // 마지막: AI가 가격표에서 상품번호·바코드 숫자를 글자로 읽는다(가격표 사진일 때)
      if (onPhotoAi) {
        setErr('⏳ 바코드 무늬로는 못 읽어 AI로 숫자를 읽는 중…');
        const code = await onPhotoAi(f).catch(() => '');
        setErr('');
        if (code) { emit(code); return; }
      }
      setErr('사진에서 바코드를 못 읽었습니다 — 바코드만 크게, 흔들림 없이 다시 찍거나 번호를 직접 넣어 주세요.');
    } catch (e: any) {
      setErr(e.message || String(e));
    } finally {
      URL.revokeObjectURL(url); setReading(false);
    }
  };

  useEffect(() => () => stop(), []); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { if (stopSignal) stop(); }, [stopSignal]); // eslint-disable-line react-hooks/exhaustive-deps

  const submit = () => {
    const c = typed.trim();
    if (!c) return;
    setTyped('');
    cb.current(clean(c));
  };

  return (
    <div className="space-y-2">
      <div className={on ? 'relative rounded-xl overflow-hidden bg-black' : 'hidden'}>
        <video ref={video} className="w-full max-h-[45vh] object-cover" muted playsInline />
        {/* 가로 조준선 — 바코드를 이 줄에 맞추면 잘 읽힌다 */}
        <div className="pointer-events-none absolute inset-x-6 top-1/2 h-0.5 bg-red-500/80" />
        {paused && <div className="absolute inset-0 bg-black/40 flex items-center justify-center text-white text-sm">확인 중…</div>}
        <button onClick={stop} className="absolute top-2 right-2 bg-black/60 text-white text-xs rounded px-2 py-1">■ 끄기</button>
        {engine && <span className="absolute top-2 left-2 bg-black/50 text-white text-[10px] rounded px-1.5 py-0.5">{engine}</span>}
        {zoom && (
          <div className="absolute bottom-2 inset-x-2 flex gap-1 justify-center">
            {[1, 2, 3, 4].filter(z => z >= zoom.min && z <= zoom.max).map(z => (
              <button key={z} onClick={() => setZoomTo(z)}
                className={`text-xs rounded-full px-2.5 py-1 ${Math.round(zoom.cur) === z ? 'bg-white text-black' : 'bg-black/60 text-white'}`}>
                {z}×
              </button>
            ))}
          </div>
        )}
      </div>
      {on && (
        <p className="text-[11px] text-gray-500">
          바코드를 <b>빨간 줄</b>에 가로로 맞추세요. 작으면 <b>2×·3×</b>로 확대하고 15~20cm 떨어지세요.
          모니터 화면은 반사·물결무늬 때문에 잘 안 읽힐 수 있어 화면을 크게 띄우고 살짝 비스듬히 찍어 주세요.
        </p>
      )}
      <div className="grid grid-cols-2 gap-2">
        {!on ? (
          <button onClick={start} className="bg-blue-600 text-white text-sm font-medium rounded-lg py-3">
            📷 카메라로 찍기
          </button>
        ) : (
          <button onClick={stop} className="border border-gray-300 text-sm rounded-lg py-3">■ 카메라 끄기</button>
        )}
        <label className={`border border-gray-300 text-sm font-medium rounded-lg py-3 text-center cursor-pointer ${reading ? 'opacity-50 pointer-events-none' : ''}`}>
          {reading ? '⏳ 읽는 중…' : '📸 사진으로 읽기'}
          <input type="file" accept="image/*" capture="environment" className="hidden"
            onClick={() => stop()}
            onChange={e => { readPhoto(e.target.files?.[0]); e.target.value = ''; }} />
        </label>
      </div>
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
