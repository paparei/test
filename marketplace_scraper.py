"""GGSEL marketplace scraper for competitor analysis.

Extracts competitor product listings, prices, and rankings from GGSEL category pages
using browser automation to bypass bot protection.
"""

import asyncio
import logging
import re
from dataclasses import dataclass
from typing import List, Optional, Dict
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeout


@dataclass
class CompetitorProduct:
    """Competitor product listing from marketplace."""
    product_id: Optional[int]
    title: str
    price_rub: float
    seller_name: str
    url: str
    rating: Optional[float] = None
    reviews_count: int = 0
    sales_count: int = 0
    position: int = 0  # Position in search results


class MarketplaceScraper:
    """Scrapes GGSEL marketplace for competitor analysis."""
    
    BASE_URL = "https://ggsel.net"
    
    def __init__(self, headless: bool = True, timeout: int = 30000):
        self.headless = headless
        self.timeout = timeout
        self._playwright = None
        self._browser = None
    
    async def detect_category_from_product(self, product_id: int) -> Optional[str]:
        """Detect category slug from product ID by following redirect.
        
        Args:
            product_id: GGSEL product ID
            
        Returns:
            Category slug or None if detection fails
            
        Example:
            102368933 -> "youtube-premium-activation"
        """
        if not self._browser:
            raise RuntimeError("Scraper not initialized. Use 'async with' context manager.")
        
        try:
            context = await self._browser.new_context(
                user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
            )
            page = await context.new_page()
            
            # Navigate to product URL
            url = f"{self.BASE_URL}/catalog/product/{product_id}"
            logging.info(f"Detecting category from product URL: {url}")
            
            await page.goto(url, wait_until='networkidle', timeout=self.timeout)
            
            # Get final URL after redirect
            final_url = page.url
            logging.info(f"Product redirected to: {final_url}")
            
            await context.close()
            
            # Extract category from URL pattern:
            # https://ggsel.net/catalog/product/title-with-category-slug-{product_id}
            # or breadcrumb navigation
            match = re.search(r'/catalog/([^/]+)/(?:product/)?', final_url)
            if match:
                category_slug = match.group(1)
                if category_slug != "product":
                    logging.info(f"Detected category slug: {category_slug}")
                    return category_slug
            
            # Fallback: extract from final URL path components
            # URL format: /catalog/product/youtube-premium-music-premium-3-12-mesiacev-5-discount-102368933
            path_match = re.search(r'/catalog/product/(.+)-\d+$', final_url)
            if path_match:
                full_slug = path_match.group(1)
                # Try to extract category part (usually first 2-3 words)
                # This is heuristic - may need refinement
                parts = full_slug.split('-')
                if len(parts) >= 2:
                    # Take first 2-3 meaningful words as category
                    category_slug = '-'.join(parts[:min(3, len(parts))])
                    logging.info(f"Heuristic category slug: {category_slug}")
                    return category_slug
            
            logging.warning(f"Could not detect category from URL: {final_url}")
            return None
            
        except Exception as e:
            logging.error(f"Category detection failed: {e}")
            return None
        
    async def __aenter__(self):
        self._playwright = await async_playwright().start()
        self._browser = await self._playwright.chromium.launch(headless=self.headless)
        return self
        
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self._browser:
            await self._browser.close()
        if self._playwright:
            await self._playwright.stop()
    
    async def scrape_category(self, category_slug: str, max_pages: int = 3) -> List[CompetitorProduct]:
        """Scrape competitor products from a category.
        
        Args:
            category_slug: Category URL slug (e.g., 'youtube-premium-activation')
            max_pages: Maximum number of pages to scrape
            
        Returns:
            List of competitor products
        """
        if not self._browser:
            raise RuntimeError("Scraper not initialized. Use 'async with' context manager.")
            
        products = []
        
        try:
            context = await self._browser.new_context(
                user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
            )
            page = await context.new_page()
            
            for page_num in range(1, max_pages + 1):
                url = f"{self.BASE_URL}/catalog/{category_slug}"
                if page_num > 1:
                    url += f"?page={page_num}"
                
                logging.info(f"Scraping {url}")
                
                try:
                    await page.goto(url, wait_until='networkidle', timeout=self.timeout)
                    await asyncio.sleep(2)  # Let JS render
                    
                    # Extract products from page
                    page_products = await self._extract_products_from_page(page)
                    
                    if not page_products:
                        logging.info(f"No products found on page {page_num}, stopping.")
                        break
                    
                    # Set position based on page
                    for i, product in enumerate(page_products):
                        product.position = (page_num - 1) * len(page_products) + i + 1
                    
                    products.extend(page_products)
                    logging.info(f"Extracted {len(page_products)} products from page {page_num}")
                    
                except PlaywrightTimeout:
                    logging.error(f"Timeout loading page {page_num}")
                    break
                except Exception as e:
                    logging.error(f"Error scraping page {page_num}: {e}")
                    break
            
            await context.close()
            
        except Exception as e:
            logging.error(f"Scraping failed: {e}")
        
        return products
    
    async def get_category_from_product_page(self, product_id: int) -> Optional[str]:
        """Get category slug by scraping breadcrumb from product page.
        
        More reliable than URL parsing - reads actual breadcrumb navigation.
        
        Args:
            product_id: GGSEL product ID
            
        Returns:
            Category slug or None
        """
        if not self._browser:
            raise RuntimeError("Scraper not initialized. Use 'async with' context manager.")
        
        try:
            context = await self._browser.new_context(
                user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
            )
            page = await context.new_page()
            
            url = f"{self.BASE_URL}/catalog/product/{product_id}"
            logging.info(f"Fetching category from product page: {url}")
            
            await page.goto(url, wait_until='networkidle', timeout=self.timeout)
            await asyncio.sleep(1)
            
            # Try to extract category from breadcrumb
            breadcrumb_selectors = [
                '.breadcrumb a[href*="/catalog/"]',
                '.breadcrumbs a[href*="/catalog/"]',
                'nav a[href*="/catalog/"]',
                '[itemtype*="BreadcrumbList"] a[href*="/catalog/"]'
            ]
            
            for selector in breadcrumb_selectors:
                try:
                    links = await page.query_selector_all(selector)
                    for link in links:
                        href = await link.get_attribute('href')
                        if href and '/catalog/' in href and '/product/' not in href:
                            # Extract category slug from href
                            match = re.search(r'/catalog/([^/?]+)', href)
                            if match:
                                category_slug = match.group(1)
                                logging.info(f"Found category from breadcrumb: {category_slug}")
                                await context.close()
                                return category_slug
                except Exception as e:
                    logging.debug(f"Breadcrumb selector {selector} failed: {e}")
                    continue
            
            # Fallback: parse from final URL
            final_url = page.url
            await context.close()
            
            return self._extract_category_from_url(final_url)
            
        except Exception as e:
            logging.error(f"Failed to get category from product page: {e}")
            return None
    
    @staticmethod
    def _extract_category_from_url(url: str) -> Optional[str]:
        """Extract category slug from GGSEL URL.
        
        Handles various URL patterns:
        - /catalog/{category}/
        - /catalog/{category}/product/{id}
        - /catalog/product/{slug}-{id}
        """
        # Pattern 1: /catalog/{category}/ or /catalog/{category}/product/
        match = re.search(r'/catalog/([^/]+)(?:/product)?', url)
        if match:
            slug = match.group(1)
            if slug != "product":
                return slug
        
        # Pattern 2: Extract from product slug (heuristic)
        # /catalog/product/youtube-premium-music-...-102368933
        match = re.search(r'/catalog/product/([a-z-]+)', url)
        if match:
            slug_part = match.group(1)
            # Common category patterns (2-3 words)
            parts = slug_part.split('-')
            if len(parts) >= 2:
                # Take first 2-3 words
                return '-'.join(parts[:min(3, len(parts))])
        
        return None
    
    async def _extract_products_from_page(self, page) -> List[CompetitorProduct]:
        """Extract product data from current page."""
        products = []
        
        try:
            # Wait for product cards to load
            await page.wait_for_selector('[data-product-id], .product-card, .good-card', timeout=5000)
        except PlaywrightTimeout:
            logging.warning("Product cards not found on page")
            return []
        
        # Get page content for parsing
        content = await page.content()
        
        # Try multiple selectors for product cards
        selectors = [
            '.product-card',
            '.good-card', 
            '[data-product-id]',
            '.catalog-item',
            '.item-card'
        ]
        
        for selector in selectors:
            cards = await page.query_selector_all(selector)
            if cards:
                logging.info(f"Found {len(cards)} products using selector: {selector}")
                break
        
        if not cards:
            logging.warning("No product cards found with any selector")
            return []
        
        for card in cards:
            try:
                product = await self._extract_product_from_card(card)
                if product:
                    products.append(product)
            except Exception as e:
                logging.debug(f"Failed to extract product from card: {e}")
                continue
        
        return products
    
    async def _extract_product_from_card(self, card) -> Optional[CompetitorProduct]:
        """Extract product data from a card element."""
        
        try:
            # Extract title
            title_elem = await card.query_selector('.title, .product-title, .good-title, h3, h4')
            title = await title_elem.inner_text() if title_elem else "Unknown"
            title = title.strip()
            
            # Extract price
            price_elem = await card.query_selector('.price, .product-price, .good-price, [data-price]')
            price_text = await price_elem.inner_text() if price_elem else "0"
            price_rub = self._parse_price(price_text)
            
            # Extract URL
            link_elem = await card.query_selector('a[href*="/goods/"], a[href*="/product/"]')
            url = await link_elem.get_attribute('href') if link_elem else ""
            if url and not url.startswith('http'):
                url = self.BASE_URL + url
            
            # Extract product ID from URL
            product_id = self._extract_product_id(url)
            
            # Extract seller name
            seller_elem = await card.query_selector('.seller, .seller-name, [data-seller]')
            seller_name = await seller_elem.inner_text() if seller_elem else "Unknown"
            seller_name = seller_name.strip()
            
            # Extract rating
            rating_elem = await card.query_selector('.rating, [data-rating]')
            rating = None
            if rating_elem:
                rating_text = await rating_elem.inner_text()
                rating = self._parse_rating(rating_text)
            
            # Extract reviews count
            reviews_elem = await card.query_selector('.reviews-count, .reviews')
            reviews_count = 0
            if reviews_elem:
                reviews_text = await reviews_elem.inner_text()
                reviews_count = self._parse_count(reviews_text)
            
            # Extract sales count
            sales_elem = await card.query_selector('.sales, .sales-count, [data-sales]')
            sales_count = 0
            if sales_elem:
                sales_text = await sales_elem.inner_text()
                sales_count = self._parse_count(sales_text)
            
            if not title or price_rub <= 0:
                return None
            
            return CompetitorProduct(
                product_id=product_id,
                title=title,
                price_rub=price_rub,
                seller_name=seller_name,
                url=url,
                rating=rating,
                reviews_count=reviews_count,
                sales_count=sales_count
            )
            
        except Exception as e:
            logging.debug(f"Error extracting product: {e}")
            return None
    
    @staticmethod
    def _parse_price(text: str) -> float:
        """Extract numeric price from text."""
        # Remove currency symbols and spaces
        clean = re.sub(r'[^\d.,]', '', text)
        clean = clean.replace(',', '.')
        
        try:
            return float(clean)
        except ValueError:
            return 0.0
    
    @staticmethod
    def _parse_rating(text: str) -> Optional[float]:
        """Extract rating number from text."""
        match = re.search(r'(\d+\.?\d*)', text)
        if match:
            try:
                return float(match.group(1))
            except ValueError:
                pass
        return None
    
    @staticmethod
    def _parse_count(text: str) -> int:
        """Extract count number from text."""
        match = re.search(r'(\d+)', text)
        if match:
            try:
                return int(match.group(1))
            except ValueError:
                pass
        return 0
    
    @staticmethod
    def _extract_product_id(url: str) -> Optional[int]:
        """Extract product ID from URL."""
        match = re.search(r'/goods/(\d+)', url)
        if match:
            try:
                return int(match.group(1))
            except ValueError:
                pass
        return None


async def scrape_category_async(category_slug: str, headless: bool = True, max_pages: int = 3) -> List[CompetitorProduct]:
    """Convenience function to scrape a category.
    
    Args:
        category_slug: Category URL slug
        headless: Run browser in headless mode
        max_pages: Maximum pages to scrape
        
    Returns:
        List of competitor products
    """
    async with MarketplaceScraper(headless=headless) as scraper:
        return await scraper.scrape_category(category_slug, max_pages)


def scrape_category_sync(category_slug: str, headless: bool = True, max_pages: int = 3) -> List[CompetitorProduct]:
    """Synchronous wrapper for category scraping.
    
    Args:
        category_slug: Category URL slug
        headless: Run browser in headless mode
        max_pages: Maximum pages to scrape
        
    Returns:
        List of competitor products
    """
    return asyncio.run(scrape_category_async(category_slug, headless, max_pages))


if __name__ == "__main__":
    # Test scraper
    logging.basicConfig(level=logging.INFO)
    
    test_categories = [
        "youtube-premium-activation",
        "spotify-premium-activation",
        "spotify-premium"
    ]
    
    for category in test_categories:
        print(f"\n{'='*60}")
        print(f"Scraping category: {category}")
        print('='*60)
        
        products = scrape_category_sync(category, headless=True, max_pages=1)
        
        print(f"\nFound {len(products)} products:")
        for i, product in enumerate(products[:5], 1):  # Show first 5
            print(f"\n{i}. {product.title}")
            print(f"   Price: {product.price_rub:.2f} RUB")
            print(f"   Seller: {product.seller_name}")
            print(f"   Position: #{product.position}")
            if product.rating:
                print(f"   Rating: {product.rating} ({product.reviews_count} reviews)")
            if product.sales_count:
                print(f"   Sales: {product.sales_count}")
            print(f"   URL: {product.url}")
