"use client";
import Link from "next/link";
import { Button } from "@/components/ui/button";
import { TrendingUp } from "lucide-react";

export function LandingNav() {
  return (
    <nav className="fixed top-0 left-0 right-0 z-50 glass border-b border-[--border]">
      <div className="max-w-7xl mx-auto px-6 h-16 flex items-center justify-between">
        {/* Logo */}
        <Link href="/" className="flex items-center gap-2.5 group">
          <div className="w-8 h-8 rounded-lg bg-[--accent-subtle] border border-[--accent-border] flex items-center justify-center group-hover:glow-accent transition-all">
            <TrendingUp size={16} className="text-[--accent]" />
          </div>
          <span className="font-semibold text-base tracking-tight">TrendFlow</span>
        </Link>

        {/* Nav links */}
        <div className="hidden md:flex items-center gap-6">
          {[
            { label: "Как работает", href: "#how-it-works" },
            { label: "Возможности", href: "#features" },
            { label: "Цены", href: "#pricing" },
          ].map((link) => (
            <Link
              key={link.href}
              href={link.href}
              className="text-sm text-[--text-secondary] hover:text-[--text-primary] transition-colors duration-150"
            >
              {link.label}
            </Link>
          ))}
        </div>

        {/* CTA */}
        <div className="flex items-center gap-3">
          <Link href="/login">
            <Button variant="ghost" size="sm">Войти</Button>
          </Link>
          <Link href="/login">
            <Button size="sm">Начать бесплатно</Button>
          </Link>
        </div>
      </div>
    </nav>
  );
}
