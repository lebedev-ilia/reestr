from __future__ import annotations

from fetcher.dataset_collector.schemas import CampaignConfig


KEYWORD_PATTERNS = [
    "{topic}",
    "{topic} 2026",
    "{topic} 2025",
    "{topic} shorts",
    "{topic} обзор",
    "{topic} топ",
    "{topic} гайд",
    "{topic} tutorial",
    "{topic} review",
    "{topic} latest",
]


CATEGORY_SEEDS = {
    "Avto_i_transport": [
        "авто обзор", "тест драйв", "ремонт автомобиля", "автомобильные новости", "электромобили",
        "гибридные авто", "автосервис", "тюнинг авто", "дрифт", "автоспорт",
        "мотоциклы", "грузовики", "дальнобой", "общественный транспорт", "поезда",
        "авиация", "корабли", "велосипеды", "car review", "car maintenance",
        "electric cars", "auto repair", "motorcycle review", "truck driver vlog", "dashcam videos",
        "supercars", "off road vehicles", "car detailing", "public transport", "aviation news",
    ],
    "Deti": [
        "детское развитие", "детское питание", "игры для детей", "развивающие занятия", "детские игрушки",
        "семейный vlog", "беременность", "новорожденный", "уход за ребенком", "детская психология",
        "школьники", "подростки", "мультфильмы для детей", "детские поделки", "детские песни",
        "parenting tips", "baby care", "kids activities", "family vlog", "toddler development",
        "children education", "mom vlog", "dad vlog", "baby food", "kids toys",
        "homeschool kids", "children psychology", "newborn care", "kids crafts", "family routine",
    ],
    "Dom_i_sad": [
        "ремонт дома", "дизайн интерьера", "уборка дома", "организация хранения", "садоводство",
        "огород", "дача", "комнатные растения", "ландшафтный дизайн", "строительство дома",
        "кухня ремонт", "ванная ремонт", "умный дом", "уютный дом", "декор дома",
        "home renovation", "interior design", "home organization", "cleaning routine", "gardening tips",
        "vegetable garden", "house plants", "diy home", "tiny house", "smart home",
        "home decor", "backyard garden", "landscaping", "apartment makeover", "room makeover",
    ],
    "Eda_i_napitki": [
        "рецепты", "быстрый ужин", "здоровое питание", "домашняя выпечка", "уличная еда",
        "кофе", "чай", "коктейли", "барбекю", "завтраки",
        "обеды", "десерты", "веганские рецепты", "мясные блюда", "рыбные блюда",
        "easy recipes", "street food", "food vlog", "cooking tutorial", "healthy meals",
        "meal prep", "baking recipes", "coffee review", "tea ceremony", "cocktail recipes",
        "bbq recipes", "breakfast ideas", "dessert recipes", "restaurant review", "kitchen hacks",
    ],
    "Finansy_i_biznes": [
        "личные финансы", "инвестиции", "фондовый рынок", "криптовалюта", "бизнес идеи",
        "стартап", "предпринимательство", "маркетинг", "продажи", "недвижимость инвестиции",
        "налоги", "бухгалтерия", "карьера", "удаленная работа", "пассивный доход",
        "personal finance", "investing", "stock market", "crypto news", "business ideas",
        "startup advice", "entrepreneur vlog", "digital marketing", "sales tips", "real estate investing",
        "tax tips", "side hustle", "passive income", "career growth", "money management",
    ],
    "Igry": [
        "lets play", "прохождение игр", "обзор игры", "новые игры", "мобильные игры",
        "киберспорт", "стрим игры", "minecraft", "roblox", "fortnite",
        "gta", "counter strike", "dota 2", "valorant", "genshin impact",
        "gaming highlights", "game review", "gameplay walkthrough", "new games", "mobile gaming",
        "esports", "stream highlights", "minecraft builds", "roblox gameplay", "fortnite clips",
        "gta roleplay", "cs2 highlights", "dota 2 guide", "valorant tips", "gaming setup",
    ],
    "Iumor": [
        "юмор", "смешные видео", "приколы", "стендап", "комедия",
        "пародия", "мемы", "реакции", "скетчи", "анекдоты",
        "шоу юмор", "импровизация", "смешные животные", "фейлы", "розыгрыши",
        "funny videos", "comedy skits", "stand up comedy", "memes", "reaction video",
        "funny moments", "fails compilation", "pranks", "satire", "parody",
        "improv comedy", "funny shorts", "viral comedy", "comedy vlog", "dark humor",
    ],
    "Kino_i_serialy": [
        "обзор фильма", "трейлер фильма", "новые фильмы", "сериалы", "обзор сериала",
        "кино новости", "разбор фильма", "лучшие фильмы", "фантастика кино", "ужасы фильмы",
        "комедии фильмы", "драмы фильмы", "аниме обзор", "netflix сериалы", "марвел",
        "movie review", "film analysis", "new movies", "tv series review", "movie trailer",
        "cinema news", "best movies", "horror movies", "sci fi movies", "drama movies",
        "comedy movies", "anime review", "netflix series", "marvel movies", "behind the scenes",
    ],
    "Moda_i_krasota": [
        "макияж", "уход за кожей", "мода", "стиль", "прически",
        "маникюр", "косметика", "бьюти обзор", "одежда", "капсульный гардероб",
        "уход за волосами", "парфюм", "женская мода", "мужской стиль", "shopping haul",
        "makeup tutorial", "skincare routine", "fashion trends", "outfit ideas", "hair styling",
        "nail art", "beauty review", "cosmetics review", "wardrobe essentials", "street style",
        "perfume review", "get ready with me", "beauty hacks", "fashion haul", "style tips",
    ],
    "Muzyka": [
        "новая музыка", "клип", "музыкальный обзор", "концерт", "живое выступление",
        "кавер", "ремикс", "битмейкинг", "гитара", "фортепиано",
        "вокал", "рэп", "поп музыка", "рок музыка", "электронная музыка",
        "new music", "music video", "live performance", "concert vlog", "song cover",
        "remix", "beat making", "guitar tutorial", "piano tutorial", "vocal coach",
        "rap music", "pop music", "rock music", "electronic music", "music production",
    ],
    "Nauka": [
        "наука", "космос", "физика", "биология", "химия",
        "история науки", "эксперименты", "астрономия", "технологии будущего", "искусственный интеллект",
        "медицина", "экология", "психология", "математика", "научпоп",
        "science explained", "space news", "physics explained", "biology facts", "chemistry experiments",
        "astronomy", "science documentary", "ai explained", "future technology", "medical science",
        "climate science", "psychology facts", "math explained", "science experiments", "educational science",
    ],
    "Novosti_i_politika": [
        "новости", "политика", "мировые новости", "экономические новости", "аналитика",
        "геополитика", "выборы", "законы", "общество", "интервью политика",
        "военные новости", "международные отношения", "новости россии", "новости сша", "новости европы",
        "breaking news", "world news", "politics analysis", "geopolitics", "election news",
        "economic news", "news commentary", "political debate", "international relations", "society news",
        "law news", "daily news", "news live", "current events", "political interview",
    ],
    "Obrazovanie": [
        "обучение", "уроки", "образование", "изучение английского", "математика уроки",
        "программирование", "история урок", "подготовка к экзаменам", "онлайн обучение", "саморазвитие",
        "конспекты", "университет", "школа", "профессии", "курсы",
        "learning tips", "study with me", "online education", "english learning", "math lessons",
        "coding tutorial", "history lesson", "exam preparation", "student vlog", "productivity for students",
        "university life", "school tips", "career education", "free courses", "language learning",
    ],
    "Puteshestvia": [
        "путешествия", "тревел влог", "обзор страны", "куда поехать", "отель обзор",
        "авиаперелет", "поездка на машине", "пляжный отдых", "горные походы", "городской туризм",
        "еда в путешествии", "бюджетное путешествие", "путешествие по россии", "европа путешествие", "азия путешествие",
        "travel vlog", "travel guide", "country review", "city tour", "hotel review",
        "flight review", "road trip", "beach travel", "hiking travel", "budget travel",
        "solo travel", "family travel", "street food travel", "travel tips", "best places to visit",
    ],
    "Sport": [
        "спорт", "футбол", "баскетбол", "хоккей", "теннис",
        "бокс", "mma", "фитнес тренировка", "бег", "велоспорт",
        "плавание", "йога", "силовые тренировки", "спортивные новости", "матч обзор",
        "sports highlights", "football highlights", "basketball training", "hockey highlights", "tennis match",
        "boxing highlights", "mma fight", "workout routine", "running tips", "cycling vlog",
        "swimming technique", "yoga flow", "strength training", "sports news", "match analysis",
    ],
    "Tehnologii": [
        "технологии", "гаджеты", "смартфон обзор", "ноутбук обзор", "искусственный интеллект",
        "нейросети", "программирование", "роботы", "кибербезопасность", "софт",
        "приложения", "обзор техники", "vr ar", "электроника", "стартап технологии",
        "tech review", "gadgets", "smartphone review", "laptop review", "ai tools",
        "machine learning", "programming tutorial", "robots", "cybersecurity", "software review",
        "apps review", "consumer electronics", "vr headset", "tech news", "developer tools",
    ],
    "Zdorovie_i_fitnes": [
        "здоровье", "фитнес", "тренировка дома", "похудение", "питание",
        "йога", "растяжка", "сон", "ментальное здоровье", "медитация",
        "реабилитация", "кардио", "силовая тренировка", "здоровые привычки", "врач советы",
        "health tips", "fitness routine", "home workout", "weight loss", "nutrition tips",
        "yoga practice", "stretching routine", "sleep health", "mental health", "meditation guide",
        "rehab exercises", "cardio workout", "strength workout", "healthy habits", "doctor advice",
    ],
    "Zivotnye": [
        "животные", "кошки", "собаки", "питомцы", "уход за собакой",
        "уход за кошкой", "дрессировка собак", "ветеринар советы", "смешные животные", "дикие животные",
        "птицы", "аквариум", "лошади", "животные спасение", "зоопарк",
        "animals", "cats", "dogs", "pets care", "dog training",
        "cat care", "veterinary tips", "funny animals", "wildlife", "birds care",
        "aquarium fish", "horses", "animal rescue", "zoo animals", "pet vlog",
    ],
}


def generate_keywords(category_name: str, *, count: int = 300) -> list[str]:
    seeds = CATEGORY_SEEDS.get(category_name)
    if not seeds:
        return []
    keywords: list[str] = []
    seen: set[str] = set()
    for topic in seeds:
        for pattern in KEYWORD_PATTERNS:
            phrase = pattern.format(topic=topic)
            if phrase in seen:
                continue
            keywords.append(phrase)
            seen.add(phrase)
            if len(keywords) >= count:
                return keywords
    return keywords


def apply_keyword_presets(config: CampaignConfig, *, min_keywords: int = 300) -> CampaignConfig:
    for category in config.categories:
        if len(category.keywords) >= min_keywords:
            continue
        generated = generate_keywords(category.name, count=min_keywords)
        if generated:
            category.keywords = generated
    return config
