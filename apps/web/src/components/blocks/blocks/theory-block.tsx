"use client";

import { useCallback, useEffect, useState } from "react";
import { Link, Trash2 } from "lucide-react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { addTheoryLink, deleteTheoryLink, listTheoryLinks, type TheoryLink } from "@/lib/api/user-content";
import { useT } from "@/lib/i18n-context";
import type { BlockComponentProps } from "@/lib/block-system/registry";

export default function TheoryBlock({ courseId }: BlockComponentProps) {
  const t = useT();
  const [links, setLinks] = useState<TheoryLink[]>([]);
  const [busy, setBusy] = useState(false);
  const [title, setTitle] = useState("");
  const [url, setUrl] = useState("");
  const [description, setDescription] = useState("");
  const [adding, setAdding] = useState(false);

  const load = useCallback(async () => {
    try {
      const data = await listTheoryLinks(courseId);
      setLinks(data);
    } catch {
      setLinks([]);
    }
  }, [courseId]);

  useEffect(() => {
    void load();
  }, [load]);

  const handleAdd = useCallback(async () => {
    if (!title.trim() || !url.trim()) return;
    setAdding(true);
    try {
      await addTheoryLink(courseId, {
        title: title.trim(),
        url: url.trim(),
        description: description.trim() || undefined,
      });
      setTitle("");
      setUrl("");
      setDescription("");
      void load();
    } catch (error) {
      toast.error(t("theory.addFailed"), {
        description: error instanceof Error ? error.message : undefined,
      });
    } finally {
      setAdding(false);
    }
  }, [courseId, title, url, description, load, t]);

  const handleDelete = useCallback(async (id: string) => {
    try {
      await deleteTheoryLink(id);
      void load();
    } catch (error) {
      toast.error(t("theory.deleteFailed"), {
        description: error instanceof Error ? error.message : undefined,
      });
    }
  }, [load, t]);

  return (
    <section className="card-shadow flex min-h-0 flex-col rounded-2xl border border-border bg-card p-5">
      <header className="flex items-center justify-between gap-3 border-b border-border/60 pb-3">
        <div className="flex items-center gap-2">
          <Link className="size-4 text-muted-foreground" aria-hidden="true" />
          <h3 className="text-base font-semibold tracking-tight text-foreground">{t("theory.title")}</h3>
        </div>
        <p className="text-xs text-muted-foreground">{t("theory.hint")}</p>
      </header>

      <div className="overflow-y-auto min-h-0">
        {links.length === 0 ? (
          <div className="flex h-48 flex-col items-center justify-center gap-3 p-8 text-center text-muted-foreground">
            <p>{t("theory.empty")}</p>
          </div>
        ) : (
          <ul className="space-y-2.5 py-1">
            {links.map((link) => (
              <li key={link.id} className="flex items-center gap-3 p-3 rounded-lg border border-border/50 bg-muted/50">
                <div className="min-w-0 flex-1">
                  <p className="text-sm font-medium text-foreground truncate">{link.title}</p>
                  <a
                    href={link.url}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="text-xs text-brand hover:underline truncate"
                    title={link.url}
                  >
                    {link.url}
                  </a>
                  {link.description && (
                    <p className="mt-0.5 text-[11px] text-muted-foreground line-clamp-2">{link.description}</p>
                  )}
                </div>
                <button
                  type="button"
                  onClick={() => void handleDelete(link.id)}
                  aria-label={t("theory.remove")}
                  className="text-muted-foreground hover:text-destructive transition-colors p-1"
                >
                  <Trash2 className="size-4" />
                </button>
              </li>
            ))}
          </ul>
        )}
      </div>

      <form
        onSubmit={(e) => {
          e.preventDefault();
          void handleAdd();
        }}
        className="mt-4 flex flex-col gap-3 border-t border-border/60 pt-3"
      >
        <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
          <Input
            placeholder={t("theory.titlePlaceholder")}
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            disabled={adding}
          />
          <Input
            placeholder={t("theory.urlPlaceholder")}
            value={url}
            onChange={(e) => setUrl(e.target.value)}
            disabled={adding}
          />
        </div>
        <textarea
          placeholder={t("theory.descriptionPlaceholder")}
          value={description}
          onChange={(e) => setDescription(e.target.value)}
          disabled={adding}
          className="min-h-[60px] w-full border border-input bg-input/40 rounded-lg px-3 py-2 text-sm text-foreground disabled:bg-muted/40 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        />
        <Button type="submit" disabled={adding || !title.trim() || !url.trim()}>
          {adding ? t("theory.adding") : t("theory.add")}
        </Button>
      </form>
    </section>
  );
}
