import Link from "next/link";
import { TrendingUp } from "lucide-react";

export function LandingFooter() {
  return (
    <footer className="border-t border-[--border] py-12">
      <div className="max-w-7xl mx-auto px-6">
        <div className="flex flex-col md:flex-row justify-between gap-8">
          {/* Brand */}
          <div className="max-w-xs">
            <Link href="/" className="flex items-center gap-2.5 mb-3">
              <div className="w-7 h-7 rounded-lg bg-[--accent-subtle] border border-[--accent-border] flex items-center justify-center">
                <TrendingUp size={14} className="text-[--accent]" />
              </div>
              <span className="font-semibold text-sm">TrendFlow</span>
            </Link>
            <p className="text-xs text-[--text-muted] leading-relaxed">
              AI-платформа для предсказания популярности видеоконтента.
              Мультимодальный анализ: текст, аудио, видео.
            </p>
          </div>

          {/* Links */}
          <div className="grid grid-cols-2 md:grid-cols-3 gap-8">
            {[
              {
                title: "Продукт",
                links: [
                  { label: "Как работает", href: "#how-it-works" },
                  { label: "Возможности", href: "#features" },
                  { label: "Цены", href: "#pricing" },
                  { label: "Статус", href: "/status" },
                ],
              },
              {
                title: "Поддержка",
                links: [
                  { label: "Документация", href: "#" },
                  { label: "API", href: "#" },
                  { label: "Telegram", href: "#" },
                ],
              },
              {
                title: "Юридическое",
                links: [
                  { label: "Политика конфиденциальности", href: "#" },
                  { label: "Условия использования", href: "#" },
                ],
              },
            ].map((group) => (
              <div key={group.title}>
                <div className="text-xs font-semibold text-[--text-primary] mb-3 uppercase tracking-wider">
                  {group.title}
                </div>
                <ul className="space-y-2">
                  {group.links.map((link) => (
                    <li key={link.label}>
                      <Link
                        href={link.href}
                        className="text-xs text-[--text-muted] hover:text-[--text-secondary] transition-colors"
                      >
                        {link.label}
                      </Link>
                    </li>
                  ))}
                </ul>
              </div>
            ))}
          </div>
        </div>

        <div className="mt-10 pt-6 border-t border-[--border] flex flex-col md:flex-row justify-between items-center gap-4">
          <p className="text-xs text-[--text-muted]">
            © 2026 TrendFlow. Все права защищены.
          </p>
          <p className="text-xs text-[--text-muted]">
            Сделано с ❤️ для YouTube-создателей
          </p>
        </div>
      </div>
    </footer>
  );
}
