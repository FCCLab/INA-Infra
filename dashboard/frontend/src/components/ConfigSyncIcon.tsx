import type { ConfigSyncStatus } from "../api/client";

export function configSyncTone(overall: string | undefined): string {
  if (overall === "synced") return "synced";
  if (overall === "syncing") return "syncing";
  if (overall === "error" || overall === "missing") return "error";
  return "unknown";
}

export function configSyncTitle(cs: ConfigSyncStatus | null | undefined): string {
  if (!cs) return "Config Sync: unknown";
  const parts = [
    `Config Sync: ${cs.summary || cs.overall || "unknown"}`,
    cs.name ? `RootSync ${cs.name}` : null,
    cs.repo ? cs.repo.replace(/\.git$/, "").split("/").slice(-2).join("/") : null,
    cs.branch ? `branch ${cs.branch}` : null,
    cs.source_commit ? `src ${cs.source_commit}` : null,
    cs.last_synced_commit ? `applied ${cs.last_synced_commit}` : null,
    cs.message || null,
    cs.error || null,
  ].filter(Boolean);
  return parts.join(" · ");
}

function SyncArrowsIcon() {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" aria-hidden="true">
      <path d="M21 12a9 9 0 0 0-15.5-6.4L3 8" strokeLinecap="round" strokeLinejoin="round" />
      <path d="M3 3v5h5" strokeLinecap="round" strokeLinejoin="round" />
      <path d="M3 12a9 9 0 0 0 15.5 6.4L21 16" strokeLinecap="round" strokeLinejoin="round" />
      <path d="M21 21v-5h-5" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

export function configSyncLabel(cs: ConfigSyncStatus | null | undefined): string {
  const overall = cs?.overall || "unknown";
  if (overall === "synced") return "Synced";
  if (overall === "syncing") return "Syncing";
  if (overall === "error") return cs?.stalled ? "Stalled" : "Sync err";
  if (overall === "missing") return "No sync";
  return "Sync —";
}

type Props = {
  status?: ConfigSyncStatus | null;
  className?: string;
  /** Icon-only (default) or icon + status word for the cluster header. */
  showLabel?: boolean;
};

/** Compact GitOps status glyph for a cluster RootSync. */
export default function ConfigSyncIcon({ status, className, showLabel }: Props) {
  const overall = status?.overall || "unknown";
  const tone = configSyncTone(overall);
  const word = configSyncLabel(status);
  const extra = className ? ` ${className}` : "";
  if (showLabel) {
    return (
      <span
        className={`cs-badge ${tone}${extra}`}
        title={configSyncTitle(status)}
        aria-label={`Config Sync ${status?.summary || word}`}
      >
        <span className={`cs-icon ${tone}`} aria-hidden="true">
          <SyncArrowsIcon />
        </span>
        <span className="cs-badge-text">{word}</span>
      </span>
    );
  }
  return (
    <span
      className={`cs-icon ${tone}${extra}`}
      title={configSyncTitle(status)}
      aria-label={`Config Sync ${status?.summary || word}`}
      role="img"
    >
      <SyncArrowsIcon />
    </span>
  );
}
