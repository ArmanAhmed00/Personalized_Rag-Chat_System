import type { NextConfig } from "next";

// Built as a static export: the Python app serves these files on the same port as the API,
// so the browser never talks to a second server and no Node.js is needed at runtime.
const config: NextConfig = {
  output: "export",
  trailingSlash: true,
  images: { unoptimized: true },
  poweredByHeader: false,
  reactStrictMode: true,
};

export default config;
