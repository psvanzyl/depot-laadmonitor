import Link from "next/link";

/**
 * The dex mark: a charging bolt inside a rounded badge, drawn inline so there is
 * no image request. It spins on hover exactly like Footballdex's ball, which is
 * the house style across the dex family.
 */
export function ChargeMark({ className = "h-9 w-9" }: { className?: string }) {
  return (
    <svg viewBox="0 0 24 24" className={className} aria-hidden="true">
      <defs>
        <linearGradient id="dexbadge" x1="0" y1="0" x2="1" y2="1">
          <stop offset="0%" stopColor="#f59e0b" />
          <stop offset="100%" stopColor="#b45309" />
        </linearGradient>
      </defs>
      <rect x="1.5" y="1.5" width="21" height="21" rx="6" fill="url(#dexbadge)" />
      <rect x="1.5" y="1.5" width="21" height="21" rx="6" fill="none" stroke="#7c2d12" strokeWidth="1.2" />
      {/* bolt */}
      <path
        d="M13.6 4.6 7.2 13h3.9l-1 6.4 6.5-8.6h-4z"
        fill="#fffbeb"
        stroke="#7c2d12"
        strokeWidth="0.8"
        strokeLinejoin="round"
      />
      {/* plug pins, so it reads as a charge point and not just a spark */}
      <circle cx="6.1" cy="6.1" r="1" fill="#fffbeb" />
      <circle cx="17.9" cy="17.9" r="1" fill="#fffbeb" />
    </svg>
  );
}

export function DexLogo({ href = "/", suffix = "Dex", prefix = "DepotCharge" }: {
  href?: string;
  prefix?: string;
  suffix?: string;
}) {
  return (
    <Link href={href} className="group flex items-center gap-2.5">
      <ChargeMark className="h-9 w-9 transition-transform duration-500 group-hover:rotate-180" />
      <span className="text-lg font-bold tracking-tight text-slate-900 whitespace-nowrap">
        {prefix}
        <span className="text-amber-600">{suffix}</span>
      </span>
    </Link>
  );
}
