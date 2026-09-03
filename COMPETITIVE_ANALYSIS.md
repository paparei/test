# Competitive Analysis & Ad Bidding System

Analyzes competitor prices in GGSEL marketplace categories and provides pricing + advertising recommendations.

## Features

- **Web scraping** of GGSEL marketplace to extract competitor prices
- **Market positioning analysis** - see where you rank vs competitors
- **Pricing recommendations** based on competitive landscape
- **Ad bidding suggestions** based on market position
- **Category-level insights** - analyze entire product categories

## How It Works

1. **Scrapes marketplace** - Uses Playwright browser automation to extract competitor listings from category pages
2. **Analyzes position** - Calculates your price percentile, ranking, and competitive position
3. **Recommends pricing** - Suggests optimal price to balance competitiveness and profitability
4. **Suggests ad budget** - Recommends daily ad spend based on market position and competition

## Usage

### Via Telegram Bot

1. Click **📊 Competitive Analysis** from main menu
2. Enter your **product ID** (category auto-detected)
   - Example: `102368933`
3. Wait 20-40 seconds for analysis (includes category detection)

**Finding your product ID:**
- Go to your product page: `https://ggsel.net/catalog/product/102368933`
- The number at the end is your product ID

### Via Command Line

```bash
python competitive_analysis.py <product_id> <category_slug>
```

Example:
```bash
python competitive_analysis.py 12345 youtube-premium-activation
```

## Installation

### Dependencies

Add to [`requirements.txt`](requirements.txt):
```txt
playwright==1.50.0
beautifulsoup4==4.13.3
```

Install Playwright browsers:
```bash
pip install playwright
playwright install chromium
```

### Docker Deployment

For server deployment, update [`Dockerfile`](Dockerfile) to include Playwright:

```dockerfile
# Install Playwright dependencies
RUN pip install playwright && \
    playwright install --with-deps chromium
```

## Output Example

```
📊 Competitive Analysis: YouTube Premium Family 12 Month
🏷️ Category: youtube-premium-activation
⏰ 2026-09-03 09:30

💰 Pricing:
• Current: 2500 RUB
• Recommended: 2350 RUB
• Change: 📉 -6.0%
• Min profitable: 2200 RUB

🎯 Market Position:
• Your position: #8
• Competitors: 47
• Price percentile: 68%
• Market range: 1800 - 3500 RUB
• Market median: 2400 RUB
• Cheaper than you: 32
• More expensive: 15

📢 Advertising:
• Recommended daily budget: 235 RUB

💡 Analysis:
• 📊 Top 9 competitors median: 2350 RUB
• 📍 Your current position: #8
• ⚠️ You're in top 10 - consider slight price adjustment
• 💰 Your price is in top 30% (more expensive than 15 competitors)
• 📉 Consider lowering to median range for more sales

🎲 Confidence: 75%

🏆 Top Competitors:
1. YouTube Premium Family [12 Months]
   💵 1899 RUB | 👤 Digital Store Plus
2. YouTube Premium 12 Month Family Plan
   💵 2100 RUB | 👤 Premium Keys
3. YouTube Family Premium 1 Year
   💵 2199 RUB | 👤 Key Master
```

## How Pricing is Calculated

### Market Position Analysis
- Scrapes top 3 pages of category (configurable)
- Identifies your product in results (by title matching)
- Calculates price percentile among competitors
- Finds median/average competitor prices

### Pricing Strategy

**If you're cheap (bottom 30%):**
- Maintain price if sales are good
- Consider increasing to market median if high satisfaction

**If you're expensive (top 30%):**
- Maintain if reviews justify premium (>90% positive, 20+ reviews)
- Otherwise lower to competitive median

**If you're mid-range:**
- Target top competitor median prices
- Adjust based on your position in results

### Profitability Check
Ensures recommended price maintains target margin:
```
min_price = current_price / (1 - category_fee - payment_fee - target_margin)
```
Default target margin: 20%

### Ad Budget Recommendation

**Top 5 position:** 50 RUB/day (maintenance)
**Position 6-10:** 10% of item price
**Outside top 10:** 15% of item price × competition multiplier

## Configuration

### Scraping Settings

In [`marketplace_scraper.py`](marketplace_scraper.py):

```python
scraper = MarketplaceScraper(
    headless=True,        # Run headless browser
    timeout=30000         # Page load timeout (ms)
)

products = await scraper.scrape_category(
    category_slug="youtube-premium-activation",
    max_pages=3           # Pages to scrape
)
```

### Analysis Settings

In [`competitive_analysis.py`](competitive_analysis.py):

```python
recommendation = analyzer.analyze_product_in_category(
    product_id=12345,
    category_slug="youtube-premium-activation",
    target_profit_margin=0.20,  # 20% target margin
    scrape_pages=3              # Pages to analyze
)
```

## Technical Details

### Marketplace Scraper

Uses Playwright for browser automation to bypass bot protection:
- Emulates real browser with proper user agent
- Waits for JavaScript rendering
- Parses product cards from rendered HTML
- Extracts: title, price, seller, rating, position

### Data Extracted

For each competitor product:
- `product_id` - GGSEL product ID (if found in URL)
- `title` - Product name
- `price_rub` - Price in rubles
- `seller_name` - Seller display name
- `rating` - Average rating (if displayed)
- `reviews_count` - Number of reviews
- `sales_count` - Total sales (if displayed)
- `position` - Position in search results (1-based)

### Analysis Algorithm

1. **Fetch your product data** via GGSEL API
2. **Scrape competitor listings** from marketplace
3. **Calculate market metrics:**
   - Price distribution (min, max, median, average)
   - Your percentile position
   - Cheaper/more expensive competitors count
4. **Determine strategy:**
   - Compare to top 20% competitor prices
   - Factor in your sales history and reviews
   - Calculate minimum profitable price
5. **Generate recommendation:**
   - Suggested price with reasoning
   - Confidence score based on data quality
   - Ad budget suggestion based on position

## Limitations

### Bot Protection
- GGSEL uses Qrator anti-bot protection
- Requires browser automation (Playwright)
- Simple curl/requests won't work
- May need to adjust selectors if marketplace HTML changes

### Ad Bid Data
- Actual competitor ad bids are hidden until next day
- Recommendations based on market position heuristics
- Not direct competitor bid visibility

### Scraping Performance
- Takes 15-30 seconds per analysis
- Depends on marketplace page load speed
- May need rate limiting for bulk analysis

### Accuracy
- Product matching by title similarity (not perfect)
- Selectors may break if GGSEL redesigns marketplace
- Category fee data requires V2 API access

## Troubleshooting

### "No products found"
- Category slug may be incorrect
- Marketplace HTML structure changed (update selectors)
- Bot protection blocking scraper

### "Timeout loading page"
- Increase timeout in MarketplaceScraper
- Check internet connection
- GGSEL marketplace may be slow/down

### "Could not analyze product"
- Product ID invalid or not found
- API authentication failed
- Product has no price data

### Playwright installation issues

**Linux server:**
```bash
# Install system dependencies
playwright install-deps chromium
playwright install chromium
```

**Docker:**
Ensure Dockerfile includes `--with-deps` flag

## Files

- [`marketplace_scraper.py`](marketplace_scraper.py) - Browser automation scraper
- [`competitive_analysis.py`](competitive_analysis.py) - Analysis engine
- [`bot_service.py`](bot_service.py) - Telegram bot integration (lines 1318-1725)

## Future Enhancements

Potential improvements:
- **Ad bid tracking** - Capture historical ad spend if API becomes available
- **Price history** - Track competitor price changes over time
- **Automated repricing** - Auto-adjust prices based on competitor changes
- **Category database** - Cache category data to reduce scraping
- **Bulk analysis** - Analyze multiple products in one category
- **Competitor alerts** - Notify when competitor changes price significantly

## Notes

- Scraping is legal for publicly visible marketplace data
- Respect GGSEL's robots.txt and rate limits
- Consider caching results to reduce load
- Browser automation adds ~100MB RAM overhead per scraper instance
