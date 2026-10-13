"use client";

import { useSyncExternalStore } from "react";
import { Monitor, Moon, Sun } from "lucide-react";

type Theme = "light" | "dark" | "system";

const STORAGE_KEY = "logsherlock-theme";

// Subscribers let every mounted control re-render when the theme changes.
const listeners = new Set<() => void>();

function notify() {
  for (const listener of listeners) listener();
}

export function applyTheme(theme: Theme) {
  const root = document.documentElement;
  if (theme === "system") {
    root.removeAttribute("data-theme");
  } else {
    root.setAttribute("data-theme", theme);
  }
  try {
    window.localStorage.setItem(STORAGE_KEY, theme);
  } catch {
    // Private browsing can block storage; the theme still applies for this page.
  }
  notify();
}

function readStoredTheme(): Theme {
  try {
    const stored = window.localStorage.getItem(STORAGE_KEY);
    if (stored === "light" || stored === "dark" || stored === "system") return stored;
  } catch {
    // Storage unavailable — fall through to the system default.
  }
  return "system";
}

// `storage` fires when another tab changes the setting.
function subscribe(onChange: () => void) {
  listeners.add(onChange);
  window.addEventListener("storage", onChange);
  return () => {
    listeners.delete(onChange);
    window.removeEventListener("storage", onChange);
  };
}

// Server render has no localStorage, so it agrees on "system" and the inline
// bootstrap script in the root layout corrects the DOM before paint.
const getServerSnapshot = (): Theme => "system";

const OPTIONS: { value: Theme; label: string; Icon: typeof Sun }[] = [
  { value: "light", label: "Light", Icon: Sun },
  { value: "dark", label: "Dark", Icon: Moon },
  { value: "system", label: "Match system", Icon: Monitor },
];

/**
 * Three-state theme switch: light, dark, or follow the OS.
 *
 * Rendered as a radio group so the current choice is announced to assistive
 * technology, rather than a toggle button whose state is ambiguous.
 */
export function ThemeToggle() {
  const theme = useSyncExternalStore(subscribe, readStoredTheme, getServerSnapshot);

  return (
    <div
      role="radiogroup"
      aria-label="Colour theme"
      className="flex items-center gap-0.5 rounded border border-edge p-0.5"
    >
      {OPTIONS.map(({ value, label, Icon }) => {
        const active = theme === value;
        return (
          <button
            key={value}
            type="button"
            role="radio"
            aria-checked={active}
            aria-label={label}
            title={label}
            onClick={() => applyTheme(value)}
            className={`rounded p-1 transition-colors ${
              active ? "bg-raised text-ink" : "text-ink-faint hover:text-ink-dim"
            }`}
          >
            <Icon className="h-3.5 w-3.5" aria-hidden />
          </button>
        );
      })}
    </div>
  );
}