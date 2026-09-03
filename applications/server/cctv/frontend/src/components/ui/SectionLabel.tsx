import type { ReactNode } from "react";

export default function SectionLabel({
  children,
  kicker,
}: {
  children: ReactNode;
  kicker?: ReactNode;
}) {
  return (
    <div className="section-label">
      <span className="section-bar" />
      <span className="section-text">{children}</span>
      {kicker != null && kicker !== false && (
        <span className="section-kicker">{kicker}</span>
      )}
    </div>
  );
}
