import type { Config } from "tailwindcss";

const config: Config = {
  darkMode: "class",
  content: [
    "./pages/**/*.{js,ts,jsx,tsx,mdx}",
    "./components/**/*.{js,ts,jsx,tsx,mdx}",
    "./app/**/*.{js,ts,jsx,tsx,mdx}",
  ],
  theme: {
    extend: {
      fontFamily: {
        sans: ["Inter", "system-ui", "sans-serif"],
        mono: ["JetBrains Mono", "monospace"],
      },
      colors: {
        bg: "#09090b",
        "bg-elevated": "#0f0f11",
        "bg-overlay": "#141416",
        "bg-hover": "#18181b",
        accent: {
          DEFAULT: "#7c3aed",
          hover: "#6d28d9",
          active: "#5b21b6",
        },
        border: {
          DEFAULT: "#1f1f23",
          subtle: "#141416",
          strong: "#27272a",
        },
      },
      backgroundImage: {
        "gradient-accent": "linear-gradient(90deg, #7c3aed, #a855f7)",
        "gradient-hero": "linear-gradient(135deg, rgba(124,58,237,0.15) 0%, rgba(9,9,11,0) 60%)",
        "gradient-card": "linear-gradient(145deg, #141416, #0f0f11)",
      },
      boxShadow: {
        accent: "0 0 20px rgba(124,58,237,0.20), 0 0 40px rgba(124,58,237,0.08)",
        "accent-lg": "0 0 30px rgba(124,58,237,0.35), 0 0 60px rgba(124,58,237,0.12)",
        card: "0 4px 12px rgba(0,0,0,0.4), 0 0 0 1px rgba(255,255,255,0.04)",
      },
      borderRadius: {
        sm: "6px",
        md: "10px",
        lg: "14px",
        xl: "20px",
      },
      animation: {
        "pulse-slow": "pulse 3s cubic-bezier(0.4, 0, 0.6, 1) infinite",
        "float": "float 6s ease-in-out infinite",
        "glow": "glow 2s ease-in-out infinite alternate",
      },
      keyframes: {
        float: {
          "0%, 100%": { transform: "translateY(0px)" },
          "50%": { transform: "translateY(-8px)" },
        },
        glow: {
          from: { boxShadow: "0 0 20px rgba(124,58,237,0.2)" },
          to: { boxShadow: "0 0 40px rgba(124,58,237,0.4)" },
        },
      },
    },
  },
  plugins: [],
};

export default config;
