import { useCallback, useEffect, useState } from "react";
import { api, ProfileRecord, ProfileClusterStatusOut } from "../api/client";
import StatusRail from "../components/StatusRail";
import Card from "../components/ui/Card";
import SectionLabel from "../components/ui/SectionLabel";

const NS_RE = /^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$/;

type Props = {
  title: string;
  subtitle: string;
};

export default function PlaceholderPage({ title, subtitle }: Props) {
  const [rec, setRec] = useState<ProfileRecord | null>(null);
  const [cluster, setCluster] = useState<ProfileClusterStatusOut | null>(null);
  const [clusterError, setClusterError] = useState<string | null>(null);
  const [clusterBusy, setClusterBusy] = useState(false);
  const [loadError, setLoadError] = useState<string | null>(null);

  const profileName = rec?.profile.name || "";
  const profileOk = NS_RE.test(profileName);

  const refreshCluster = useCallback(async () => {
    if (!profileOk) {
      setCluster(null);
      setClusterError(null);
      return;
    }
    setClusterBusy(true);
    try {
      const st = await api.clusterStatus(profileName);
      setCluster(st);
      setClusterError(null);
    } catch (e) {
      setClusterError(e instanceof Error ? e.message : String(e));
    } finally {
      setClusterBusy(false);
    }
  }, [profileName, profileOk]);

  useEffect(() => {
    (async () => {
      try {
        const list = await api.listProfiles();
        const first = list.profiles[0];
        if (first) setRec(first);
        else {
          const defs = await api.profileDefaults();
          setRec({ ...defs, updated_at: "" } as ProfileRecord);
        }
        setLoadError(null);
      } catch (e) {
        setLoadError(e instanceof Error ? e.message : String(e));
      }
    })();
  }, []);

  useEffect(() => {
    if (!profileOk) return;
    void refreshCluster();
    const id = window.setInterval(() => void refreshCluster(), 10000);
    return () => window.clearInterval(id);
  }, [profileOk, refreshCluster]);

  return (
    <div className="page-layout">
      <div className="page">
        <Card className="tier" glow>
          <SectionLabel kicker="coming soon">{title}</SectionLabel>
          <p className="hint" style={{ marginTop: 0 }}>
            {subtitle}
          </p>
          {loadError && <p className="hint error">{loadError}</p>}
          {profileName && (
            <p className="hint">
              Status reflects profile <code>{profileName}</code>. Switch back to
              Planning to edit.
            </p>
          )}
        </Card>
      </div>

      <StatusRail
        profileName={profileName}
        profileOk={profileOk}
        isExisting={Boolean(rec && rec.updated_at)}
        dirtyName={false}
        savedAt={rec?.updated_at || ""}
        networkCollapsed={true}
        sliceCount={rec?.slices?.length || 0}
        maxSlices={rec?.profile.max_slices || 0}
        plSolved={Boolean(rec?.pl_result?.ok)}
        plMessage={rec?.pl_result?.ok ? rec.pl_result.message : null}
        deployed={Boolean(rec?.deployed)}
        deployedAt={rec?.deployed_at || ""}
        cluster={cluster}
        clusterError={clusterError}
        refreshing={clusterBusy}
        onRefresh={() => void refreshCluster()}
      />
    </div>
  );
}
