import { getPreferredLocale } from "../lib/locale";
import { LanguageSelector } from "./language-selector";

export async function SiteHeader() {
  const adminUrl = process.env.HARDATLAS_ADMIN_URL ?? "http://localhost:3001";
  const locale = await getPreferredLocale();
  return (
    <header className="site-header">
      <a className="brand" href="/">
        <span className="brand-mark">A</span>
        <span>ATLAS</span>
      </a>
      <nav aria-label="主导航">
        <a href="/">探索</a>
        <a href="/categories">分类</a>
        <a href="/topics">专题</a>
        <a href="/about">关于</a>
        <a href="/search?q=">全部条目</a>
        <a href="/ask">知识问答</a>
        <a href="/collections">我的收藏</a>
      </nav>
      <div className="header-actions">
        <LanguageSelector initialLocale={locale} />
        <a className="workspace" href="/collections">
          个人空间
        </a>
        <a className="maintenance-link" href={adminUrl}>
          进入工作台
        </a>
      </div>
    </header>
  );
}
