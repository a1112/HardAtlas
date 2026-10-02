import type { NextConfig } from "next";

const config: NextConfig = {
  reactStrictMode: true,
  trailingSlash: true,
  basePath: "/apps/hardatlas-admin",
  experimental: { cpus: 1 },
  transpilePackages: ["@hardatlas/design-tokens", "@hardatlas/ui"],
};

export default config;
