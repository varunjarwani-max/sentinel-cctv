import type { Config } from "tailwindcss";

const config: Config = {
  content: [
    "./app/**/*.{js,ts,jsx,tsx,mdx}",
    "./components/**/*.{js,ts,jsx,tsx,mdx}",
  ],
  theme: {
    extend: {
      fontFamily: {
        sans: ["Inter", "ui-sans-serif", "system-ui", "sans-serif"],
        mono: ["\"JetBrains Mono\"", "ui-monospace", "SFMono-Regular", "monospace"],
      },
      colors: {
        noc: {
          bg: "#0A0D14",
          panel: "#111827",
          accent: "#3B82F6",
          critical: "#EF4444",
          warning: "#F59E0B",
          online: "#10B981",
          border: "#1F2937",
        },
      },
      keyframes: {
        "flash-border": {
          "0%, 100%": { boxShadow: "0 0 0 0 rgba(239, 68, 68, 0)" },
          "50%": { boxShadow: "0 0 0 3px rgba(239, 68, 68, 0.9)" },
        },
        "pulse-ring": {
          "0%": { transform: "scale(0.5)", opacity: "1" },
          "100%": { transform: "scale(2.5)", opacity: "0" },
        },
      },
      animation: {
        "flash-border": "flash-border 0.6s ease-in-out infinite",
        "pulse-ring": "pulse-ring 1.5s cubic-bezier(0.4, 0, 0.6, 1) infinite",
      },
    },
  },
  plugins: [],
};

export default config;
