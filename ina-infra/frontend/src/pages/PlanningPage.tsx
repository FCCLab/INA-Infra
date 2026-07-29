import { useEffect, useMemo, useRef, useState } from "react";
import {
  api,
  DEFAULT_PROFILE,
  DEFAULT_SLICES,
  EDGE_RF_NODES,
  EdgeNodeOut,
  IpPlan,
  NetworkIn,
  PlSolveResponse,
  Profile,
  ProfileRecord,
  SliceIn,
  StreamHandlers,
} from "../api/client";
import Topology from "../components/Topology";
import NetworkSettingsForm from "../components/NetworkSettingsForm";
import FieldHelp from "../components/FieldHelp";
import StatusRail from "../components/StatusRail";
import type { ProfileClusterStatusOut } from "../api/client";

const emptySlice = (id: number): SliceIn => ({
  id,
  t_bar: 40,
  d_bar: 100,
  h_s: 0,
  eta_t0: 2.5,
  slice_type: "custom",
});

const NS_RE = /^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$/;

const DEFAULT_NETWORK: NetworkIn = {
  b_total: 273,
  w_c: 1.0,
  w_p: 1000.0,
  beta_demand: 0.1,
  p_prb_ded: 0.5,
  p_prb_prio: 0.1,
  alpha_cu: 1.02,
  alpha_upf: 0.81,
  gamma_c: 0.5,
  gamma_r: 0.008,
  gamma_g: 1.0,
  min_r_cu: 10,
  min_r_upf: 10,
  c_n_capacity: { "0": 55, "1": 52, "2": 61 },
  r_n_capacity: { "0": 64, "1": 64, "2": 64 },
  c_a_capacity: { "0": 41, "1": 25, "2": 90 },
  r_a_capacity: { "0": 2600, "1": 1400, "2": 5625 },
  g_a_capacity: { "0": 22, "1": 12, "2": 45 },
  p_c: { "0": 2.5, "1": 0.25, "2": 0.001 },
  p_r: { "0": 0.5, "1": 0.05, "2": 0.002 },
  p_g: { "0": 2.5, "1": 0.5, "2": 0.1 },
  d_rf: 20,
  d_f1: { "0": 2, "1": 20, "2": 40 },
  d_n3: {
    "0-0": 2, "0-1": 20, "0-2": 40,
    "1-0": 20, "1-1": 2, "1-2": 20,
    "2-0": 40, "2-1": 20, "2-2": 2,
  },
  d_n6: {
    "0-0": 2, "0-1": 25, "0-2": 50,
    "1-0": 25, "1-1": 2, "1-2": 35,
    "2-0": 50, "2-1": 35, "2-2": 2,
  },
};

/** Normalize d_f1 to CU-site keys; strip "0-j" pair form / drop du_site. */
function normalizeNetwork(raw: NetworkIn | null | undefined): NetworkIn {
  const { du_site: _drop, ...rest } = (raw || {}) as NetworkIn & { du_site?: number };
  const n = { ...DEFAULT_NETWORK, ...rest };
  const df1 = n.d_f1 || {};
  const keys = Object.keys(df1);
  const hasPairs = keys.some((k) => k.includes("-") || k.includes(","));
  if (hasPairs) {
    const site: Record<string, number> = {};
    for (const j of [0, 1, 2]) {
      const v = df1[`0-${j}`] ?? df1[`0,${j}`] ?? df1[String(j)];
      site[String(j)] =
        v !== undefined ? Number(v) : Number(DEFAULT_NETWORK.d_f1![String(j)]);
    }
    n.d_f1 = site;
  }
  return n;
}

export default function PlanningPage() {
  const [profile, setProfile] = useState<Profile>(DEFAULT_PROFILE);
  const [slices, setSlices] = useState<SliceIn[]>(DEFAULT_SLICES);
  const [networkIn, setNetworkIn] = useState<NetworkIn>(DEFAULT_NETWORK);
  const [profileNames, setProfileNames] = useState<string[]>([]);
  const [selectedName, setSelectedName] = useState<string>("");
  const [savedAt, setSavedAt] = useState<string>("");
  const [showNet, setShowNet] = useState(false);
  const [result, setResult] = useState<PlSolveResponse | null>(null);
  const [consoleOpen, setConsoleOpen] = useState(false);
  const [consoleTitle, setConsoleTitle] = useState("Console");
  const [consoleText, setConsoleText] = useState("");
  const [consoleMessage, setConsoleMessage] = useState("");
  const [consoleOk, setConsoleOk] = useState<boolean | null>(null);
  const [consoleFiles, setConsoleFiles] = useState<string[]>([]);
  const [consoleFilesLabel, setConsoleFilesLabel] = useState("Files");
  const consoleRef = useRef<HTMLPreElement>(null);
  const streamAbortRef = useRef<AbortController | null>(null);
  const [rolloutBusy, setRolloutBusy] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [status, setStatus] = useState<string | null>(null);
  const [commitMsg, setCommitMsg] = useState("ina-pl: deploy profile manifests");
  const [deployed, setDeployed] = useState(false);
  const [deployedAt, setDeployedAt] = useState<string>("");
  const [deployFiles, setDeployFiles] = useState<string[]>([]);
  const [clusterStatus, setClusterStatus] = useState<ProfileClusterStatusOut | null>(
    null,
  );
  const [clusterError, setClusterError] = useState<string | null>(null);
  const [clusterBusy, setClusterBusy] = useState(false);
  const [edgeNodes, setEdgeNodes] = useState<EdgeNodeOut[]>([]);
  const [edgeNodesError, setEdgeNodesError] = useState<string | null>(null);

  async function refreshEdgeNodes() {
    try {
      const out = await api.edgeNodes();
      setEdgeNodes(out.nodes || []);
      setEdgeNodesError(out.error || null);
      return out;
    } catch (e) {
      setEdgeNodesError(e instanceof Error ? e.message : String(e));
      setEdgeNodes([]);
      return null;
    }
  }

  const edgeRfOptions = useMemo(() => {
    const names = edgeNodes.map((n) => n.name);
    const fallback = [...EDGE_RF_NODES];
    const merged = names.length > 0 ? names : fallback;
    // Keep current profile selection visible even if node went NotReady.
    for (const cur of [profile.du_node, profile.ue_node]) {
      if (cur && !merged.includes(cur)) merged.push(cur);
    }
    return merged;
  }, [edgeNodes, profile.du_node, profile.ue_node]);

  const edgeNodeByName = useMemo(() => {
    const m = new Map<string, EdgeNodeOut>();
    for (const n of edgeNodes) m.set(n.name, n);
    return m;
  }, [edgeNodes]);

  function edgeOptionLabel(name: string): string {
    const n = edgeNodeByName.get(name);
    if (!n) return name;
    const bits = [
      name,
      n.ready ? "Ready" : "NotReady",
      n.multus_master || null,
      n.internal_ip || null,
    ].filter(Boolean);
    return bits.join(" · ");
  }

  async function refreshNames() {
    const list = await api.listProfiles();
    setProfileNames(list.names);
    return list;
  }

  function clearSolve() {
    setResult(null);
  }

  function resetConsole(title: string) {
    setConsoleOpen(true);
    setConsoleTitle(title);
    setConsoleText("");
    setConsoleMessage("");
    setConsoleOk(null);
    setConsoleFiles([]);
    setConsoleFilesLabel("Files");
  }

  function appendConsole(line: string) {
    setConsoleText((prev) => (prev ? `${prev}\n${line}` : line));
  }

  function streamHandlers(): StreamHandlers {
    return {
      onLog: (stream, line) => {
        appendConsole(stream === "stderr" ? `[err] ${line}` : line);
      },
      onStatus: (message) => {
        if (message) appendConsole(`# ${message}`);
      },
      onError: (message) => {
        if (message) appendConsole(`! ${message}`);
      },
    };
  }

  function applyRecord(rec: ProfileRecord) {
    setProfile({
      ...DEFAULT_PROFILE,
      ...rec.profile,
      du_node: rec.profile.du_node || rec.profile.ue_node || "usrp",
      // DU + UE always co-located on the same edge worker for rfsim.
      ue_node:
        rec.profile.du_node ||
        rec.profile.ue_node ||
        "usrp",
    });
    setSlices(rec.slices);
    const net =
      rec.network && Object.keys(rec.network).length
        ? normalizeNetwork(rec.network)
        : DEFAULT_NETWORK;
    setNetworkIn(net);
    setSelectedName(rec.profile.name);
    setSavedAt(rec.updated_at);
    setDeployed(Boolean(rec.deployed));
    setDeployedAt(rec.deployed_at || "");
    setDeployFiles(rec.deploy_files || []);
    if (rec.pl_result?.ok) {
      setResult(rec.pl_result);
    } else {
      setResult(null);
    }
  }

  async function loadProfile(name: string) {
    const rec = await api.getProfile(name);
    applyRecord(rec);
    const extra = rec.pl_result_file
      ? ` · last PL: ${rec.pl_result_file.split("/").pop()}`
      : "";
    setStatus(`Loaded profile “${rec.profile.name}”${extra}`);
    setError(null);
  }

  useEffect(() => {
    (async () => {
      try {
        const list = await api.listProfiles();
        setProfileNames(list.names);
        const first = list.profiles[0];
        if (first) {
          applyRecord(first);
        } else {
          const defs = await api.profileDefaults();
          applyRecord({ ...defs, updated_at: "" } as ProfileRecord);
        }
        await refreshEdgeNodes();
      } catch (e) {
        setError(e instanceof Error ? e.message : String(e));
      }
    })();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const nextId = useMemo(
    () => (slices.length ? Math.max(...slices.map((s) => s.id)) + 1 : 1),
    [slices],
  );

  const profileOk = NS_RE.test(profile.name);
  const isExisting = selectedName !== "" && profileNames.includes(selectedName);
  const dirtyName = selectedName !== "" && profile.name !== selectedName;

  async function refreshClusterStatus() {
    if (!NS_RE.test(profile.name)) {
      setClusterStatus(null);
      setClusterError(null);
      return;
    }
    setClusterBusy(true);
    try {
      const st = await api.clusterStatus(profile.name);
      setClusterStatus(st);
      setClusterError(null);
    } catch (e) {
      setClusterError(e instanceof Error ? e.message : String(e));
    } finally {
      setClusterBusy(false);
    }
  }

  useEffect(() => {
    if (!profileOk) {
      setClusterStatus(null);
      return;
    }
    void refreshClusterStatus();
    const id = window.setInterval(() => {
      void refreshClusterStatus();
    }, 10000);
    return () => window.clearInterval(id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [profile.name, profileOk]);

  function updateProfile(patch: Partial<Profile>) {
    setProfile((p) => ({ ...p, ...patch }));
    clearSolve();
    setStatus(null);
  }

  function updateNetwork(patch: Partial<NetworkIn>) {
    setNetworkIn((prev) => ({ ...prev, ...patch }));
    clearSolve();
    setStatus(null);
  }

  function updateSlice(idx: number, patch: Partial<SliceIn>) {
    setSlices((prev) => prev.map((s, i) => (i === idx ? { ...s, ...patch } : s)));
    clearSolve();
    setStatus(null);
  }

  function removeSlice(idx: number) {
    setSlices((prev) => prev.filter((_, i) => i !== idx));
    clearSolve();
    setStatus(null);
  }

  function addSlice() {
    if (slices.length >= profile.max_slices) return;
    setSlices((p) => [...p, emptySlice(nextId)]);
    clearSolve();
    setStatus(null);
  }

  async function onRestoreProfileDefaults() {
    if (!profileOk) {
      setError("Profile name must be a valid K8s namespace");
      return;
    }
    if (!isExisting || dirtyName) {
      setError("Select a saved profile (unsaved name changes must be saved first)");
      return;
    }
    if (
      !window.confirm(
        `Restore builtin defaults for profile "${profile.name}"?\n\n` +
          "Resets subnet, max slices, DNN prefix, RAN node, network settings, " +
          "and 4-slice SLAs. Deploy status is kept. Clears the PL result.",
      )
    ) {
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const rec = await api.restoreProfileDefaults(profile.name);
      applyRecord(rec);
      setResult(null);
      setStatus(`Restored profile defaults for “${rec.profile.name}”`);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  async function onRestoreDefaultSlices() {
    if (
      !window.confirm(
        "Restore the default 4-slice SLAs (CCTV / Physical AI / OTT / IoT)?\n\n" +
          "Clears the current PL result.",
      )
    ) {
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const defs = await api.defaults();
      setSlices(defs);
      clearSolve();
      if (profileOk && isExisting && !dirtyName) {
        const rec = await api.saveProfile(profile.name, {
          profile,
          slices: defs,
          network: networkIn,
          updated_at: "",
        });
        applyRecord(rec);
        setStatus(`Restored and saved default slice SLAs for “${profile.name}”`);
      } else {
        setStatus("Restored default slice SLAs — Save to persist");
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  async function onSave() {
    if (!profileOk) {
      setError("Profile name must be a valid K8s namespace");
      return;
    }
    if (!slices.length) {
      setError("Add at least one slice before saving");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const rec = await api.saveProfile(profile.name, {
        profile,
        slices,
        network: networkIn,
        updated_at: "",
      });
      applyRecord(rec);
      await refreshNames();
      setStatus(
        `Updated profile “${rec.profile.name}” (slices + network) in database`,
      );
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  async function onAddProfile() {
    const name = window.prompt(
      "New profile name (K8s namespace):",
      `${profile.name}-2`,
    );
    if (!name) return;
    const trimmed = name.trim();
    if (!NS_RE.test(trimmed)) {
      setError("Invalid K8s namespace name");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const rec = await api.createProfile({
        profile: { ...profile, name: trimmed },
        slices,
        network: networkIn,
        copy_from: selectedName || undefined,
      });
      applyRecord(rec);
      await refreshNames();
      clearSolve();
      setStatus(`Created profile “${rec.profile.name}”`);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  async function onRemoveProfile() {
    const name = selectedName || profile.name;
    if (!name) return;
    if (
      !window.confirm(
        `Delete profile “${name}” from the database?\n\n` +
          `This does not remove GitOps manifests under namespaces/${name}/.`,
      )
    ) {
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const out = await api.deleteProfile(name);
      const list = await refreshNames();
      const next = out.remaining[0] || list.names[0];
      if (next) await loadProfile(next);
      setStatus(`Deleted profile “${name}”`);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  async function onSelectProfile(name: string) {
    if (!name) return;
    setBusy(true);
    setError(null);
    try {
      await loadProfile(name);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  async function onSolve() {
    if (!profileOk) {
      setError("Profile name must be a valid K8s namespace");
      return;
    }
    setBusy(true);
    setError(null);
    setStatus(null);
    try {
      const res = await api.solve(slices, profile, networkIn);
      setResult(res);
      if (!res.ok) setError(res.message || "Solve failed");
      else {
        setStatus(res.message);
        const list = await api.listProfiles();
        setProfileNames(list.names);
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      setResult(null);
    } finally {
      setBusy(false);
    }
  }

  useEffect(() => {
    if (consoleRef.current) {
      consoleRef.current.scrollTop = consoleRef.current.scrollHeight;
    }
  }, [consoleText]);

  async function onApply(dryRun: boolean) {
    if (!result?.ok) return;
    if (
      !dryRun &&
      !window.confirm(
        `Deploy profile "${profile.name}" to the clusters?\n\n` +
          `Writes namespaces/${profile.name}/ then pushes to Gitea ` +
          `(Config Sync) for N=${slices.length} slice(s) on ${profile.subnet}.`,
      )
    ) {
      return;
    }
    setBusy(true);
    setError(null);
    resetConsole(dryRun ? "Dry deploy" : "Deploy");
    try {
      const res = await api.applyStream(
        {
          result,
          slices,
          profile,
          commit_message: dryRun
            ? commitMsg.replace("deploy", "dry-deploy")
            : commitMsg,
          dry_run: dryRun,
        },
        streamHandlers(),
      );
      setConsoleOk(res.ok);
      setConsoleMessage(
        (res.dry_run ? "[dry deploy] " : "") + (res.message || ""),
      );
      setConsoleFiles(res.written_files || []);
      setConsoleFilesLabel("Written files");
      if (res.profile) {
        setDeployed(Boolean(res.profile.deployed));
        setDeployedAt(res.profile.deployed_at || "");
        setDeployFiles(res.profile.deploy_files || res.written_files || []);
        setSavedAt(res.profile.updated_at || savedAt);
      } else {
        setDeployFiles(res.written_files || []);
        if (!dryRun && res.ok) {
          setDeployed(true);
          setDeployedAt(new Date().toISOString());
        }
      }
      if (!res.ok) setError(res.message);
      else {
        setStatus(
          dryRun
            ? `Dry deploy: ${res.written_files.length} file(s) saved on profile`
            : `Deployed: ${res.written_files.length} file(s)`,
        );
        void refreshClusterStatus();
      }
    } catch (e) {
      setConsoleOk(false);
      setConsoleMessage(e instanceof Error ? e.message : String(e));
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  async function onUndeploy() {
    if (!deployed) return;
    if (
      !window.confirm(
        `Undeploy profile "${profile.name}"?\n\n` +
          `Deletes namespaces/${profile.name}/ from GitOps repos and pushes to Gitea ` +
          `(Config Sync will prune the workloads).`,
      )
    ) {
      return;
    }
    setBusy(true);
    setError(null);
    resetConsole("Undeploy");
    try {
      const res = await api.undeployStream(
        {
          profile,
          commit_message: "ina-pl: undeploy profile manifests",
          dry_run: false,
        },
        streamHandlers(),
      );
      setConsoleOk(res.ok);
      setConsoleMessage(res.message || "");
      setConsoleFiles(res.removed_paths || []);
      setConsoleFilesLabel("Removed paths");
      if (res.profile) {
        setDeployed(Boolean(res.profile.deployed));
        setDeployedAt(res.profile.deployed_at || "");
        setDeployFiles(res.profile.deploy_files || []);
        setSavedAt(res.profile.updated_at || savedAt);
      } else if (res.ok) {
        setDeployed(false);
        setDeployedAt("");
        setDeployFiles([]);
      }
      if (!res.ok) setError(res.message);
      else {
        setStatus(`Undeployed “${profile.name}”`);
        void refreshClusterStatus();
      }
    } catch (e) {
      setConsoleOk(false);
      setConsoleMessage(e instanceof Error ? e.message : String(e));
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  async function onProfileRollout(opts?: {
    skip_ues?: boolean;
    skip_ran?: boolean;
    only_ues?: boolean;
  }) {
    if (!profileOk) return;
    if (
      !window.confirm(
        `Staged rollout for profile "${profile.name}"?\n\n` +
          `Order: UPF → SMF → PFCP → CU-CP → CU-UP → DU → UEs\n` +
          `(same bring-up order as oai-slice-deployment; may take several minutes)`,
      )
    ) {
      return;
    }
    const ctrl = new AbortController();
    streamAbortRef.current = ctrl;
    setRolloutBusy(true);
    setBusy(true);
    setError(null);
    resetConsole("Profile rollout");
    try {
      const res = await api.profileRolloutStream(
        profile.name,
        {
          slice_count: slices.length,
          ...opts,
        },
        streamHandlers(),
        { signal: ctrl.signal },
      );
      setConsoleOk(res.ok);
      setConsoleMessage(
        (res.message || "") +
          (res.exit_code != null ? ` (exit ${res.exit_code})` : ""),
      );
      if (!res.ok && res.exit_code !== 130) setError(res.message);
      else if (res.exit_code === 130) setStatus("Rollout stopped");
      void refreshClusterStatus();
    } catch (e) {
      const aborted =
        ctrl.signal.aborted ||
        (e instanceof DOMException && e.name === "AbortError") ||
        (e instanceof Error && /abort/i.test(e.message));
      if (aborted) {
        setConsoleOk(false);
        setConsoleMessage("Rollout stopped");
        setStatus("Rollout stopped");
      } else {
        setConsoleOk(false);
        setConsoleMessage(e instanceof Error ? e.message : String(e));
        setError(e instanceof Error ? e.message : String(e));
      }
    } finally {
      if (streamAbortRef.current === ctrl) streamAbortRef.current = null;
      setRolloutBusy(false);
      setBusy(false);
    }
  }

  async function onStopRollout() {
    streamAbortRef.current?.abort();
    try {
      const res = await api.profileRolloutStop(profile.name);
      appendConsole(`# ${res.message}`);
      setStatus(res.message);
    } catch (e) {
      appendConsole(
        `! stop failed: ${e instanceof Error ? e.message : String(e)}`,
      );
    }
  }

  const ipPlan: IpPlan | null | undefined = result?.ok ? result.ip_plan : null;

  return (
    <div className="page-layout">
      <div className="page">
      <section className="panel">
        <div className="panel-head">
          <h2>Profile</h2>
          <div className="actions">
            <button type="button" disabled={busy} onClick={onSave}>
              {isExisting && !dirtyName ? "Update" : "Save"}
            </button>
            <button
              type="button"
              disabled={busy || !isExisting || dirtyName || !profileOk}
              onClick={() => void onRestoreProfileDefaults()}
              title="Reset profile fields, network settings, and slice SLAs to builtins"
            >
              Restore profile defaults
            </button>
            <button type="button" disabled={busy} onClick={onAddProfile}>
              Add profile
            </button>
            <button
              type="button"
              className="danger"
              disabled={busy || profileNames.length === 0}
              onClick={onRemoveProfile}
            >
              Remove
            </button>
          </div>
        </div>
        <div className="profile-grid">
          <FieldHelp
            label="Saved profiles"
            help="Select a profile loaded from the SQLite database on the API host."
          >
            <select
              value={selectedName}
              disabled={busy || profileNames.length === 0}
              onChange={(e) => onSelectProfile(e.target.value)}
            >
              {profileNames.map((n) => (
                <option key={n} value={n}>
                  {n}
                </option>
              ))}
            </select>
          </FieldHelp>
          <FieldHelp
            label="Name (namespace)"
            help="K8s namespace on central/regional/edge. Must be a DNS label (a-z0-9-)."
          >
            <input
              value={profile.name}
              onChange={(e) => updateProfile({ name: e.target.value.trim() })}
            />
          </FieldHelp>
          <FieldHelp
            label="Multus subnet"
            help="Macvlan CIDR for this profile (default 10.1.140.0/24). IPs use host = base[role] + n."
          >
            <input
              value={profile.subnet}
              onChange={(e) => updateProfile({ subnet: e.target.value.trim() })}
            />
          </FieldHelp>
          <FieldHelp
            label="Max slices"
            help="Upper bound on Add slice for this profile (IP bands sized for this cap)."
          >
            <input
              type="number"
              min={1}
              max={32}
              value={profile.max_slices}
              onChange={(e) =>
                updateProfile({ max_slices: Math.max(1, Number(e.target.value) || 1) })
              }
            />
          </FieldHelp>
          <FieldHelp
            label="DNN prefix"
            help="UE PDU pool prefix: {prefix}.{n}.0/24 for slice index n (avoids clash with OAI 10.1.n.0/24)."
          >
            <input
              value={profile.dnn_prefix}
              onChange={(e) => updateProfile({ dnn_prefix: e.target.value.trim() })}
            />
          </FieldHelp>
          <FieldHelp
            className="field-help-wide"
            label="RAN node (DU + UE)"
            help="Single edge worker for both OAI DU and UEs (rfsim). Auto-detected from the edge cluster. usrp → Multus enp4s0f0; VMs → enp7s0; bare-metal (edge-2) → eno1."
          >
            <select
              className="ran-node-select"
              value={profile.du_node || profile.ue_node || "usrp"}
              disabled={busy}
              onChange={(e) => {
                const n = e.target.value;
                updateProfile({ du_node: n, ue_node: n });
              }}
              onFocus={() => {
                void refreshEdgeNodes();
              }}
            >
              {edgeRfOptions.map((n) => (
                <option key={n} value={n} title={edgeOptionLabel(n)}>
                  {edgeOptionLabel(n)}
                </option>
              ))}
            </select>
          </FieldHelp>
        </div>
        {edgeNodesError && (
          <p className="hint error">Edge nodes: {edgeNodesError}</p>
        )}
        {!edgeNodesError && edgeNodes.length > 0 && (
          <p className="hint">
            Edge nodes ({edgeNodes.length}):{" "}
            {edgeNodes
              .map(
                (n) =>
                  `${n.name}${n.ready ? "" : " (NotReady)"}${
                    n.multus_master ? `/${n.multus_master}` : ""
                  }`,
              )
              .join(", ")}
          </p>
        )}
        {profile.du_node &&
          profile.ue_node &&
          profile.du_node !== profile.ue_node && (
            <p className="hint error">
              DU ({profile.du_node}) and UE ({profile.ue_node}) differ — pick a
              RAN node to co-locate them.
            </p>
          )}
        {!profileOk && <p className="hint error">Invalid K8s namespace name.</p>}
        {dirtyName && (
          <p className="hint">
            Name differs from loaded “{selectedName}” — Save will store under the new name
            (upsert).
          </p>
        )}
        {savedAt && (
          <p className="hint">
            Last saved: <code>{savedAt}</code>
          </p>
        )}
        {status && <p className="hint ok-inline">{status}</p>}
      </section>

      <section className="panel">
        <div className="panel-head">
          <h2>Network settings</h2>
          <button type="button" onClick={() => setShowNet((v) => !v)}>
            {showNet ? "Hide" : "Show"}
          </button>
        </div>
        {showNet ? (
          <>
            <p className="hint">Part of profile · hover ? for help</p>
            <NetworkSettingsForm value={networkIn} onChange={updateNetwork} />
          </>
        ) : (
          <p className="hint">Collapsed — Show to edit substrate variables for this profile.</p>
        )}
      </section>

      <section className="panel">
        <div className="panel-head">
          <h2>
            Slice SLAs{" "}
            <span className="muted">
              (N={slices.length}/{profile.max_slices})
            </span>
          </h2>
          <div className="actions">
            <button
              type="button"
              disabled={busy}
              onClick={() => void onRestoreDefaultSlices()}
              title="Reset to default 4-slice SLAs (CCTV / Physical AI / OTT / IoT)"
            >
              Restore slice defaults
            </button>
            <button
              type="button"
              disabled={slices.length >= profile.max_slices}
              onClick={addSlice}
            >
              Add slice
            </button>
            <button
              type="button"
              className="primary"
              disabled={busy || !slices.length || !profileOk}
              onClick={onSolve}
            >
              {busy ? "Working…" : "Solve PL"}
            </button>
          </div>
        </div>
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>
                  ID{" "}
                  <span className="help-q" title="Slice identifier used in PL and IP plan (n is row order 1..N)." role="img">?</span>
                </th>
                <th>
                  Type{" "}
                  <span className="help-q" title="Label only (eMBB / URLLC / mMTC / custom). Not used by the MILP." role="img">?</span>
                </th>
                <th>
                  t_bar (Mbps){" "}
                  <span className="help-q" title="Minimum throughput SLA (Mbps)." role="img">?</span>
                </th>
                <th>
                  d_bar (ms){" "}
                  <span className="help-q" title="Maximum end-to-end delay SLA (ms)." role="img">?</span>
                </th>
                <th>
                  h_s{" "}
                  <span className="help-q" title="Hard isolation: 1 = dedicated PRBs; 0 = shared." role="img">?</span>
                </th>
                <th>
                  eta_t0{" "}
                  <span className="help-q" title="Planning radio efficiency (Mbps per PRB)." role="img">?</span>
                </th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {slices.map((s, i) => (
                <tr key={`${s.id}-${i}`}>
                  <td>
                    <input
                      type="number"
                      value={s.id}
                      onChange={(e) => updateSlice(i, { id: Number(e.target.value) })}
                    />
                  </td>
                  <td>
                    <input
                      value={s.slice_type}
                      onChange={(e) => updateSlice(i, { slice_type: e.target.value })}
                    />
                  </td>
                  <td>
                    <input
                      type="number"
                      step="1"
                      value={s.t_bar}
                      onChange={(e) => updateSlice(i, { t_bar: Number(e.target.value) })}
                    />
                  </td>
                  <td>
                    <input
                      type="number"
                      step="1"
                      value={s.d_bar}
                      onChange={(e) => updateSlice(i, { d_bar: Number(e.target.value) })}
                    />
                  </td>
                  <td>
                    <select
                      value={s.h_s}
                      onChange={(e) => updateSlice(i, { h_s: Number(e.target.value) })}
                    >
                      <option value={0}>0 shared</option>
                      <option value={1}>1 dedicated</option>
                    </select>
                  </td>
                  <td>
                    <input
                      type="number"
                      step="0.1"
                      value={s.eta_t0}
                      onChange={(e) => updateSlice(i, { eta_t0: Number(e.target.value) })}
                    />
                  </td>
                  <td>
                    <button type="button" className="danger" onClick={() => removeSlice(i)}>
                      Remove
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>

      {error && <div className="banner error">{error}</div>}

      {result?.ok && (
        <section className="panel">
          <div className="panel-head">
            <h2>PL result</h2>
            <span className="muted">{result.message}</span>
          </div>
          <Topology slices={result.slices} />
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Slice</th>
                  <th>CU</th>
                  <th>UPF</th>
                  <th>APP</th>
                  <th>b_min</th>
                  <th>CU CPU</th>
                  <th>UPF CPU</th>
                  <th>APP CPU</th>
                </tr>
              </thead>
              <tbody>
                {[...result.slices]
                  .sort((a, b) => a.id - b.id)
                  .map((s) => (
                  <tr key={s.id}>
                    <td>
                      S{s.id} {s.slice_type && <span className="muted">({s.slice_type})</span>}
                    </td>
                    <td>{s.placement.cu}</td>
                    <td>{s.placement.upf}</td>
                    <td>{s.placement.app}</td>
                    <td>{s.resources.b_min ?? "—"}</td>
                    <td>{s.resources.a_c_cu.toFixed(2)}</td>
                    <td>{s.resources.a_c_upf.toFixed(2)}</td>
                    <td>{s.resources.a_c_app.toFixed(2)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          {ipPlan && (
            <>
              <h3 className="subhead">IP plan ({ipPlan.subnet})</h3>
              <div className="table-wrap">
                <table>
                  <thead>
                    <tr>
                      <th>Role</th>
                      <th>Address</th>
                    </tr>
                  </thead>
                  <tbody>
                    <tr>
                      <td>AMF N2</td>
                      <td>
                        <code>{ipPlan.shared.amf_n2}</code>
                      </td>
                    </tr>
                    <tr>
                      <td>NRF Nnrf (SBI)</td>
                      <td>
                        <code>
                          {ipPlan.shared.nrf_sbi ||
                            (ipPlan.shared.amf_n2
                              ? `${ipPlan.shared.amf_n2.replace(/\.\d+$/, ".11")}`
                              : "—")}
                        </code>
                      </td>
                    </tr>
                    <tr>
                      <td>SMF N4</td>
                      <td>
                        <code>{ipPlan.shared.smf_n4}</code>
                      </td>
                    </tr>
                    <tr>
                      <td>CU-CP N2 / F1-C / E1</td>
                      <td>
                        <code>
                          {ipPlan.shared.cucp_n2} / {ipPlan.shared.cucp_f1c} /{" "}
                          {ipPlan.shared.cucp_e1}
                        </code>
                      </td>
                    </tr>
                    <tr>
                      <td>DU F1 / RF</td>
                      <td>
                        <code>
                          {ipPlan.shared.du_f1} / {ipPlan.shared.du_rf}
                        </code>
                      </td>
                    </tr>
                    <tr>
                      <td>FlexRIC / xApp</td>
                      <td>
                        <code>
                          {ipPlan.shared.flexric_e2} / {ipPlan.shared.xapp_e2}
                        </code>
                      </td>
                    </tr>
                  </tbody>
                </table>
              </div>
              <div className="table-wrap">
                <table>
                  <thead>
                    <tr>
                      <th>n</th>
                      <th>UPF N3/N4/N6</th>
                      <th>CU-UP E1/F1U/N3</th>
                      <th>UE RF</th>
                      <th>DNN</th>
                      <th>Sites</th>
                    </tr>
                  </thead>
                  <tbody>
                    {ipPlan.slices.map((s) => (
                      <tr key={s.n}>
                        <td>{s.n}</td>
                        <td>
                          <code>
                            {s.upf_n3} / {s.upf_n4} / {s.upf_n6}
                          </code>
                        </td>
                        <td>
                          <code>
                            {s.cuup_e1} / {s.cuup_f1u} / {s.cuup_n3}
                          </code>
                        </td>
                        <td>
                          <code>{s.ue_rf}</code>
                        </td>
                        <td>
                          <code>{s.dnn_cidr}</code>
                        </td>
                        <td className="muted">
                          CU={s.site_cu} UPF={s.site_upf}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </>
          )}

          <div className="apply-box">
            <div className="actions" style={{ marginBottom: 8 }}>
              <span
                className={
                  deployed ? "status-pill ok" : "status-pill muted"
                }
              >
                {deployed
                  ? `Deployed${deployedAt ? ` · ${deployedAt.replace("T", " ").slice(0, 19)}` : ""}`
                  : "Not deployed"}
              </span>
            </div>
            <label>
              Commit message
              <input
                value={commitMsg}
                onChange={(e) => setCommitMsg(e.target.value)}
                style={{ width: "100%", marginTop: 4 }}
              />
            </label>
            <div className="actions" style={{ marginTop: 12 }}>
              <button type="button" disabled={busy} onClick={() => onApply(true)}>
                Dry deploy
              </button>
              <button
                type="button"
                className="primary"
                disabled={busy}
                onClick={() => onApply(false)}
              >
                Deploy
              </button>
              {deployed && (
                <button
                  type="button"
                  disabled={busy}
                  onClick={() => void onUndeploy()}
                  title="Remove profile namespace from GitOps and push"
                >
                  Undeploy
                </button>
              )}
              <button
                type="button"
                disabled={busy || !profileOk || rolloutBusy}
                onClick={() => void onProfileRollout()}
                title="Staged restart: UPF → SMF → PFCP → CU-CP → CU-UP → DU → UEs"
              >
                {rolloutBusy ? "Rolling out…" : "Profile rollout"}
              </button>
              {rolloutBusy && (
                <button
                  type="button"
                  className="danger"
                  onClick={() => void onStopRollout()}
                  title="Stop the staged rollout script"
                >
                  Stop rollout
                </button>
              )}
            </div>
            <p className="hint">
              <strong>Deploy</strong> writes <code>namespaces/{profile.name}/</code> and
              pushes to Gitea. <strong>Dry deploy</strong> writes locally and saves the
              file list on the profile without pushing.{" "}
              {deployed && (
                <>
                  <strong>Undeploy</strong> removes the namespace from GitOps.
                </>
              )}{" "}
              Live command output streams into the Console below.
            </p>
          </div>
        </section>
      )}

      {(consoleOpen || deployFiles.length > 0) && (
        <section className="panel console-panel">
          <div className="panel-head">
            <h2>{consoleOpen ? consoleTitle : "Console"}</h2>
            {rolloutBusy ? (
              <button
                type="button"
                className="danger"
                onClick={() => void onStopRollout()}
                title="Stop the staged rollout script"
              >
                Stop
              </button>
            ) : (
              <button
                type="button"
                disabled={busy}
                onClick={() => {
                  setConsoleText("");
                  setConsoleMessage("");
                  setConsoleOk(null);
                  setConsoleFiles([]);
                  if (!deployFiles.length) setConsoleOpen(false);
                }}
              >
                Clear
              </button>
            )}
          </div>
          {consoleMessage && (
            <p
              className={
                consoleOk == null ? "hint" : consoleOk ? "ok" : "error"
              }
            >
              {consoleMessage}
            </p>
          )}
          <pre className="net-pre console-pre" ref={consoleRef}>
            {consoleText ||
              (busy
                ? "Waiting for output…"
                : consoleOpen
                  ? "(empty)"
                  : "Run Deploy, Undeploy, or Profile rollout to stream command output here.")}
          </pre>
          {consoleFiles.length > 0 && (
            <details style={{ marginTop: 8 }} open>
              <summary className="hint">
                {consoleFilesLabel} ({consoleFiles.length})
              </summary>
              <ul className="file-list">
                {consoleFiles.map((f) => (
                  <li key={f}>
                    <code>{f}</code>
                  </li>
                ))}
              </ul>
            </details>
          )}
          {!consoleFiles.length && deployFiles.length > 0 && (
            <details style={{ marginTop: 8 }}>
              <summary className="hint">
                Saved file list ({deployFiles.length})
              </summary>
              <ul className="file-list">
                {deployFiles.map((f) => (
                  <li key={f}>
                    <code>{f}</code>
                  </li>
                ))}
              </ul>
            </details>
          )}
        </section>
      )}
      </div>

      <StatusRail
        profileName={profile.name}
        profileOk={profileOk}
        isExisting={isExisting}
        dirtyName={dirtyName}
        savedAt={savedAt}
        networkCollapsed={!showNet}
        sliceCount={slices.length}
        maxSlices={profile.max_slices}
        plSolved={Boolean(result?.ok)}
        plMessage={result?.ok ? result.message : null}
        deployed={deployed}
        deployedAt={deployedAt}
        cluster={clusterStatus}
        clusterError={clusterError}
        refreshing={clusterBusy}
        onRefresh={() => void refreshClusterStatus()}
        rolloutBusy={rolloutBusy}
        onRollout={() => void onProfileRollout()}
        onStopRollout={() => void onStopRollout()}
      />
    </div>
  );
}
