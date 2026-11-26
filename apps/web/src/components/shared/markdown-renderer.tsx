"use client";

import {
  Children,
  isValidElement,
  useEffect,
  useRef,
  useState,
  type ReactNode,
} from "react";
import DOMPurify from "dompurify";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import remarkMath from "remark-math";
import rehypeKatex from "rehype-katex";
import { buildMermaidFallbackText, stabilizeMermaidCode } from "@/lib/markdown/mermaid";
import { cn } from "@/lib/utils";
import "katex/dist/katex.min.css";

type MermaidInstance = typeof import("mermaid")["default"];

/**
 * Mermaid bakes its colours into the SVG it produces, so it has to be told which
 * theme the app is in. With `theme: "default"` on a dark card the labels come out
 * dark-on-dark and the diagram looks empty.
 *
 * The app switches its own `.dark` class rather than relying on the OS setting
 * alone, so both are checked: the media query covers a system-preference install,
 * the class covers the explicit toggle in settings.
 */
function prefersDark(): boolean {
  if (typeof window === "undefined") return false;
  const bySystem = window.matchMedia?.("(prefers-color-scheme: dark)")?.matches ?? false;
  const byApp = document.documentElement.classList.contains("dark");
  return bySystem || byApp;
}

/** True while the app is in dark mode, re-rendering when it changes. */
function useIsDark(): boolean {
  const [dark, setDark] = useState(() => prefersDark());

  useEffect(() => {
    const media = window.matchMedia?.("(prefers-color-scheme: dark)");
    const onChange = () => setDark(prefersDark());
    // The class is toggled on <html> by the settings toggle, which no media query
    // event reports, so watch the attribute directly.
    const observer = new MutationObserver(onChange);
    observer.observe(document.documentElement, {
      attributes: true,
      attributeFilter: ["class"],
    });
    media?.addEventListener?.("change", onChange);
    return () => {
      observer.disconnect();
      media?.removeEventListener?.("change", onChange);
    };
  }, []);

  return dark;
}

let mermaidLoader: Promise<MermaidInstance> | null = null;
let mermaidTheme: "dark" | "default" | null = null;

function getMermaid(dark: boolean) {
  const theme = dark ? "dark" : "default";
  if (!mermaidLoader || mermaidTheme !== theme) {
    mermaidTheme = theme;
    mermaidLoader = import("mermaid").then(({ default: mermaid }) => {
      mermaid.initialize({
        startOnLoad: false,
        theme,
        suppressErrorRendering: true,
        // Labels must be drawn as SVG <text>, not as HTML inside <foreignObject>.
        // The sanitiser strips foreignObject by default — rightly, it is a known
        // injection vector — and that removed every label while leaving the node
        // shapes behind, which is why the diagrams looked empty. Turning HTML
        // labels off keeps the text inside the SVG, so it survives without
        // allow-listing an unsafe element.
        htmlLabels: false,
        // Diagrams are often wider than the column they sit in. Letting the SVG
        // shrink to fit turns long labels into unreadable slivers, so it keeps its
        // own width and the container scrolls instead.
        flowchart: { useMaxWidth: false },
        mindmap: { useMaxWidth: false },
        sequence: { useMaxWidth: false },
        gantt: { useMaxWidth: false },
        themeVariables: { fontFamily: "inherit" },
      });
      return mermaid;
    });
  }
  return mermaidLoader;
}

function createMermaidRenderId(): string {
  if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") {
    return `mermaid-${crypto.randomUUID()}`;
  }
  return `mermaid-${Math.random().toString(36).slice(2, 10)}`;
}

function MermaidBlock({ code }: { code: string }) {
  const renderIdRef = useRef<string | null>(null);
  const [svgMarkup, setSvgMarkup] = useState("");
  const [fallbackText, setFallbackText] = useState("");
  const isDark = useIsDark();

  if (renderIdRef.current == null) {
    renderIdRef.current = createMermaidRenderId();
  }

  useEffect(() => {
    let cancelled = false;

    (async () => {
      try {
        const mermaid = await getMermaid(isDark);
        const preparedCode = stabilizeMermaidCode(code);
        const renderId = renderIdRef.current ?? createMermaidRenderId();
        renderIdRef.current = renderId;
        const { svg } = await mermaid.render(renderId, preparedCode);
        if (!cancelled) {
          setSvgMarkup(DOMPurify.sanitize(svg));
          setFallbackText("");
        }
      } catch {
        if (!cancelled) {
          setSvgMarkup("");
          setFallbackText(buildMermaidFallbackText(code));
        }
      }
    })();

    return () => {
      cancelled = true;
    };
  }, [code, isDark]);

  if (svgMarkup) {
    return (
      // Scrolls rather than shrinking: a squeezed diagram has readable-looking
      // shapes with unreadable labels, which reads as "the text is missing".
      <div className="my-4 overflow-x-auto rounded-xl border border-border/40 bg-muted/10 p-2">
        <div
          className="flex min-w-full justify-center [&_svg]:h-auto [&_svg]:max-w-none"
          dangerouslySetInnerHTML={{ __html: svgMarkup }}
        />
      </div>
    );
  }

  if (fallbackText) {
    return (
      <div className="my-4">
        <div className="rounded-xl border border-border/60 bg-muted/20 p-3">
          <p className="mb-2 text-xs font-medium text-muted-foreground">
            Diagram preview unavailable. Showing a text outline instead.
          </p>
          <pre className="whitespace-pre-wrap text-sm text-foreground">{fallbackText}</pre>
        </div>
      </div>
    );
  }

  return (
    <div className="my-4">
      <div className="rounded-xl border border-border/40 bg-muted/10 px-3 py-2 text-xs text-muted-foreground">
        Rendering diagram...
      </div>
    </div>
  );
}

/**
 * Models are not consistent about math delimiters: some write `$x$`, others
 * `\(x\)` or `\[x\]`. remark-math only understands the dollar forms, so the
 * bracket forms used to reach the screen as raw backslashes. Convert them
 * outside fenced/inline code, where a backslash may be intentional.
 */
function normalizeMathDelimiters(content: string): string {
  const parts = content.split(/(```[\s\S]*?```|~~~[\s\S]*?~~~|`[^`\n]*`)/g);
  return parts
    .map((part, index) => {
      if (index % 2 === 1) return part;
      return part
        .replace(/\\\[([\s\S]*?)\\\]/g, (_, expression: string) => {
          return `\n$$\n${expression.trim()}\n$$\n`;
        })
        .replace(/\\\(([\s\S]*?)\\\)/g, (_, expression: string) => {
          return `$${expression.trim()}$`;
        })
        // remark-math only treats `$$...$$` as display math when each
        // delimiter sits alone on its line. Models often write the opening
        // delimiter after a list marker, or keep the closing delimiter at the
        // end of the last equation line, which leaves the rest as raw LaTeX.
        .replace(
          /\$\$([\s\S]*?)\$\$/g,
          (match: string, expression: string, offset: number, source: string) => {
            const lineStart = source.lastIndexOf("\n", offset - 1) + 1;
            const prefix = source.slice(lineStart, offset);
            const listMarker = /^\s*(?:[-+*]|\d+[.)])\s+/.exec(prefix);
            // Continuation lines of a list item need at least as much
            // indentation as the text after its marker.
            const indent = listMarker
              ? " ".repeat(listMarker[0].length)
              : (/^[ \t]*/.exec(prefix)?.[0] ?? "");
            const startsBlock =
              prefix.trim() === "" ||
              (listMarker !== null && prefix.trim() === listMarker[0].trim());

            const body = expression
              .replace(/^\n+|\n+$/g, "")
              .split("\n")
              .map((line) => {
                if (!line.trim()) return "";
                const leading = /^[ \t]*/.exec(line)?.[0] ?? "";
                return leading.length >= indent.length
                  ? line
                  : indent + line.replace(/^[ \t]*/, "");
              })
              .join("\n");

            // The closing delimiter must also stand alone. If prose follows
            // the formula on the same line, move it below the block.
            const tail = source.slice(offset + match.length).split("\n", 1)[0];
            const head = startsBlock ? "$$\n" : `\n\n${indent}$$\n`;
            const close = `${indent}$$${tail.trim() ? "\n" : ""}`;
            return `${head}${body}${body ? "\n" : ""}${close}`;
          },
        );
    })
    .join("");
}

/**
 * A mermaid code block is rendered by the `pre` element as well as by `code`.
 * Without this check the diagram ended up inside the normal code-block `<pre>`,
 * which added the wrong padding and background around the SVG.
 */
function containsMermaid(children: ReactNode): boolean {
  return Children.toArray(children).some((child) => {
    if (!isValidElement(child)) return false;
    const props = child.props as {
      className?: string;
      "data-mermaid"?: string;
    };
    return (
      props["data-mermaid"] === "true" ||
      /(^|\s)language-mermaid(\s|$)/.test(props.className ?? "")
    );
  });
}

interface MarkdownRendererProps {
  content: string;
  className?: string;
}

export function MarkdownRenderer({
  content,
  className,
}: MarkdownRendererProps) {
  return (
    <div role="article" className={cn("markdown-body", className)}>
      <ReactMarkdown
        remarkPlugins={[remarkMath, remarkGfm]}
        rehypePlugins={[[rehypeKatex, { throwOnError: false, strict: false }]]}
        components={{
          pre({ children }) {
            if (containsMermaid(children)) return <>{children}</>;
            return <pre>{children}</pre>;
          },
          code({ className: codeClassName, children }) {
            const match = /language-([\w-]+)/.exec(codeClassName || "");
            const language = match?.[1];

            if (language === "mermaid") {
              return (
                <div data-mermaid="true">
                  <MermaidBlock code={String(children).trim()} />
                </div>
              );
            }

            // The surrounding `pre` from ReactMarkdown owns block styling;
            // this element only carries the language class. Returning another
            // `<pre>` here used to produce a nested `<pre><pre>`.
            return <code className={codeClassName}>{children}</code>;
          },
          table({ children }) {
            return (
              <div className="my-4 overflow-x-auto">
                <table>{children}</table>
              </div>
            );
          },
        }}
      >
        {normalizeMathDelimiters(content)}
      </ReactMarkdown>
    </div>
  );
}
