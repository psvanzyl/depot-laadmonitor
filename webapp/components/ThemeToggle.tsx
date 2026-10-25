"use client";

import { useEffect, useState } from "react";
import { Monitor, Moon, Sun } from "lucide-react";

/**
 * Light / dark / system, the same control on every site in the family.
 *
 * The class lives on <html> and THEME_INIT in layout.tsx sets it before first
 * paint, so there is no flash. This only changes it afterwards.
 */
export type ThemeMode = "light" | "dark" | "system";

const KEY = "depotdex-theme";

const OPTIONS: { mode: ThemeMode; label: string; Icon: typeof Sun }[] = [
  { mode: "light", label: "Licht", Icon: Sun },
  { mode: "dark", label: "Donker", Icon: Moon },
  { mode: "system", label: "Systeem", Icon: Monitor },
];

function prefersDark() {
  return window.matchMedia("(prefers-color-scheme: dark)").matches;
}

/** Apply a mode to <html>. Mirrors THEME_INIT in layout.tsx. */
export function paint(mode: ThemeMode) {
  const dark = mode === "dark" || (mode === "system" && prefersDark());
  const root = document.documentElement;
  root.classList.toggle("dark", dark);
  root.style.colorScheme = dark ? "dark" : "light";
}

export function readMode(): ThemeMode {
  const stored = window.localStorage.getItem(KEY);
  return stored === "dark" || stored === "system" ? stored : "light";
}

export default function ThemeToggle() {
  // SSR and the first render are always "light"; the effect syncs the stored mode.
  const [mode, setMode] = useState<ThemeMode>("light");

  useEffect(() => {
    setMode(readMode());
    const mq = window.matchMedia("(prefers-color-scheme: dark)");
    const onChange = () => {
      if (readMode() === "system") paint("system");
    };
    mq.addEventListener("change", onChange);
    return () => mq.removeEventListener("change", onChange);
  }, []);

  const choose = (next: ThemeMode) => {
    setMode(next);
    try {
      window.localStorage.setItem(KEY, next);
    } catch {
      /* private mode: the choice just will not persist */
    }
    paint(next);
  };

  return (
    <div
      role="group"
      aria-label="Kleurthema"
      data-testid="theme-toggle"
      className="flex flex-none items-center gap-0.5 rounded-xl border border-slate-200 bg-white p-0.5"
    >
      {OPTIONS.map(({ mode: m, label, Icon }) => (
        <button
          key={m}
          type="button"
          onClick={() => choose(m)}
          aria-pressed={mode === m}
          aria-label={`${label} thema`}
          title={`${label} thema`}
          data-testid={`theme-${m}`}
          className={`grid h-7 w-7 place-items-center rounded-[9px] transition ${
            mode === m ? "bg-slate-100 text-slate-900" : "text-slate-500 hover:text-slate-900"
          }`}
        >
          <Icon className="h-3.5 w-3.5" />
        </button>
      ))}
    </div>
  );
}
