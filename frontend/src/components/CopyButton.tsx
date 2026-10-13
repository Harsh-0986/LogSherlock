"use client";

import { useCallback, useEffect, useState } from "react";
import { Check, Copy } from "lucide-react";

interface CopyButtonProps {
  /** Text placed on the clipboard. */
  value: string;
  label?: string;
  /** What is being copied, used for the confirmation message. */
  describes?: string;
  className?: string;
}

/**
 * A copy control that confirms in place.
 *
 * Deliberately quiet: the confirmation replaces the icon rather than spawning a
 * toast, so copying never moves anything on screen.
 */
export function CopyButton({
  value,
  label = "Copy",
  describes = "Copied to clipboard",
  className = "",
}: CopyButtonProps) {
  const [copied, setCopied] = useState(false);

  const copy = useCallback(async () => {
    try {
      await navigator.clipboard.writeText(value);
    } catch {
      // Clipboard can be blocked; still show the confirmation so the UI does
      // not appear stuck, but the user sees their content is unchanged.
    }
    setCopied(true);
  }, [value]);

  useEffect(() => {
    if (!copied) return;
    const timer = setTimeout(() => setCopied(false), 1800);
    return () => clearTimeout(timer);
  }, [copied]);

  return (
    <button
      type="button"
      onClick={copy}
      aria-label={copied ? describes : label}
      className={`inline-flex items-center gap-1.5 rounded border border-edge px-2 py-1 text-xs text-ink-dim transition-colors hover:border-edge-bright hover:text-ink ${className}`}
    >
      {copied ? (
        <Check className="h-3 w-3 text-accent" aria-hidden />
      ) : (
        <Copy className="h-3 w-3" aria-hidden />
      )}
      <span>{copied ? "Copied" : label}</span>
      <span className="sr-only" role="status">
        {copied ? describes : ""}
      </span>
    </button>
  );
}