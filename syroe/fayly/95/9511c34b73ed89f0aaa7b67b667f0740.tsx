"use client";
import { motion } from "framer-motion";
import { FileText, Music, Video, TrendingUp, GitCompare, Lightbulb } from "lucide-react";

const features = [
  {
    icon: FileText,
    title: "Анализ текста",
    desc: "22 компонента: ASR транскрипция, тональность, читаемость заголовка, эмбеддинги описания.",
    tag: "TextProcessor",
  },
  {
    icon: Music,
    title: "Анализ аудио",
    desc: "24 компонента: диаризация спикеров, эмоции, ритм, тональность музыки, качество записи.",
    tag: "AudioProcessor",
  },
  {
    icon: Video,
    title: "Анализ видео",
    desc: "29 компонентов: детекция объектов, лиц, брендов, качество кадра, смены сцен, движение.",
    tag: "VisualProcessor",
  },
  {
    icon: TrendingUp,
    title: "Прогноз популярности",
    desc: "Предсказываем просмотры и лайки на 7, 14 и 21 день с confidence interval.",
    tag: "ML Models",
  },
  {
    icon: GitCompare,
    title: "Сравнение с похожими",
    desc: "Similarity metrics через CLIP embeddings. Сравниваем с топ-5 похожих видео по всем модальностям.",
    tag: "Analytics",
  },
  {
    icon: Lightbulb,
    title: "Персональные рекомендации",
    desc: "Конкретные советы по улучшению с ожидаемым эффектом. Приоритизированный список действий.",
    tag: "AI Insights",
  },
];

export function FeaturesGrid() {
  return (
    <section id="features" className="py-24 border-t border-[--border]">
      <div className="max-w-7xl mx-auto px-6">
        <motion.div
          initial={{ opacity: 0, y: 20 }}
          whileInView={{ opacity: 1, y: 0 }}
          viewport={{ once: true }}
          transition={{ duration: 0.5, ease: [0.16, 1, 0.3, 1] }}
          className="text-center mb-16"
        >
          <h2 className="text-4xl font-bold mb-4">Возможности</h2>
          <p className="text-[--text-secondary] text-lg max-w-xl mx-auto">
            Мультимодальный анализ — текст, аудио и видео одновременно
          </p>
        </motion.div>

        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
          {features.map((feature, i) => (
            <motion.div
              key={feature.title}
              initial={{ opacity: 0, y: 16 }}
              whileInView={{ opacity: 1, y: 0 }}
              viewport={{ once: true }}
              transition={{ duration: 0.4, ease: [0.16, 1, 0.3, 1], delay: i * 0.06 }}
              className="card card-accent p-6 group cursor-default"
            >
              <div className="flex items-start gap-4">
                <div className="w-10 h-10 rounded-lg bg-[--accent-subtle] border border-[--accent-border] flex items-center justify-center flex-shrink-0 group-hover:glow-accent transition-all">
                  <feature.icon size={18} className="text-[--accent]" />
                </div>
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2 mb-2">
                    <h3 className="font-semibold text-sm">{feature.title}</h3>
                  </div>
                  <p className="text-xs text-[--text-secondary] leading-relaxed">{feature.desc}</p>
                  <div className="mt-3">
                    <span className="text-xs font-mono text-[--text-muted] bg-[--surface] px-2 py-0.5 rounded">
                      {feature.tag}
                    </span>
                  </div>
                </div>
              </div>
            </motion.div>
          ))}
        </div>
      </div>
    </section>
  );
}
