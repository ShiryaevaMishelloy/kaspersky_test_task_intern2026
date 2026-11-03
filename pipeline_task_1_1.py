import hashlib
import json
import re
import time
import random
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urljoin
from bs4 import BeautifulSoup
from pydantic import BaseModel, Field, computed_field
from playwright.sync_api import sync_playwright

MAX_ITEMS = 15
OUTPUT_FILE = "output_dataset.jsonl"


class ExtractedItem(BaseModel):
    title: Optional[str] = None
    text: Optional[str] = None
    author: Optional[str] = None
    published_at: Optional[str] = None
    item_url: Optional[str] = None
    extra_info: Dict[str, Any] = Field(default_factory=dict)

    @computed_field
    @property
    def item_id(self) -> str:
        base = self.item_url or f"{self.title}_{self.author}_{self.published_at}"
        return hashlib.sha256(base.encode("utf-8")).hexdigest()[:16]


class DeepMasterLiveScraper:
    def __init__(self):
        pass

    @staticmethod
    def clean(text: Optional[str], max_len: int = 3000) -> Optional[str]:
        if not text:
            return None
        text = re.sub(r"\s+", " ", text).strip()
        if len(text) > max_len:
            return text[:max_len] + "..."
        return text or None

    def _init_browser_page(self, p):
        browser = p.chromium.launch(
            headless=False,  # Видимый режим для безопасного прохождения защиты
            args=[
                "--disable-blink-features=AutomationControlled",
                "--disable-infobars",
                "--no-sandbox",
                "--disable-dev-shm-usage"
            ]
        )
        context = browser.new_context(
            viewport={"width": 1920, "height": 1080},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
            locale="ru-RU",
            timezone_id="Europe/Moscow"
        )
        page = context.new_page()
        page.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
        return browser, page

    def scrape_all(self) -> List[ExtractedItem]:
        all_items = []

        with sync_playwright() as p:
            browser, page = self._init_browser_page(p)

            try:
                # 1. IREcommend (Отзывы Ozon.travel)
                try:
                    url = "https://irecommend.ru/content/sait-ozontravel-0"
                    print(f"\n[*] Глубокий сбор Irecommend: {url}")
                    page.goto(url, wait_until="domcontentloaded", timeout=60000)
                    page.wait_for_timeout(3000)

                    soup = BeautifulSoup(page.content(), "lxml")
                    count = 0
                    for node in soup.select(".smTeaser, ul.list-comments > li, .item, div[class*='review']"):
                        if count >= MAX_ITEMS:
                            break
                        author_el = node.select_one(".authorName a, .reviewer, .username, [class*='author']")
                        date_el = node.select_one(".created, time, .date, [class*='date']")
                        title_el = node.select_one(".reviewTitle a, .title a, a[href*='/content/']")
                        text_el = node.select_one(".reviewTextSnippet, .teaser-item, .text, [class*='text']")
                        rating_els = node.select(".stars .on, .rating-star.active, [class*='star-on']")

                        title = self.clean(title_el.get_text()) if title_el else None
                        text = self.clean(text_el.get_text(" ")) if text_el else title
                        if not text or len(text) < 15:
                            continue

                        all_items.append(ExtractedItem(
                            title=title or "Отзыв Ozon.travel",
                            text=text,
                            author=self.clean(author_el.get_text()) if author_el else "Аноним",
                            published_at=self.clean(date_el.get_text()) if date_el else "Актуально",
                            item_url=urljoin(url, title_el["href"]) if (title_el and title_el.get("href")) else url,
                            extra_info={
                                "source_type": "irecommend_review",
                                "rating": len(rating_els),
                                "platform": "irecommend.ru"
                            }
                        ))
                        count += 1
                    print(f"  [OK] Загружено из Irecommend: {count}")
                except Exception as e:
                    print(f"  [WARN] Ошибка Irecommend: {e}")


                # 2. 5KOLESO (LiveJournal Блог)
                try:
                    url = "https://5koleso.livejournal.com"
                    print(f"\n[*] Глубокий сбор 5koleso: {url}")
                    page.goto(url, wait_until="domcontentloaded", timeout=60000)
                    page.wait_for_timeout(3000)

                    soup = BeautifulSoup(page.content(), "lxml")
                    count = 0
                    for entry in soup.select("article.entryunit, .post-asset, .entry, div[class*='post']"):
                        if count >= MAX_ITEMS:
                            break
                        title_el = entry.select_one("h2 a, h3 a, .subj-link, .entry-title a")
                        body_el = entry.select_one(".entryunit__body, .asset-body, .entry-content, div[class*='body']")
                        date_el = entry.select_one("time, .datetime, [class*='date']")

                        title = self.clean(title_el.get_text()) if title_el else "Пост блога"
                        text = self.clean(body_el.get_text(" "), max_len=2500) if body_el else title

                        all_items.append(ExtractedItem(
                            title=title,
                            text=text,
                            author="5koleso",
                            published_at=self.clean(date_el.get_text()) if date_el else datetime.now(
                                timezone.utc).strftime("%Y-%m-%d"),
                            item_url=title_el.get("href") if title_el else url,
                            extra_info={
                                "source_type": "livejournal_blog",
                                "platform": "5koleso.livejournal.com"
                            }
                        ))
                        count += 1
                    print(f"  [OK] Загружено из 5koleso: {count}")
                except Exception as e:
                    print(f"  [WARN] Ошибка 5koleso: {e}")


                # 3. DZEN (Новости IT/Компьютеры)
                try:
                    url = "https://dzen.ru/news/rubric/computers"
                    print(f"\n[*] Глубокий сбор Dzen: {url}")
                    page.goto(url, wait_until="domcontentloaded", timeout=60000)
                    page.wait_for_timeout(4000)

                    # Умный скролл: прокручиваем страницу, пока не наберем 15 карточек или пока не перестанут появляться новые
                    last_height = page.evaluate("document.body.scrollHeight")
                    scroll_attempts = 0
                    max_scroll_attempts = 8 # джейлбрейк / лимит попыток скролла

                    while scroll_attempts < max_scroll_attempts:
                        # Проверяем, сколько уже собрали
                        soup = BeautifulSoup(page.content(), "lxml")
                        cards = soup.select("article, [class*='mg-card'], [class*='news-card'], div[data-testid]")
                        if len(cards) >= MAX_ITEMS:
                            break

                        # Скроллим до самого низа
                        page.evaluate("window.scrollTo(0, document.body.scrollHeight);")
                        page.wait_for_timeout(3000)  # Даем время подгрузиться новому контенту

                        new_height = page.evaluate("document.body.scrollHeight")
                        if new_height == last_height:
                            # Если высота не изменилась, значит, дошли до конца ленты
                            break
                        last_height = new_height
                        scroll_attempts += 1

                    # Финальный сбор после подгрузки
                    soup = BeautifulSoup(page.content(), "lxml")
                    count = 0
                    for card in soup.select("article, [class*='mg-card'], [class*='news-card'], div[data-testid]"):
                        if count >= MAX_ITEMS:
                            break
                        title_el = card.select_one("h2, h3, [class*='title']")
                        lead_el = card.select_one("[class*='annotation'], [class*='text']")
                        src_el = card.select_one("[class*='source'], [class*='agency']")
                        link_el = card.select_one("a[href]")

                        title = self.clean(title_el.get_text()) if title_el else None
                        if not title or len(title) < 10:
                            continue

                        all_items.append(ExtractedItem(
                            title=title,
                            text=self.clean(lead_el.get_text(" ")) if lead_el else title,
                            author=self.clean(src_el.get_text()) if src_el else "Дзен Канал",
                            published_at=datetime.now(timezone.utc).strftime("%Y-%m-%d"),
                            item_url=urljoin(url, link_el["href"]) if (link_el and link_el.get("href")) else url,
                            extra_info={
                                "source_type": "news_aggregator",
                                "rubric": "computers",
                                "platform": "dzen.ru"
                            }
                        ))
                        count += 1
                    print(f"  [OK] Загружено из Dzen: {count}")
                except Exception as e:
                    print(f"  [WARN] Ошибка Dzen: {e}")

                # 4. 4PDA (Форум)
                try:
                    url = "https://4pda.to/forum/index.php?showforum=320"
                    print(f"\n[*] Глубокий сбор 4PDA: {url}")
                    page.goto(url, wait_until="domcontentloaded", timeout=60000)
                    page.wait_for_timeout(3000)

                    soup = BeautifulSoup(page.content(), "lxml")
                    count = 0
                    for a_tag in soup.select("a[href*='showtopic=']"):
                        if count >= MAX_ITEMS:
                            break
                        title = self.clean(a_tag.get_text())
                        href = a_tag.get("href", "")
                        if not title or len(title) < 5 or "view=getlast" in href:
                            continue

                        all_items.append(ExtractedItem(
                            title=title,
                            text=f"Обсуждение на форуме 4PDA: {title}",
                            author="4PDA Member",
                            published_at="Актуально",
                            item_url=urljoin("https://4pda.to/forum/", href),
                            extra_info={
                                "source_type": "forum_topic",
                                "platform": "4pda.to"
                            }
                        ))
                        count += 1
                    print(f"  [OK] Загружено из 4PDA: {count}")
                except Exception as e:
                    print(f"  [WARN] Ошибка 4PDA: {e}")

                # 5. OTZOVIK (Банки и финансовые системы)
                try:
                    url = "https://otzovik.com/money_systems/banks/"
                    print(f"\n[*] Глубокий сбор Otzovik: {url}")
                    page.goto(url, wait_until="domcontentloaded", timeout=60000)
                    page.wait_for_timeout(4000)

                    if "captcha" in page.url.lower() or "вы робот" in page.content().lower():
                        print("[!] Капча на Отзовике! Пройдите её в браузере...")
                        input("Нажмите Enter после прохождения капчи...")

                    soup = BeautifulSoup(page.content(), "lxml")
                    count = 0
                    for a_tag in soup.select("a[href*='/reviews/'], a[href*='/opinion/'], a.product-name, a.item-link"):
                        if count >= MAX_ITEMS:
                            break
                        name = self.clean(a_tag.get_text())
                        href = a_tag.get("href", "")
                        if not name or len(name) < 3:
                            continue

                        all_items.append(ExtractedItem(
                            title=f"Каталог Отзовик: {name}",
                            text=f"Финансовый объект каталога Отзовик: {name}. Сбор детальных отзывов и пользовательских оценок.",
                            author="Сообщество Отзовик",
                            published_at="Актуально",
                            item_url=urljoin("https://otzovik.com", href),
                            extra_info={
                                "source_type": "catalog_object",
                                "category": "banks_and_money_systems",
                                "platform": "otzovik.com"
                            }
                        ))
                        count += 1
                    print(f"  [OK] Загружено из Otzovik: {count}")
                except Exception as e:
                    print(f"  [WARN] Ошибка Otzovik: {e}")


                # 6. DRIVE2 (Автомобильные темы)
                try:
                    url = "https://www.drive2.ru/featured-topics/CVQ0gEAAcHQ/"
                    print(f"\n[*] Глубокий сбор Drive2: {url}")
                    page.goto(url, wait_until="domcontentloaded", timeout=60000)
                    page.wait_for_timeout(3000)

                    soup = BeautifulSoup(page.content(), "lxml")
                    count = 0
                    for card in soup.select(".c-post-preview, article"):
                        if count >= MAX_ITEMS:
                            break
                        title_el = card.select_one(".c-post-preview__title a, a.c-link--text, h2 a")
                        lead_el = card.select_one(".c-post-preview__lead, .c-post__body")
                        author_el = card.select_one(".c-username, a[href*='/users/']")
                        likes_el = card.select_one(".c-counter__value, [class*='likes']")

                        if not title_el:
                            continue
                        title = self.clean(title_el.get_text())
                        text = self.clean(lead_el.get_text(" "), max_len=2000) if lead_el else title

                        all_items.append(ExtractedItem(
                            title=title,
                            text=text,
                            author=self.clean(author_el.get_text()) if author_el else "Drive2 Driver",
                            published_at=datetime.now(timezone.utc).strftime("%Y-%m-%d"),
                            item_url=urljoin(url, title_el["href"]) if title_el.get("href") else url,
                            extra_info={
                                "source_type": "automotive_blog",
                                "likes": self.clean(likes_el.get_text()) if likes_el else "0",
                                "platform": "drive2.ru"
                            }
                        ))
                        count += 1
                    print(f"  [OK] Загружено из Drive2: {count}")
                except Exception as e:
                    print(f"  [WARN] Ошибка Drive2: {e}")

            finally:
                browser.close()

        return all_items


if __name__ == "__main__":
    scraper = DeepMasterLiveScraper()
    dataset = scraper.scrape_all()

    output_path = Path(OUTPUT_FILE)
    with output_path.open("w", encoding="utf-8") as f:
        for item in dataset:
            f.write(item.model_dump_json() + "\n")


    print(f"[SUCCESS] Успешно собрано расширенных записей: {len(dataset)}")
    print(f"[FILE] Данные сохранены в: {output_path.resolve()}")
