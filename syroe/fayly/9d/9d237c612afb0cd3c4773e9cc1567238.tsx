"use client";
import { motion } from "framer-motion";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Play, ArrowRight, Sparkles } from "lucide-react";
import { HeroCard3D } from "@/components/three/hero-card";

function fadeInVariants(delay = 0) {
  return {
    hidden: { opacity: 0, y: 20 },
    visible: {
      opacity: 1,
      y: 0,
      transition: { duration: 0.5, ease: "easeOut" as const, delay },
    },
  };
}

export function HeroSection() {
  return (
    <section className="relative min-h-screen flex items-center overflow-hidden">
      {/* Фоновый градиент */}
      <div className="absolute inset-0 bg-gradient-hero pointer-events-none" />

      {/* Фоновые частицы */}
      <div className="absolute inset-0 overflow-hidden pointer-events-none">
        {Array.from({ length: 20 }).map((_, i) => (
          <div
            key={i}
            className="absolute w-1 h-1 bg-[--accent] rounded-full opacity-20 animate-pulse-slow"
            style={{
              left: `${Math.random() * 100}%`,
              top: `${Math.random() * 100}%`,
              animationDelay: `${Math.random() * 4}s`,
              animationDuration: `${3 + Math.random() * 4}s`,
            }}
          />
        ))}
      </div>

      <div className="relative z-10 max-w-7xl mx-auto px-6 py-24 grid grid-cols-1 lg:grid-cols-2 gap-16 items-center">
        {/* Левая колонка — текст */}
        <div className="space-y-8">
          <motion.div variants={fadeInVariants(0)} initial="hidden" animate="visible">
            <Badge variant="default" className="gap-2 text-xs uppercase tracking-widest py-1 px-3">
              <Sparkles size={10} />
              Предсказание популярности · Powered by AI
            </Badge>
          </motion.div>

          <motion.h1
            variants={fadeInVariants(0.1)}
            initial="hidden"
            animate="visible"
            className="text-5xl lg:text-6xl font-bold leading-[1.05] max-w-xl"
          >
            Узнайте, станет ли ваше видео{" "}
            <span className="gradient-text">вирусным</span>
            {" "}— до публикации
          </motion.h1>

          <motion.p
            variants={fadeInVariants(0.2)}
            initial="hidden"
            animate="visible"
            className="text-lg text-[--text-secondary] leading-relaxed max-w-lg"
          >
            TrendFlow анализирует видео по 75+ параметрам и предсказывает просмотры и лайки
            через 7, 14 и 21 день с помощью мультимодального ИИ.
          </motion.p>

          <motion.div
            variants={fadeInVariants(0.3)}
            initial="hidden"
            animate="visible"
            className="flex flex-col sm:flex-row gap-3"
          >
            <Button variant="gradient" size="lg">
              Попробовать бесплатно
              <ArrowRight size={18} />
            </Button>
            <Button variant="ghost" size="lg">
              <Play size={16} className="text-[--accent]" />
              Смотреть демо
            </Button>
          </motion.div>

          <motion.p
            variants={fadeInVariants(0.4)}
            initial="hidden"
            animate="visible"
            className="text-xs text-[--text-muted]"
          >
            14 дней бесплатно · Без кредитной карты · Invite-only доступ
          </motion.p>

          {/* Статистика */}
          <motion.div
            variants={fadeInVariants(0.5)}
            initial="hidden"
            animate="visible"
            className="grid grid-cols-3 gap-6 pt-4 border-t border-[--border]"
          >
            {[
              { value: "75+", label: "параметров" },
              { value: "3", label: "модальности" },
              { value: "21д", label: "горизонт прогноза" },
            ].map((stat) => (
              <div key={stat.label}>
                <div className="font-mono text-2xl font-bold text-[--text-primary]">
                  {stat.value}
                </div>
                <div className="text-xs text-[--text-muted] mt-0.5">{stat.label}</div>
              </div>
            ))}
          </motion.div>
        </div>

        {/* Правая колонка — 3D карточка */}
        <motion.div
          initial={{ opacity: 0, x: 40 }}
          animate={{ opacity: 1, x: 0 }}
          transition={{ duration: 0.8, ease: [0.16, 1, 0.3, 1], delay: 0.3 }}
          className="hidden lg:flex items-center justify-center"
        >
          <HeroCard3D />
        </motion.div>
      </div>
    </section>
  );
}
