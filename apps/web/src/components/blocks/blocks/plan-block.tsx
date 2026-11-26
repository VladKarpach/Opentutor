"use client";

import { PlanSection } from "@/components/sections/plan-section";
import type { BlockComponentProps } from "@/lib/block-system/registry";

export default function PlanBlock({ courseId }: BlockComponentProps) {
  return <PlanSection courseId={courseId} />;
}
