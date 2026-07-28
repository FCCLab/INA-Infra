import type { ReactNode } from "react";

/** Label + hover-only ? tip (not clickable). */
export default function FieldHelp({
  label,
  help,
  children,
  className,
}: {
  label: string;
  help: string;
  children: ReactNode;
  className?: string;
}) {
  return (
    <label className={["field-help", className].filter(Boolean).join(" ")}>
      <span className="field-help-head">
        <span className="field-help-label">{label}</span>
        <span className="help-q" title={help} aria-label={help} role="img">
          ?
        </span>
      </span>
      {children}
    </label>
  );
}
