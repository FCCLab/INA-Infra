const STATUS_DOT_CLASS = {
  ok: "dot-ok",
  warn: "dot-warn",
  bad: "dot-bad",
} as const;

export default function StatusDot({
  state = "ok",
  label,
  title,
}: {
  state?: keyof typeof STATUS_DOT_CLASS;
  label?: string;
  title?: string;
}) {
  const dotClass = STATUS_DOT_CLASS[state] || STATUS_DOT_CLASS.ok;
  return (
    <span className="status-line" title={title || label}>
      <span className={"status-dot " + dotClass} />
      {label && <span className="mono">{label}</span>}
    </span>
  );
}
