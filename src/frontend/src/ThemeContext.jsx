import { createContext, useContext, useEffect, useState } from "react";

const Ctx = createContext({ theme: "light", toggle: () => {} });
const STORAGE_KEY = "reviewer_theme";

export function ThemeProvider({ children }) {
  const [theme, setTheme] = useState(
    () => localStorage.getItem(STORAGE_KEY) || "light"
  );

  useEffect(() => {
    localStorage.setItem(STORAGE_KEY, theme);
    document.documentElement.setAttribute("data-theme", theme);
    document.documentElement.style.colorScheme = theme;
  }, [theme]);

  const toggle = () => setTheme((t) => (t === "light" ? "dark" : "light"));

  return <Ctx.Provider value={{ theme, toggle }}>{children}</Ctx.Provider>;
}

export const useTheme = () => useContext(Ctx);

/**
 * Pick a class string based on current theme.
 * Usage: const { d } = useTheme();  d("dark-class", "light-class")
 */
export function useThemeClasses() {
  const { theme } = useTheme();
  return {
    theme,
    d: (darkCls, lightCls) => (theme === "dark" ? darkCls : lightCls),
  };
}
