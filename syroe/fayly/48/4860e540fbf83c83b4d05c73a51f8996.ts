import { type ClassValue, clsx } from "clsx";
import { twMerge } from "tailwind-merge";

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

export function formatNumber(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}М`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}к`;
  return n.toLocaleString("ru");
}

export function formatCredits(n: number): string {
  return n.toLocaleString("ru") + " кр.";
}

export function scoreColor(score: number): string {
  if (score >= 70) return "text-green-400";
  if (score >= 40) return "text-yellow-400";
  return "text-red-400";
}

export function scoreBg(score: number): string {
  if (score >= 70) return "bg-green-400/10 border-green-400/20 text-green-400";
  if (score >= 40) return "bg-yellow-400/10 border-yellow-400/20 text-yellow-400";
  return "bg-red-400/10 border-red-400/20 text-red-400";
}

export function scoreLabel(score: number): string {
  if (score >= 80) return "Высокий потенциал";
  if (score >= 60) return "Хороший потенциал";
  if (score >= 40) return "Средний потенциал";
  if (score >= 20) return "Низкий потенциал";
  return "Очень низкий потенциал";
}
