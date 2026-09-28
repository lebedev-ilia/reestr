"use client";
import { Canvas } from "@react-three/fiber";
import { Float, Html, PerspectiveCamera } from "@react-three/drei";
import { useEffect, useState } from "react";
import { motion, AnimatePresence } from "framer-motion";

type CardState = "upload" | "processing" | "result";

function AnimatedCard() {
  const [state, setState] = useState<CardState>("upload");
  const [progress, setProgress] = useState(0);
  const [score] = useState(78);

  useEffect(() => {
    const t1 = setTimeout(() => setState("processing"), 1800);
    const t2 = setTimeout(() => setState("result"), 4500);
    return () => { clearTimeout(t1); clearTimeout(t2); };
  }, []);

  useEffect(() => {
    if (state !== "processing") return;
    const interval = setInterval(() => {
      setProgress((p) => (p >= 100 ? 100 : p + 4));
    }, 100);
    return () => clearInterval(interval);
  }, [state]);

  return (
    <Html center distanceFactor={6}>
      <div
        className="w-72 rounded-2xl border border-[--border] bg-[--bg-elevated] shadow-2xl overflow-hidden"
        style={{ fontFamily: "Inter, sans-serif" }}
      >
        {/* Header */}
        <div className="px-4 py-3 border-b border-[--border] flex items-center gap-2">
          <div className="w-2 h-2 rounded-full bg-[--accent] animate-pulse" />
          <span className="text-xs font-medium" style={{ color: "var(--text-secondary)" }}>
            TrendFlow · Анализ
          </span>
        </div>

        <div className="p-4 min-h-48">
          <AnimatePresence mode="wait">
            {state === "upload" && (
              <motion.div
                key="upload"
                initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}
                className="flex flex-col items-center justify-center h-40 gap-3"
              >
                <div className="w-12 h-12 rounded-xl bg-[--accent-subtle] border border-[--accent-border] flex items-center justify-center">
                  <svg viewBox="0 0 24 24" fill="none" stroke="#7c3aed" strokeWidth="2" className="w-6 h-6">
                    <path d="M21 15v4a2 2 0 01-2 2H5a2 2 0 01-2-2v-4M17 8l-5-5-5 5M12 3v12" strokeLinecap="round" strokeLinejoin="round" />
                  </svg>
                </div>
                <div className="text-center">
                  <div className="text-sm font-medium" style={{ color: "var(--text-primary)" }}>
                    youtube.com/watch?v=...
                  </div>
                  <div className="text-xs mt-1" style={{ color: "var(--text-muted)" }}>
                    Загрузка видео...
                  </div>
                </div>
              </motion.div>
            )}

            {state === "processing" && (
              <motion.div
                key="processing"
                initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}
                className="space-y-3"
              >
                <div className="text-xs font-medium mb-3" style={{ color: "var(--text-secondary)" }}>
                  Анализируем видео...
                </div>
                {["VisualProcessor", "AudioProcessor", "TextProcessor"].map((name, i) => (
                  <div key={name}>
                    <div className="flex justify-between mb-1">
                      <span className="text-xs" style={{ color: "var(--text-muted)" }}>{name}</span>
                      <span className="text-xs font-mono" style={{ color: "var(--accent)" }}>
                        {Math.min(100, progress + (i === 0 ? 0 : i === 1 ? -20 : -40))}%
                      </span>
                    </div>
                    <div className="h-1 rounded-full" style={{ background: "var(--bg-hover)" }}>
                      <motion.div
                        className="h-full rounded-full"
                        style={{ background: "var(--accent)" }}
                        animate={{ width: `${Math.min(100, progress + (i === 0 ? 0 : i === 1 ? -20 : -40))}%` }}
                        transition={{ duration: 0.3 }}
                      />
                    </div>
                  </div>
                ))}
              </motion.div>
            )}

            {state === "result" && (
              <motion.div
                key="result"
                initial={{ opacity: 0, scale: 0.95 }}
                animate={{ opacity: 1, scale: 1 }}
                className="flex flex-col items-center gap-4"
              >
                <div className="text-xs font-medium" style={{ color: "var(--text-secondary)" }}>
                  Virality Score
                </div>
                {/* Score ring */}
                <div className="relative w-24 h-24">
                  <svg viewBox="0 0 100 100" className="w-full h-full -rotate-90">
                    <circle cx="50" cy="50" r="42" stroke="var(--border)" fill="none" strokeWidth="8" />
                    <motion.circle
                      cx="50" cy="50" r="42" fill="none" strokeWidth="8"
                      stroke="#7c3aed" strokeLinecap="round"
                      initial={{ strokeDasharray: "0 264" }}
                      animate={{ strokeDasharray: `${score * 2.64} ${264 - score * 2.64}` }}
                      transition={{ duration: 1.2, ease: [0.16, 1, 0.3, 1] }}
                    />
                  </svg>
                  <div className="absolute inset-0 flex flex-col items-center justify-center">
                    <motion.span
                      className="font-mono text-2xl font-bold"
                      style={{ color: "var(--text-primary)", fontFamily: "JetBrains Mono, monospace" }}
                      initial={{ opacity: 0 }}
                      animate={{ opacity: 1 }}
                      transition={{ delay: 0.5 }}
                    >
                      {score}
                    </motion.span>
                  </div>
                </div>
                <div className="w-full space-y-1">
                  {[
                    { label: "7 дней", views: "12.4к", likes: "890" },
                    { label: "21 день", views: "41.2к", likes: "3.4к" },
                  ].map((p) => (
                    <div key={p.label} className="flex justify-between text-xs px-2 py-1 rounded-md" style={{ background: "var(--surface)" }}>
                      <span style={{ color: "var(--text-muted)" }}>{p.label}</span>
                      <span style={{ color: "var(--text-secondary)", fontFamily: "JetBrains Mono, monospace" }}>
                        {p.views} 👁 · {p.likes} 👍
                      </span>
                    </div>
                  ))}
                </div>
              </motion.div>
            )}
          </AnimatePresence>
        </div>
      </div>
    </Html>
  );
}

export default function HeroCard3DInner() {
  return (
    <div className="w-[480px] h-[420px]">
      <Canvas>
        <PerspectiveCamera makeDefault position={[0, 0, 5]} fov={45} />
        <ambientLight intensity={0.4} />
        <pointLight position={[5, 5, 5]} intensity={0.8} color="#7c3aed" />
        <pointLight position={[-5, -3, 2]} intensity={0.3} color="#a855f7" />

        <Float speed={1.5} rotationIntensity={0.15} floatIntensity={0.6}>
          <AnimatedCard />
        </Float>
      </Canvas>
    </div>
  );
}
