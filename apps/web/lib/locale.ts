import { cookies, headers } from "next/headers";

export const supportedLocales = ["zh-CN", "en"] as const;
export type SupportedLocale = (typeof supportedLocales)[number];

function normalizeLocale(
  value: string | undefined,
): SupportedLocale | undefined {
  if (!value) return undefined;
  const normalized = value.trim().toLowerCase();
  if (normalized === "zh" || normalized.startsWith("zh-")) return "zh-CN";
  if (normalized === "en" || normalized.startsWith("en-")) return "en";
  return undefined;
}

export async function getPreferredLocale(): Promise<SupportedLocale> {
  const cookieLocale = normalizeLocale(
    (await cookies()).get("atlas-locale")?.value,
  );
  if (cookieLocale) return cookieLocale;
  const accepted = (await headers()).get("accept-language")?.split(",") ?? [];
  for (const item of accepted) {
    const locale = normalizeLocale(item.split(";")[0]);
    if (locale) return locale;
  }
  return "zh-CN";
}
