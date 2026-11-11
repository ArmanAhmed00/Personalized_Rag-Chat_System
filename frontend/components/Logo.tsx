// An original mark: a desk lamp whose light falls on a highlighted line of text.

export function Logo({ size = 28, title }: { size?: number; title?: string }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 32 32"
      role={title ? "img" : undefined}
      aria-hidden={title ? undefined : true}
      aria-label={title}
    >
      <rect x="1" y="1" width="30" height="30" rx="8" fill="#14213d" />
      <path d="M17.6 13.6 24.4 11.4 26 22.6H14.4Z" fill="#ffe36e" opacity="0.55" />
      <rect x="12.5" y="21" width="14" height="3.4" rx="1.7" fill="#ffe36e" />
      <path d="M6 25.5h20" stroke="#fff" strokeWidth="1.6" strokeLinecap="round" />
      <path
        d="M9 25.2V17l6.3-6.2"
        fill="none"
        stroke="#fff"
        strokeWidth="2"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
      <path d="M13.8 8.6 20.7 5.6 25.2 11.6 17.2 14.3Z" fill="#fff" />
      <circle cx="15.3" cy="10.8" r="1.6" fill="#14213d" stroke="#fff" strokeWidth="1.2" />
    </svg>
  );
}
