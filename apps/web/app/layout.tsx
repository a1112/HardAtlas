import "@hardatlas/design-tokens/tokens.css";
import "./styles.css";
import type { Metadata } from "next";
import type { ReactNode } from "react";
import { getPreferredLocale } from "../lib/locale";

export const metadata: Metadata = {
  title: "Atlas / 专业知识百科",
  description:
    "一个持续生长、来源透明、由专家与智能 Agent 共同维护的专业百科全书。",
};

export default async function RootLayout({
  children,
}: {
  children: ReactNode;
}) {
  const locale = await getPreferredLocale();
  return (
    <html lang={locale}>
      <body>{children}</body>
    </html>
  );
}
