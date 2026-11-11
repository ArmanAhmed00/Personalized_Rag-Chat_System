// A small set of stroke icons (24×24 grid, 1.8px strokes). Decorative unless given a label.

const PATHS: Record<string, string> = {
  home: "M4 11.5 12 5l8 6.5V20a1 1 0 0 1-1 1h-4.5v-6h-5v6H5a1 1 0 0 1-1-1z",
  library: "M5 4h4v16H5zM10 4h4v16h-4zM15.5 5.2l3.6-1 3 15.2-3.6 1z",
  search: "M10.5 17a6.5 6.5 0 1 0 0-13 6.5 6.5 0 0 0 0 13zM15.2 15.2 20 20",
  chat: "M5 5h14a1 1 0 0 1 1 1v9a1 1 0 0 1-1 1h-8l-5 4v-4H5a1 1 0 0 1-1-1V6a1 1 0 0 1 1-1z",
  analysis: "M4 20h16M7 16v-5M12 16V6M17 16v-8",
  preferences: "M5 7h9M18 7h1M5 17h3M12 17h7M16 4.5v5M10 14.5v5",
  settings:
    "M12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6zM19.4 15a1.6 1.6 0 0 0 .3 1.8l.1.1-1.9 1.9-.1-.1a1.6 1.6 0 0 0-1.8-.3 1.6 1.6 0 0 0-1 1.5V20h-2.7v-.2a1.6 1.6 0 0 0-1-1.5 1.6 1.6 0 0 0-1.8.3l-.1.1-1.9-1.9.1-.1a1.6 1.6 0 0 0 .3-1.8 1.6 1.6 0 0 0-1.5-1H4v-2.7h.2a1.6 1.6 0 0 0 1.5-1 1.6 1.6 0 0 0-.3-1.8l-.1-.1 1.9-1.9.1.1a1.6 1.6 0 0 0 1.8.3h.1a1.6 1.6 0 0 0 1-1.5V4h2.7v.2a1.6 1.6 0 0 0 1 1.5 1.6 1.6 0 0 0 1.8-.3l.1-.1 1.9 1.9-.1.1a1.6 1.6 0 0 0-.3 1.8v.1a1.6 1.6 0 0 0 1.5 1h.2v2.7h-.2a1.6 1.6 0 0 0-1.5 1z",
  overview: "M4 4h7v7H4zM13 4h7v4h-7zM13 10h7v10h-7zM4 13h7v7H4z",
  metrics: "M4 19 9 13l4 3 7-9",
  traces: "M4 6h9M7 11h10M10 16h7M4 21h16",
  processing: "M12 4v4M12 16v4M4 12h4M16 12h4M6.3 6.3l2.8 2.8M14.9 14.9l2.8 2.8M6.3 17.7l2.8-2.8M14.9 9.1l2.8-2.8",
  evaluations: "M9 11l2.5 2.5L16 9M5 4h14v16H5z",
  activity: "M8 5h11M8 12h11M8 19h11M4.5 5h.01M4.5 12h.01M4.5 19h.01",
  data: "M12 3c4.4 0 8 1.3 8 3s-3.6 3-8 3-8-1.3-8-3 3.6-3 8-3zM4 6v12c0 1.7 3.6 3 8 3s8-1.3 8-3V6M4 12c0 1.7 3.6 3 8 3s8-1.3 8-3",
  menu: "M4 7h16M4 12h16M4 17h16",
  close: "M6 6l12 12M18 6 6 18",
  upload: "M12 16V5M7.5 9.5 12 5l4.5 4.5M5 19h14",
  plus: "M12 5v14M5 12h14",
  thumbUp: "M7 11v9H4v-9zM7 11l4-7c1.4 0 2.3 1.1 2 2.4L12.5 10H19a1.5 1.5 0 0 1 1.5 1.7l-1.2 7A1.5 1.5 0 0 1 17.8 20H7",
  thumbDown: "M17 13V4h3v9zM17 13l-4 7c-1.4 0-2.3-1.1-2-2.4l.5-3.6H5a1.5 1.5 0 0 1-1.5-1.7l1.2-7A1.5 1.5 0 0 1 6.2 4H17",
  folder: "M4 6a1 1 0 0 1 1-1h4.5l2 2H19a1 1 0 0 1 1 1v10a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1z",
  external: "M14 5h5v5M19 5l-8 8M18 14v5H5V6h5",
  trash: "M5 7h14M10 7V5h4v2M7 7l1 13h8l1-13",
  refresh: "M19 8a7.5 7.5 0 1 0 .9 6M19 4v4h-4",
  stop: "M7 7h10v10H7z",
  send: "M5 12h13M13 6l6 6-6 6",
  check: "M5 12.5l4.5 4.5L19 7.5",
  warning: "M12 4 21 20H3zM12 10v4M12 17h.01",
  lock: "M7 11V8a5 5 0 0 1 10 0v3M6 11h12v9H6z",
  download: "M12 5v11M7.5 11.5 12 16l4.5-4.5M5 19h14",
  copy: "M8 8h11v11H8zM5 16V5h11",
};

export type IconName = keyof typeof PATHS;

export function Icon({ name, size = 18, label }: { name: IconName | string; size?: number; label?: string }) {
  const d = PATHS[name] ?? PATHS.home;
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={1.8}
      strokeLinecap="round"
      strokeLinejoin="round"
      role={label ? "img" : undefined}
      aria-hidden={label ? undefined : true}
      aria-label={label}
      focusable="false"
    >
      <path d={d} />
    </svg>
  );
}
