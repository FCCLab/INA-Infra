import type { CSSProperties } from "react";
import type { SliceAppType } from "../api/client";
import { consolePairFor, type ExternalLink } from "../lib/applicationConsoles";

const ExtIcon = (
  <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
    <path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6" />
    <polyline points="15 3 21 3 21 9" />
    <line x1="10" y1="14" x2="21" y2="3" />
  </svg>
);

const CONSOLE: CSSProperties = {
  display: "inline-flex",
  alignItems: "center",
  gap: 6,
  background: "rgba(59, 130, 246, 0.15)",
  color: "#60a5fa",
  border: "1px solid rgba(59, 130, 246, 0.35)",
  textDecoration: "none",
  fontWeight: 600,
  fontSize: 12,
  padding: "6px 12px",
  borderRadius: 6,
};

const GRAF: CSSProperties = {
  ...CONSOLE,
  background: "rgba(249, 115, 22, 0.15)",
  color: "#fb923c",
  border: "1px solid rgba(249, 115, 22, 0.35)",
};

const TAG_CONSOLE: CSSProperties = {
  fontSize: 11,
  padding: "3px 8px",
  whiteSpace: "nowrap",
  flexShrink: 0,
  fontWeight: 700,
  background: "rgba(59, 130, 246, 0.15)",
  color: "#60a5fa",
  border: "1px solid rgba(59, 130, 246, 0.4)",
  borderRadius: 6,
  textDecoration: "none",
  cursor: "pointer",
};

const TAG_GRAF: CSSProperties = {
  ...TAG_CONSOLE,
  fontWeight: 600,
  background: "rgba(249, 115, 22, 0.12)",
  color: "#fb923c",
  border: "1px solid rgba(249, 115, 22, 0.25)",
};

function Link({
  item,
  style,
  compact,
}: {
  item: ExternalLink;
  style: CSSProperties;
  compact?: boolean;
}) {
  return (
    <a href={item.href} target="_blank" rel="noreferrer" className={compact ? "tag" : "button"} style={style} title={item.title}>
      {!compact && ExtIcon}
      {item.label} ↗
    </a>
  );
}

export function AppConsoleButtons({ appType }: { appType: SliceAppType }) {
  const pair = consolePairFor(appType);
  if (!pair) return null;
  return (
    <>
      <Link item={pair.console} style={CONSOLE} />
      <Link item={pair.grafana} style={GRAF} />
    </>
  );
}

export function AppConsoleTags({ appType }: { appType: SliceAppType }) {
  const pair = consolePairFor(appType);
  if (!pair) return null;
  return (
    <>
      <Link item={pair.console} style={TAG_CONSOLE} compact />
      <Link item={pair.grafana} style={TAG_GRAF} compact />
    </>
  );
}
