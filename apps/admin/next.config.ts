import type { NextConfig } from "next";

const config: NextConfig = {
  reactStrictMode: true,
  transpilePackages: ["@hardatlas/design-tokens", "@hardatlas/ui"],
};

export default config;
