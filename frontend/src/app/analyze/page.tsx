"use client";

import { Suspense, useCallback, useEffect, useRef, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import Link from "next/link";
import {
  AlertCircle,
  FileUp,
  Loader2,
  Play,
  Upload,
  X,
} from "lucide-react";
import { ApiError, api, type InvestigationStatusResponse } from "@/lib/api";
import { StepProgress } from "@/components/StepProgress";
import { pluralise } from "@/lib/format";

const ACCEPTED = ".log,.txt,.jsonl";
const TERMINAL = new Set(["completed", "partial", "failed"]);
const POLL_INTERVAL_MS = 700;

const RUNTIMES = [
  { value: "auto", label: "Detect automatically" },
  { value: "python", label: "Python" },
  { value: "nodejs", label: "Node.js" },
  { value: "dotnet", label: ".NET" },
  { value: "jsonl", label: "JSONL" },
  { value: "generic", label: "Generic" },
];

function AnalyzeView() {
  const router = useRouter();
  const params = useSearchParams();

  const [content, setContent] = useState("");
  const [filename, setFilename] = useState<string | null>(null);
  const [runtime, setRuntime] = useState("auto");
  const [context, setContext] = useState("");

  const [dragging, setDragging] = useState(false);
  const [inputError, setInputError] = useState<string | null>(null);
  const [submitError, setSubmitError] = useState<string | null>(null);
  const [starting, setStarting] = useState(false);
  const [progress, setProgress] = useState<InvestigationStatusResponse | null>(null);

  const fileInputRef = useRef<HTMLInputElement>(null);
  const sampleLoaded = useRef(false);

  // --- sample log ------------------------------------------------------
  useEffect(() => {
    if (sampleLoaded.current) return;
    if (params.get("sample") !== "1") return;
    sampleLoaded.current = true;

    void fetch("/sample.log")
      .then((res) => (res.ok ? res.text() : Promise.reject(new Error("missing"))))
      .then((text) => {
        setContent(text);
        setFilename("sample-node-api.log");
      })
      .catch(() => {
        setInputError("The bundled sample log could not be loaded.");
      });
  }, [params]);

  // --- file handling ----------------------------------------------------
  const acceptFile = useCallback(
    async (file: File) => {
      setInputError(null);

      const name = file.name.toLowerCase();
      if (!/\.(log|txt|jsonl)$/.test(name)) {
        setInputError("Choose a .log, .txt or .jsonl file.");
        return;
      }
      if (file.size > 5 * 1024 * 1024) {
        setInputError("That file is larger than 5 MB.");
        return;
      }

      try {
        const text = await file.text();
        setContent(text);
        setFilename(file.name);
      } catch {
        setInputError("That file could not be read.");
      }
    },
    [],
  );

  const lineCount = content ? content.split("\n").length : 0;
  const byteSize = new TextEncoder().encode(content).length;

  // --- submit -----------------------------------------------------------
  const start = useCallback(async () => {
    if (!content.trim()) {
      setInputError("Paste some logs or choose a file first.");
      return;
    }
    if (lineCount > 20_000) {
      setInputError(`That is ${pluralise(lineCount, "line")}; the limit is 20,000.`);
      return;
    }

    setInputError(null);
    setSubmitError(null);
    setStarting(true);

    try {
      const { id } = await api.startInvestigation({
        content,
        filename,
        runtime,
        context: context.trim() || null,
      });
      router.push(`/investigations/${id}`);
    } catch (error) {
      setStarting(false);
      if (error instanceof ApiError) {
        setSubmitError(error.message);
      } else {
        setSubmitError("The investigation could not be started.");
      }
    }
  }, [content, context, filename, lineCount, router, runtime]);

  // --- progress (shown on this page if the router push is deferred) -----
  useEffect(() => {
    const id = progress?.id;
    if (!id || TERMINAL.has(progress.status)) return;

    const timer = setInterval(async () => {
      try {
        const next = await api.investigationStatus(id);
        setProgress(next);
        if (TERMINAL.has(next.status)) {
          router.push(`/investigations/${id}`);
        }
      } catch {
        // Transient poll failure: keep trying until the deadline below.
      }
    }, POLL_INTERVAL_MS);

    return () => clearInterval(timer);
  }, [progress, router]);

  const clear = () => {
    setContent("");
    setFilename(null);
    setInputError(null);
    setContext("");
  };

  return (
    <div className="mx-auto max-w-6xl px-6 py-10">
      <header className="mb-8">
        <h1 className="text-2xl font-semibold tracking-tight">Analyze logs</h1>
        <p className="mt-1.5 text-sm text-ink-dim">
          Paste output or drop a file. Secrets are redacted before anything is stored or sent.
        </p>
      </header>

      {progress && !TERMINAL.has(progress.status) && (
        <section className="mb-8 rounded border border-edge bg-panel px-5 py-4">
          <h2 className="mb-2 text-sm font-medium">Running</h2>
          <StepProgress steps={progress.steps} />
        </section>
      )}

      <div className="grid gap-6 lg:grid-cols-[1fr_20rem]">
        {/* --- Editor / drop zone ------------------------------------- */}
        <div>
          <div
            onDragOver={(e) => {
              e.preventDefault();
              setDragging(true);
            }}
            onDragLeave={() => setDragging(false)}
            onDrop={(e) => {
              e.preventDefault();
              setDragging(false);
              const file = e.dataTransfer.files?.[0];
              if (file) void acceptFile(file);
            }}
            className={`relative rounded border transition-colors ${
              dragging ? "border-accent bg-panel" : "border-edge bg-panel"
            }`}
          >
            <div className="flex items-center justify-between border-b border-edge px-4 py-2">
              <span className="truncate font-mono text-xs text-ink-dim">
                {filename ?? "pasted input"}
              </span>
              <div className="flex items-center gap-3">
                <span className="font-mono text-[11px] text-ink-faint">
                  {lineCount > 0 ? pluralise(lineCount, "line") : "empty"}
                  {byteSize > 0 && ` · ${(byteSize / 1024).toFixed(1)} KB`}
                </span>
                {(content || filename) && (
                  <button
                    type="button"
                    onClick={clear}
                    aria-label="Clear input"
                    className="text-ink-faint transition-colors hover:text-ink"
                  >
                    <X className="h-3.5 w-3.5" aria-hidden />
                  </button>
                )}
              </div>
            </div>

            <textarea
              value={content}
              onChange={(e) => {
                setContent(e.target.value);
                if (e.target.value.length > 0) setFilename(null);
              }}
              spellCheck={false}
              aria-label="Log content"
              placeholder={
                "Paste log output here, or drop a .log, .txt or .jsonl file.\n\n" +
                "Python tracebacks, Node.js stacks and .NET exceptions are all recognised."
              }
              className="scroll-quiet h-[26rem] w-full resize-none bg-transparent px-4 py-3 font-mono text-xs leading-relaxed text-ink outline-none placeholder:text-ink-faint"
            />

            {dragging && (
              <div className="pointer-events-none absolute inset-0 flex items-center justify-center rounded surface-drop">
                <span className="flex items-center gap-2 text-sm text-ink">
                  <FileUp className="h-4 w-4" aria-hidden />
                  Drop to load
                </span>
              </div>
            )}
          </div>

          <input
            ref={fileInputRef}
            type="file"
            accept={ACCEPTED}
            className="sr-only"
            onChange={(e) => {
              const file = e.target.files?.[0];
              if (file) void acceptFile(file);
            }}
          />

          <div className="mt-3 flex flex-wrap items-center gap-3">
            <button
              type="button"
              onClick={() => fileInputRef.current?.click()}
              className="inline-flex items-center gap-2 rounded border border-edge-bright px-3 py-1.5 text-sm text-ink transition-colors hover:border-ink-faint"
            >
              <Upload className="h-3.5 w-3.5" aria-hidden />
              Choose a file
            </button>

            <button
              type="button"
              onClick={start}
              disabled={starting}
              className="inline-flex items-center gap-2 rounded bg-accent px-4 py-1.5 text-sm font-medium text-base transition-opacity hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-50"
            >
              {starting ? (
                <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden />
              ) : (
                <Play className="h-3.5 w-3.5" aria-hidden />
              )}
              {starting ? "Starting…" : "Analyze"}
            </button>

            <span className="text-xs text-ink-faint">.log, .txt, .jsonl up to 5 MB</span>
          </div>

          {inputError && (
            <p className="mt-3 flex items-start gap-2 text-sm text-critical" role="alert">
              <AlertCircle className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden />
              {inputError}
            </p>
          )}
          {submitError && (
            <p className="mt-3 flex items-start gap-2 text-sm text-critical" role="alert">
              <AlertCircle className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden />
              {submitError}
            </p>
          )}
        </div>

        {/* --- Options --------------------------------------------------- */}
        <aside className="space-y-6">
          <div>
            <label
              htmlFor="runtime"
              className="block text-sm font-medium text-ink"
            >
              Log format
            </label>
            <select
              id="runtime"
              value={runtime}
              onChange={(e) => setRuntime(e.target.value)}
              className="mt-2 w-full rounded border border-edge bg-raised px-2.5 py-2 text-sm text-ink outline-none"
            >
              {RUNTIMES.map((option) => (
                <option key={option.value} value={option.value}>
                  {option.label}
                </option>
              ))}
            </select>
            <p className="mt-1.5 text-xs text-ink-faint">
              Detection is usually right. Force it if grouping looks wrong.
            </p>
          </div>

          <div>
            <label htmlFor="context" className="block text-sm font-medium text-ink">
              Context <span className="font-normal text-ink-faint">(optional)</span>
            </label>
            <textarea
              id="context"
              value={context}
              onChange={(e) => setContext(e.target.value)}
              rows={5}
              placeholder="What were you doing when this happened? A deploy, a config change, a traffic spike."
              className="mt-2 w-full resize-none rounded border border-edge bg-raised px-2.5 py-2 text-sm text-ink outline-none placeholder:text-ink-faint"
            />
            <p className="mt-1.5 text-xs text-ink-faint">
              Passed to the model as untrusted data.
            </p>
          </div>

          <p className="border-t border-edge pt-4 text-xs leading-relaxed text-ink-faint">
            Looking for a starting point?{" "}
            <Link href="/analyze?sample=1" className="text-ink-dim hover:text-ink">
              Load a sample log
            </Link>
            .
          </p>
        </aside>
      </div>
    </div>
  );
}

/**
 * `useSearchParams` suspends during prerender, so the view is wrapped in Suspense.
 */
export default function AnalyzePage() {
  return (
    <Suspense fallback={<AnalyzeSkeleton />}>
      <AnalyzeView />
    </Suspense>
  );
}

function AnalyzeSkeleton() {
  return (
    <div className="mx-auto max-w-6xl px-6 py-10">
      <h1 className="text-2xl font-semibold tracking-tight">Analyze logs</h1>
      <div className="mt-8 grid gap-6 lg:grid-cols-[1fr_20rem]">
        <div className="h-[30rem] rounded border border-edge bg-panel" />
        <div className="h-64 rounded border border-edge bg-panel" />
      </div>
    </div>
  );
}
