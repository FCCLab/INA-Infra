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
  SliceApplicationConfig,
  SliceIn,
  StreamHandlers,
  OaiRegistryStatus,
} from "../api/client";
import Topology from "../components/Topology";
import ApplicationSettingsBox from "../components/ApplicationSettingsBox";
import ApplicationServerSettingsBox from "../components/ApplicationServerSettingsBox";
import NetworkSettingsForm from "../components/NetworkSettingsForm";
import OaiImagesForm from "../components/OaiImagesForm";
import FieldHelp from "../components/FieldHelp";
import StatusRail from "../components/StatusRail";
import Card from "../components/ui/Card";
import SectionLabel from "../components/ui/SectionLabel";
import KpiStrip from "../components/ui/KpiStrip";
import { useDialog } from "../components/ui/Dialog";
import type { ProfileClusterStatusOut } from "../api/client";

type ApplyRun =
  | "generate"
  | "push"
  | "deploy"
  | "clear"
  | "undeploy"
  | "rollout";

/** Sub-step while Deploy (gen→push) or Undeploy (clear→push→cleanup) runs. */
type ApplyStep = "generate" | "push" | "clear" | "cleanup";

function BtnProgress({ active }: { active: boolean }) {
  if (!active) return null;
  return (
    <span className="btn-progress" aria-hidden>
      <span className="btn-progress-spin" />
    </span>
  );
}

function getClusterTagClass(cluster?: string | null): string {
  if (!cluster) return "tag-cluster-auto";
  const c = cluster.toLowerCase();
  if (c.includes("edge")) return "tag-cluster-edge";
  if (c.includes("regional")) return "tag-cluster-regional";
  if (c.includes("central")) return "tag-cluster-central";
  return "tag-cluster-auto";
}

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
  const dialog = useDialog();
  const [profile, setProfile] = useState<Profile>(DEFAULT_PROFILE);
  const [slices, setSlices] = useState<SliceIn[]>(DEFAULT_SLICES);
  const [networkIn, setNetworkIn] = useState<NetworkIn>(DEFAULT_NETWORK);
  const [applications, setApplications] = useState<Record<string, SliceApplicationConfig>>({});
  const [profileNames, setProfileNames] = useState<string[]>([]);
  const [profileSubnets, setProfileSubnets] = useState<Record<string, string>>(
    {},
  );
  const [selectedName, setSelectedName] = useState<string>("");
  const [savedAt, setSavedAt] = useState<string>("");
  const [showProfile, setShowProfile] = useState(true);
  const [showSliceConfig, setShowSliceConfig] = useState(true);
  const [showNet, setShowNet] = useState(false);
  const [showSlices, setShowSlices] = useState(true);
  const [showPlResult, setShowPlResult] = useState(true);
  const [registryStatus, setRegistryStatus] = useState<OaiRegistryStatus | null>(null);
  const [registryLoading, setRegistryLoading] = useState(false);
  const [result, setResult] = useState<PlSolveResponse | null>(null);
  const [consoleOpen, setConsoleOpen] = useState(false);
  const [consoleTitle, setConsoleTitle] = useState("Console");
  const [consoleText, setConsoleText] = useState("");
  const [consoleMessage, setConsoleMessage] = useState("");
  const [consoleOk, setConsoleOk] = useState<boolean | null>(null);
  const [consoleFiles, setConsoleFiles] = useState<string[]>([]);
  const [consoleFilesLabel, setConsoleFilesLabel] = useState("Files");
  /** Follow new stream lines; pauses if the operator scrolls up. */
  const [consoleAutoScroll, setConsoleAutoScroll] = useState(true);
  const consoleRef = useRef<HTMLPreElement>(null);
  const consolePanelRef = useRef<HTMLDivElement | null>(null);
  const streamAbortRef = useRef<AbortController | null>(null);
  const [rolloutBusy, setRolloutBusy] = useState(false);
  const [busy, setBusy] = useState(false);
  const [runningAction, setRunningAction] = useState<ApplyRun | null>(null);
  const [runningStep, setRunningStep] = useState<ApplyStep | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [status, setStatus] = useState<string | null>(null);
  const [commitMsg, setCommitMsg] = useState("ina-pl: deploy profile manifests");
  const [deployed, setDeployed] = useState(false);
  const [deployedAt, setDeployedAt] = useState<string>("");
  const [deployFiles, setDeployFiles] = useState<string[]>([]);
  /** After Clear, Push stays enabled so Clear -- Push can sync deletions. */
  const [clearPendingPush, setClearPendingPush] = useState(false);
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

  async function refreshRegistryImages(force = false) {
    setRegistryLoading(true);
    try {
      const res = await api.getOaiRegistryImages(force);
      setRegistryStatus(res);
    } catch {
      // ignore
    } finally {
      setRegistryLoading(false);
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
    return merged.slice().sort((a, b) => a.localeCompare(b));
  }, [edgeNodes, profile.du_node, profile.ue_node]);

  const edgeNodeByName = useMemo(() => {
    const m = new Map<string, EdgeNodeOut>();
    for (const n of edgeNodes) m.set(n.name, n);
    return m;
  }, [edgeNodes]);

  function edgeOptionDetail(name: string): string {
    const n = edgeNodeByName.get(name);
    if (!n) return name;
    return [
      name,
      n.ready ? "Ready" : "NotReady",
      n.multus_master || null,
      n.internal_ip || null,
    ]
      .filter(Boolean)
      .join(" · ");
  }

  /** Compact label for the closed <select> (full detail in title / hint). */
  function edgeOptionLabel(name: string): string {
    const n = edgeNodeByName.get(name);
    if (!n) return name;
    return `${name}${n.ready ? "" : " · NotReady"}`;
  }

  async function refreshNames() {
    const list = await api.listProfiles();
    setProfileNames(list.names);
    const subs: Record<string, string> = {};
    for (const rec of list.profiles) {
      const sub =
        rec.profile.subnet ||
        rec.pl_result?.ip_plan?.subnet ||
        rec.pl_result?.profile?.subnet;
      if (sub) subs[rec.profile.name] = sub;
    }
    setProfileSubnets(subs);
    return list;
  }

  function clearSolve() {
    setResult(null);
  }

  function scrollConsoleToBottom() {
    const el = consoleRef.current;
    if (!el) return;
    el.scrollTop = el.scrollHeight;
  }

  function enableConsoleAutoScroll() {
    setConsoleAutoScroll(true);
    // Scroll after paint so new text is included.
    requestAnimationFrame(() => scrollConsoleToBottom());
  }

  function resetConsole(title: string) {
    setConsoleOpen(true);
    setConsoleTitle(title);
    setConsoleText("");
    setConsoleMessage("");
    setConsoleOk(null);
    setConsoleFiles([]);
    setConsoleFilesLabel("Files");
    setConsoleAutoScroll(true);
    // Bring stream into view when an action starts.
    requestAnimationFrame(() => {
      consolePanelRef.current?.scrollIntoView({
        behavior: "smooth",
        block: "nearest",
      });
      scrollConsoleToBottom();
    });
  }

  function appendConsole(line: string) {
    setConsoleText((prev) => (prev ? `${prev}\n${line}` : line));
  }

  function onConsoleScroll() {
    const el = consoleRef.current;
    if (!el) return;
    const distance = el.scrollHeight - el.scrollTop - el.clientHeight;
    // Near bottom → keep following; scrolled up → pause.
    setConsoleAutoScroll(distance < 40);
  }

  function streamHandlers(opts?: {
    trackDeploySteps?: boolean;
    trackUndeploySteps?: boolean;
  }): StreamHandlers {
    return {
      onLog: (stream, line) => {
        appendConsole(stream === "stderr" ? `[err] ${line}` : line);
      },
      onStatus: (message) => {
        if (message) appendConsole(`# ${message}`);
        if (!message) return;
        const m = message.toLowerCase();
        if (opts?.trackDeploySteps) {
          if (
            m.includes("rendering") ||
            m.includes("wrote ") ||
            m.includes("multus")
          ) {
            setRunningStep("generate");
          }
          if (m.startsWith("$ ") || m.includes("syncing mysql")) {
            setRunningStep("push");
          }
        }
        if (opts?.trackUndeploySteps) {
          if (
            m.includes("clearing") ||
            m.includes("undeploying") ||
            m.startsWith("removed ")
          ) {
            setRunningStep("clear");
          }
          if (m.startsWith("$ ")) {
            setRunningStep("push");
          }
          if (m.includes("cleanup") || m.includes("forcing")) {
            setRunningStep("cleanup");
          }
        }
      },
      onError: (message) => {
        if (message) appendConsole(`! ${message}`);
      },
    };
  }

  function applyRecord(rec: ProfileRecord) {
    // Profile identity: prefer saved row, then last PL result / IP plan.
    const plProf = rec.pl_result?.ok ? rec.pl_result.profile : null;
    const subnet =
      rec.profile.subnet ||
      rec.pl_result?.ip_plan?.subnet ||
      plProf?.subnet ||
      DEFAULT_PROFILE.subnet;
    const maxSlices =
      rec.profile.max_slices ?? plProf?.max_slices ?? DEFAULT_PROFILE.max_slices;
    const dnnPrefix =
      rec.profile.dnn_prefix ||
      plProf?.dnn_prefix ||
      DEFAULT_PROFILE.dnn_prefix;
    const ranNode =
      rec.profile.du_node ||
      rec.profile.ue_node ||
      plProf?.du_node ||
      plProf?.ue_node ||
      "usrp";
    setProfile({
      ...DEFAULT_PROFILE,
      ...rec.profile,
      name: rec.profile.name,
      subnet,
      max_slices: maxSlices,
      dnn_prefix: dnnPrefix,
      // DU + UE always co-located on the same edge worker for rfsim.
      du_node: ranNode,
      ue_node: ranNode,
    });
    setSlices(rec.slices);
    setApplications(rec.applications || {});
    const net =
      rec.network && Object.keys(rec.network).length
        ? normalizeNetwork(rec.network)
        : DEFAULT_NETWORK;
    setNetworkIn(net);
    setSelectedName(rec.profile.name);
    setProfileSubnets((prev) =>
      subnet ? { ...prev, [rec.profile.name]: subnet } : prev,
    );
    setSavedAt(rec.updated_at);
    setDeployed(Boolean(rec.deployed));
    setDeployedAt(rec.deployed_at || "");
    setDeployFiles(rec.deploy_files || []);
    setClearPendingPush(false);
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
        const list = await refreshNames();
        const first = list.profiles[0];
        if (first) {
          applyRecord(first);
        } else {
          const defs = await api.profileDefaults();
          applyRecord({ ...defs, updated_at: "" } as ProfileRecord);
        }
        await refreshEdgeNodes();
        void refreshRegistryImages();
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

  /** Multus /24 collisions across saved profiles (live edit of current counted). */
  const subnetConflicts = useMemo(() => {
    const norm = (s: string) => s.trim().toLowerCase();
    const map: Record<string, string> = { ...profileSubnets };
    const liveKey = dirtyName ? profile.name : selectedName || profile.name;
    if (liveKey && profile.subnet?.trim()) {
      map[liveKey] = profile.subnet.trim();
    }
    if (dirtyName && selectedName) {
      // Name rename in progress — current Multus applies to the new name only.
      delete map[selectedName];
    }
    const bySub: Record<string, string[]> = {};
    for (const [name, sub] of Object.entries(map)) {
      const key = norm(sub || "");
      if (!key) continue;
      (bySub[key] ||= []).push(name);
    }
    return Object.entries(bySub)
      .filter(([, names]) => names.length > 1)
      .map(([subnet, names]) => ({
        subnet,
        names: names.slice().sort(),
      }))
      .sort((a, b) => a.subnet.localeCompare(b.subnet));
  }, [
    profileSubnets,
    profile.subnet,
    profile.name,
    selectedName,
    dirtyName,
  ]);

  const currentSubnetConflict = useMemo(() => {
    const mine = (profile.subnet || "").trim().toLowerCase();
    if (!mine) return null;
    return (
      subnetConflicts.find((g) => g.subnet === mine) || null
    );
  }, [subnetConflicts, profile.subnet]);

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
      !(await dialog.confirm({
        title: `Restore defaults for “${profile.name}”?`,
        message:
          "Resets subnet, max slices, DNN prefix, RAN node, network settings, " +
          "application servers, and 4-slice SLAs. Deploy status is kept. Clears the PL result.",
        confirmLabel: "Restore",
        danger: true,
      }))
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
      !(await dialog.confirm({
        title: "Restore default slice SLAs?",
        message:
          "Resets to CCTV / Physical AI / OTT / IoT and clears the current PL result.",
        confirmLabel: "Restore",
        danger: true,
      }))
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
        applications,
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
    const name = await dialog.prompt({
      title: "Add profile",
      message: "New profile name (K8s namespace):",
      defaultValue: `${profile.name}-2`,
      confirmLabel: "Create",
    });
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
      setStatus(
        `Created profile “${rec.profile.name}” · Multus ${rec.profile.subnet}` +
          ` · DNN ${rec.profile.dnn_prefix} · RAN ${rec.profile.du_node}`,
      );
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
      !(await dialog.confirm({
        title: `Delete profile “${name}”?`,
        message: `Removes it from the database. GitOps manifests under namespaces/${name}/ are not deleted.`,
        confirmLabel: "Delete",
        danger: true,
      }))
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
        // Align profile fields with the solve / IP plan — do not use
        // updateProfile() here; that clears the PL result.
        if (res.profile) {
          const ran =
            res.profile.du_node || res.profile.ue_node || profile.du_node;
          setProfile((p) => ({
            ...p,
            subnet: res.profile!.subnet || res.ip_plan?.subnet || p.subnet,
            max_slices: res.profile!.max_slices ?? p.max_slices,
            dnn_prefix: res.profile!.dnn_prefix || p.dnn_prefix,
            du_node: ran,
            ue_node: ran,
          }));
        } else if (res.ip_plan?.subnet) {
          setProfile((p) => ({ ...p, subnet: res.ip_plan!.subnet }));
        }
        await refreshNames();
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      setResult(null);
    } finally {
      setBusy(false);
    }
  }

  useEffect(() => {
    if (!consoleAutoScroll) return;
    scrollConsoleToBottom();
  }, [consoleText, consoleAutoScroll, consoleMessage]);

  function applyDeployStateFromProfile(
    resProfile: {
      deployed?: boolean;
      deployed_at?: string | null;
      deploy_files?: string[];
      updated_at?: string;
    } | null | undefined,
    writtenFallback: string[] = [],
  ) {
    if (resProfile) {
      setDeployed(Boolean(resProfile.deployed));
      setDeployedAt(resProfile.deployed_at || "");
      // Use ?? so an explicit empty list after Clear is kept (not overwritten).
      setDeployFiles(
        resProfile.deploy_files !== undefined && resProfile.deploy_files !== null
          ? resProfile.deploy_files
          : writtenFallback,
      );
      setSavedAt(resProfile.updated_at || savedAt);
    }
  }

  const hasGeneratedConfig =
    deployed || deployFiles.length > 0 || clearPendingPush;

  function beginRun(action: ApplyRun, step: ApplyStep | null = null) {
    setBusy(true);
    setRunningAction(action);
    setRunningStep(step);
    setError(null);
  }

  function endRun() {
    setBusy(false);
    setRunningAction(null);
    setRunningStep(null);
  }

  const genRunning =
    runningAction === "generate" || runningStep === "generate";
  const pushRunning = runningAction === "push" || runningStep === "push";
  const clearRunning = runningAction === "clear" || runningStep === "clear";
  const deployRunning = runningAction === "deploy";
  const undeployRunning =
    runningAction === "undeploy" || runningStep === "cleanup";
  const rolloutRunning = runningAction === "rollout";

  /** Generate config — write locally, no push. */
  async function onGenerate() {
    if (!result?.ok) return;
    beginRun("generate");
    resetConsole("Generate config");
    try {
      const res = await api.applyStream(
        {
          result,
          slices,
          profile,
          commit_message: commitMsg.replace("deploy", "generate-config"),
          dry_run: true,
        },
        streamHandlers(),
      );
      setConsoleOk(res.ok);
      setConsoleMessage("[generate] " + (res.message || ""));
      setConsoleFiles(res.written_files || []);
      setConsoleFilesLabel("Written files");
      if (res.profile) {
        applyDeployStateFromProfile(res.profile, res.written_files || []);
      } else {
        setDeployFiles(res.written_files || []);
      }
      if (res.ok) setClearPendingPush(false);
      if (!res.ok) setError(res.message);
      else {
        setStatus(
          `Generated config: ${res.written_files.length} file(s) — Push to sync`,
        );
        void refreshClusterStatus();
      }
    } catch (e) {
      setConsoleOk(false);
      setConsoleMessage(e instanceof Error ? e.message : String(e));
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      endRun();
    }
  }

  /** Deploy = Generate + Push. */
  async function onDeploy() {
    if (!result?.ok) return;
    if (
      !(await dialog.confirm({
        title: `Deploy “${profile.name}”?`,
        message:
          `Generate config + Push: writes namespaces/${profile.name}/ then ` +
          `pushes to Gitea (Config Sync) for N=${slices.length} slice(s) on ${profile.subnet}.`,
        confirmLabel: "Deploy",
      }))
    ) {
      return;
    }
    beginRun("deploy", "generate");
    resetConsole("Deploy");
    try {
      const res = await api.applyStream(
        {
          result,
          slices,
          profile,
          commit_message: commitMsg,
          dry_run: false,
        },
        streamHandlers({ trackDeploySteps: true }),
      );
      setConsoleOk(res.ok);
      setConsoleMessage("[deploy] " + (res.message || ""));
      setConsoleFiles(res.written_files || []);
      setConsoleFilesLabel("Written files");
      if (res.profile) {
        applyDeployStateFromProfile(res.profile, res.written_files || []);
      } else {
        setDeployFiles(res.written_files || []);
        if (res.ok) {
          setDeployed(true);
          setDeployedAt(new Date().toISOString());
        }
      }
      if (res.ok) setClearPendingPush(false);
      if (!res.ok) setError(res.message);
      else {
        setStatus(`Deployed: ${res.written_files.length} file(s)`);
        void refreshClusterStatus();
      }
    } catch (e) {
      setConsoleOk(false);
      setConsoleMessage(e instanceof Error ? e.message : String(e));
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      endRun();
    }
  }

  /** Push — sync current local GitOps trees to Gitea (no re-render). */
  async function onPush() {
    if (!profileOk) return;
    if (
      !(await dialog.confirm({
        title: `Push “${profile.name}”?`,
        message:
          "Pushes the current local GitOps trees to Gitea (no re-render). " +
          "Use after Generate config, or after Clear to sync deletions.",
        confirmLabel: "Push",
      }))
    ) {
      return;
    }
    beginRun("push");
    resetConsole("Push");
    try {
      const res = await api.pushStream(
        {
          profile,
          commit_message: commitMsg.replace("deploy", "push"),
        },
        streamHandlers(),
      );
      setConsoleOk(res.ok);
      setConsoleMessage("[push] " + (res.message || ""));
      setConsoleFiles(res.written_files || []);
      setConsoleFilesLabel("Files on disk");
      if (res.profile) {
        applyDeployStateFromProfile(res.profile, res.written_files || []);
      } else if (res.ok) {
        setDeployed(Boolean(res.deployed));
        setDeployFiles(res.written_files || []);
        if (!res.deployed) setDeployedAt("");
      }
      if (res.ok) setClearPendingPush(false);
      if (!res.ok) setError(res.message);
      else {
        setStatus(res.message || "Push complete");
        void refreshClusterStatus();
      }
    } catch (e) {
      setConsoleOk(false);
      setConsoleMessage(e instanceof Error ? e.message : String(e));
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      endRun();
    }
  }

  /** Clear generated config — local remove only (no push). */
  async function onClear() {
    if (!profileOk) return;
    if (
      !(await dialog.confirm({
        title: `Clear generated config for “${profile.name}”?`,
        message:
          `Removes local namespaces/${profile.name}/ under repos/ and clears ` +
          "the generated file list. Does not push — use Push afterward to " +
          "sync deletions to Gitea, or Undeploy (= Clear + Push + cluster cleanup).",
        confirmLabel: "Clear",
        danger: true,
      }))
    ) {
      return;
    }
    beginRun("clear");
    resetConsole("Clear generated config");
    try {
      const res = await api.undeployStream(
        {
          profile,
          commit_message: "ina-pl: clear profile manifests",
          dry_run: true,
        },
        streamHandlers(),
      );
      setConsoleOk(res.ok);
      setConsoleMessage("[clear] " + (res.message || ""));
      setConsoleFiles(res.removed_paths || []);
      setConsoleFilesLabel("Removed paths");
      // Always reflect cleared local state (don't trust nested profile only).
      setDeployed(false);
      setDeployedAt("");
      setDeployFiles([]);
      if (res.profile) {
        applyDeployStateFromProfile({
          ...res.profile,
          deployed: false,
          deploy_files: [],
        });
      }
      if (res.ok) {
        setClearPendingPush(true);
        const n = (res.removed_paths || []).length;
        setStatus(
          n > 0
            ? `Cleared ${n} local path(s) for “${profile.name}” — Push to sync deletions`
            : `No local namespaces/${profile.name}/ found — already clear (Push still syncs if Gitea has leftovers)`,
        );
        void refreshClusterStatus();
      } else {
        setError(res.message);
      }
    } catch (e) {
      setConsoleOk(false);
      setConsoleMessage(e instanceof Error ? e.message : String(e));
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      endRun();
    }
  }

  /** Undeploy = Clear + Push (+ cluster cleanup). */
  async function onUndeploy() {
    if (!profileOk) return;
    if (
      !(await dialog.confirm({
        title: `Undeploy “${profile.name}”?`,
        message:
          `Clear + Push: deletes namespaces/${profile.name}/ from GitOps repos, ` +
          "pushes to Gitea, and force-cleans cluster namespaces.",
        confirmLabel: "Undeploy",
        danger: true,
      }))
    ) {
      return;
    }
    beginRun("undeploy", "clear");
    resetConsole("Undeploy");
    try {
      const res = await api.undeployStream(
        {
          profile,
          commit_message: "ina-pl: undeploy profile manifests",
          dry_run: false,
        },
        streamHandlers({ trackUndeploySteps: true }),
      );
      setConsoleOk(res.ok);
      setConsoleMessage(
        res.message ? `[undeploy]\n${res.message}` : "[undeploy]",
      );
      setConsoleFiles(res.removed_paths || []);
      setConsoleFilesLabel("Removed paths");
      if (res.profile) {
        applyDeployStateFromProfile(res.profile);
      } else if (res.ok) {
        setDeployed(false);
        setDeployedAt("");
        setDeployFiles([]);
      }
      if (res.ok) setClearPendingPush(false);
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
      endRun();
    }
  }

  async function onProfileRollout(opts?: {
    skip_ues?: boolean;
    skip_ran?: boolean;
    only_ues?: boolean;
  }) {
    if (!profileOk) return;
    if (
      !(await dialog.confirm({
        title: `Start rollout for “${profile.name}”?`,
        message:
          "Order: UPF → SMF → PFCP → CU-CP → CU-UP → DU → UEs. " +
          "Same bring-up order as oai-slice-deployment; may take several minutes.",
        confirmLabel: "Rollout",
      }))
    ) {
      return;
    }
    const ctrl = new AbortController();
    streamAbortRef.current = ctrl;
    setRolloutBusy(true);
    beginRun("rollout");
    resetConsole("Rollout");
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
      endRun();
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

  const kpis = [
    {
      label: "Profile",
      kicker: "namespace",
      value: profile.name || "—",
      icon: (
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
          <rect x="3" y="3" width="7" height="7" rx="1.5" />
          <rect x="14" y="3" width="7" height="7" rx="1.5" />
          <rect x="14" y="14" width="7" height="7" rx="1.5" />
          <rect x="3" y="14" width="7" height="7" rx="1.5" />
        </svg>
      ),
      bad: !profileOk,
    },
    {
      label: "Slices",
      kicker: `max ${profile.max_slices}`,
      value: String(slices.length),
      icon: (
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
          <path d="M4 20v-4M10 20v-9M16 20v-14M22 20V9" />
        </svg>
      ),
    },
    {
      label: "PL solve",
      kicker: "placement",
      value: result?.ok ? "Solved" : "Pending",
      icon: (
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
          <path d="M22 11.08V12a10 10 0 1 1-5.93-9.14" />
          <polyline points="22 4 12 14.01 9 11.01" />
        </svg>
      ),
      bad: Boolean(result && !result.ok),
    },
    {
      label: "Deploy",
      kicker: "GitOps",
      value: deployed ? "Live" : "Idle",
      icon: (
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
          <path d="M13 2 4 14h6l-1 8 9-12h-6z" strokeLinejoin="round" />
        </svg>
      ),
    },
  ];

  return (
    <div className="page-layout">
      <div className="page">
      <KpiStrip items={kpis} />

      <Card className="tier">
        <div className="panel-head">
          <SectionLabel kicker={showProfile ? (savedAt ? savedAt.slice(0, 19) : undefined) : "collapsed"}>
            Profile
          </SectionLabel>
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
            <button type="button" onClick={() => setShowProfile((v) => !v)}>
              {showProfile ? "Hide" : "Show"}
            </button>
          </div>
        </div>

        {showProfile ? (
          <>
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
                      {profileSubnets[n] ? `${n} · ${profileSubnets[n]}` : n}
                    </option>
                  ))}
                </select>
              </FieldHelp>
              <FieldHelp
                label="Name (namespace)"
                help="K8s namespace on central/regional/edge. Must be a DNS label (a-z0-9-)."
              >
                <input
                  key={`name-${selectedName}`}
                  value={profile.name}
                  onChange={(e) => updateProfile({ name: e.target.value.trim() })}
                />
              </FieldHelp>
              <FieldHelp
                className={currentSubnetConflict ? "field-conflict" : undefined}
                label="Multus subnet"
                help="Per-profile macvlan /24 (e.g. ina-infra → 10.1.140.0/24, next profile → 10.1.141.0/24). Add profile auto-picks a free /24. IPs use host = base[role] + n."
              >
                <input
                  key={`subnet-${selectedName}`}
                  value={profile.subnet}
                  onChange={(e) => updateProfile({ subnet: e.target.value.trim() })}
                  aria-invalid={Boolean(currentSubnetConflict)}
                />
              </FieldHelp>
              <FieldHelp
                label="Max slices"
                help="Per-profile upper bound on Add slice (IP bands sized for this cap)."
              >
                <input
                  key={`max-${selectedName}`}
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
                help="Per-profile UE PDU pool prefix: {prefix}.{n}.0/24. Add profile auto-picks a free 10.14x when the copied prefix is taken (aligned with Multus 10.1.14x)."
              >
                <input
                  key={`dnn-${selectedName}`}
                  value={profile.dnn_prefix}
                  onChange={(e) => updateProfile({ dnn_prefix: e.target.value.trim() })}
                />
              </FieldHelp>
              <FieldHelp
                className="field-help-wide"
                label="RAN node (DU + UE)"
                help="Per-profile edge worker for OAI DU + UEs (rfsim). usrp → Multus enp4s0f0; VMs → enp7s0; bare-metal (edge-2) → eno1."
              >
                <select
                  key={`ran-${selectedName}`}
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
                    <option key={n} value={n} title={edgeOptionDetail(n)}>
                      {edgeOptionLabel(n)}
                    </option>
                  ))}
                </select>
              </FieldHelp>
            </div>

            {edgeNodesError && (
              <p className="hint error" style={{ marginTop: 12 }}>Edge nodes: {edgeNodesError}</p>
            )}
            {!edgeNodesError && edgeNodes.length > 0 && (
              <div className="edge-nodes-strip" style={{ marginTop: 12 }}>
                <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 6 }}>
                  <span style={{ fontSize: 11, fontWeight: 600, color: "#94a3b8", textTransform: "uppercase", letterSpacing: "0.05em" }}>
                    Discovered Edge Nodes ({edgeNodes.length})
                  </span>
                  <span style={{ fontSize: 11, color: "var(--text-dim)" }}>· Multus master & IP mapping</span>
                </div>
                <div
                  style={{
                    display: "grid",
                    gridTemplateColumns: "repeat(auto-fit, minmax(180px, 1fr))",
                    gap: 8,
                  }}
                >
                  {edgeNodes.map((n) => {
                    const isSelected = (profile.du_node === n.name) || (profile.ue_node === n.name);
                    return (
                      <div
                        key={n.name}
                        style={{
                          display: "flex",
                          flexDirection: "column",
                          padding: "6px 10px",
                          borderRadius: 6,
                          background: isSelected ? "rgba(192, 132, 252, 0.12)" : "rgba(15, 23, 42, 0.4)",
                          border: isSelected
                            ? "1px solid rgba(192, 132, 252, 0.4)"
                            : "1px solid rgba(255, 255, 255, 0.08)",
                        }}
                      >
                        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                          <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
                            <span
                              style={{
                                width: 6,
                                height: 6,
                                borderRadius: "50%",
                                backgroundColor: n.ready ? "#22c55e" : "#ef4444",
                              }}
                            />
                            <strong style={{ fontSize: 12, color: isSelected ? "#c084fc" : "var(--text)" }}>
                              {n.name}
                            </strong>
                          </div>
                          {isSelected && (
                            <span
                              style={{
                                fontSize: 9,
                                fontWeight: 700,
                                padding: "1px 5px",
                                borderRadius: 4,
                                background: "rgba(192, 132, 252, 0.25)",
                                color: "#c084fc",
                                textTransform: "uppercase",
                              }}
                            >
                              Active RAN
                            </span>
                          )}
                        </div>
                        <div style={{ display: "flex", justifyContent: "space-between", fontSize: 11, color: "#94a3b8", marginTop: 4, fontFamily: "monospace" }}>
                          <span>{n.multus_master || "—"}</span>
                          <span>{n.internal_ip || "—"}</span>
                        </div>
                      </div>
                    );
                  })}
                </div>
              </div>
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
            {subnetConflicts.length > 0 && (
              <p className="hint warn" role="status">
                Warning: Multus subnet clash
                {subnetConflicts.map((g) => (
                  <span key={g.subnet}>
                    {" — "}
                    <code>{g.subnet}</code> used by {g.names.join(", ")}
                  </span>
                ))}
                . Each profile needs its own /24.
              </p>
            )}
            {savedAt && (
              <p className="hint">
                Last saved: <code>{savedAt}</code>
              </p>
            )}
            {status && <p className="hint ok-inline">{status}</p>}
          </>
        ) : (
          <p className="hint">Collapsed — Show to view or edit profile namespace, subnets, and RAN node.</p>
        )}
      </Card>

      <Card className="tier">
        <div className="panel-head">
          <SectionLabel kicker={`${slices.length} slices · images · network · servers`}>
            Slice config
          </SectionLabel>
          <button type="button" onClick={() => setShowSliceConfig((v) => !v)}>
            {showSliceConfig ? "Hide" : "Show"}
          </button>
        </div>
        {showSliceConfig ? (
          <>
            <p className="hint">
              Slice-level settings for this profile: container images, network substrate, application servers, and SLAs. Saved with Update; PL Solve uses network + SLAs; GitOps Deploy applies images and servers.
            </p>
            <div className="slice-config-sections">
      <OaiImagesForm
        value={profile.oai_images}
        registryStatus={registryStatus}
        loading={registryLoading}
        onRefresh={() => void refreshRegistryImages(true)}
        onChange={(oai_images) => updateProfile({ oai_images })}
        disabled={busy}
        embedded
      />

      <div className="slice-config-section">
        <div className="panel-head">
          <SectionLabel kicker={showNet ? "editing" : "collapsed"}>
            Network settings
          </SectionLabel>
          <button type="button" onClick={() => setShowNet((v) => !v)}>
            {showNet ? "Hide" : "Show"}
          </button>
        </div>
        {showNet ? (
          <>
            <p className="hint">Part of slice config · hover ? for help</p>
            <NetworkSettingsForm value={networkIn} onChange={updateNetwork} />
          </>
        ) : (
          <p className="hint">Collapsed — Show to edit substrate variables for this profile.</p>
        )}
      </div>

      <ApplicationServerSettingsBox
        profile={profile}
        slices={slices}
        applications={applications}
        plResult={result}
        onChangeApplications={setApplications}
        disabled={busy}
        embedded
      />

      <div className="slice-config-section">
        <div className="panel-head">
          <SectionLabel kicker={showSlices ? `N=${slices.length}/${profile.max_slices}` : "collapsed"}>
            Slice SLAs
          </SectionLabel>
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
            <button type="button" onClick={() => setShowSlices((v) => !v)}>
              {showSlices ? "Hide" : "Show"}
            </button>
          </div>
        </div>
        {showSlices ? (
          <div className="table-wrap">
            <table className="dtable">
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
                  <tr key={s.id}>
                    <td>
                      <span className="badge badge-accent">S{s.id}</span>
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
                        step="0.5"
                        min={0.1}
                        value={s.t_bar}
                        onChange={(e) =>
                          updateSlice(i, { t_bar: Number(e.target.value) })
                        }
                      />
                    </td>
                    <td>
                      <input
                        type="number"
                        step="1"
                        min={0.1}
                        value={s.d_bar}
                        onChange={(e) =>
                          updateSlice(i, { d_bar: Number(e.target.value) })
                        }
                      />
                    </td>
                    <td>
                      <select
                        value={s.h_s}
                        onChange={(e) =>
                          updateSlice(i, { h_s: Number(e.target.value) })
                        }
                      >
                        <option value={0}>0 (shared)</option>
                        <option value={1}>1 (dedicated)</option>
                      </select>
                    </td>
                    <td>
                      <input
                        type="number"
                        step="0.1"
                        min={0.01}
                        value={s.eta_t0}
                        onChange={(e) =>
                          updateSlice(i, { eta_t0: Number(e.target.value) })
                        }
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
        ) : (
          <p className="hint">Collapsed — Show to view or edit slice SLAs and requirements.</p>
        )}
      </div>
            </div>
          </>
        ) : (
          <p className="hint">Collapsed — Show to edit container images, network, application servers, and slice SLAs.</p>
        )}
      </Card>

      {error && <div className="banner error">{error}</div>}

      {result?.ok && (
        <Card className="tier" glow>
          <header className="result-title-block" style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start" }}>
            <div>
              <div className="result-eyebrow">
                <span className="status-dot dot-ok" />
                <span className="result-eyebrow-text">PL result</span>
                <span className="site-badge">{result.slices.length} slice(s)</span>
              </div>
              <h2 className="result-title">Optimal placement and IP plan</h2>
            </div>
            <div className="actions">
              <button type="button" onClick={() => setShowPlResult((v) => !v)}>
                {showPlResult ? "Hide" : "Show"}
              </button>
            </div>
          </header>

          {showPlResult ? (
            <>

          <section className="result-section">
            <div className="result-section-head">
              <SectionLabel
                kicker={
                  <span style={{ display: "inline-flex", gap: 6, alignItems: "center" }}>
                    <span className="tag tag-cluster-edge" style={{ fontSize: 9.5 }}>Edge</span>
                    <span className="tag tag-cluster-regional" style={{ fontSize: 9.5 }}>Regional</span>
                    <span className="tag tag-cluster-central" style={{ fontSize: 9.5 }}>Central</span>
                  </span>
                }
              >
                Placement
              </SectionLabel>
            </div>
            <Topology slices={result.slices} />
          </section>

          <section className="result-section">
            <div className="result-section-head">
              <SectionLabel>Resources</SectionLabel>
            </div>
            <div className="table-wrap">
              <table className="dtable">
                <thead>
                  <tr>
                    <th>Slice</th>
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
                          S{s.id}{" "}
                          {s.slice_type && (
                            <span className="muted">({s.slice_type})</span>
                          )}
                        </td>
                        <td className="mono">{s.resources.b_min ?? "—"}</td>
                        <td className="mono">{s.resources.a_c_cu.toFixed(2)}</td>
                        <td className="mono">{s.resources.a_c_upf.toFixed(2)}</td>
                        <td className="mono">{s.resources.a_c_app.toFixed(2)}</td>
                      </tr>
                    ))}
                </tbody>
              </table>
            </div>
          </section>

          {ipPlan && (
            <section className="result-section">
              <div className="result-section-head">
                <SectionLabel kicker={ipPlan.subnet}>IP plan</SectionLabel>
              </div>
              <div className="table-wrap">
                <table className="dtable">
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
                        <span className="tag tag-cluster-central" style={{ marginLeft: 8, fontSize: 9.5 }}>Central</span>
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
                        <span className="tag tag-cluster-central" style={{ marginLeft: 8, fontSize: 9.5 }}>Central</span>
                      </td>
                    </tr>
                    <tr>
                      <td>SMF N4</td>
                      <td>
                        <code>{ipPlan.shared.smf_n4}</code>
                        <span className="tag tag-cluster-central" style={{ marginLeft: 8, fontSize: 9.5 }}>Central</span>
                      </td>
                    </tr>
                    <tr>
                      <td>CU-CP N2 / F1-C / E1</td>
                      <td>
                        <code>
                          {ipPlan.shared.cucp_n2} / {ipPlan.shared.cucp_f1c} /{" "}
                          {ipPlan.shared.cucp_e1}
                        </code>
                        <span className="tag tag-cluster-edge" style={{ marginLeft: 8, fontSize: 9.5 }}>Edge</span>
                      </td>
                    </tr>
                    <tr>
                      <td>DU F1 / RF</td>
                      <td>
                        <code>
                          {ipPlan.shared.du_f1} / {ipPlan.shared.du_rf}
                        </code>
                        <span className="tag tag-cluster-edge" style={{ marginLeft: 8, fontSize: 9.5 }}>Edge</span>
                      </td>
                    </tr>
                    <tr>
                      <td>FlexRIC / xApp</td>
                      <td>
                        <code>
                          {ipPlan.shared.flexric_e2} / {ipPlan.shared.xapp_e2}
                        </code>
                        <span className="tag tag-cluster-edge" style={{ marginLeft: 8, fontSize: 9.5 }}>Edge</span>
                      </td>
                    </tr>
                  </tbody>
                </table>
              </div>
              <div className="table-wrap">
                <table className="dtable">
                  <thead>
                    <tr>
                      <th>n</th>
                      <th>UPF N3</th>
                      <th>CU-UP N3</th>
                      <th>UPF N4</th>
                      <th>UPF N6</th>
                      <th>CU-UP E1</th>
                      <th>CU-UP F1-U</th>
                      <th>UE RF</th>
                      <th>DNN</th>
                      <th>Sites</th>
                    </tr>
                  </thead>
                  <tbody>
                    {ipPlan.slices.map((s) => (
                      <tr key={s.n}>
                        <td className="mono">{s.n}</td>
                        <td>
                          <code>{s.upf_n3}</code>
                        </td>
                        <td>
                          <code>{s.cuup_n3}</code>
                        </td>
                        <td>
                          <code>{s.upf_n4}</code>
                        </td>
                        <td>
                          <code>{s.upf_n6}</code>
                        </td>
                        <td>
                          <code>{s.cuup_e1}</code>
                        </td>
                        <td>
                          <code>{s.cuup_f1u}</code>
                        </td>
                        <td>
                          <code>{s.ue_rf}</code>
                        </td>
                        <td>
                          <code>{s.dnn_cidr}</code>
                        </td>
                        <td>
                          <div style={{ display: "inline-flex", gap: 4, flexWrap: "wrap", alignItems: "center" }}>
                            {s.site_cu && (
                              <span className={`tag ${getClusterTagClass(s.site_cu)}`} style={{ fontSize: 9.5 }}>
                                CU: {s.site_cu}
                              </span>
                            )}
                            {s.site_upf && (
                              <span className={`tag ${getClusterTagClass(s.site_upf)}`} style={{ fontSize: 9.5 }}>
                                UPF: {s.site_upf}
                              </span>
                            )}
                            {s.site_app && (
                              <span className={`tag ${getClusterTagClass(s.site_app)}`} style={{ fontSize: 9.5 }}>
                                APP: {s.site_app}
                              </span>
                            )}
                          </div>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </section>
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
            <div className="apply-tree" style={{ marginTop: 12 }}>
              <div className="apply-tree-combo apply-tree-gen">
                <button
                  type="button"
                  className={"btn-tone-ok" + (genRunning ? " is-running" : "")}
                  disabled={busy || !result?.ok}
                  onClick={() => void onGenerate()}
                  title="Write manifests locally without pushing to Gitea"
                >
                  <BtnProgress active={genRunning} />
                  Generate config
                </button>
                <span className="apply-tree-caption" aria-hidden>
                  &nbsp;
                </span>
              </div>
              <svg
                className="apply-tree-fork"
                viewBox="0 0 40 100"
                preserveAspectRatio="none"
                aria-hidden
              >
                <path
                  d="M0 25 H20 V50 H40 M0 75 H20 V50"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth="2"
                  vectorEffect="non-scaling-stroke"
                />
              </svg>
              <button
                type="button"
                className={
                  "apply-tree-push btn-tone-ok" +
                  (pushRunning ? " is-running" : "")
                }
                disabled={busy || !profileOk || !hasGeneratedConfig}
                onClick={() => void onPush()}
                title="Push current local GitOps trees to Gitea (no re-render). After Clear, Push syncs deletions."
              >
                <BtnProgress active={pushRunning} />
                Push
              </button>
              <svg
                className="apply-tree-split"
                viewBox="0 0 40 100"
                preserveAspectRatio="none"
                aria-hidden
              >
                <path
                  d="M0 50 H20 V25 H40 M20 50 V75 H40"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth="2"
                  vectorEffect="non-scaling-stroke"
                />
              </svg>
              <div className="apply-tree-combo apply-tree-deploy">
                <button
                  type="button"
                  className={"primary" + (deployRunning ? " is-running" : "")}
                  disabled={busy || !result?.ok}
                  onClick={() => void onDeploy()}
                  title="Generate config + Push"
                >
                  <BtnProgress active={deployRunning} />
                  Deploy
                </button>
                <span className="apply-tree-caption">
                  {deployRunning
                    ? runningStep === "push"
                      ? "pushing…"
                      : "generating…"
                    : "gen + push"}
                </span>
              </div>

              <div className="apply-tree-combo apply-tree-clr">
                <button
                  type="button"
                  className={
                    "btn-tone-warn" + (clearRunning ? " is-running" : "")
                  }
                  disabled={busy || !profileOk || !hasGeneratedConfig}
                  onClick={() => void onClear()}
                  title="Remove local namespaces/<profile>/; does not push"
                >
                  <BtnProgress active={clearRunning} />
                  Clear generated config
                </button>
                <span className="apply-tree-caption" aria-hidden>
                  &nbsp;
                </span>
              </div>
              <div className="apply-tree-combo apply-tree-undeploy">
                <button
                  type="button"
                  className={
                    "btn-tone-warn" + (undeployRunning ? " is-running" : "")
                  }
                  disabled={busy || !profileOk || !hasGeneratedConfig}
                  onClick={() => void onUndeploy()}
                  title="Clear + Push + cluster cleanup"
                >
                  <BtnProgress active={undeployRunning} />
                  Undeploy
                </button>
                <span className="apply-tree-caption">
                  {runningAction === "undeploy"
                    ? runningStep === "push"
                      ? "pushing…"
                      : runningStep === "cleanup"
                        ? "cleanup…"
                        : "clearing…"
                    : "clear + push"}
                </span>
              </div>
            </div>
            <div className="actions" style={{ marginTop: 12 }}>
              <button
                type="button"
                className={rolloutRunning ? "is-running" : undefined}
                disabled={busy || !profileOk || rolloutBusy}
                onClick={() => void onProfileRollout()}
                title="Staged restart only: UPF → SMF → PFCP → CU-CP → CU-UP → DU → UEs"
              >
                <BtnProgress active={rolloutRunning} />
                {rolloutBusy ? "Rolling out…" : "Rollout"}
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
              Left steps feed into shared <strong>Push</strong>.{" "}
              <strong>Deploy</strong> / <strong>Undeploy</strong> are the
              shortcuts. Undeploy also force-cleans cluster namespaces.{" "}
              <strong>Rollout</strong> is separate — staged NF restart only.
            </p>
          </div>
            </>
          ) : (
            <p className="hint" style={{ marginTop: 10 }}>
              Collapsed — Show to view optimal placement matrix, resources, and IP plan.
            </p>
          )}
        </Card>
      )}

      <ApplicationSettingsBox
        profile={profile}
        slices={slices}
        applications={applications}
        onChangeApplications={setApplications}
        disabled={busy}
        onDeployStart={(title) => {
          resetConsole(title);
          appendConsole(`[${new Date().toLocaleTimeString()}] Starting: ${title}`);
        }}
        onDeployLog={(_stream, line) => {
          appendConsole(line);
        }}
        onDeployStatus={(msg) => {
          setStatus(msg);
          appendConsole(`ℹ ${msg}`);
        }}
        onDeployDone={(msg) => {
          setStatus(msg);
          appendConsole(`✔ ${msg}`);
          if (profile.name) {
            void loadProfile(profile.name);
          }
        }}
        onDeployError={(err) => {
          setError(err);
          appendConsole(`✖ Error: ${err}`);
        }}
      />

      {(consoleOpen || deployFiles.length > 0) && (
        <div ref={consolePanelRef}>
          <Card className="tier console-panel">
            <div className="panel-head">
              <SectionLabel kicker="stream">
                {consoleOpen ? consoleTitle : "Console"}
              </SectionLabel>
              <div className="actions">
                {!consoleAutoScroll && consoleText.length > 0 && (
                  <button
                    type="button"
                    className="primary"
                    onClick={() => enableConsoleAutoScroll()}
                    title="Jump to latest output and resume auto-scroll"
                  >
                    To bottom
                  </button>
                )}
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
                      setConsoleAutoScroll(true);
                      if (!deployFiles.length) setConsoleOpen(false);
                    }}
                  >
                    Clear
                  </button>
                )}
              </div>
            </div>
            {consoleMessage && (
              <pre
                className={
                  "console-summary" +
                  (consoleOk == null ? "" : consoleOk ? " ok" : " error")
                }
              >
                {consoleMessage}
              </pre>
            )}
            <pre
              className="net-pre console-pre"
              ref={consoleRef}
              onScroll={onConsoleScroll}
            >
              {consoleText ||
                (busy
                  ? "Waiting for output…"
                  : consoleOpen
                    ? "(empty)"
                    : "Run Generate config, Deploy, or Rollout to stream output here.")}
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
          </Card>
        </div>
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
        appServerCount={Object.values(applications).filter((a) => a.enabled && a.app_type !== "none").length}
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
