import type { NextConfig } from "next";

const config: NextConfig = {
  reactStrictMode: true,
  transpilePackages: [
    "@hardatlas/contracts",
    "@hardatlas/design-tokens",
    "@hardatlas/ui",
  ],
};

export default config;
