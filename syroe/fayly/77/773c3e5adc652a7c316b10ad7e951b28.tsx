"use client";
import { motion } from "framer-motion";
import { Upload, Settings, BarChart3 } from "lucide-react";

const steps = [
  {
    num: "01",
    icon: Upload,
    title: "Загрузите видео",
    desc: "Вставьте YouTube-ссылку или загрузите файл напрямую. Поддерживаем видео до 20 минут.",
  },
  {
    num: "02",
    icon: Settings,
    title: "Настройте анализ",
    desc: "Выберите из 75+ параметров: текст, аудио, визуальные данные. Используйте пресеты или создайте свою конфигурацию.",
  },
  {
    num: "03",
    icon: BarChart3,
    title: "Получите прогноз",
    desc: "Детальный отчёт с Virality Score, предсказанием просмотров и лайков на 7, 14 и 21 день.",
  },
];

export function HowItWorks() {
  return (
    <section id="how-it-works" className="py-24 border-t border-[--border]">
      <div className="max-w-7xl mx-auto px-6">
        <motion.div
          initial={{ opacity: 0, y: 20 }}
          whileInView={{ opacity: 1, y: 0 }}
          viewport={{ once: true }}
          transition={{ duration: 0.5, ease: [0.16, 1, 0.3, 1] }}
          className="text-center mb-16"
        >
          <h2 className="text-4xl font-bold mb-4">Как это работает</h2>
          <p className="text-[--text-secondary] text-lg max-w-xl mx-auto">
            От загрузки видео до прогноза — за 3 простых шага
          </p>
        </motion.div>

        <div className="grid grid-cols-1 md:grid-cols-3 gap-8 relative">
          {/* Соединительная линия (только десктоп) */}
          <div className="hidden md:block absolute top-12 left-[33%] right-[33%] h-px border-t border-dashed border-[--border]" />

          {steps.map((step, i) => (
            <motion.div
              key={step.num}
              initial={{ opacity: 0, y: 20 }}
              whileInView={{ opacity: 1, y: 0 }}
              viewport={{ once: true }}
              transition={{ duration: 0.5, ease: [0.16, 1, 0.3, 1], delay: i * 0.1 }}
              className="flex flex-col items-center text-center gap-4"
            >
              <div className="relative">
                {/* Номер шага */}
                <div className="text-xs font-mono font-bold text-[--accent] mb-3 tracking-widest">
                  {step.num}
                </div>
                {/* Иконка */}
                <div className="w-14 h-14 rounded-xl bg-[--accent-subtle] border border-[--accent-border] flex items-center justify-center mx-auto glow-accent">
                  <step.icon size={24} className="text-[--accent]" />
                </div>
              </div>
              <div>
                <h3 className="text-lg font-semibold mb-2">{step.title}</h3>
                <p className="text-sm text-[--text-secondary] leading-relaxed">{step.desc}</p>
              </div>
            </motion.div>
          ))}
        </div>
      </div>
    </section>
  );
}
