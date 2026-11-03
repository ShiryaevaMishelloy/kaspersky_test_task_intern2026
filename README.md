# kaspersky_test_task_intern2026

Пайплайн сбора, классификации и анализа внешних источников.

Репозиторий содержит решение тестового задания для отдела конкурентной разведки / разработки. Проект включает в себя рабочий прототип живого парсера для 6 разнородных источников данных, строгую валидацию через Pydantic, обработку капч и механизмы обхода антибот-систем.

---

## 📂 Структура репозитория

- `pipeline_task_1_1.py` — главный скрипт живого парсинга всех 6 источников в реальном времени.
- `output_dataset.jsonl` — итоговый структурированный датасет в формате JSON Lines (66+ записей).
- `requirements.txt` — список необходимых зависимостей проекта.

---

## 🎯 Источники данных

1. **Irecommend** (`https://irecommend.ru/content/sait-ozontravel-0`) — отзывы пользователей и рейтинги.
2. **LiveJournal / 5koleso** (`https://5koleso.livejournal.com`) — блоговые публикации.
3. **Дзен** (`https://dzen.ru/news/rubric/computers`) — новостной агрегатор с динамической подгрузкой (Infinite Scroll).
4. **4PDA** (`https://4pda.to/forum/index.php?showforum=320`) — тематический форум и обсуждения.
5. **Отзовик** (`https://otzovik.com/money_systems/banks/`) — каталог отзывов и финансовых систем (с обработкой защитных экранов).
6. **Drive2** (`https://www.drive2.ru/featured-topics/CVQ0gEAAcHQ/`) — автомобильное комьюнити и статьи с метриками вовлеченности.

---

## 🚀 Быстрый старт и запуск

### 1. Установка зависимостей
```bash
python -m venv venv
# Для Windows:
venv\Scripts\activate
# Для Linux/macOS:
source venv/bin/activate

pip install -r requirements.txt
playwright install
