import { useEffect, useState, type CSSProperties, type ReactNode } from "react";
import { AccentKey, buildColors, fmtSiteNow } from "../../lib/theme";

const THEME_DARK_KEY = "cctv.theme.dark";

function readStoredDark(fallback = true): boolean {
  try {
    const raw = localStorage.getItem(THEME_DARK_KEY);
    if (raw === "1" || raw === "true") return true;
    if (raw === "0" || raw === "false") return false;
  } catch {
    /* private mode / blocked storage */
  }
  return fallback;
}

function writeStoredDark(dark: boolean) {
  try {
    localStorage.setItem(THEME_DARK_KEY, dark ? "1" : "0");
  } catch {
    /* ignore */
  }
}

export type NavTab = {
  id: string;
  label: string;
  icon: ReactNode;
  disabled?: boolean;
};

type Brand = {
  product: string;
  title: string;
  shortName: string;
  logo: string;
  logoPng?: string;
};

function SiteClock({ tz }: { tz: string }) {
  const [now, setNow] = useState(() => fmtSiteNow(tz));
  useEffect(() => {
    const id = window.setInterval(() => setNow(fmtSiteNow(tz)), 1000);
    return () => window.clearInterval(id);
  }, [tz]);
  return (
    <span className="clock" title={tz}>
      <span className="status-dot dot-ok" />
      {now}
    </span>
  );
}

function DarkToggle({ dark, onToggle }: { dark: boolean; onToggle: () => void }) {
  return (
    <button
      type="button"
      className="icon-btn icon-btn-sq"
      onClick={onToggle}
      title={dark ? "Switch to light mode" : "Switch to dark mode"}
      aria-label={dark ? "Switch to light mode" : "Switch to dark mode"}
    >
      {dark ? (
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true">
          <circle cx="12" cy="12" r="4" />
          <path d="M12 2v2M12 20v2M4.93 4.93l1.41 1.41M17.66 17.66l1.41 1.41M2 12h2M20 12h2M4.93 19.07l1.41-1.41M17.66 6.34l1.41-1.41" />
        </svg>
      ) : (
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true">
          <path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z" />
        </svg>
      )}
    </button>
  );
}

function Rail({
  tabs,
  tab,
  setTab,
  brand,
}: {
  tabs: NavTab[];
  tab: string;
  setTab: (id: string) => void;
  brand: Brand;
}) {
  return (
    <aside className="rail">
      <div className="rail-brand" title={brand.title}>
        <picture>
          {brand.logoPng && <source srcSet={brand.logoPng} type="image/png" />}
          <img className="rail-logo" src={brand.logo} alt={brand.product} />
        </picture>
      </div>

      <nav className="rail-nav" aria-label="Main navigation">
        {tabs.map(({ id, label, icon, disabled }) => (
          <button
            key={id}
            type="button"
            className={
              "rail-tab" +
              (tab === id ? " rail-tab-active" : "") +
              (disabled ? " rail-tab-disabled" : "")
            }
            onClick={() => !disabled && setTab(id)}
            disabled={disabled}
            title={disabled ? "Coming later" : label}
            aria-current={tab === id ? "page" : undefined}
          >
            <span className="rail-tab-icon">{icon}</span>
            <span className="rail-tab-label">{label}</span>
          </button>
        ))}
      </nav>

      <div className="rail-foot">
        <div className="rail-status" title={`${brand.shortName} · Healthy`}>
          <span className="rail-status-dot" />
          <span className="rail-status-text">{brand.shortName}</span>
        </div>
      </div>
    </aside>
  );
}

export default function AppShell({
  tabs,
  tab,
  setTab,
  crumb,
  siteLabel = "INA · CCTV Video Vision AI",
  clockTz = "Asia/Singapore",
  brand,
  children,
  topbarExtra,
}: {
  tabs: NavTab[];
  tab: string;
  setTab: (id: string) => void;
  crumb: string;
  siteLabel?: string;
  clockTz?: string;
  brand: Brand;
  children: ReactNode;
  topbarExtra?: ReactNode;
}) {
  const [dark, setDark] = useState(() => readStoredDark(true));
  const [accent] = useState<AccentKey>("Energy green");
  const colors = buildColors(dark, accent);

  useEffect(() => {
    writeStoredDark(dark);
  }, [dark]);

  const appClass = `app ${dark ? "theme-dark" : "theme-light"} density-comfy`;
  const appVars = {
    "--accent": colors.accent,
    "--accent2": colors.accent2,
    "--accent-glow": colors.accentGlow,
    "--bad": colors.bad,
  } as CSSProperties;

  return (
    <div className={appClass} style={appVars}>
      <Rail tabs={tabs} tab={tab} setTab={setTab} brand={brand} />
      <div className="app-body">
        <div className="main-col">
          <header className="topbar">
            {crumb ? <div className="crumb">{crumb}</div> : null}
            {siteLabel ? <span className="site-badge">{siteLabel}</span> : null}
            <div className="topbar-right">
              {topbarExtra}
              <SiteClock tz={clockTz} />
              <DarkToggle dark={dark} onToggle={() => setDark((d) => !d)} />
            </div>
          </header>
          <main className="dash">{children}</main>
        </div>
      </div>
    </div>
  );
}
