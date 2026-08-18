import type { CSSProperties, ReactNode } from "react";

export default function Card({
  children,
  className,
  glow,
  style,
  title,
}: {
  children: ReactNode;
  className?: string;
  glow?: boolean;
  style?: CSSProperties;
  title?: string;
}) {
  return (
    <div
      className={
        "card" + (glow ? " card-glow" : "") + (className ? ` ${className}` : "")
      }
      style={style}
      title={title}
    >
      {children}
    </div>
  );
}
