import "@fontsource-variable/bricolage-grotesque";
import "@fontsource-variable/literata";
import "@/styles/tokens.css";
import "@/styles/app.css";

import type { Metadata, Viewport } from "next";
import type { ReactNode } from "react";

import { Shell } from "@/components/Shell";

export const metadata: Metadata = {
  title: { default: "Outlawz", template: "%s · Outlawz" },
  description: "Chat with your own documents. Every answer cites the page it came from.",
  icons: { icon: "/icon.svg" },
  robots: { index: false, follow: false },
};

export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  themeColor: "#14213d",
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en">
      <body>
        <Shell>{children}</Shell>
      </body>
    </html>
  );
}
