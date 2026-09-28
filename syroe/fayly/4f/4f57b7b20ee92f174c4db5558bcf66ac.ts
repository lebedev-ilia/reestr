import axios from "axios";

const API_URL = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

export const api = axios.create({
  baseURL: API_URL,
  headers: { "Content-Type": "application/json" },
  withCredentials: true,
});

// Добавляем токен из localStorage к каждому запросу
api.interceptors.request.use((config) => {
  const token = typeof window !== "undefined" ? localStorage.getItem("tf_token") : null;
  if (token) config.headers.Authorization = `Bearer ${token}`;
  return config;
});

// Обрабатываем 401 — редирект на логин
api.interceptors.response.use(
  (res) => res,
  (error) => {
    if (error.response?.status === 401 && typeof window !== "undefined") {
      localStorage.removeItem("tf_token");
      window.location.href = "/login";
    }
    return Promise.reject(error);
  }
);

export const WS_URL = process.env.NEXT_PUBLIC_WS_URL || "ws://localhost:8000";
