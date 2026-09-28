"use client";
import dynamic from "next/dynamic";
import { useEffect, useState } from "react";

// Lazy load Three.js — только на десктопе
const HeroCard3DInner = dynamic(() => import("./hero-card-inner"), {
  ssr: false,
  loading: () => <HeroCardFallback />,
});

function HeroCardFallback() {
  return (
    <div className="w-80 h-80 rounded-2xl border border-[--border] bg-[--bg-elevated] flex items-center justify-center animate-pulse">
      <div className="text-[--text-muted] text-sm">Загрузка...</div>
    </div>
  );
}

export function HeroCard3D() {
  const [isMobile, setIsMobile] = useState(false);

  useEffect(() => {
    const check = () => setIsMobile(window.innerWidth < 1024);
    check();
    window.addEventListener("resize", check);
    return () => window.removeEventListener("resize", check);
  }, []);

  if (isMobile) return null;

  return <HeroCard3DInner />;
}
