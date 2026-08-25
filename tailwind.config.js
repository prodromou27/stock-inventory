/** @type {import('tailwindcss').Config} */
module.exports = {
  // Scans every server-rendered template for class names actually used, so
  // the compiled static/css/app.css only ships the Tailwind utilities this
  // app references — see assets/tailwind/input.css for the component-class
  // layer (.btn, .card, .badge, etc.) every template already uses.
  content: ["./templates/**/*.html", "./apps/**/templates/**/*.html"],
  // Not "class" — templates never toggle a .dark class; dark mode already
  // works by redefining the --color-* custom properties under
  // prefers-color-scheme (assets/tailwind/input.css's @layer base), so every
  // color utility below inherits it automatically with no dark: variants
  // needed anywhere.
  darkMode: "media",
  theme: {
    extend: {
      // Every value here is a CSS custom property already defined in
      // assets/tailwind/input.css's @layer base (identical to the design
      // tokens the hand-written stylesheet used before this Tailwind
      // migration) — utilities like bg-primary or rounded-md resolve
      // through the variable, not a value baked in here.
      colors: {
        bg: "var(--color-bg)",
        surface: "var(--color-surface)",
        border: "var(--color-border)",
        "border-strong": "var(--color-border-strong)",
        text: "var(--color-text)",
        muted: "var(--color-text-muted)",
        faint: "var(--color-text-faint)",
        primary: {
          DEFAULT: "var(--color-primary)",
          hover: "var(--color-primary-hover)",
          bg: "var(--color-primary-bg)",
          contrast: "var(--color-primary-contrast)",
        },
        success: { DEFAULT: "var(--color-success)", bg: "var(--color-success-bg)" },
        warning: { DEFAULT: "var(--color-warning)", bg: "var(--color-warning-bg)" },
        danger: { DEFAULT: "var(--color-danger)", bg: "var(--color-danger-bg)" },
        info: { DEFAULT: "var(--color-info)", bg: "var(--color-info-bg)" },
      },
      borderRadius: {
        sm: "var(--radius-sm)",
        md: "var(--radius-md)",
        lg: "var(--radius-lg)",
      },
      boxShadow: {
        sm: "var(--shadow-sm)",
        md: "var(--shadow-md)",
        lg: "var(--shadow-lg)",
      },
      fontFamily: {
        sans: "var(--font-sans)",
        mono: "var(--font-mono)",
      },
    },
  },
  plugins: [],
};
