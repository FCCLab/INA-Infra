import { useState } from "react";
import type { OaiRegistryStatus } from "../api/client";
import Card from "./ui/Card";
import SectionLabel from "./ui/SectionLabel";
import FieldHelp from "./FieldHelp";

interface Props {
  value?: Record<string, string>;
  registryStatus: OaiRegistryStatus | null;
  loading?: boolean;
  onRefresh: () => void;
  onChange: (val: Record<string, string>) => void;
  disabled?: boolean;
  embedded?: boolean;
}

const COMPONENT_KEYS = [
  { key: "cucp", label: "OAI CU-CP", desc: "Centralized Unit Control Plane" },
  { key: "du", label: "OAI DU", desc: "Distributed Unit (F1/RF)" },
  { key: "cuup", label: "OAI CU-UP", desc: "Centralized Unit User Plane (E1/F1-U/N3)" },
  { key: "ue", label: "OAI UE Sim", desc: "Simulated NR User Equipment" },
  { key: "flexric", label: "OAI FlexRIC", desc: "Near-RT RIC (E2)" },
  { key: "xapp", label: "NWS xApp", desc: "PRB Slicing xApp" },
  { key: "smf", label: "OAI SMF", desc: "Session Management Function (5GC)" },
];

export default function OaiImagesForm({
  value = {},
  registryStatus,
  loading = false,
  onRefresh,
  onChange,
  disabled = false,
  embedded = false,
}: Props) {
  const [showImages, setShowImages] = useState(true);
  const [customMode, setCustomMode] = useState<boolean>(() => {
    return Object.keys(value || {}).some(
      (k) => k !== "mode" && Boolean(value[k]) && value[k] !== "latest" && value[k] !== "auto",
    );
  });

  const getResolvedTag = (key: string): string => {
    const override = value?.[key];
    if (override && override !== "latest" && override !== "auto") {
      return override.includes(":") ? override.split(":").pop() || override : override;
    }
    return registryStatus?.components?.[key]?.latest_tag || "latest";
  };

  const handleComponentChange = (key: string, tag: string) => {
    const updated = { ...(value || {}) };
    if (!tag || tag === "latest" || tag === "auto") {
      delete updated[key];
    } else {
      updated[key] = tag;
    }
    onChange(updated);
  };

  const handleResetAllToLatest = () => {
    onChange({});
    setCustomMode(false);
  };

  const connected = Boolean(registryStatus?.connected);
  const kicker = connected
    ? `${registryStatus?.registry_host} · ${customMode ? "custom tags" : "latest"}`
    : "local defaults";

  const body = (
    <>
      <div className="panel-head">
        <SectionLabel kicker={kicker}>Container Images</SectionLabel>
        <div className="actions" style={{ flexWrap: "wrap", gap: 8 }}>
          <span
            className={`status-pill ${connected ? "status-pill-ready" : "status-pill-warn"}`}
            style={{
              fontSize: 11,
              padding: "2px 8px",
              display: "inline-flex",
              alignItems: "center",
              gap: 5,
              whiteSpace: "nowrap",
            }}
          >
            <span
              style={{
                width: 6,
                height: 6,
                borderRadius: "50%",
                backgroundColor: connected ? "#22c55e" : "#f59e0b",
              }}
            />
            {connected ? "Registry" : "Offline"}
          </span>
          <button
            type="button"
            disabled={loading || disabled}
            onClick={onRefresh}
            title="Refresh tags from Docker registry"
          >
            {loading ? "Refreshing…" : "Refresh"}
          </button>
          {showImages && (
            <div
              style={{
                display: "inline-flex",
                background: "rgba(15, 23, 42, 0.6)",
                padding: 2,
                borderRadius: 16,
                border: "1px solid rgba(255, 255, 255, 0.1)",
              }}
            >
              <button
                type="button"
                style={{
                  padding: "3px 10px",
                  fontSize: 11,
                  borderRadius: 14,
                  border: "none",
                  background: !customMode ? "rgba(0, 194, 212, 0.25)" : "transparent",
                  color: !customMode ? "#00C2D4" : "#94a3b8",
                  cursor: "pointer",
                  fontWeight: !customMode ? 600 : 400,
                }}
                onClick={() => handleResetAllToLatest()}
              >
                Latest (Auto)
              </button>
              <button
                type="button"
                style={{
                  padding: "3px 10px",
                  fontSize: 11,
                  borderRadius: 14,
                  border: "none",
                  background: customMode ? "rgba(192, 132, 252, 0.25)" : "transparent",
                  color: customMode ? "#c084fc" : "#94a3b8",
                  cursor: "pointer",
                  fontWeight: customMode ? 600 : 400,
                }}
                onClick={() => setCustomMode(true)}
              >
                Custom Overrides
              </button>
            </div>
          )}
          <button type="button" onClick={() => setShowImages((v) => !v)}>
            {showImages ? "Hide" : "Show"}
          </button>
        </div>
      </div>

      {showImages ? (
        <>
          <p className="hint">
            Slice config: RAN and 5GC container tags (CU-CP, DU, CU-UP, UE, FlexRIC, xApp, SMF). Empty / Latest uses the lab registry. Saved with the profile and applied on GitOps Deploy.
          </p>

          {!customMode ? (
            <div
              className="app-config-grid"
              style={{
                display: "grid",
                gridTemplateColumns: "repeat(3, minmax(0, 1fr))",
                gap: 8,
              }}
            >
              {COMPONENT_KEYS.map((c) => {
                const tag = getResolvedTag(c.key);
                return (
                  <div
                    key={c.key}
                    style={{
                      display: "flex",
                      flexDirection: "column",
                      padding: "6px 10px",
                      background: "rgba(15, 23, 42, 0.5)",
                      border: "1px solid rgba(255, 255, 255, 0.08)",
                      borderRadius: 6,
                    }}
                  >
                    <span style={{ fontSize: 11, color: "#94a3b8", fontWeight: 500 }}>
                      {c.label}
                    </span>
                    <span
                      style={{
                        fontSize: 12,
                        fontFamily: "monospace",
                        color: "#38bdf8",
                        fontWeight: 600,
                        marginTop: 2,
                        overflow: "hidden",
                        textOverflow: "ellipsis",
                        whiteSpace: "nowrap",
                      }}
                      title={tag}
                    >
                      {tag}
                    </span>
                  </div>
                );
              })}
            </div>
          ) : (
            <>
              <div
                className="app-config-grid"
                style={{
                  display: "grid",
                  gridTemplateColumns: "repeat(3, minmax(0, 1fr))",
                  gap: 10,
                }}
              >
                {COMPONENT_KEYS.map((c) => {
                  const compInfo = registryStatus?.components?.[c.key];
                  const availableTags = compInfo?.available_tags || [];
                  const currentVal = value?.[c.key] || "";

                  return (
                    <FieldHelp
                      key={c.key}
                      label={c.label}
                      help={`${c.desc}. Available in ${compInfo?.repo || c.key}.`}
                    >
                      <select
                        value={currentVal || "latest"}
                        disabled={disabled}
                        onChange={(e) => handleComponentChange(c.key, e.target.value)}
                        style={{ width: "100%", fontFamily: "monospace", fontSize: 12 }}
                      >
                        <option value="latest">
                          Latest (Auto: {compInfo?.latest_tag || "default"})
                        </option>
                        {availableTags.map((t) => (
                          <option key={t} value={t}>
                            {t} {t === compInfo?.latest_tag ? "★ Latest" : ""}
                          </option>
                        ))}
                      </select>
                    </FieldHelp>
                  );
                })}
              </div>
              <div style={{ marginTop: 8, display: "flex", justifyContent: "flex-end" }}>
                <button
                  type="button"
                  style={{ fontSize: 11, borderRadius: 14, padding: "3px 10px" }}
                  onClick={handleResetAllToLatest}
                >
                  Reset all to Latest
                </button>
              </div>
            </>
          )}
        </>
      ) : (
        <p className="hint">Collapsed — Show to view or override RAN / 5GC container tags.</p>
      )}
    </>
  );

  if (embedded) {
    return <div className="slice-config-section">{body}</div>;
  }
  return <Card className="tier">{body}</Card>;
}
