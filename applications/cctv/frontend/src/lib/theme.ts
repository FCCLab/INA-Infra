/** NeuroRAN accent palettes + color resolver (FIXED). */

export const ACCENTS = {
  "Energy green": {
    accent: "#16E6A0",
    accent2: "#00C2D4",
    light: { accent: "#2d8f5f", accent2: "#5cb85c" },
  },
  "Telco teal": { accent: "#15D6C6", accent2: "#2E8BFF" },
  "Signal blue": { accent: "#3E9BFF", accent2: "#7C5CFF" },
  Volt: { accent: "#9BE635", accent2: "#15D6C6" },
} as const;

export type AccentKey = keyof typeof ACCENTS;

export type ThemeColors = {
  accent: string;
  accent2: string;
  accentGlow: string;
  bad: string;
  text: string;
};

export function buildColors(dark: boolean, accentKey: AccentKey): ThemeColors {
  const base = ACCENTS[accentKey] || ACCENTS["Energy green"];
  const a =
    !dark && "light" in base && base.light
      ? base.light
      : { accent: base.accent, accent2: base.accent2 };
  return {
    accent: a.accent,
    accent2: a.accent2,
    accentGlow: dark ? a.accent + "88" : a.accent + "55",
    bad: dark ? "#FF5A6E" : "#E11D48",
    text: dark ? "#eef2f9" : "#1a2e1a",
  };
}

export function fmtSiteNow(tz = "Asia/Singapore", date = new Date()): string {
  const parts = new Intl.DateTimeFormat("en-GB", {
    timeZone: tz,
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  }).formatToParts(date);
  const pick = (type: Intl.DateTimeFormatPartTypes) =>
    parts.find((p) => p.type === type)?.value || "00";
  return `${pick("hour")}:${pick("minute")}:${pick("second")}`;
}
