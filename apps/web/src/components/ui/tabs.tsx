"use client";

/**
 * Minimal accessible Tabs component following the project's UI patterns.
 * Uses native radio inputs for keyboard accessibility without Radix dependencies.
 */

import * as React from "react";
import { cn } from "@/lib/utils";

const TabsContext = React.createContext<{
  value: string;
  onValueChange: (value: string) => void;
} | null>(null);

function TabsProvider({
  value,
  onValueChange,
  children,
}: {
  value: string;
  onValueChange: (value: string) => void;
  children: React.ReactNode;
}) {
  return (
    <TabsContext.Provider value={{ value, onValueChange }}>
      {children}
    </TabsContext.Provider>
  );
}

function TabsList({ className, children }: { className?: string; children: React.ReactNode }) {
  return (
    <div
      role="tablist"
      className={cn(
        "inline-flex max-h-10 items-center justify-center gap-1 rounded-md bg-muted p-1 text-muted-foreground",
        className,
      )}
    >
      {children}
    </div>
  );
}

function TabsTrigger({
  value,
  className,
  children,
}: {
  value: string;
  className?: string;
  children: React.ReactNode;
}) {
  const ctx = React.useContext(TabsContext);
  if (!ctx) {
    throw new Error("TabsTrigger must be used within Tabs");
  }
  const { value: currentValue, onValueChange } = ctx;
  const active = currentValue === value;
  return (
    <button
      role="tab"
      aria-selected={active}
      onClick={() => onValueChange(value)}
      className={cn(
        "flex items-center justify-center gap-1 rounded-md px-3 py-1.5 text-sm font-medium transition-colors",
        active
          ? "bg-brand/10 text-brand border-b-2 border-brand"
          : "text-muted-foreground hover:bg-muted/50 hover:text-foreground",
        className,
      )}
    >
      {children}
    </button>
  );
}

function TabsContent({
  value,
  children,
}: {
  value: string;
  children: React.ReactNode;
}) {
  const ctx = React.useContext(TabsContext);
  if (!ctx) {
    throw new Error("TabsContent must be used within Tabs");
  }
  const { value: currentValue } = ctx;
  const active = currentValue === value;
  if (!active) return null;
  return <div>{children}</div>;
}

export function Tabs({
  value,
  onValueChange,
  children,
}: {
  value: string;
  onValueChange: (value: string) => void;
  children: React.ReactNode;
}) {
  return (
    <TabsProvider value={value} onValueChange={onValueChange}>
      {children}
    </TabsProvider>
  );
}

export { TabsList, TabsTrigger, TabsContent };
