import { useEffect } from "react";
import useStore from "../../store/useStore";

export function ThemeRoot({ children }) {
  const theme = useStore((s) => s.theme);
  useEffect(() => {
    document.documentElement.setAttribute("data-theme", theme);
    if (theme === "dark") document.documentElement.classList.add("dark");
    else document.documentElement.classList.remove("dark");
  }, [theme]);
  return children;
}
