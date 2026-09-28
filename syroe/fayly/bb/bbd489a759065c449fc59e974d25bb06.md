# TrendFlow — Design System
> Полная дизайн-спецификация для Claude AI. Читать перед написанием ЛЮБОГО UI компонента.
> Версия: 1.0 | Стиль: Dark OLED + Minimalist Tech | Акцент: Фиолетовый

---

## 1. ЯДРО ДИЗАЙНА

### Философия
**"Data-first, human-readable"** — интерфейс исчезает, данные говорят сами за себя.
- Тёмный фон → контент в фокусе
- Фиолетовые акценты → не кричащие, а направляющие
- Каждая анимация объясняет причинно-следственную связь, не украшает

### Стиль: Dark OLED + Minimalist SaaS
Категория: `Dark Mode (OLED)` с элементами `Drill-Down Analytics Dashboard`
Подтверждено: UI Pro Max → style "dark tech analytics SaaS" | product "analytics dashboard"

---

## 2. ЦВЕТОВАЯ СИСТЕМА

### 2.1 Токены (CSS Custom Properties)

```css
:root {
  /* ─── Фоны ─────────────────────────────────── */
  --bg:             #09090b;   /* Основной фон (почти чёрный, не #000) */
  --bg-elevated:    #0f0f11;   /* Карточки, панели */
  --bg-overlay:     #141416;   /* Модалки, dropdown */
  --bg-hover:       #18181b;   /* Hover состояние элементов */

  /* ─── Поверхности ───────────────────────────── */
  --surface:        rgba(255,255,255,0.03);  /* Тонкие карточки */
  --surface-hover:  rgba(255,255,255,0.06);
  --surface-active: rgba(255,255,255,0.09);

  /* ─── Текст ─────────────────────────────────── */
  --text-primary:   #fafafa;   /* Заголовки, важный текст */
  --text-secondary: #a1a1aa;   /* Описания, подписи */
  --text-muted:     #52525b;   /* Плейсхолдеры, disabled */
  --text-inverse:   #09090b;   /* Текст на акцентном фоне */

  /* ─── Акцент (Фиолетовый) ───────────────────── */
  --accent:         #7c3aed;   /* Основной акцент */
  --accent-hover:   #6d28d9;   /* Hover */
  --accent-active:  #5b21b6;   /* Active/pressed */
  --accent-subtle:  rgba(124,58,237,0.10); /* Тонкий фон */
  --accent-glow:    rgba(124,58,237,0.25); /* Свечение */
  --accent-border:  rgba(124,58,237,0.30); /* Граница с акцентом */

  /* ─── Бордеры ───────────────────────────────── */
  --border:         #1f1f23;   /* Стандартная граница */
  --border-subtle:  #141416;   /* Очень тонкая */
  --border-strong:  #27272a;   /* Сильная */

  /* ─── Статусы ───────────────────────────────── */
  --success:        #22c55e;
  --success-subtle: rgba(34,197,94,0.10);
  --warning:        #f59e0b;
  --warning-subtle: rgba(245,158,11,0.10);
  --error:          #ef4444;
  --error-subtle:   rgba(239,68,68,0.10);
  --info:           #3b82f6;
  --info-subtle:    rgba(59,130,246,0.10);

  /* ─── Градиенты ─────────────────────────────── */
  --gradient-hero:       linear-gradient(135deg, rgba(124,58,237,0.15) 0%, rgba(9,9,11,0) 60%);
  --gradient-accent:     linear-gradient(90deg, #7c3aed, #a855f7);
  --gradient-card:       linear-gradient(145deg, #141416, #0f0f11);
  --gradient-glow-card:  linear-gradient(145deg, rgba(124,58,237,0.05), transparent);

  /* ─── Тени ──────────────────────────────────── */
  --shadow-sm:   0 1px 2px rgba(0,0,0,0.5);
  --shadow-md:   0 4px 12px rgba(0,0,0,0.4), 0 0 0 1px rgba(255,255,255,0.04);
  --shadow-lg:   0 8px 24px rgba(0,0,0,0.5), 0 0 0 1px rgba(255,255,255,0.06);
  --shadow-accent: 0 0 20px rgba(124,58,237,0.20), 0 0 40px rgba(124,58,237,0.08);

  /* ─── Радиус скруглений ─────────────────────── */
  --radius-sm:   6px;
  --radius-md:   10px;
  --radius-lg:   14px;
  --radius-xl:   20px;
  --radius-full: 9999px;

  /* ─── Переходы ──────────────────────────────── */
  --transition-fast:   150ms ease-out;
  --transition-base:   250ms ease-out;
  --transition-slow:   400ms ease-out;
  --spring:            cubic-bezier(0.16, 1, 0.3, 1);  /* Framer spring feel */
}
```

### 2.2 Семантика использования

| Ситуация | Токен |
|---|---|
| Фон страницы | `--bg` |
| Карточки, sidebar | `--bg-elevated` |
| Dropdown, tooltip, modal | `--bg-overlay` |
| Основной текст | `--text-primary` |
| Второстепенный текст | `--text-secondary` |
| Метки, placeholder | `--text-muted` |
| Кнопка CTA, ссылки | `--accent` |
| Тонкая граница | `--border` |
| Акцентная граница при focus | `--accent-border` |

---

## 3. ТИПОГРАФИКА

### 3.1 Шрифты
```
Основной: Inter (variable font)
Монospace: JetBrains Mono
```

**Google Fonts import:**
```css
@import url('https://fonts.googleapis.com/css2?family=Inter:ital,opsz,wght@0,14..32,100..900&family=JetBrains+Mono:wght@400;500;600&display=swap');
```

**Tailwind config:**
```js
fontFamily: {
  sans: ['Inter', 'system-ui', 'sans-serif'],
  mono: ['JetBrains Mono', 'monospace'],
}
```

### 3.2 Типографическая шкала

| Токен | px | weight | line-height | Применение |
|---|---|---|---|---|
| `text-4xl` | 36px | 700 | 1.1 | H1 лендинга |
| `text-3xl` | 30px | 600 | 1.15 | H2 секции |
| `text-2xl` | 24px | 600 | 1.25 | H3 карточки |
| `text-xl`  | 20px | 500 | 1.3  | H4, заголовки вкладок |
| `text-base`| 15px | 400 | 1.6  | Основной текст |
| `text-sm`  | 13px | 400 | 1.5  | Метки, описания |
| `text-xs`  | 11px | 500 | 1.4  | Бейджи, uppercase метки |

**Правило:** числа, метрики, кредиты, скоры — всегда `font-mono`.

### 3.3 Правила

```css
/* Заголовок с отрицательным трекингом (как Vercel/Linear) */
h1, h2 { letter-spacing: -0.02em; }
h3, h4  { letter-spacing: -0.01em; }

/* Аналитические данные — tabular figures */
.metric, .number, .score { font-variant-numeric: tabular-nums; }

/* Максимальная ширина читаемых параграфов */
p { max-width: 65ch; }
```

---

## 4. КОМПОНЕНТЫ

### 4.1 Кнопки

```tsx
// Основная (CTA)
<Button className="
  bg-[--accent] hover:bg-[--accent-hover] active:bg-[--accent-active]
  text-white font-medium
  px-5 py-2.5 rounded-[--radius-md]
  transition-all duration-150
  shadow-[0_0_20px_rgba(124,58,237,0.2)]
  hover:shadow-[0_0_30px_rgba(124,58,237,0.35)]
  hover:scale-[1.02] active:scale-[0.98]
">

// Вторичная
<Button variant="secondary" className="
  bg-[--surface] hover:bg-[--surface-hover]
  border border-[--border] hover:border-[--border-strong]
  text-[--text-primary]
  transition-all duration-150
">

// Ghost
<Button variant="ghost" className="
  text-[--text-secondary] hover:text-[--text-primary]
  hover:bg-[--surface-hover]
">

// С градиентом (hero CTA)
<Button className="
  bg-gradient-to-r from-[#7c3aed] to-[#a855f7]
  hover:from-[#6d28d9] hover:to-[#9333ea]
  text-white font-semibold
  px-6 py-3 rounded-[--radius-md]
  shadow-[0_0_30px_rgba(124,58,237,0.3)]
  hover:shadow-[0_0_40px_rgba(124,58,237,0.45)]
  transition-all duration-200
  hover:scale-[1.02]
">
```

### 4.2 Карточки

```tsx
// Стандартная карточка дашборда
<div className="
  bg-[--bg-elevated]
  border border-[--border]
  rounded-[--radius-lg]
  p-5
  transition-all duration-200
  hover:border-[--border-strong]
  hover:shadow-[--shadow-md]
">

// Карточка с акцентной hover-рамкой
<div className="
  bg-[--bg-elevated]
  border border-[--border]
  rounded-[--radius-lg]
  p-5
  transition-all duration-200
  hover:border-[--accent-border]
  hover:shadow-[--shadow-accent]
  group
">

// Карточка-метрика (Virality Score, баланс и тд)
<div className="
  bg-gradient-to-br from-[--bg-overlay] to-[--bg-elevated]
  border border-[--border]
  rounded-[--radius-lg]
  p-6
  relative overflow-hidden
">
  {/* Фоновое свечение */}
  <div className="absolute -top-8 -right-8 w-32 h-32 bg-[--accent-glow] rounded-full blur-2xl" />
  ...
</div>
```

### 4.3 Input / Form

```tsx
<Input className="
  bg-[--bg-hover]
  border border-[--border]
  focus:border-[--accent-border]
  focus:ring-2 focus:ring-[--accent-subtle]
  text-[--text-primary]
  placeholder:text-[--text-muted]
  rounded-[--radius-md]
  transition-all duration-150
  h-10 px-3
">
```

### 4.4 Badge / Бейдж

```tsx
// Virality Score Badge
const scoreColor = score >= 70 ? 'text-green-400 bg-green-400/10 border-green-400/20'
                 : score >= 40 ? 'text-yellow-400 bg-yellow-400/10 border-yellow-400/20'
                               : 'text-red-400 bg-red-400/10 border-red-400/20';

<span className={`
  font-mono font-semibold text-sm
  px-2.5 py-0.5
  rounded-full border
  ${scoreColor}
`}>
  {score}
</span>

// Статус анализа
const statusStyles = {
  queued:     'text-zinc-400 bg-zinc-400/10 border-zinc-400/20',
  processing: 'text-blue-400 bg-blue-400/10 border-blue-400/20',
  completed:  'text-green-400 bg-green-400/10 border-green-400/20',
  failed:     'text-red-400 bg-red-400/10 border-red-400/20',
  canceled:   'text-zinc-500 bg-zinc-500/10 border-zinc-500/20',
}
```

### 4.5 Sidebar

```tsx
<aside className="
  w-60 h-screen fixed left-0 top-0
  bg-[--bg-elevated]
  border-r border-[--border]
  flex flex-col
">
  {/* Logo */}
  <div className="px-4 py-5 border-b border-[--border]">
    <Logo />
  </div>

  {/* Nav items */}
  <nav className="flex-1 px-3 py-4 space-y-1 overflow-y-auto">
    <NavItem icon={LayoutDashboard} label="Обзор" href="/dashboard" />
    {/* ... */}
  </nav>

  {/* Credit balance */}
  <div className="px-4 py-3 border-t border-[--border]">
    <div className="text-xs text-[--text-muted] mb-1">Баланс</div>
    <div className="font-mono font-semibold text-[--text-primary]">
      {balance.toLocaleString('ru')} кр.
    </div>
    <div className="mt-2">
      <Button size="sm" className="w-full" variant="outline">
        Пополнить
      </Button>
    </div>
  </div>

  {/* User */}
  <div className="px-4 py-4 border-t border-[--border]">
    <UserMenu />
  </div>
</aside>

// Nav item
<Link href={href} className={cn(
  "flex items-center gap-3 px-3 py-2 rounded-[--radius-md]",
  "text-sm font-medium transition-colors duration-150",
  isActive
    ? "bg-[--accent-subtle] text-[--accent] border border-[--accent-border]"
    : "text-[--text-secondary] hover:text-[--text-primary] hover:bg-[--surface-hover]"
)}>
  <Icon size={16} />
  {label}
</Link>
```

---

## 5. АНИМАЦИИ (Framer Motion)

### 5.1 Базовые variants

```tsx
// Появление снизу (для карточек, секций)
export const fadeInUp = {
  hidden: { opacity: 0, y: 16 },
  visible: {
    opacity: 1,
    y: 0,
    transition: { duration: 0.4, ease: [0.16, 1, 0.3, 1] }
  }
};

// Stagger для списков (delay между элементами)
export const staggerContainer = {
  hidden: {},
  visible: {
    transition: { staggerChildren: 0.08, delayChildren: 0.1 }
  }
};

// Для чисел (CountUp при появлении в viewport)
export const numberReveal = {
  hidden: { opacity: 0, scale: 0.8 },
  visible: {
    opacity: 1,
    scale: 1,
    transition: { type: 'spring', stiffness: 200, damping: 15 }
  }
};

// Страничный переход
export const pageTransition = {
  initial: { opacity: 0 },
  animate: { opacity: 1, transition: { duration: 0.3 } },
  exit:    { opacity: 0, transition: { duration: 0.2 } }
};
```

### 5.2 Правила анимаций

| Тип | Длительность | Easing |
|---|---|---|
| Hover карточки | 150ms | ease-out |
| Появление контента | 400ms | `cubic-bezier(0.16,1,0.3,1)` |
| Модалки | 250ms | spring |
| Страничный переход | 300ms | ease-out |
| Прогресс-бар | 600ms | ease-out |
| Выход (exit) | 200ms | ease-in |

**Обязательно:** всегда учитывать `prefers-reduced-motion`:
```tsx
const prefersReduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
const duration = prefersReduced ? 0 : 0.4;
```

---

## 6. 3D ЛЕНДИНГ (React Three Fiber)

### Концепция
Справа от Hero-текста — парящая 3D-карточка, показывающая процесс анализа (демо).

### Реализация

```tsx
// Структура компонента
<Canvas
  camera={{ position: [0, 0, 5], fov: 45 }}
  style={{ position: 'absolute', right: 0, top: 0 }}
  className="w-[600px] h-[500px]"
  dpr={[1, 2]}
>
  <ambientLight intensity={0.3} />
  <pointLight position={[5, 5, 5]} intensity={0.8} color="#7c3aed" />
  <pointLight position={[-5, -5, 2]} intensity={0.3} color="#a855f7" />

  <Float
    speed={1.5}
    rotationIntensity={0.3}
    floatIntensity={0.8}
    floatingRange={[-0.1, 0.1]}
  >
    <FloatingDashboardCard />
  </Float>

  {/* Фоновые частицы */}
  <ParticleField count={80} color="#7c3aed" />
</Canvas>

// FloatingDashboardCard — показывает анимированное демо UI через <Html>
// Три состояния с автопереходом:
// 1. "Загрузка видео..." (1.5s)
// 2. "Анализ..." + прогресс-бар (2s)
// 3. "Virality Score: 78" + мини-график (остаётся)
```

### Детали карточки
```tsx
<Html center distanceFactor={8}>
  <div className="w-72 rounded-xl bg-[--bg-elevated] border border-[--border] p-4 shadow-2xl">
    {state === 'loading' && <LoadingState />}
    {state === 'processing' && <ProcessingState progress={progress} />}
    {state === 'result' && <ResultCard score={78} />}
  </div>
</Html>
```

### На мобиле — отключить
```tsx
const isMobile = useMediaQuery({ maxWidth: 1024 });
if (isMobile) return <StaticHeroImage />;
```

---

## 7. СТРУКТУРА ЛЕНДИНГА

### Hero Section (детали)

```
Бейдж: "Предсказание популярности · Powered by AI"
  → style: text-xs uppercase tracking-widest, bg-accent-subtle, border-accent-border

H1: "Узнайте, станет ли ваше видео вирусным — до публикации"
  → 3 строки, max-w-xl, font-bold text-4xl tracking-tight

Подзаголовок:
  → text-[--text-secondary] text-lg max-w-lg leading-relaxed

CTA группа:
  → [Попробовать бесплатно]  (gradient button, large)
  → [Смотреть демо →]        (ghost button, с иконкой Play)

Под CTA: "14 дней бесплатно · Без кредитной карты"
  → text-xs text-[--text-muted]
```

### Stats Strip
```
Фон: bg-[--bg-elevated] border-y border-[--border]
4 числа: анимация CountUp при появлении в viewport (Intersection Observer)
Разделители: вертикальная линия border-[--border]
```

### How It Works
```
Заголовок: text-center, H2
3 шага: numbered (01, 02, 03 в акцентном цвете)
Соединительная линия между шагами (SVG dashed line с анимацией stroke-dashoffset)
```

### Features Grid
```
Сетка: grid-cols-3 (desktop), grid-cols-1 (mobile)
Карточки: hover → border-[--accent-border] + shadow-[--shadow-accent]
Иконки: Lucide, 20px, color: --accent
```

### Pricing Section
```
Переключатель Месяц/Год:
  → toggle button group, при выборе Год → badge "Скидка 20%"

3 карточки:
  → Стандарт, Про (highlighted — ring-2 ring-[--accent]), Бизнес

Highlighted Про план:
  → border-[--accent-border], shadow-[--shadow-accent]
  → Бейдж "Популярный" сверху (bg-accent, text-white)
```

---

## 8. СТРАНИЦА РЕЗУЛЬТАТОВ

### Virality Score (главный элемент)

```tsx
// Большой круговой gauge
<div className="relative w-40 h-40">
  {/* SVG circle progress */}
  <svg viewBox="0 0 100 100" className="rotate-[-90deg]">
    <circle cx="50" cy="50" r="42" stroke="var(--border)" fill="none" strokeWidth="8" />
    <circle
      cx="50" cy="50" r="42"
      stroke={scoreGradient}
      fill="none"
      strokeWidth="8"
      strokeLinecap="round"
      strokeDasharray={`${score * 2.64} ${264 - score * 2.64}`}
      style={{ transition: 'stroke-dasharray 1s cubic-bezier(0.16,1,0.3,1)' }}
    />
  </svg>

  {/* Score number */}
  <div className="absolute inset-0 flex flex-col items-center justify-center">
    <span className="font-mono text-4xl font-bold text-[--text-primary]">{score}</span>
    <span className="text-xs text-[--text-muted]">/ 100</span>
  </div>
</div>
```

### Прогноз (3 карточки)

```tsx
<div className="grid grid-cols-3 gap-3">
  {[7, 14, 21].map(days => (
    <div key={days} className="
      bg-[--bg-elevated] border border-[--border]
      rounded-[--radius-lg] p-4
      hover:border-[--accent-border] transition-colors
    ">
      <div className="text-xs text-[--text-muted] mb-2">через {days} дней</div>
      <div className="font-mono text-xl font-semibold">{views}к 👁</div>
      <div className="font-mono text-sm text-[--text-secondary] mt-1">{likes}к 👍</div>
      <MiniLineChart data={data} />
    </div>
  ))}
</div>
```

### Вкладки результатов (Tabs)

```tsx
<Tabs defaultValue="overview">
  <TabsList className="
    bg-[--bg-hover] border border-[--border]
    rounded-[--radius-lg] p-1
  ">
    {['Обзор', 'Контент', 'Аудио', 'Видео', 'Прогноз', 'Рекомендации', 'Сравнение'].map(tab => (
      <TabsTrigger key={tab} className="
        data-[state=active]:bg-[--accent-subtle]
        data-[state=active]:text-[--accent]
        data-[state=active]:border data-[state=active]:border-[--accent-border]
        rounded-[--radius-md]
        transition-all duration-150
      ">
        {tab}
      </TabsTrigger>
    ))}
  </TabsList>
</Tabs>
```

### Карточки компонентов (в вкладках)

```
Layout: 2 колонки на десктопе (metrics + chart)
Левая колонка: название компонента, ключевые метрики (font-mono крупно)
Правая колонка: выбранный тип графика
Под графиком: "Что это значит?" — разворачиваемый текст
Кнопки выбора типа графика: [Бар] [Линия] [Распред] — icon-only с tooltip
```

---

## 9. СТРАНИЦА ПРОГРЕССА

### Live состояние

```tsx
<div className="max-w-3xl mx-auto space-y-6">
  {/* Общий прогресс */}
  <div>
    <div className="flex justify-between mb-2">
      <span className="text-sm text-[--text-secondary]">{currentStage}</span>
      <span className="font-mono text-sm text-[--accent]">{progress}%</span>
    </div>
    <div className="h-1.5 bg-[--bg-hover] rounded-full overflow-hidden">
      <motion.div
        className="h-full bg-gradient-to-r from-[--accent] to-[#a855f7] rounded-full"
        style={{ width: `${progress}%` }}
        transition={{ duration: 0.6, ease: [0.16, 1, 0.3, 1] }}
      />
    </div>
  </div>

  {/* Live кадры */}
  <div className="grid grid-cols-3 gap-3">
    {frames.map((frame, i) => (
      <motion.div
        key={frame.id}
        initial={{ opacity: 0, scale: 0.9 }}
        animate={{ opacity: 1, scale: 1 }}
        className="relative rounded-[--radius-md] overflow-hidden bg-[--bg-elevated] border border-[--border]"
      >
        <img src={frame.url} className="w-full aspect-video object-cover" />
        {frame.bboxes && <BBoxOverlay bboxes={frame.bboxes} />}
        <div className="absolute bottom-0 left-0 right-0 p-2 bg-gradient-to-t from-black/70 to-transparent">
          <span className="text-xs text-white font-mono">{frame.timestamp}</span>
        </div>
      </motion.div>
    ))}
  </div>

  {/* Лог компонентов */}
  <div className="bg-[--bg-elevated] border border-[--border] rounded-[--radius-lg] p-4">
    <h3 className="text-sm font-medium mb-4">Компоненты</h3>
    <ComponentProgress components={components} />
  </div>
</div>
```

---

## 10. ADMIN ПАНЕЛЬ

### Layout
```
Идентичный sidebar как в dashboard, но с admin-навигацией.
Дополнительный бейдж "ADMIN" в header красного цвета.
```

### Таблицы данных
```tsx
<Table>
  <TableHeader className="bg-[--bg-hover]">
    <TableRow className="border-[--border] hover:bg-transparent">
      <TableHead className="text-[--text-muted] text-xs uppercase tracking-wider">
        {column.name}
      </TableHead>
    </TableRow>
  </TableHeader>
  <TableBody>
    <TableRow className="border-[--border] hover:bg-[--surface-hover] transition-colors">
      <TableCell className="font-mono text-sm">{cell}</TableCell>
    </TableRow>
  </TableBody>
</Table>
```

---

## 11. ЧАРТЫ — КОНФИГУРАЦИЯ

### Recharts (основные графики)

```tsx
// Общие стили для всех чартов
const chartConfig = {
  style: {
    background: 'transparent',
  },
  theme: {
    grid: '#1f1f23',       // --border
    axis: '#52525b',       // --text-muted
    tooltip: '#141416',    // --bg-overlay
    accent: '#7c3aed',     // --accent
  }
};

// Линейный график прогноза
<LineChart data={data}>
  <CartesianGrid strokeDasharray="3 3" stroke="#1f1f23" />
  <XAxis tick={{ fill: '#52525b', fontSize: 12, fontFamily: 'JetBrains Mono' }} />
  <YAxis tick={{ fill: '#52525b', fontSize: 12, fontFamily: 'JetBrains Mono' }} />
  <Tooltip
    contentStyle={{
      background: '#141416',
      border: '1px solid #1f1f23',
      borderRadius: '10px',
      fontFamily: 'JetBrains Mono',
    }}
  />
  <Line type="monotone" dataKey="views" stroke="#7c3aed" strokeWidth={2} dot={{ fill: '#7c3aed', r: 4 }} />
  <Area type="monotone" dataKey="confidence" fill="rgba(124,58,237,0.08)" stroke="none" />
</LineChart>

// Radar Chart (модальности)
<RadarChart>
  <PolarGrid stroke="#1f1f23" />
  <PolarAngleAxis tick={{ fill: '#a1a1aa', fontSize: 11 }} />
  <Radar fill="rgba(124,58,237,0.15)" stroke="#7c3aed" strokeWidth={2} />
</RadarChart>
```

### Nivo (тепловые карты, распределения)

```tsx
// Heatmap кадров
const nivoTheme = {
  background: 'transparent',
  textColor: '#52525b',
  fontSize: 12,
  axis: { ticks: { text: { fill: '#52525b' } } },
  grid: { line: { stroke: '#1f1f23' } },
  tooltip: { container: { background: '#141416', border: '1px solid #1f1f23' } }
};
```

---

## 12. ДОСТУПНОСТЬ

### Обязательные требования (WCAG AA)
```
✓ Контраст текста ≥ 4.5:1 (основной) / 3:1 (большой)
  Проверка: --text-primary (#fafafa) на --bg (#09090b) = 19.5:1 ✓
  Проверка: --text-secondary (#a1a1aa) на --bg (#09090b) = 7.2:1 ✓
  Проверка: --accent (#7c3aed) на --bg (#09090b) = 5.8:1 ✓

✓ Все кнопки: min 44×44px touch target
✓ Focus ring: 2px solid --accent, offset 2px
✓ aria-label на всех icon-only кнопках
✓ role="status" на live прогресс обновлениях
✓ prefers-reduced-motion: все анимации → duration: 0
✓ Keyboard navigation: Tab order = visual order
```

```css
/* Focus ring глобально */
:focus-visible {
  outline: 2px solid var(--accent);
  outline-offset: 2px;
  border-radius: var(--radius-sm);
}
```

---

## 13. АДАПТИВНОСТЬ

```
Mobile first: base стили для 375px
Breakpoints (Tailwind):
  sm: 640px   → планшет (portrait)
  md: 768px   → планшет (landscape)
  lg: 1024px  → ноутбук (sidebar появляется)
  xl: 1280px  → десктоп
  2xl: 1440px → широкий

Mobile-specific:
  - Sidebar → bottom navigation (5 items max)
  - Таблицы → горизонтальный скролл (overflow-x-auto)
  - Графики → упрощённый вид или горизонтальная ориентация
  - 3D фон → отключён (useMediaQuery < 1024px)
  - Grid чартов: 1 колонка вместо 2
```

---

## 14. СОСТОЯНИЯ ЗАГРУЗКИ

### Skeleton (предпочтительный вариант)

```tsx
// Всегда показывать skeleton при загрузке > 0ms
// Никогда не показывать пустой экран

<div className="animate-pulse space-y-3">
  <div className="h-6 w-48 bg-[--bg-hover] rounded-[--radius-sm]" />
  <div className="h-4 w-full bg-[--bg-hover] rounded-[--radius-sm]" />
  <div className="h-4 w-3/4 bg-[--bg-hover] rounded-[--radius-sm]" />
</div>

// Для карточек метрик
<div className="bg-[--bg-elevated] border border-[--border] rounded-[--radius-lg] p-5 animate-pulse">
  <div className="h-4 w-24 bg-[--bg-hover] rounded mb-3" />
  <div className="h-8 w-32 bg-[--bg-hover] rounded" />
</div>
```

### Toast уведомления (Sonner)

```tsx
// Конфигурация Sonner
<Toaster
  theme="dark"
  toastOptions={{
    style: {
      background: 'var(--bg-overlay)',
      border: '1px solid var(--border)',
      color: 'var(--text-primary)',
      borderRadius: 'var(--radius-md)',
    },
  }}
/>

// Использование
toast.success('Анализ завершён!', { icon: '✓' });
toast.error('Ошибка анализа', { description: 'Попробуйте ещё раз' });
toast.loading('Запуск анализа...');
```

---

## 15. EMPTY STATES

```tsx
// Нет анализов
<div className="flex flex-col items-center justify-center py-20 text-center">
  <div className="w-16 h-16 rounded-full bg-[--accent-subtle] flex items-center justify-center mb-4">
    <BarChart3 className="text-[--accent]" size={28} />
  </div>
  <h3 className="text-lg font-semibold mb-2">Нет анализов</h3>
  <p className="text-[--text-muted] text-sm max-w-xs mb-6">
    Загрузите видео или вставьте YouTube-ссылку, чтобы начать
  </p>
  <Button>Запустить первый анализ</Button>
</div>
```

---

## 16. CHECKLIST ПЕРЕД СДАЧЕЙ КОМПОНЕНТА

```
□ Все цвета через CSS токены (var(--...)), не хардкодить hex
□ Числа/метрики через font-mono
□ Hover state: transition-all duration-150
□ Focus ring видим (outline через :focus-visible)
□ Skeleton при загрузке
□ Empty state при нет данных
□ Error state с кнопкой retry
□ Адаптивность: проверено на 375px и 1440px
□ aria-label на icon-only элементах
□ prefers-reduced-motion обработан
□ Все строки в messages/ru.json (нет хардкода текста в JSX)
```
