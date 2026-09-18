import React, { useEffect, useState } from "react";

/**
 * Renders user avatar including animated GIF when the user allows motion.
 * Falls back to initials on error, missing image, or prefers-reduced-motion + gif.
 */
export default function ProfileAvatar({
  src,
  name = "?",
  size = 72,
  className = "",
  version = 0,
}) {
  const [failed, setFailed] = useState(false);
  const [reduceMotion, setReduceMotion] = useState(false);

  useEffect(() => {
    if (typeof window === "undefined" || !window.matchMedia) return;
    const mq = window.matchMedia("(prefers-reduced-motion: reduce)");
    const apply = () => setReduceMotion(!!mq.matches);
    apply();
    mq.addEventListener?.("change", apply);
    return () => mq.removeEventListener?.("change", apply);
  }, []);

  useEffect(() => {
    setFailed(false);
  }, [src, version]);

  const initials = String(name || "?")
    .split(/\s+/)
    .map((p) => p[0])
    .join("")
    .slice(0, 2)
    .toUpperCase() || "?";

  const isGif = /\.gif(?:$|\?)/i.test(src || "") || /avatar\.gif/i.test(src || "");
  const hideAnim = reduceMotion && isGif;

  if (!src || failed || hideAnim) {
    return (
      <div
        className={`sp-profile-avatar sp-profile-avatar--fallback ${className}`}
        style={{ width: size, height: size, fontSize: Math.max(12, size * 0.32) }}
        aria-label={name}
        title={hideAnim ? "Animation paused (reduced motion)" : name}
      >
        {initials}
      </div>
    );
  }

  const url = src.includes("?") ? `${src}&v=${version}` : `${src}?v=${version}`;
  return (
    <img
      className={`sp-profile-avatar ${className}`}
      src={url}
      alt={name}
      width={size}
      height={size}
      style={{ width: size, height: size }}
      onError={() => setFailed(true)}
    />
  );
}
