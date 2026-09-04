(() => {
  const STORAGE_KEY = "theme";
  const button = document.getElementById("theme-toggle");
  if (!button) return;

  const root = document.documentElement;
  const themeColorMeta = document.querySelector('meta[name="theme-color"]');
  // Kept in sync with assets/tailwind/input.css's --color-bg for each theme —
  // this is the one place a bare hex has to live outside the stylesheet,
  // since <meta name="theme-color"> can't read a CSS custom property.
  const THEME_COLOR = { light: "#f4f6f8", dark: "#0f1216" };

  function currentTheme() {
    return root.getAttribute("data-theme") === "dark" ? "dark" : "light";
  }

  function applyState(theme) {
    button.setAttribute("aria-pressed", String(theme === "dark"));
    button.setAttribute("aria-label", theme === "dark" ? "Switch to light theme" : "Switch to dark theme");
    if (themeColorMeta) themeColorMeta.setAttribute("content", THEME_COLOR[theme]);
  }

  // The inline snippet in base.html's <head> already set data-theme before
  // first paint (avoiding a flash) — this just syncs the button's own state
  // to whatever it decided, once the DOM exists to hold a button at all.
  applyState(currentTheme());

  button.addEventListener("click", () => {
    const next = currentTheme() === "dark" ? "light" : "dark";
    if (next === "dark") {
      root.setAttribute("data-theme", "dark");
    } else {
      root.removeAttribute("data-theme");
    }
    try {
      localStorage.setItem(STORAGE_KEY, next);
    } catch (error) {
      /* private browsing / storage disabled — theme just won't persist across visits */
    }
    applyState(next);
  });
})();
