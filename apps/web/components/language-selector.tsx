"use client";

import type { SupportedLocale } from "../lib/locale";

export function LanguageSelector({
  initialLocale,
}: {
  initialLocale: SupportedLocale;
}) {
  return (
    <label className="language">
      <span className="sr-only">内容语言</span>
      <select
        aria-label="内容语言"
        defaultValue={initialLocale}
        onChange={(event) => {
          const locale = event.currentTarget.value;
          document.cookie = `atlas-locale=${encodeURIComponent(
            locale,
          )}; Path=/; Max-Age=31536000; SameSite=Lax`;
          document.documentElement.lang = locale;
          window.location.reload();
        }}
      >
        <option value="zh-CN">简体中文</option>
        <option value="en">English</option>
      </select>
    </label>
  );
}
