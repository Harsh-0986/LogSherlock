import Link from "next/link";

/**
 * Page footer. Lives outside `layout.tsx` so that importing it from a Client
 * Component does not drag the layout (and its `metadata` export) into the
 * client bundle.
 */
export function Footer() {
  return (
    <footer className="border-t border-edge px-6 py-6">
      <div className="mx-auto flex max-w-6xl flex-wrap items-center gap-x-6 gap-y-2 text-xs text-ink-faint">
        <span>Logs stay on your machine unless you configure a hosted model.</span>
        <Link href="/settings" className="ml-auto hover:text-ink-dim">
          Model settings
        </Link>
      </div>
    </footer>
  );
}