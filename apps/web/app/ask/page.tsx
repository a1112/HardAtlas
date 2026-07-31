import { KnowledgeAnswerWorkbench } from "../../components/knowledge-answer-workbench";
import { SiteHeader } from "../../components/site-header";
import { getPreferredLocale } from "../../lib/locale";

export const dynamic = "force-dynamic";

export default async function AskPage({
  searchParams,
}: {
  searchParams: Promise<{ q?: string }>;
}) {
  const [params, locale] = await Promise.all([
    searchParams,
    getPreferredLocale(),
  ]);
  return (
    <main>
      <SiteHeader />
      <KnowledgeAnswerWorkbench
        initialQuestion={(params.q ?? "").slice(0, 1000)}
        locale={locale}
      />
    </main>
  );
}
