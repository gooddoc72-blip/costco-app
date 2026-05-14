/** 공통 폼 입력 필드 — label + input + hint */
interface FieldProps {
  label: string;
  value: string | number;
  onChange: (v: string | number) => void;
  type?: string;
  step?: number;
  hint?: string;
}

export default function Field(p: FieldProps) {
  return (
    <div>
      <label className="block text-xs text-gray-600 mb-1">{p.label}</label>
      <input
        type={p.type || 'text'}
        step={p.step}
        value={p.value}
        onChange={(e) => {
          const v = p.type === 'number' ? parseFloat(e.target.value) || 0 : e.target.value;
          p.onChange(v);
        }}
        className="w-full px-3 py-2 border border-gray-200 rounded-lg text-sm"
      />
      {p.hint && <p className="text-[10px] text-gray-400 mt-1">{p.hint}</p>}
    </div>
  );
}
