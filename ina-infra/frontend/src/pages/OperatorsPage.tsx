import { useCallback, useEffect, useMemo, useState, type Dispatch, type SetStateAction } from "react";
import {
  api,
  OperatorListOut,
  OperatorNfOut,
  OperatorOut,
  OperatorResourceTarget,
} from "../api/client";
import Card from "../components/ui/Card";
import SectionLabel from "../components/ui/SectionLabel";

type ResDraft = {
  cpu_limit: string;
  cpu_request: string;
  memory_limit: string;
  memory_request: string;
  gpu_limit: string;
  gpu_request: string;
  vram_limit: string;
  vram_request: string;
};

type DraftMap = Record<string, ResDraft>;

const DRAFT_KEYS: (keyof ResDraft)[] = [
  "cpu_limit",
  "cpu_request",
  "memory_limit",
  "memory_request",
  "gpu_limit",
  "gpu_request",
  "vram_limit",
  "vram_request",
];

const EMPTY_DRAFT: ResDraft = {
  cpu_limit: "",
  cpu_request: "",
  memory_limit: "",
  memory_request: "",
  gpu_limit: "",
  gpu_request: "",
  vram_limit: "",
  vram_request: "",
};

function asDraft(partial?: Partial<ResDraft> | null): ResDraft {
  const out = { ...EMPTY_DRAFT };
  if (!partial) return out;
  for (const key of DRAFT_KEYS) {
    out[key] = String(partial[key] ?? "");
  }
  return out;
}

type ResKind = "cpu" | "memory" | "gpu" | "vram";

const RES_ROWS: {
  kind: ResKind;
  label: string;
  limPh: string;
  reqPh: string;
  limKey: keyof ResDraft;
  reqKey: keyof ResDraft;
  reportedLim: (nf: OperatorNfOut) => string | null | undefined;
  reportedReq: (nf: OperatorNfOut) => string | null | undefined;
  desiredLim: (d: OperatorResourceTarget) => string | null | undefined;
  desiredReq: (d: OperatorResourceTarget) => string | null | undefined;
}[] = [
  {
    kind: "cpu",
    label: "CPU",
    limPh: "e.g. 300m",
    reqPh: "e.g. 50m",
    limKey: "cpu_limit",
    reqKey: "cpu_request",
    reportedLim: (nf) => nf.reported_cpu_limit,
    reportedReq: (nf) => nf.reported_cpu_request,
    desiredLim: (d) => d.cpu_limit,
    desiredReq: (d) => d.cpu_request,
  },
  {
    kind: "memory",
    label: "RAM",
    limPh: "e.g. 512Mi",
    reqPh: "e.g. 128Mi",
    limKey: "memory_limit",
    reqKey: "memory_request",
    reportedLim: (nf) => nf.reported_memory_limit,
    reportedReq: (nf) => nf.reported_memory_request,
    desiredLim: (d) => d.memory_limit,
    desiredReq: (d) => d.memory_request,
  },
  {
    kind: "gpu",
    label: "GPU",
    limPh: "e.g. 1",
    reqPh: "e.g. 1",
    limKey: "gpu_limit",
    reqKey: "gpu_request",
    reportedLim: (nf) => nf.reported_gpu_limit,
    reportedReq: (nf) => nf.reported_gpu_request,
    desiredLim: (d) => d.gpu_limit,
    desiredReq: (d) => d.gpu_request,
  },
  {
    kind: "vram",
    label: "VRAM",
    limPh: "e.g. 8Gi",
    reqPh: "e.g. 8Gi",
    limKey: "vram_limit",
    reqKey: "vram_request",
    reportedLim: (nf) => nf.reported_vram_limit,
    reportedReq: (nf) => nf.reported_vram_request,
    desiredLim: (d) => d.vram_limit,
    desiredReq: (d) => d.vram_request,
  },
];

function nfKey(operatorId: string, nf: string) {
  return `${operatorId}::${nf}`;
}

/** Prefer live reported; fall back to desired. */
function pickCurrent(
  live?: string | null,
  desired?: string | null,
): string {
  return (live || desired || "").trim();
}

function draftFromNf(nf: OperatorNfOut): ResDraft {
  const d = nf.desired;
  return asDraft({
    cpu_limit: pickCurrent(nf.reported_cpu_limit, d?.cpu_limit),
    cpu_request: pickCurrent(nf.reported_cpu_request, d?.cpu_request),
    memory_limit: pickCurrent(nf.reported_memory_limit, d?.memory_limit),
    memory_request: pickCurrent(nf.reported_memory_request, d?.memory_request),
    gpu_limit: pickCurrent(nf.reported_gpu_limit, d?.gpu_limit),
    gpu_request: pickCurrent(nf.reported_gpu_request, d?.gpu_request),
    vram_limit: pickCurrent(nf.reported_vram_limit, d?.vram_limit),
    vram_request: pickCurrent(nf.reported_vram_request, d?.vram_request),
  });
}

function formatLastSeen(iso: string): { absolute: string; relative: string } {
  if (!iso) return { absolute: "—", relative: "never" };
  const t = Date.parse(iso);
  if (Number.isNaN(t)) return { absolute: iso, relative: iso };
  const d = new Date(t);
  const absolute = d.toLocaleString(undefined, {
    year: "numeric",
    month: "short",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
  const sec = Math.max(0, Math.round((Date.now() - t) / 1000));
  let relative: string;
  if (sec < 5) relative = "just now";
  else if (sec < 60) relative = `${sec}s ago`;
  else if (sec < 3600) relative = `${Math.floor(sec / 60)}m ago`;
  else if (sec < 86400) relative = `${Math.floor(sec / 3600)}h ago`;
  else relative = `${Math.floor(sec / 86400)}d ago`;
  return { absolute, relative };
}

function applyTone(status: string): "ok" | "warn" | "err" | "muted" {
  if (status === "ok") return "ok";
  if (status === "pending") return "warn";
  if (status === "error") return "err";
  return "muted";
}

function kindLabel(kind: string): string {
  const k = (kind || "").toLowerCase();
  if (k === "cuup") return "CU-UP";
  if (k === "cucp") return "CU-CP";
  if (k === "du") return "DU";
  return kind || "NF";
}

function currentQty(
  live?: string | null,
  desired?: string | null,
): { display: string; hint?: string } {
  const l = (live || "").trim();
  const d = (desired || "").trim();
  if (l && d && l !== d) return { display: l, hint: `desired ${d}` };
  if (l) return { display: l };
  if (d) return { display: d, hint: "desired (not live yet)" };
  return { display: "—" };
}

/** Fields in `keys` that differ from live/desired current. */
function rowChangedBody(
  d: ResDraft,
  nf: OperatorNfOut,
  keys: (keyof ResDraft)[],
): Partial<ResDraft> {
  const baseline = draftFromNf(nf);
  const body: Partial<ResDraft> = {};
  for (const key of keys) {
    const next = (d[key] ?? "").trim();
    const cur = (baseline[key] ?? "").trim();
    if (next && next !== cur) body[key] = next;
  }
  return body;
}

export default function OperatorsPage() {
  const [list, setList] = useState<OperatorListOut | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [applyError, setApplyError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [drafts, setDrafts] = useState<DraftMap>({});
  const [applying, setApplying] = useState<string | null>(null);
  const [, setTick] = useState(0);

  const refresh = useCallback(async () => {
    setBusy(true);
    try {
      const data = await api.listOperators();
      setList(data);
      setError(null);
      // Surface latest agent apply failures in the status rail.
      const applyFails: string[] = [];
      for (const op of data.operators) {
        for (const nf of op.nfs) {
          if (nf.apply_status === "error" && nf.apply_message) {
            applyFails.push(`${nf.name}: ${nf.apply_message}`);
          }
        }
      }
      setApplyError(applyFails.length ? applyFails.join("\n") : null);
      // Keep user edits; only seed empty drafts for new NFs.
      setDrafts((prev) => {
        const next = { ...prev };
        for (const c of data.operators) {
          for (const nf of c.nfs) {
            const k = nfKey(c.id, nf.name);
            const seeded = draftFromNf(nf);
            if (!next[k]) {
              next[k] = seeded;
              continue;
            }
            // Keep dirty fields; refresh unchanged fields from live.
            const cur = asDraft(next[k]);
            const merged = { ...seeded };
            for (const key of DRAFT_KEYS) {
              if ((cur[key] ?? "").trim() && (cur[key] ?? "").trim() !== (seeded[key] ?? "").trim()) {
                merged[key] = cur[key];
              }
            }
            next[k] = merged;
          }
        }
        return next;
      });
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }, []);

  useEffect(() => {
    void refresh();
    const id = window.setInterval(() => void refresh(), 5000);
    return () => window.clearInterval(id);
  }, [refresh]);

  useEffect(() => {
    const id = window.setInterval(() => setTick((n) => n + 1), 1000);
    return () => window.clearInterval(id);
  }, []);

  const applyResources = async (
    c: OperatorOut,
    nfName: string,
    keys?: (keyof ResDraft)[],
  ) => {
    const k = nfKey(c.id, nfName);
    const nf = c.nfs.find((n) => n.name === nfName);
    if (!nf) {
      setApplyError(`NF not found: ${nfName}`);
      return;
    }
    const full = asDraft(drafts[k]);
    const useKeys = keys ?? DRAFT_KEYS;
    const body = rowChangedBody(full, nf, useKeys);
    if (Object.keys(body).length === 0) {
      setApplyError("No changes in this row — edit a value first.");
      return;
    }
    const applyKey = keys ? `${k}::${keys.join("+")}` : k;
    setApplying(applyKey);
    setApplyError(null);
    try {
      await api.setOperatorResources(c.id, nfName, body);
      await refresh();
    } catch (e) {
      setApplyError(e instanceof Error ? e.message : String(e));
    } finally {
      setApplying(null);
    }
  };

  const forget = async (id: string) => {
    try {
      await api.deleteOperator(id);
      await refresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  };

  const operators = list?.operators ?? [];
  const onlineCount = useMemo(
    () => operators.filter((o) => o.online).length,
    [operators],
  );

  return (
    <div className="page-layout">
      <div className="page">
        <Card className="tier" glow>
          <div className="op-intro-head">
            <div className="op-intro-title">
              <SectionLabel kicker="compute">Operator agents</SectionLabel>
              <span
                className="help-q"
                title={`Partial row updates. CPU applies in place; RAM/GPU/VRAM are logged hooks for now. Pane removed after ${list?.stale_after_sec ?? 30}s without a beat; reconnect brings it back.`}
                role="img"
                aria-label="About operator agents"
              >
                ?
              </span>
            </div>
          </div>
          {error && <p className="hint error">{error}</p>}
          {operators.length === 0 && !error ? (
            <div className="op-empty-hint" role="status">
              <span className="op-empty-hint-label">Hint</span>
              <p>
                Waiting for an agent. A new one will appear here when it
                connects.
              </p>
              <p>
                The pane is empty after an API reload until{" "}
                <code>oai-ran-operator</code> reconnects (usually a few
                seconds). If it stays empty, check{" "}
                <code>INA_INFRA_API_URL</code> on that deployment.
              </p>
            </div>
          ) : (
            <div className="op-list">
              {operators.map((c) => (
                <OperatorCard
                  key={c.id}
                  op={c}
                  drafts={drafts}
                  setDrafts={setDrafts}
                  applying={applying}
                  onApply={applyResources}
                  onForget={forget}
                />
              ))}
            </div>
          )}
        </Card>
      </div>

      <OperatorsStatusRail
        operators={operators}
        onlineCount={onlineCount}
        staleAfterSec={list?.stale_after_sec ?? 30}
        busy={busy}
        error={error}
        applyError={applyError}
        onRefresh={() => void refresh()}
        onClearError={() => {
          setError(null);
          setApplyError(null);
        }}
      />
    </div>
  );
}

function OperatorsStatusRail({
  operators,
  onlineCount,
  staleAfterSec,
  busy,
  error,
  applyError,
  onRefresh,
  onClearError,
}: {
  operators: OperatorOut[];
  onlineCount: number;
  staleAfterSec: number;
  busy: boolean;
  error: string | null;
  applyError: string | null;
  onRefresh: () => void;
  onClearError: () => void;
}) {
  const errText = applyError || error;

  return (
    <Card className="status-rail">
      <div className="panel-head">
        <SectionLabel kicker="live">Status</SectionLabel>
        <div className="actions">
          <button type="button" disabled={busy} onClick={onRefresh}>
            {busy ? "…" : "Refresh"}
          </button>
        </div>
      </div>

      {errText && (
        <div className="op-status-error" role="alert">
          <div className="op-status-error-head">
            <strong>Error</strong>
            <button type="button" className="btn" onClick={onClearError}>
              Dismiss
            </button>
          </div>
          <pre className="op-status-error-body">{errText}</pre>
        </div>
      )}

      <div className="status-block">
        <h3>Agents</h3>
        <ul className="status-rows">
          <li>
            <span className="status-label">Online</span>
            <span className={"status-pill " + (onlineCount ? "ok" : "err")}>
              {onlineCount}/{operators.length}
            </span>
          </li>
          <li>
            <span className="status-label">Drop after</span>
            <span className="status-pill muted">{staleAfterSec}s</span>
          </li>
        </ul>
      </div>

      {operators.length === 0 ? (
        <div className="status-block">
          <h3>Operator</h3>
          <ul className="status-rows">
            <li>
              <span className="status-label">Agent</span>
              <span className="status-pill muted">none</span>
            </li>
          </ul>
        </div>
      ) : (
        operators.map((op) => {
          const seen = formatLastSeen(op.last_seen);
          return (
            <div key={op.id} className="status-block">
              <h3>{op.id}</h3>
              <ul className="status-rows">
                <li>
                  <span className="status-label">State</span>
                  <span className={"status-pill " + (op.online ? "ok" : "err")}>
                    {op.online ? "Online" : "Offline"}
                  </span>
                </li>
                <li>
                  <span className="status-label">Last seen</span>
                  <span className="status-pill muted" title={seen.absolute}>
                    {seen.relative}
                  </span>
                </li>
                <li>
                  <span className="status-label">Cluster</span>
                  <span className="status-pill muted">{op.cluster || "—"}</span>
                </li>
                <li>
                  <span className="status-label">Namespace</span>
                  <span className="status-pill muted">{op.namespace || "—"}</span>
                </li>
                <li>
                  <span className="status-label">Agent</span>
                  <span className="status-pill muted">
                    {op.version ? `v${op.version}` : "—"}
                  </span>
                </li>
                {op.message?.trim() ? (
                  <li>
                    <span className="status-label">Message</span>
                    <span
                      className="status-pill muted"
                      title={op.message}
                    >
                      {op.message}
                    </span>
                  </li>
                ) : null}
              </ul>
              {op.nfs.length > 0 && (
                <div className="status-cluster">
                  <div className="status-cluster-head">
                    <strong>NFs</strong>
                    <span className="status-pill muted">{op.nfs.length}</span>
                  </div>
                  <ul className="status-rows">
                    {op.nfs.map((nf) => {
                      const tone = applyTone(nf.apply_status);
                      const ready =
                        nf.replicas > 0
                          ? `${nf.ready_replicas}/${nf.replicas}`
                          : String(nf.ready_replicas);
                      return (
                        <li key={nf.name}>
                          <span className="status-label" title={nf.name}>
                            {kindLabel(nf.kind)} · {ready}
                          </span>
                          <span
                            className={"status-pill " + tone}
                            title={nf.apply_message || nf.name}
                          >
                            {nf.apply_status || "—"}
                            {nf.desired?.generation
                              ? ` · g${nf.desired.generation}`
                              : ""}
                          </span>
                        </li>
                      );
                    })}
                  </ul>
                </div>
              )}
            </div>
          );
        })
      )}
    </Card>
  );
}

function OperatorCard({
  op,
  drafts,
  setDrafts,
  applying,
  onApply,
  onForget,
}: {
  op: OperatorOut;
  drafts: DraftMap;
  setDrafts: Dispatch<SetStateAction<DraftMap>>;
  applying: string | null;
  onApply: (c: OperatorOut, nf: string, keys?: (keyof ResDraft)[]) => void;
  onForget: (id: string) => void;
}) {
  return (
    <div className={"op-card" + (op.online ? "" : " op-card-offline")}>
      <div className="op-card-head">
        <div className="op-card-title-row">
          <h3 className="op-card-title">
            <span className={"status-dot " + (op.online ? "dot-ok" : "dot-bad")} />
            {op.id}
          </h3>
        </div>
        <div className="op-card-head-right">
          <button type="button" className="btn" onClick={() => onForget(op.id)}>
            Forget
          </button>
        </div>
      </div>

      {op.nfs.length === 0 ? (
        <p className="muted" style={{ marginBottom: 0 }}>
          No NFs reported yet.
        </p>
      ) : (
        <div className="op-nf-alloc-list">
          {op.nfs.map((nf) => (
            <NfAllocator
              key={nf.name}
              op={op}
              nf={nf}
              drafts={drafts}
              setDrafts={setDrafts}
              applying={applying}
              onApply={onApply}
            />
          ))}
        </div>
      )}
    </div>
  );
}

function QtyEdit({
  value,
  placeholder,
  current,
  currentHint,
  onChange,
}: {
  value: string;
  placeholder: string;
  current: string;
  currentHint?: string;
  onChange: (v: string) => void;
}) {
  const dirty =
    (value || "").trim() !== "" &&
    (value || "").trim() !== (current === "—" ? "" : current);
  return (
    <div className={"op-qty-edit" + (dirty ? " is-dirty" : "")}>
      <input
        className="op-res-input"
        value={value}
        placeholder={placeholder}
        onChange={(e) => onChange(e.target.value)}
        aria-label="edit value"
      />
      <span className="op-qty-sep" aria-hidden>
        /
      </span>
      <span className="op-qty-current mono" title={currentHint || "current (live)"}>
        {current}
      </span>
    </div>
  );
}

function NfAllocator({
  op,
  nf,
  drafts,
  setDrafts,
  applying,
  onApply,
}: {
  op: OperatorOut;
  nf: OperatorNfOut;
  drafts: DraftMap;
  setDrafts: Dispatch<SetStateAction<DraftMap>>;
  applying: string | null;
  onApply: (c: OperatorOut, nf: string, keys?: (keyof ResDraft)[]) => void;
}) {
  const k = nfKey(op.id, nf.name);
  const d = asDraft(drafts[k] ?? draftFromNf(nf));
  const ready =
    nf.replicas > 0
      ? `${nf.ready_replicas}/${nf.replicas}`
      : String(nf.ready_replicas);

  const setField = (key: keyof ResDraft, value: string) => {
    setDrafts((prev) => ({
      ...prev,
      [k]: { ...asDraft(prev[k] ?? draftFromNf(nf)), [key]: value },
    }));
  };

  return (
    <div className="op-nf-alloc">
      <div className="op-nf-alloc-head">
        <div className="op-nf-alloc-left">
          <div className="op-nf-name">
            <code>{nf.name}</code>
            <span className="op-nf-ready muted">{ready} ready</span>
          </div>
          <span className="op-kind-chip">{kindLabel(nf.kind)}</span>
        </div>
      </div>

      <div className="table-wrap">
        <table className="slice-table op-res-table">
          <thead>
            <tr>
              <th>Resource</th>
              <th>
                Limit{" "}
                <span className="op-col-hint">edit / current</span>
              </th>
              <th>
                Request{" "}
                <span className="op-col-hint">edit / current</span>
              </th>
              <th />
            </tr>
          </thead>
          <tbody>
            {RES_ROWS.filter((row) =>
              (nf.controllable || []).includes(row.kind),
            ).map((row) => {
              const lim = currentQty(
                row.reportedLim(nf),
                nf.desired ? row.desiredLim(nf.desired) : null,
              );
              const req = currentQty(
                row.reportedReq(nf),
                nf.desired ? row.desiredReq(nf.desired) : null,
              );
              const rowKeys: (keyof ResDraft)[] = [row.limKey, row.reqKey];
              const rowBody = rowChangedBody(d, nf, rowKeys);
              const rowHasEdits = Object.keys(rowBody).length > 0;
              const rowApplyKey = `${k}::${rowKeys.join("+")}`;
              const rowBusy = applying === rowApplyKey;

              return (
                <tr key={row.kind}>
                  <td>
                    <span className="op-res-label">{row.label}</span>
                  </td>
                  <td>
                    <QtyEdit
                      value={d[row.limKey]}
                      placeholder={row.limPh}
                      current={lim.display}
                      currentHint={lim.hint}
                      onChange={(v) => setField(row.limKey, v)}
                    />
                  </td>
                  <td>
                    <QtyEdit
                      value={d[row.reqKey]}
                      placeholder={row.reqPh}
                      current={req.display}
                      currentHint={req.hint}
                      onChange={(v) => setField(row.reqKey, v)}
                    />
                  </td>
                  <td>
                    <button
                      type="button"
                      className="btn btn-primary"
                      disabled={rowBusy || !op.online || !rowHasEdits}
                      onClick={() => onApply(op, nf.name, rowKeys)}
                      title={
                        rowHasEdits
                          ? `Apply ${row.label} changes only`
                          : `Edit ${row.label} first`
                      }
                    >
                      {rowBusy ? "…" : "Apply"}
                    </button>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}
