import type { CompatibilityStatus } from "@hardatlas/contracts";

const labels: Record<CompatibilityStatus, string> = {
  compatible: "兼容",
  conditional: "有条件兼容",
  incompatible: "不兼容",
  unknown: "信息不足",
};

const icons: Record<CompatibilityStatus, string> = {
  compatible: "✓",
  conditional: "!",
  incompatible: "×",
  unknown: "?",
};

export function StatusBadge({ status }: { status: CompatibilityStatus }) {
  return (
    <span
      className={`ha-status ha-status--${status}`}
      aria-label={labels[status]}
    >
      <span aria-hidden="true">{icons[status]}</span>
      {labels[status]}
    </span>
  );
}
