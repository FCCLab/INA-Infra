import { SliceResultOut } from "../api/client";

const SITES = ["Edge", "Regional", "Central"] as const;
type Site = (typeof SITES)[number];

const SITES_CONFIG: Record<Site, { tagClass: string; segClass: string }> = {
  Edge: { tagClass: "tag-cluster-edge", segClass: "place-seg-edge" },
  Regional: { tagClass: "tag-cluster-regional", segClass: "place-seg-regional" },
  Central: { tagClass: "tag-cluster-central", segClass: "place-seg-central" },
};

const ROLES = [
  { key: "cu" as const, label: "CU-UP", tag: "tag-accent" },
  { key: "upf" as const, label: "UPF", tag: "tag-muted" },
  { key: "app" as const, label: "APP", tag: "tag-app" },
];

type Props = { slices: SliceResultOut[] };

function rolesAtSite(s: SliceResultOut, site: Site): string[] {
  const out: string[] = [];
  for (const role of ROLES) {
    if (s.placement[role.key] === site) out.push(role.label);
  }
  return out;
}

function roleTagClass(label: string): string {
  return ROLES.find((r) => r.label === label)?.tag || "tag-muted";
}

/** One horizontal bar per slice: Edge | Regional | Central from PL JSON. */
export default function Topology({ slices }: Props) {
  const sorted = [...slices].sort((a, b) => a.id - b.id);

  return (
    <div className="place-matrix" aria-label="Slice placement by site">
      <div className="place-matrix-head">
        <span className="place-legend" aria-label="NF roles">
          {ROLES.map((r) => (
            <span key={r.key} className={`tag ${r.tag}`}>
              {r.label}
            </span>
          ))}
        </span>
      </div>

      <div className="place-site-axis" aria-hidden="true">
        <span className="place-site-axis-spacer" />
        {SITES.map((site) => (
          <span key={site} className="place-site-axis-label">
            <span
              className={`tag ${SITES_CONFIG[site].tagClass}`}
              style={{ fontSize: 11, padding: "3px 10px", fontWeight: 700 }}
            >
              {site}
            </span>
          </span>
        ))}
      </div>

      <div className="place-bars">
        {sorted.map((s) => (
          <article key={s.id} className="place-bar" aria-label={`Slice ${s.id}`}>
            <header className="place-bar-label">
              <span className="place-bar-accent" />
              <div className="place-bar-title">
                <span className="mono">Slice {s.id}</span>
                {s.slice_type ? (
                  <span className="place-slice-type">{s.slice_type}</span>
                ) : null}
              </div>
            </header>

            <div className="place-bar-track">
              {SITES.map((site) => {
                const roles = rolesAtSite(s, site);
                const hit = roles.length > 0;
                const siteCfg = SITES_CONFIG[site];
                return (
                  <div
                    key={site}
                    className={
                      "place-bar-seg" +
                      (hit ? ` place-bar-seg-hit ${siteCfg.segClass}` : "")
                    }
                  >
                    <span className="place-bar-seg-name">{site}</span>
                    {hit ? (
                      <div className="place-chips">
                        {roles.map((label) => (
                          <span
                            key={label}
                            className={`tag ${roleTagClass(label)}`}
                          >
                            {label}
                          </span>
                        ))}
                      </div>
                    ) : (
                      <span className="place-dash">—</span>
                    )}
                  </div>
                );
              })}
            </div>
          </article>
        ))}
      </div>
    </div>
  );
}
