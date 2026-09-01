/**
 * DevOS Theme Customizer
 * Full theme customization UI with live preview.
 * Controls: accent color, border radius, glow, blur, density, typography, animation speed.
 */
import React, { useState } from "react";
import { Palette, RotateCcw, Download, Upload, Check } from "lucide-react";
import { useThemeStore } from "../../store/themeStore";
import { themeRegistry } from "../../theme/themeRegistry";

const PRESET_ACCENTS = [
  "#58a6ff", "#3fb950", "#f85149", "#d29922", "#bc8cff",
  "#fb923c", "#22d3ee", "#f472b6", "#a855f7", "#00ff7f",
  "#60a5fa", "#34d399", "#fbbf24", "#a78bfa", "#f87171",
];

export default function ThemeCustomizer() {
  const {
    activeTheme, customization, reducedMotion, themes,
    setTheme, setCustomization, resetCustomization, setReducedMotion,
  } = useThemeStore();
  const [tab, setTab] = useState("themes");

  const handleExport = () => {
    const data = JSON.stringify({ theme: activeTheme, customization }, null, 2);
    const blob = new Blob([data], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `devos-theme-${activeTheme}.json`;
    a.click();
    URL.revokeObjectURL(url);
  };

  const handleImport = (e) => {
    const file = e.target.files && e.target.files[0];
    if (!file) return;
    const reader = new FileReader();
    reader.onload = (ev) => {
      try {
        const data = JSON.parse(ev.target.result);
        if (data.theme) setTheme(data.theme);
        if (data.customization) setCustomization(data.customization);
      } catch (err) {
        alert("Invalid theme file");
      }
    };
    reader.readAsText(file);
  };

  return (
    <div className="devos-theme-customizer">
      {/* Header */}
      <div className="devos-customizer-header">
        <div className="devos-customizer-title">
          <Palette size={16} />
          <span>Theme Studio</span>
        </div>
        <div className="devos-customizer-actions">
          <button onClick={handleExport} title="Export theme" className="devos-customizer-btn">
            <Download size={13} />
          </button>
          <label title="Import theme" className="devos-customizer-btn" style={{ cursor: "pointer" }}>
            <Upload size={13} />
            <input type="file" accept=".json" onChange={handleImport} style={{ display: "none" }} />
          </label>
          <button onClick={resetCustomization} title="Reset to defaults" className="devos-customizer-btn">
            <RotateCcw size={13} />
          </button>
        </div>
      </div>

      {/* Tabs */}
      <div className="devos-customizer-tabs">
        <button onClick={() => setTab("themes")} className={tab === "themes" ? "active" : ""}>Themes</button>
        <button onClick={() => setTab("customize")} className={tab === "customize" ? "active" : ""}>Customize</button>
        <button onClick={() => setTab("motion")} className={tab === "motion" ? "active" : ""}>Motion</button>
      </div>

      {/* Body */}
      <div className="devos-customizer-body">
        {/* Themes Tab */}
        {tab === "themes" && (
          <div className="devos-theme-grid">
            {themes.map((theme) => {
              const td = themeRegistry.get(theme.id);
              return (
                <button
                  key={theme.id}
                  onClick={() => setTheme(theme.id)}
                  className={"devos-theme-card" + (theme.id === activeTheme ? " active" : "")}
                  style={{
                    background: (td && td.colors["bg-1"]) || "#161b22",
                    borderColor: (td && td.colors["accent"]) || "#58a6ff",
                  }}
                >
                  <div className="devos-theme-preview">
                    <div className="devos-theme-swatch" style={{ background: td && td.colors["accent"] }} />
                    <div className="devos-theme-swatch" style={{ background: td && td.colors["green"] }} />
                    <div className="devos-theme-swatch" style={{ background: td && td.colors["red"] }} />
                    <div className="devos-theme-swatch" style={{ background: td && td.colors["yellow"] }} />
                    <div className="devos-theme-swatch" style={{ background: td && td.colors["purple"] }} />
                  </div>
                  <span className="devos-theme-card-name">{theme.name}</span>
                  <span className="devos-theme-card-cat">{theme.isDark ? "Dark" : "Light"}</span>
                  {theme.id === activeTheme && (
                    <Check size={14} className="devos-theme-check" />
                  )}
                </button>
              );
            })}
          </div>
        )}

        {/* Customize Tab */}
        {tab === "customize" && (
          <div className="devos-customize-controls">
            {/* Accent Color */}
            <div className="devos-control-group">
              <label className="devos-control-label">Accent Color</label>
              <div className="devos-accent-grid">
                {PRESET_ACCENTS.map((color) => (
                  <button
                    key={color}
                    onClick={() => setCustomization({ accent: color })}
                    className={"devos-accent-swatch" + (customization.accent === color ? " active" : "")}
                    style={{ background: color }}
                    aria-label={"Set accent to " + color}
                  />
                ))}
              </div>
              <input
                type="color"
                value={customization.accent}
                onChange={(e) => setCustomization({ accent: e.target.value })}
                className="devos-accent-picker"
                aria-label="Custom accent color"
              />
            </div>

            {/* Border Radius */}
            <div className="devos-control-group">
              <label className="devos-control-label">
                Border Radius
                <span className="devos-control-value">{customization.borderRadius}px</span>
              </label>
              <input
                type="range"
                min="0"
                max="20"
                value={customization.borderRadius}
                onChange={(e) => setCustomization({ borderRadius: parseInt(e.target.value, 10) })}
                className="devos-slider"
              />
            </div>

            {/* Glow Intensity */}
            <div className="devos-control-group">
              <label className="devos-control-label">
                Glow Intensity
                <span className="devos-control-value">{Math.round(customization.glowIntensity * 100)}%</span>
              </label>
              <input
                type="range"
                min="0"
                max="1"
                step="0.1"
                value={customization.glowIntensity}
                onChange={(e) => setCustomization({ glowIntensity: parseFloat(e.target.value) })}
                className="devos-slider"
              />
            </div>

            {/* Blur */}
            <div className="devos-control-group">
              <label className="devos-control-label">
                Background Blur
                <span className="devos-control-value">{customization.blur}px</span>
              </label>
              <input
                type="range"
                min="0"
                max="30"
                value={customization.blur}
                onChange={(e) => setCustomization({ blur: parseInt(e.target.value, 10) })}
                className="devos-slider"
              />
            </div>

            {/* Density */}
            <div className="devos-control-group">
              <label className="devos-control-label">Density</label>
              <div className="devos-segmented">
                {["compact", "comfortable", "spacious"].map((d) => (
                  <button
                    key={d}
                    onClick={() => setCustomization({ density: d })}
                    className={customization.density === d ? "active" : ""}
                  >
                    {d.charAt(0).toUpperCase() + d.slice(1)}
                  </button>
                ))}
              </div>
            </div>

            {/* Typography Scale */}
            <div className="devos-control-group">
              <label className="devos-control-label">
                Typography Scale
                <span className="devos-control-value">{customization.typographyScale}x</span>
              </label>
              <input
                type="range"
                min="0.85"
                max="1.25"
                step="0.05"
                value={customization.typographyScale}
                onChange={(e) => setCustomization({ typographyScale: parseFloat(e.target.value) })}
                className="devos-slider"
              />
            </div>

            {/* Border Width */}
            <div className="devos-control-group">
              <label className="devos-control-label">
                Border Width
                <span className="devos-control-value">{customization.borderWidth}px</span>
              </label>
              <input
                type="range"
                min="0"
                max="3"
                value={customization.borderWidth}
                onChange={(e) => setCustomization({ borderWidth: parseInt(e.target.value, 10) })}
                className="devos-slider"
              />
            </div>
          </div>
        )}

        {/* Motion Tab */}
        {tab === "motion" && (
          <div className="devos-customize-controls">
            <div className="devos-control-group">
              <label className="devos-control-label">
                Animation Speed
                <span className="devos-control-value">{customization.animationSpeed}x</span>
              </label>
              <input
                type="range"
                min="0.25"
                max="2"
                step="0.25"
                value={customization.animationSpeed}
                onChange={(e) => setCustomization({ animationSpeed: parseFloat(e.target.value) })}
                className="devos-slider"
              />
              <p className="devos-control-hint">Controls how fast animations play across the interface.</p>
            </div>

            <div className="devos-control-group">
              <label className="devos-control-label">Reduced Motion</label>
              <button
                onClick={() => setReducedMotion(!reducedMotion)}
                className={"devos-toggle" + (reducedMotion ? " on" : "")}
                role="switch"
                aria-checked={reducedMotion}
                aria-label="Toggle reduced motion"
              >
                <span className="devos-toggle-knob" />
              </button>
              <p className="devos-control-hint">
                When enabled, all animations are disabled for accessibility.
              </p>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
