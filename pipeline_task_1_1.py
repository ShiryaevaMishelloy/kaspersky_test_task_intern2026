import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urljoin, urlparse, urlunparse
from bs4 import BeautifulSoup
from curl_cffi import requests
from pydantic import BaseModel, Field, computed_field


class ExtractedItem(BaseModel):
    """Единица извлеченного контента (отзыв, пост, сюжет, тема форума)."""
    item_url: Optional[str] = None
    title: Optional[str] = None
    text: Optional[str] = None
    author: Optional[str] = None
    published_at_raw: Optional[str] = None
    metrics: Dict[str, Any] = Field(default_factory=dict)
    domain_attributes: Dict[str, Any] = Field(default_factory=dict)

    @computed_field
    @property
    def item_id(self) -> str:
        base = self.item_url or f"{self.title}_{self.author}_{self.published_at_raw}"
        return hashlib.sha256(base.encode("utf-8")).hexdigest()[:16]

    @computed_field
    @property
    def content_hash(self) -> Optional[str]:
        if not self.text and not self.title:
            return None
        payload = f"{self.title or ''}||{self.text or ''}".strip()
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class SourceDocument(BaseModel):
    """Контракт корневого документа источника с метаданными и DQ-метриками."""
    source_url: str
    canonical_url: str
    domain: str
    source_class: str
    page_granularity: str  # catalog_index | object_hub | topic_feed
    scraped_at_utc: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    http_status: int
    page_title: Optional[str] = None
    open_graph: Dict[str, str] = Field(default_factory=dict)
    json_ld_blocks: List[Dict[str, Any]] = Field(default_factory=list)
    hub_metrics: Dict[str, Any] = Field(default_factory=dict)
    items: List[ExtractedItem] = Field(default_factory=list)

    @computed_field
    @property
    def doc_id(self) -> str:
        return hashlib.sha256(self.canonical_url.encode("utf-8")).hexdigest()

    @computed_field
    @property
    def dq_report(self) -> Dict[str, Any]:
        """Автоматический контроль качества данных (Data Quality Check)."""
        total = len(self.items)
        if total == 0:
            return {"dq_passed": False, "items_count": 0, "completeness_score": 0.0}

        with_title = sum(1 for i in self.items if i.title)
        with_text = sum(1 for i in self.items if i.text)
        with_author = sum(1 for i in self.items if i.author)
        with_date = sum(1 for i in self.items if i.published_at_raw)

        completeness = round((with_title + with_text + with_author + with_date) / (4 * total), 3)
        return {
            "dq_passed": total > 0 and completeness >= 0.4,
            "items_count": total,
            "completeness_score": completeness,
            "null_rates": {
                "title_null_rate": round(1 - with_title / total, 3),
                "text_null_rate": round(1 - with_text / total, 3),
                "author_null_rate": round(1 - with_author / total, 3),
                "date_null_rate": round(1 - with_date / total, 3),
            },
        }


class CompetitiveIntelligenceScraper:
    def __init__(self):
        # TLS-имперсонация браузера для обхода базового антибота (Cloudflare/JA3)
        self.session = requests.Session(impersonate="chrome120")
        self.headers = {"Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8"}

    @staticmethod
    def canonicalize_url(url: str) -> str:
        parsed = urlparse(url.strip())
        return urlunparse((parsed.scheme, parsed.netloc.lower(), parsed.path, "", parsed.query, ""))

    def fetch(self, url: str, local_html_path: Optional[str] = None) -> tuple[int, str]:
        if local_html_path and Path(local_html_path).exists():
            return 200, Path(local_html_path).read_text(encoding="utf-8", errors="ignore")

        try:
            resp = self.session.get(url, headers=self.headers, timeout=15, verify=False)
            if "4pda.to" in url and resp.encoding and resp.encoding.lower() != "utf-8":
                resp.encoding = "windows-1251"
            return resp.status_code, resp.text
        except Exception as e:
            print(f"[WARN] Сайт {url} сбросил соединение ({e}). Пропускаем или берем локальный файл.")
            return 504, ""

    @staticmethod
    def parse_structured_meta(soup: BeautifulSoup) -> tuple[Dict[str, str], List[Dict[str, Any]]]:
        og_data = {}
        for tag in soup.find_all("meta", attrs={"property": re.compile(r"^og:")}):
            if tag.get("property") and tag.get("content"):
                og_data[tag["property"]] = tag["content"].strip()

        json_ld = []
        for script in soup.find_all("script", attrs={"type": "application/ld+json"}):
            if script.string:
                try:
                    loaded = json.loads(script.string)
                    if isinstance(loaded, dict):
                        json_ld.append(loaded)
                    elif isinstance(loaded, list):
                        json_ld.extend(loaded)
                except json.JSONDecodeError:
                    continue
        return og_data, json_ld

    # --- Специфичные адаптеры источников ---
    def _parse_irecommend(self, soup: BeautifulSoup, base_url: str) -> tuple[Dict, List[ExtractedItem]]:
        avg_r = soup.select_one(".average-rating .rating, [itemprop='ratingValue']")
        cnt_r = soup.select_one(".votes-total, [itemprop='reviewCount']")
        hub_metrics = {
            "entity_name": soup.select_one("h1").get_text(strip=True) if soup.select_one("h1") else None,
            "avg_rating": avg_r.get_text(strip=True) if avg_r else None,
            "total_reviews": cnt_r.get_text(strip=True) if cnt_r else None,
        }

        items = []
        for node in soup.select(".list-comments li, .smTeaser, .views-row"):
            title_el = node.select_one(".reviewTitle a, .title a")
            text_el = node.select_one(".reviewTextSnippet, .teaser-item")
            author_el = node.select_one(".authorName a, .author")
            date_el = node.select_one(".created, time")
            stars = len(node.select(".stars .on, .fivestar-Widget .on"))
            pros = [p.get_text(strip=True) for p in node.select(".plus li, .pros")]
            cons = [c.get_text(strip=True) for c in node.select(".minus li, .cons")]

            if title_el or text_el:
                items.append(
                    ExtractedItem(
                        item_url=urljoin(base_url, title_el["href"]) if (title_el and title_el.get("href")) else None,
                        title=title_el.get_text(strip=True) if title_el else None,
                        text=text_el.get_text(" ", strip=True) if text_el else None,
                        author=author_el.get_text(strip=True) if author_el else None,
                        published_at_raw=date_el.get_text(strip=True) if date_el else None,
                        metrics={"star_rating": stars if stars > 0 else None},
                        domain_attributes={"pros": pros, "cons": cons},
                    )
                )
        return hub_metrics, items

    def _parse_livejournal(self, soup: BeautifulSoup, base_url: str) -> tuple[Dict, List[ExtractedItem]]:
        items = []
        for entry in soup.select("article.entryunit, .post-asset, .entry"):
            title_el = entry.select_one("h2 a, h3 a, .entryunit__title a, .subj-link")
            body_el = entry.select_one(".entryunit__body, .asset-body, .entry-content")
            date_el = entry.select_one("time, .entryunit__date, .datetime")
            tags = [t.get_text(strip=True) for t in entry.select(".entryunit__tags a, .ljtags a, a[rel='tag']")]
            comm_el = entry.select_one(".entryunit__comments a, .comments-links a")

            if title_el or body_el:
                items.append(
                    ExtractedItem(
                        item_url=title_el.get("href") if title_el else None,
                        title=title_el.get_text(strip=True) if title_el else None,
                        text=body_el.get_text(" ", strip=True)[:1500] if body_el else None,
                        author="5koleso",
                        published_at_raw=(date_el.get("datetime") or date_el.get_text(strip=True)) if date_el else None,
                        metrics={"comments_raw": comm_el.get_text(strip=True) if comm_el else "0"},
                        domain_attributes={"tags": tags, "platform": "LiveJournal"},
                    )
                )
        return {"blog_account": "5koleso"}, items

    def _parse_dzen_news(self, soup: BeautifulSoup, base_url: str) -> tuple[Dict, List[ExtractedItem]]:
        items = []
        for card in soup.select("article, [class*='news-card'], [class*='mg-card']"):
            title_el = card.select_one("h2, h3, [class*='title']")
            lead_el = card.select_one("[class*='annotation'], [class*='text']")
            src_el = card.select_one("[class*='source-name'], [class*='agency']")
            time_el = card.select_one("time, [class*='time'], [class*='date']")
            link_el = card.select_one("a[href]")

            if title_el and title_el.get_text(strip=True):
                items.append(
                    ExtractedItem(
                        item_url=urljoin(base_url, link_el["href"]) if link_el else None,
                        title=title_el.get_text(strip=True),
                        text=lead_el.get_text(" ", strip=True) if lead_el else None,
                        author=src_el.get_text(strip=True) if src_el else None,
                        published_at_raw=time_el.get_text(strip=True) if time_el else None,
                        domain_attributes={"rubric": "computers", "is_aggregated_story": True},
                    )
                )
        return {"aggregator": "dzen_news", "rubric": "computers"}, items

    def _parse_4pda(self, soup: BeautifulSoup, base_url: str) -> tuple[Dict, List[ExtractedItem]]:
        items = []
        for row in soup.select("tr[id^='t-'], table.ipbtable tr"):
            link = row.select_one("span[id^='tid-'] a, a[href*='showtopic=']")
            if not link:
                continue
            desc = row.select_one(".desc, span[id^='tdesc-']")
            tds = row.find_all("td")
            starter = tds[2].get_text(strip=True) if len(tds) > 2 else None
            replies = tds[3].get_text(strip=True) if len(tds) > 3 else None
            views = tds[4].get_text(strip=True) if len(tds) > 4 else None
            last_post = tds[5].get_text(" ", strip=True) if len(tds) > 5 else None

            items.append(
                ExtractedItem(
                    item_url=urljoin(base_url, link["href"]),
                    title=link.get_text(strip=True),
                    text=desc.get_text(strip=True) if desc else None,
                    author=starter,
                    published_at_raw=last_post,
                    metrics={"replies_count": replies, "views_count": views},
                    domain_attributes={"forum_id": 320},
                )
            )
        nav = soup.select_one("#navstrip")
        return {"breadcrumb": nav.get_text(" > ", strip=True) if nav else None}, items

    def _parse_otzovik(self, soup: BeautifulSoup, base_url: str) -> tuple[Dict, List[ExtractedItem]]:
        items = []
        for node in soup.select(".product-list .item, .products-table tr, .item-info"):
            name_el = node.select_one("a.product-name, h3 a, a[href*='/reviews/']")
            if not name_el:
                continue
            rating_el = node.select_one(".product-rating, .rating-score")
            cnt_el = node.select_one(".reviews-counter, a.reviews-count")

            items.append(
                ExtractedItem(
                    item_url=urljoin(base_url, name_el["href"]),
                    title=name_el.get_text(strip=True),
                    metrics={
                        "avg_rating": rating_el.get_text(strip=True) if rating_el else None,
                        "reviews_count": cnt_el.get_text(strip=True) if cnt_el else None,
                    },
                    domain_attributes={"category": "money_systems/banks"},
                )
            )
        return {"catalog_category": "Банки"}, items

    def _parse_drive2(self, soup: BeautifulSoup, base_url: str) -> tuple[Dict, List[ExtractedItem]]:
        items = []
        for card in soup.select(".c-post-preview, .js-entity, article"):
            title_el = card.select_one(".c-post-preview__title a, h2 a, h3 a")
            lead_el = card.select_one(".c-post-preview__lead, .c-post__body")
            author_el = card.select_one(".c-username, a[href*='/users/']")
            car_el = card.select_one("a[href*='/r/'], .c-car-title")
            date_el = card.select_one("time, [data-time]")
            likes_el = card.select_one(".c-like__counter, [class*='like']")

            if title_el or lead_el:
                items.append(
                    ExtractedItem(
                        item_url=urljoin(base_url, title_el["href"]) if (title_el and title_el.get("href")) else None,
                        title=title_el.get_text(strip=True) if title_el else None,
                        text=lead_el.get_text(" ", strip=True) if lead_el else None,
                        author=author_el.get_text(strip=True) if author_el else None,
                        published_at_raw=(date_el.get("datetime") or date_el.get_text(strip=True)) if date_el else None,
                        metrics={"likes": likes_el.get_text(strip=True) if likes_el else None},
                        domain_attributes={
                            "car_object": car_el.get_text(strip=True) if car_el else None,
                            "car_url": urljoin(base_url, car_el["href"]) if (car_el and car_el.get("href")) else None,
                        },
                    )
                )
        h1 = soup.select_one("h1")
        return {"topic_name": h1.get_text(strip=True) if h1 else None}, items

    def run_for_url(self, url: str, local_html: Optional[str] = None) -> SourceDocument:
        status, html = self.fetch(url, local_html)
        soup = BeautifulSoup(html, "lxml")
        og_data, json_ld = self.parse_structured_meta(soup)
        domain = urlparse(url).netloc.lower()

        if "irecommend.ru" in domain:
            s_class, gran = "review_platform", "object_hub"
            hub_m, items = self._parse_irecommend(soup, url)
        elif "livejournal.com" in domain:
            s_class, gran = "blog_platform", "topic_feed"
            hub_m, items = self._parse_livejournal(soup, url)
        elif "dzen.ru" in domain:
            s_class, gran = "news_aggregator", "topic_feed"
            hub_m, items = self._parse_dzen_news(soup, url)
        elif "4pda.to" in domain:
            s_class, gran = "thematic_forum", "catalog_index"
            hub_m, items = self._parse_4pda(soup, url)
        elif "otzovik.com" in domain:
            s_class, gran = "review_platform", "catalog_index"
            hub_m, items = self._parse_otzovik(soup, url)
        elif "drive2.ru" in domain:
            s_class, gran = "vertical_social_community", "topic_feed"
            hub_m, items = self._parse_drive2(soup, url)
        else:
            s_class, gran, hub_m, items = "unknown", "unknown", {}, []

        return SourceDocument(
            source_url=url,
            canonical_url=self.canonicalize_url(url),
            domain=domain,
            source_class=s_class,
            page_granularity=gran,
            http_status=status,
            page_title=soup.title.get_text(strip=True) if soup.title else None,
            open_graph=og_data,
            json_ld_blocks=json_ld,
            hub_metrics=hub_m,
            items=items,
        )

if __name__ == "__main__":
    SOURCES = [
        ("https://irecommend.ru/content/sait-ozontravel-0", "1.html"),
        ("https://5koleso.livejournal.com", "2.html"),
        ("https://dzen.ru/news/rubric/computers", "3.html"),
        ("https://4pda.to/forum/index.php?showforum=320", "4.html"),
        ("https://otzovik.com/money_systems/banks/", "5.html"),
        ("https://www.drive2.ru/featured-topics/CVQ0gEAAcHQ/", "6.html"),
    ]

    scraper = CompetitiveIntelligenceScraper()
    with open("output_dataset.jsonl", "w", encoding="utf-8") as out_file:
        for url, local_file in SOURCES:
            doc = scraper.run_for_url(url, local_html=local_file)
            out_file.write(doc.model_dump_json() + "\n")
            print(f"[{doc.source_class}] {doc.domain} | HTTP: {doc.http_status} | DQ: {doc.dq_report}")
