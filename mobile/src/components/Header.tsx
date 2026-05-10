interface HeaderProps {
  title: string;
  subtitle?: string;
  right?: React.ReactNode;
}

export default function Header({ title, subtitle, right }: HeaderProps) {
  return (
    <header className="sticky top-0 z-30 bg-white border-b border-gray-200">
      <div className="flex items-center justify-between px-4 h-14">
        <div>
          <h1 className="text-base font-bold text-gray-900">{title}</h1>
          {subtitle && <p className="text-xs text-gray-500">{subtitle}</p>}
        </div>
        {right}
      </div>
    </header>
  );
}
