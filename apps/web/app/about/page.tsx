import { SiteHeader } from "../../components/site-header";

export default function AboutPage() {
  return (
    <main className="about-page">
      <SiteHeader />
      <section className="about-hero">
        <p className="eyebrow">ABOUT ATLAS</p>
        <h1>Atlas 维基栈</h1>
        <p>
          Atlas 是一个面向长生命周期知识资产的百科式产品。首页以搜索与分类为第一入口，知识条目与关系由统一版本化模型驱动，分类、条目、关系与变更全过程可追溯。
        </p>
      </section>

      <section className="about-grid">
        <article>
          <h2>架构目标</h2>
          <p>
            以搜索与分类为主入口，优先支持条目发现、分层分类浏览、实体关系导航和来源可审计。
            任何新知识域通过可扩展的 Domain Pack 接入，不要求重构前端。
          </p>
        </article>
        <article>
          <h2>扩展策略</h2>
          <p>
            领域与实体模型由统一 Schema 驱动。新增动物、植物、电子元件后，可继续扩展到地理、数学、历史等领域。
            只要发布新的 pack，即可通过运营后台进入运行中治理闭环。
          </p>
        </article>
        <article>
          <h2>治理与可信</h2>
          <p>
            变更先进入任务与提案，再经过规则、评审和发布。知识与关系始终保留版本与来源
            元数据，支持回滚、比对和复核。
          </p>
        </article>
        <article>
          <h2>与硬件能力关系</h2>
          <p>
            兼容性与设备扫描能力为可选扩展能力，不是系统入口依赖。若未启用，Atlas 仍可完整运行为通用百科。
          </p>
        </article>
      </section>
    </main>
  );
}
