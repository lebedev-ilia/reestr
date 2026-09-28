import * as React from "react";
import { cn } from "@/lib/utils/cn";

interface BadgeProps extends React.HTMLAttributes<HTMLSpanElement> {
  variant?: "default" | "success" | "warning" | "error" | "info" | "muted";
}

const variantClasses: Record<string, string> = {
  default: "bg-[--accent-subtle] text-[--accent] border-[--accent-border]",
  success: "bg-green-400/10 text-green-400 border-green-400/20",
  warning: "bg-yellow-400/10 text-yellow-400 border-yellow-400/20",
  error: "bg-red-400/10 text-red-400 border-red-400/20",
  info: "bg-blue-400/10 text-blue-400 border-blue-400/20",
  muted: "bg-[var(--surface)] text-[--text-muted] border-[--border]",
};

export function Badge({ className, variant = "default", children, ...props }: BadgeProps) {
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1 px-2.5 py-0.5",
        "text-xs font-medium rounded-full border",
        variantClasses[variant],
        className
      )}
      {...props}
    >
      {children}
    </span>
  );
}
