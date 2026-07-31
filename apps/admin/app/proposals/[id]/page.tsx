import { ProposalReview } from "../../../components/proposal-review";

export default async function ProposalPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  return <ProposalReview proposalId={id} />;
}
