import "@hardatlas/design-tokens/tokens.css";
import "./styles.css";
import type { Metadata } from "next";
import type { ReactNode } from "react";

export const metadata: Metadata = {
  title: "HardAtlas 知识治理后台",
  description: "通用百科的来源、Agent、证据、提案与发布治理工作台",
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="zh-CN">
      <body>{children}</body>
    </html>
  );
}
