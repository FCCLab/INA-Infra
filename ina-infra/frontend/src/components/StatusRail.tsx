import {
  ClusterDeployStatus,
  ConfigSyncStatus,
  ProfileClusterStatusOut,
} from "../api/client";
import Card from "./ui/Card";
import SectionLabel from "./ui/SectionLabel";

type Row = {
  label: string;
  value: string;
  tone?: "ok" | "warn" | "err" | "muted";
  title?: string;
};

type Props = {
  profileName: string;
  profileOk: boolean;
  isExisting: boolean;
  dirtyName: boolean;
  savedAt: string;
  networkCollapsed: boolean;
  sliceCount: number;
  maxSlices: number;
  plSolved: boolean;
  plMessage?: string | null;
  deployed?: boolean;
  deployedAt?: string;
  cluster: ProfileClusterStatusOut | null;
  clusterError: string | null;
  refreshing: boolean;
  onRefresh: () => void;
  rolloutBusy?: boolean;
  onRollout?: () => void;
  onStopRollout?: () => void;
};

function toneClass(tone?: Row["tone"]) {
  if (tone === "ok") return "status-pill ok";
  if (tone === "warn") return "status-pill warn";
  if (tone === "err") return "status-pill err";
  return "status-pill muted";
}

function overallTone(
  overall: string | undefined,
): Row["tone"] {
  if (overall === "ready") return "ok";
  if (overall === "degraded" || overall === "error" || overall === "missing")
    return "err";
  if (overall === "partial" || overall === "empty") return "warn";
  return "muted";
}

function syncTone(overall: string | undefined): Row["tone"] {
  if (overall === "synced") return "ok";
  if (overall === "syncing") return "warn";
  if (overall === "error" || overall === "missing") return "err";
  return "muted";
}

function syncTitle(cs: ConfigSyncStatus | null | undefined): string {
  if (!cs) return "—";
  return cs.summary || cs.overall || "—";
}

function ClusterBlock({ st }: { st: ClusterDeployStatus }) {
  const title = st.cluster.charAt(0).toUpperCase() + st.cluster.slice(1);
  const cs = st.config_sync;
  const syncHint = cs
    ? [
        cs.name || "RootSync",
        cs.source_commit ? `src ${cs.source_commit}` : null,
        cs.last_synced_commit ? `applied ${cs.last_synced_commit}` : null,
        cs.message || null,
      ]
        .filter(Boolean)
        .join(" · ")
    : "";
  return (
    <div className="status-cluster">
      <div className="status-cluster-head">
        <strong>{title}</strong>
        <span className={toneClass(overallTone(st.overall))}>{st.summary}</span>
      </div>
      <ul className="status-rows">
        <li>
          <span className="status-label">Namespace</span>
          <span className={toneClass(st.namespace_exists ? "ok" : "err")}>
            {st.namespace_exists
              ? st.namespace_phase || "Active"
              : "Missing"}
          </span>
        </li>
        <li>
          <span className="status-label">Config Sync</span>
          <span
            className={toneClass(syncTone(cs?.overall))}
            title={syncHint || undefined}
          >
            {syncTitle(cs)}
          </span>
        </li>
      </ul>
      {cs?.error && <p className="hint error">{cs.error}</p>}
      {st.error && <p className="hint error">{st.error}</p>}
      {st.deployments.length === 0 ? (
        <p className="hint">
          {st.namespace_exists
            ? "No Deployments in namespace"
            : st.overall === "missing"
              ? "—"
              : "No Deployments"}
        </p>
      ) : (
        <div className="table-wrap status-deploy-table">
          <table>
            <thead>
              <tr>
                <th>Deployment</th>
                <th>Ready</th>
                <th>Status</th>
              </tr>
            </thead>
            <tbody>
              {st.deployments.map((d) => (
                <tr key={`${st.cluster}-${d.name}`}>
                  <td>
                    <code>{d.name}</code>
                  </td>
                  <td>{d.ready_text}</td>
                  <td>
                    <span
                      className={toneClass(
                        d.ok ? "ok" : d.exists ? "warn" : "err",
                      )}
                    >
                      {d.status}
                    </span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

export default function StatusRail({
  profileName,
  profileOk,
  isExisting,
  dirtyName,
  savedAt,
  networkCollapsed,
  sliceCount,
  maxSlices,
  plSolved,
  plMessage,
  deployed = false,
  deployedAt = "",
  cluster,
  clusterError,
  refreshing,
  onRefresh,
  rolloutBusy = false,
  onRollout,
  onStopRollout,
}: Props) {
  const profileRows: Row[] = [
    {
      label: "Name",
      value: profileName || "—",
      tone: profileOk ? "ok" : "err",
    },
    {
      label: "DB",
      value: !profileOk
        ? "Invalid"
        : dirtyName
          ? "Renamed (unsaved)"
          : isExisting
            ? "Saved"
            : "New",
      tone: !profileOk ? "err" : dirtyName ? "warn" : isExisting ? "ok" : "warn",
    },
    {
      label: "Deploy",
      value: deployed
        ? deployedAt
          ? `Yes · ${deployedAt.replace("T", " ").slice(0, 16)}`
          : "Yes"
        : "No",
      tone: deployed ? "ok" : "muted",
    },
    {
      label: "Last saved",
      value: savedAt ? savedAt.replace("T", " ").slice(0, 19) : "—",
      tone: "muted",
    },
  ];

  const networkRows: Row[] = [
    {
      label: "Panel",
      value: networkCollapsed ? "Collapsed" : "Editing",
      tone: "muted",
    },
    {
      label: "Scope",
      value: "Per-profile substrate",
      tone: "ok",
    },
  ];

  const sliceRows: Row[] = [
    {
      label: "Slices",
      value: `${sliceCount} / ${maxSlices}`,
      tone: sliceCount > 0 ? "ok" : "warn",
    },
    {
      label: "PL",
      value: plSolved ? "Solved" : "Not solved",
      tone: plSolved ? "ok" : "warn",
    },
  ];
  if (plSolved && plMessage) {
    sliceRows.push({
      label: "Result",
      value: plMessage.length > 48 ? `${plMessage.slice(0, 48)}…` : plMessage,
      tone: "muted",
    });
  }

  const clusterBlocks: ClusterDeployStatus[] =
    cluster?.clusters && cluster.clusters.length > 0
      ? cluster.clusters
      : cluster
        ? [
            {
              cluster: cluster.cluster || "central",
              context: cluster.context || "",
              namespace: cluster.namespace,
              namespace_exists: cluster.namespace_exists,
              namespace_phase: cluster.namespace_phase,
              overall: cluster.overall,
              summary: cluster.summary,
              error: cluster.error,
              deployments: cluster.deployments,
              expected: cluster.expected,
              config_sync: cluster.config_sync ?? null,
            },
          ]
        : [];

  const syncEntries =
    cluster?.config_syncs && cluster.config_syncs.length > 0
      ? cluster.config_syncs
      : clusterBlocks.map((st) => ({
          cluster: st.cluster,
          context: st.context,
          config_sync: st.config_sync as NonNullable<typeof st.config_sync>,
        }));

  const syncRows: Row[] = cluster
    ? [
        {
          label: "Overall",
          value: cluster.config_sync_summary || cluster.config_sync_overall || "—",
          tone: syncTone(cluster.config_sync_overall),
        },
        ...syncEntries.map((row) => {
          const cs = row.config_sync;
          const label =
            row.cluster.charAt(0).toUpperCase() + row.cluster.slice(1);
          const hint = cs
            ? [
                cs.name || "RootSync",
                cs.source_commit ? `src ${cs.source_commit}` : null,
                cs.last_synced_commit
                  ? `applied ${cs.last_synced_commit}`
                  : null,
                cs.message || null,
              ]
                .filter(Boolean)
                .join(" · ")
            : undefined;
          return {
            label,
            value: syncTitle(cs),
            tone: syncTone(cs?.overall),
            title: hint,
          };
        }),
      ]
    : [];

  return (
    <Card className="status-rail">
      <div className="panel-head">
        <SectionLabel kicker="live">Status</SectionLabel>
        <div className="actions">
          {onRollout && (
            <button
              type="button"
              disabled={refreshing || rolloutBusy || !profileOk}
              onClick={onRollout}
              title="Staged restart: UPF → SMF → PFCP → CU-CP → CU-UP → DU → UEs"
            >
              {rolloutBusy ? "Rolling…" : "Rollout"}
            </button>
          )}
          {rolloutBusy && onStopRollout && (
            <button
              type="button"
              className="danger"
              onClick={onStopRollout}
              title="Stop the staged rollout script"
            >
              Stop
            </button>
          )}
          <button type="button" disabled={refreshing || !profileOk} onClick={onRefresh}>
            {refreshing ? "…" : "Refresh"}
          </button>
        </div>
      </div>

      <div className="status-block">
        <h3>Profile</h3>
        <ul className="status-rows">
          {profileRows.map((r) => (
            <li key={r.label}>
              <span className="status-label">{r.label}</span>
              <span className={toneClass(r.tone)}>{r.value}</span>
            </li>
          ))}
        </ul>
      </div>

      <div className="status-block">
        <h3>Network settings</h3>
        <ul className="status-rows">
          {networkRows.map((r) => (
            <li key={r.label}>
              <span className="status-label">{r.label}</span>
              <span className={toneClass(r.tone)}>{r.value}</span>
            </li>
          ))}
        </ul>
      </div>

      <div className="status-block">
        <h3>Slice SLAs / PL</h3>
        <ul className="status-rows">
          {sliceRows.map((r) => (
            <li key={r.label}>
              <span className="status-label">{r.label}</span>
              <span className={toneClass(r.tone)} title={r.value}>
                {r.value}
              </span>
            </li>
          ))}
        </ul>
      </div>

      <div className="status-block">
        <h3>
          Config Sync{" "}
          <span className="muted">(RootSync)</span>
        </h3>
        {clusterError && <p className="hint error">{clusterError}</p>}
        {!cluster && !clusterError && (
          <p className="hint">No sync status yet.</p>
        )}
        {syncRows.length > 0 && (
          <ul className="status-rows">
            {syncRows.map((r) => (
              <li key={`sync-${r.label}`}>
                <span className="status-label">{r.label}</span>
                <span
                  className={toneClass(r.tone)}
                  title={r.title || r.value}
                >
                  {r.value}
                </span>
              </li>
            ))}
          </ul>
        )}
        {syncEntries.length > 0 && (
          <p className="hint">
            mgmt + central / regional / edge · hover for commit detail
          </p>
        )}
      </div>

      <div className="status-block">
        <h3>
          Deployments{" "}
          <span className="muted">
            ({cluster ? cluster.summary : "central / regional / edge"})
          </span>
        </h3>
        {clusterError && <p className="hint error">{clusterError}</p>}
        {!cluster && !clusterError && (
          <p className="hint">No cluster status yet.</p>
        )}
        {clusterBlocks.map((st) => (
          <ClusterBlock key={st.cluster} st={st} />
        ))}
        {clusterBlocks.length > 0 && (
          <p className="hint">
            Polls <code>{profileName}</code> on central / regional / edge · 1/1 =
            Running
          </p>
        )}
      </div>
    </Card>
  );
}
