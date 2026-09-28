"use client";
import * as React from "react";
import { cn } from "@/lib/utils/cn";
import { Loader2 } from "lucide-react";

export interface ButtonProps extends React.ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: "default" | "secondary" | "ghost" | "destructive" | "gradient" | "outline";
  size?: "sm" | "md" | "lg" | "icon";
  loading?: boolean;
}

const variantClasses: Record<string, string> = {
  default: "bg-[--accent] hover:bg-[--accent-hover] active:bg-[--accent-active] text-white shadow-[0_0_20px_rgba(124,58,237,0.2)] hover:shadow-[0_0_30px_rgba(124,58,237,0.35)]",
  gradient: "bg-gradient-to-r from-[#7c3aed] to-[#a855f7] hover:from-[#6d28d9] hover:to-[#9333ea] text-white shadow-[0_0_30px_rgba(124,58,237,0.3)] hover:shadow-[0_0_40px_rgba(124,58,237,0.45)]",
  secondary: "bg-[var(--surface)] hover:bg-[var(--surface-hover)] border border-[--border] hover:border-[--border-strong] text-[--text-primary]",
  outline: "border border-[--border] hover:border-[--border-strong] hover:bg-[var(--surface-hover)] text-[--text-secondary] hover:text-[--text-primary]",
  ghost: "text-[--text-secondary] hover:text-[--text-primary] hover:bg-[var(--surface-hover)]",
  destructive: "bg-red-500/10 hover:bg-red-500/20 border border-red-500/20 hover:border-red-500/40 text-red-400",
};

const sizeClasses: Record<string, string> = {
  sm: "px-3 py-1.5 text-xs rounded-[--radius-md] gap-1.5",
  md: "px-4 py-2 text-sm rounded-[--radius-md] gap-2",
  lg: "px-6 py-3 text-base rounded-[--radius-lg] gap-2.5",
  icon: "w-9 h-9 rounded-[--radius-md] p-0",
};

export const Button = React.forwardRef<HTMLButtonElement, ButtonProps>(
  ({ className, variant = "default", size = "md", loading, disabled, children, ...props }, ref) => {
    return (
      <button
        ref={ref}
        disabled={disabled || loading}
        className={cn(
          "inline-flex items-center justify-center font-medium",
          "transition-all duration-150",
          "hover:scale-[1.02] active:scale-[0.98]",
          "disabled:opacity-50 disabled:cursor-not-allowed disabled:hover:scale-100",
          "focus-visible:outline-2 focus-visible:outline-[--accent] focus-visible:outline-offset-2",
          variantClasses[variant],
          sizeClasses[size],
          className
        )}
        {...props}
      >
        {loading ? <Loader2 className="animate-spin" size={size === "sm" ? 12 : 16} /> : null}
        {children}
      </button>
    );
  }
);

Button.displayName = "Button";
