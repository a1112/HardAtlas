import { CollectionWorkspace } from "../../components/collection-workspace";
import { SiteHeader } from "../../components/site-header";

export const dynamic = "force-dynamic";

export default function CollectionsPage() {
  return (
    <main>
      <SiteHeader />
      <CollectionWorkspace />
    </main>
  );
}
