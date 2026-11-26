import { describe, it, expect, beforeEach, vi } from "vitest";
import { render, screen, waitFor } from "@/test-utils";

/**
 * Diagrams were invisible in dark mode.
 *
 * Mermaid bakes its colours into the SVG, and it was initialised with
 * `theme: "default"`, which writes dark labels. On the app's dark card that is
 * dark-on-dark: the shapes are there, the text is not. It also shrank the SVG to fit
 * the column, which squeezes long labels until they are unreadable.
 *
 * The component caches the mermaid instance in module scope, so each test resets
 * the modules and imports both the component and mermaid fresh. Without that the
 * first test's initialisation would satisfy every later assertion.
 */

const renderMermaid = vi.fn();
const initialize = vi.fn();

vi.mock("mermaid", () => ({
  default: {
    initialize: (...a: unknown[]) => initialize(...a),
    render: (...a: unknown[]) => renderMermaid(...a),
  },
}));

const DIAGRAM = "```mermaid\nmindmap\n  root((Производная))\n    Геометрия\n```";

function setDark(on: boolean) {
  document.documentElement.classList.toggle("dark", on);
}

function stubMatchMedia(dark: boolean) {
  Object.defineProperty(window, "matchMedia", {
    configurable: true,
    writable: true,
    value: (query: string) => ({
      matches: dark,
      media: query,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      dispatchEvent: vi.fn(),
    }),
  });
}

/** Fresh component + fresh mermaid, with no cached instance between tests. */
async function renderFresh(content: string, { dark = false } = {}) {
  setDark(dark);
  stubMatchMedia(dark);
  vi.resetModules();
  const { MarkdownRenderer } = await import("./markdown-renderer");
  return render(<MarkdownRenderer content={content} />);
}

describe("MarkdownRenderer diagrams", () => {
  beforeEach(() => {
    initialize.mockClear();
    renderMermaid.mockReset();
    renderMermaid.mockResolvedValue({
      svg: '<svg><text>Производная</text></svg>',
    });
  });

  it("renders the diagram instead of showing raw code", async () => {
    await renderFresh(DIAGRAM);
    await waitFor(() => expect(renderMermaid).toHaveBeenCalled());
  });

  it("asks mermaid for SVG labels, not HTML in foreignObject", async () => {
    // The actual cause of the empty diagrams: labels are HTML inside
    // <foreignObject>, the sanitiser strips that element, and the node shapes were
    // left with nothing to label.
    await renderFresh(DIAGRAM);
    await waitFor(() =>
      expect(initialize).toHaveBeenCalledWith(
        expect.objectContaining({ htmlLabels: false }),
      ),
    );
  });

  it("keeps HTML labels out of the flowchart config too", async () => {
    await renderFresh(DIAGRAM);
    await waitFor(() =>
      expect(initialize).toHaveBeenCalledWith(
        expect.objectContaining({
          flowchart: expect.not.objectContaining({ htmlLabels: true }),
        }),
      ),
    );
  });

  it("uses the light mermaid theme when the app is light", async () => {
    await renderFresh(DIAGRAM);
    await waitFor(() =>
      expect(initialize).toHaveBeenCalledWith(
        expect.objectContaining({ theme: "default" }),
      ),
    );
  });

  it("switches mermaid to its dark theme when the app is dark", async () => {
    // The bug itself: a light-theme diagram on a dark card renders dark text, so
    // the labels are invisible.
    await renderFresh(DIAGRAM, { dark: true });
    await waitFor(() =>
      expect(initialize).toHaveBeenCalledWith(
        expect.objectContaining({ theme: "dark" }),
      ),
    );
  });

  it("follows the app's own dark class, not only the system preference", async () => {
    // Settings toggles .dark on <html>; no media event reports it.
    stubMatchMedia(false);
    await renderFresh(DIAGRAM, { dark: true });
    await waitFor(() =>
      expect(initialize).toHaveBeenCalledWith(
        expect.objectContaining({ theme: "dark" }),
      ),
    );
  });

  it("keeps the diagram at its own width so labels stay readable", async () => {
    await renderFresh(DIAGRAM);
    await waitFor(() => expect(renderMermaid).toHaveBeenCalled());
    // The container scrolls rather than squeezing the SVG. The Tailwind arbitrary
    // variant cannot be used as a querySelector — jsdom reads the brackets as an
    // attribute selector — so the class string is checked instead.
    const scroller = document.querySelector(".overflow-x-auto");
    expect(scroller).toBeTruthy();
    const wrapper = scroller?.querySelector("div");
    expect(wrapper?.className).toContain("min-w-full");
    expect(wrapper?.className).toContain("max-w-none");
  });

  it("falls back to a text outline when mermaid cannot parse", async () => {
    renderMermaid.mockRejectedValue(new Error("parse error"));
    await renderFresh(DIAGRAM);
    expect(
      await screen.findByText(/Diagram preview unavailable/),
    ).toBeTruthy();
  });

  it("leaves ordinary text alone", async () => {
    await renderFresh("Просто текст без схемы.");
    expect(await screen.findByText(/Просто текст/)).toBeTruthy();
    expect(initialize).not.toHaveBeenCalled();
  });
});

describe("MarkdownRenderer formatting", () => {
  it("renders GFM tables instead of raw pipe characters", async () => {
    await renderFresh("| Понятие | Значение |\n| --- | --- |\n| Сила | масса × ускорение |");
    expect(screen.getByRole("table")).toBeTruthy();
    expect(screen.getByText("масса × ускорение")).toBeTruthy();
  });

  it("renders inline and display LaTeX", async () => {
    await renderFresh("Формула $x^2$ и\n\n$$\\frac{1}{2}$$");
    await waitFor(() => expect(document.querySelector(".katex")).toBeTruthy());
    expect(document.querySelector(".katex-display")).toBeTruthy();
  });

  it("accepts bracket-style math delimiters from the model", async () => {
    await renderFresh("Формула \\(a^2+b^2\\) в тексте.");
    await waitFor(() => expect(document.querySelector(".katex")).toBeTruthy());
    expect(document.body.textContent).not.toContain("\\(");
  });

  it("renders multi-line display math that starts after a list marker", async () => {
    await renderFresh(
      "- $$\\int_a^b (f(x)+g(x))\\,dx\n  = \\int_a^b f(x)\\,dx + \\int_a^b g(x)\\,dx,$$\n" +
        "- $$\\int_a^b k f(x)\\,dx\n  = k\\int_a^b f(x)\\,dx,$$",
    );
    await waitFor(() =>
      expect(document.querySelectorAll(".katex-display")).toHaveLength(2),
    );
    expect(document.body.textContent).not.toContain("$$");
  });

  it("does not turn display delimiters inside a code fence into math", async () => {
    await renderFresh("```text\n$$\nnot math\n$$\n```");
    expect(document.querySelector(".katex-display")).toBeNull();
    expect(document.querySelector("pre code")?.textContent).toContain("$$");
  });

  it("does not nest a code block inside another pre", async () => {
    await renderFresh("```python\nprint('ok')\n```");
    expect(document.querySelectorAll("pre pre")).toHaveLength(0);
    expect(document.querySelector("pre code")?.textContent).toContain("print('ok')");
  });
});