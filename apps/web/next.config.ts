import type { NextConfig } from "next";

const config: NextConfig = {
  reactStrictMode: true,
  trailingSlash: true,
  basePath: "/apps/hardatlas",
  experimental: { cpus: 1 },
  transpilePackages: [
    "@hardatlas/contracts",
    "@hardatlas/design-tokens",
    "@hardatlas/ui",
  ],
};

export default config;
