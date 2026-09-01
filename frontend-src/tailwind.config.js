/** @type {import('tailwindcss').Config} */
module.exports = {
  content: ["./src/**/*.{js,jsx,ts,tsx}"],
  darkMode: "class",
  theme: {
    extend: {
      colors: {
        obsidian: {
          950: "#03060b",
          900: "#0a0f17",
          850: "#101626",
          800: "#151c2e",
          750: "#1b2335",
          700: "#222b3d",
          600: "#313b4f",
        },
        mint: {
          300: "#7fffd4",
          400: "#4ade80",
          500: "#22c55e",
          glow: "rgba(74, 222, 128, 0.45)",
        },
        danger: "#f85149",
        warning: "#f59e0b",
      },
      fontFamily: {
        mono: ["JetBrains Mono", "Fira Code", "Cascadia Code", "monospace"],
        ui: ["-apple-system", "BlinkMacSystemFont", "Segoe UI", "sans-serif"],
      },
      boxShadow: {
        glass: "0 8px 32px rgba(0, 0, 0, 0.35)",
        glow: "0 0 18px rgba(74, 222, 128, 0.35)",
      },
      backdropBlur: {
        panel: "16px",
      },
      animation: {
        pulse: "pulse 2s cubic-bezier(0.4, 0, 0.6, 1) infinite",
        "node-float": "nodeFloat 6s ease-in-out infinite",
      },
      keyframes: {
        pulse: {
          "0%, 100%": { opacity: 1 },
          "50%": { opacity: 0.5 },
        },
        nodeFloat: {
          "0%, 100%": { transform: "translateY(0)" },
          "50%": { transform: "translateY(-6px)" },
        },
      },
    },
  },
  plugins: [],
};
