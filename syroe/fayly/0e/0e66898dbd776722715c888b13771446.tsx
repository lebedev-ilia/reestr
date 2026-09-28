import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "TrendFlow — Предсказание популярности видео на основе ИИ",
  description:
    "Загрузите видео и узнайте его потенциал до публикации. Мультимодальный анализ: текст, аудио, видео. Прогноз просмотров и лайков на 7, 14 и 21 день.",
  openGraph: {
    title: "TrendFlow",
    description: "AI-предсказание популярности видео",
    locale: "ru_RU",
    type: "website",
  },
};

export default function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="ru" className="h-full" style={{ colorScheme: "dark" }}>
      <body className="min-h-full bg-[--bg] text-[--text-primary] antialiased">
        {children}
      </body>
    </html>
  );
}
