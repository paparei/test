"""Competitive analysis and bidding recommendation engine for GGSEL marketplace.

Analyzes competitor prices, market positioning, and recommends optimal pricing and ad bidding.
"""

import logging
import statistics
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple
from datetime import datetime

from config import Config
from ggsel_api import GGSelAPI
from marketplace_scraper import MarketplaceScraper, CompetitorProduct, scrape_category_sync


@dataclass
class ProductMetrics:
    """Performance metrics for a product."""
    product_id: int
    name: str
    current_price_rub: float
    total_sales: int
    total_refunds: int
    good_reviews: int
    bad_reviews: int
    in_stock: int
    category_id: Optional[int] = None
    category_fee: float = 0.0
    payment_fee: float = 0.0
    
    @property
    def conversion_rate(self) -> float:
        """Calculate successful transaction rate."""
        total_orders = self.total_sales + self.total_refunds
        return self.total_sales / total_orders if total_orders > 0 else 0.0
    
    @property
    def satisfaction_rate(self) -> float:
        """Calculate customer satisfaction rate."""
        total_reviews = self.good_reviews + self.bad_reviews
        return self.good_reviews / total_reviews if total_reviews > 0 else 0.0
    
    @property
    def net_margin_percent(self) -> float:
        """Calculate net margin after all fees."""
        return 100.0 - self.category_fee - self.payment_fee


@dataclass
class MarketPosition:
    """Market positioning analysis."""
    your_price: float
    your_position: Optional[int]  # Position in category if found
    competitors_count: int
    price_percentile: float  # 0-100, where you are in price distribution
    min_price: float
    max_price: float
    median_price: float
    avg_price: float
    cheaper_count: int  # How many competitors are cheaper
    more_expensive_count: int


@dataclass
class PricingRecommendation:
    """Pricing and advertising recommendation."""
    product_id: int
    product_name: str
    category_slug: str
    current_price_rub: float
    recommended_price_rub: int
    min_profitable_price_rub: int
    price_change_percent: float
    market_position: MarketPosition
    ad_bid_recommendation: Optional[float]  # Recommended daily ad budget in RUB
    confidence: float  # 0.0 to 1.0
    reasoning: List[str]
    top_competitors: List[CompetitorProduct]
    timestamp: datetime


class CompetitiveAnalyzer:
    """Analyze competitor prices and recommend pricing + ad bidding strategy."""
    
    def __init__(self, api: GGSelAPI, config: Config):
        self.api = api
        self.config = config
        self.logger = logging.getLogger(__name__)
    
    def analyze_product_in_category(
        self,
        product_id: int,
        category_slug: Optional[str] = None,
        target_profit_margin: float = 0.20,
        scrape_pages: int = 3
    ) -> Optional[PricingRecommendation]:
        """Analyze product against competitors in category.
        
        Args:
            product_id: Your product ID
            category_slug: Category URL slug (optional - will auto-detect if not provided)
            target_profit_margin: Target profit margin (0.20 = 20%)
            scrape_pages: Number of marketplace pages to scrape
            
        Returns:
            PricingRecommendation or None if analysis fails
        """
        # Get your product info
        product_info = self.api.get_product_info(product_id)
        if not product_info:
            self.logger.error(f"Could not fetch product {product_id}")
            return None
        
        # Auto-detect category if not provided
        if not category_slug:
            self.logger.info(f"Auto-detecting category for product {product_id}...")
            category_slug = self._detect_category_sync(product_id)
            if not category_slug:
                self.logger.error(f"Failed to auto-detect category for product {product_id}")
                return None
            self.logger.info(f"Detected category: {category_slug}")
        
        # Extract current metrics
        metrics = self._extract_product_metrics(product_id, product_info)
        if not metrics:
            return None
        
        # Scrape competitor data
        self.logger.info(f"Scraping category: {category_slug}")
        competitors = scrape_category_sync(category_slug, headless=True, max_pages=scrape_pages)
        
        if not competitors:
            self.logger.warning(f"No competitors found in {category_slug}")
            return None
        
        self.logger.info(f"Found {len(competitors)} competitors")
        
        # Analyze market position
        market_position = self._analyze_market_position(metrics.current_price_rub, competitors, metrics.name)
        
        # Calculate min profitable price
        min_price = self._calculate_min_profitable_price(
            metrics.current_price_rub,
            metrics.category_fee,
            metrics.payment_fee,
            target_profit_margin
        )
        
        # Generate pricing recommendation
        recommended_price, reasoning, confidence = self._recommend_price(
            metrics,
            market_position,
            min_price,
            competitors
        )
        
        # Recommend ad bidding
        ad_bid = self._recommend_ad_budget(
            market_position,
            recommended_price,
            metrics.total_sales
        )
        if ad_bid:
            reasoning.append(f"💰 Recommended daily ad budget: {ad_bid:.0f} RUB")
        
        # Get top 5 competitors for reference
        top_competitors = sorted(competitors, key=lambda x: x.position)[:5]
        
        price_change = ((recommended_price - metrics.current_price_rub) 
                       / metrics.current_price_rub * 100) if metrics.current_price_rub > 0 else 0
        
        return PricingRecommendation(
            product_id=product_id,
            product_name=metrics.name,
            category_slug=category_slug,
            current_price_rub=metrics.current_price_rub,
            recommended_price_rub=recommended_price,
            min_profitable_price_rub=min_price,
            price_change_percent=price_change,
            market_position=market_position,
            ad_bid_recommendation=ad_bid,
            confidence=confidence,
            reasoning=reasoning,
            top_competitors=top_competitors,
            timestamp=datetime.now()
        )
    
    def _detect_category_sync(self, product_id: int) -> Optional[str]:
        """Auto-detect category slug from product ID (sync wrapper).
        
        Args:
            product_id: GGSEL product ID
            
        Returns:
            Category slug or None
        """
        import asyncio
        from marketplace_scraper import MarketplaceScraper
        
        async def _detect():
            try:
                async with MarketplaceScraper(headless=True) as scraper:
                    # Try breadcrumb extraction first (most reliable)
                    category = await scraper.get_category_from_product_page(product_id)
                    if category:
                        return category
                    
                    # Fallback to URL-based detection
                    category = await scraper.detect_category_from_product(product_id)
                    return category
            except Exception as e:
                self.logger.error(f"Category detection error: {e}")
                return None
        
        try:
            return asyncio.run(_detect())
        except Exception as e:
            self.logger.error(f"Failed to run category detection: {e}")
            return None
    
    def _extract_product_metrics(self, product_id: int, data: Dict[str, Any]) -> Optional[ProductMetrics]:
        """Extract metrics from product API data."""
        try:
            item = data.get('item', {})
            
            # Get price
            price_rub = 0.0
            if 'price' in item:
                price_rub = float(item['price'])
            
            # Get category fees
            category_id = item.get('category_id')
            category_fee = 0.0
            payment_fee = 0.0
            
            if category_id:
                # Try to get V2 offer for fee info
                offers = self.api.list_v2_offers(item_id=product_id)
                if offers and len(offers) > 0:
                    offer = offers[0]
                    cat_info = offer.get('category', {})
                    category_fee = float(cat_info.get('fee', 0))
                    payment_fee = float(cat_info.get('payment_fee', 0))
            
            return ProductMetrics(
                product_id=product_id,
                name=item.get('name', 'Unknown'),
                current_price_rub=price_rub,
                total_sales=int(item.get('sales', 0)),
                total_refunds=int(item.get('refunds', 0)),
                good_reviews=int(item.get('good_reviews', 0)),
                bad_reviews=int(item.get('bad_reviews', 0)),
                in_stock=int(item.get('in_stock', 0)),
                category_id=category_id,
                category_fee=category_fee,
                payment_fee=payment_fee
            )
        except Exception as e:
            self.logger.error(f"Error extracting metrics: {e}")
            return None
    
    def _analyze_market_position(
        self, 
        your_price: float, 
        competitors: List[CompetitorProduct],
        your_title: str
    ) -> MarketPosition:
        """Analyze where you stand in the market."""
        prices = [c.price_rub for c in competitors if c.price_rub > 0]
        
        if not prices:
            return MarketPosition(
                your_price=your_price,
                your_position=None,
                competitors_count=0,
                price_percentile=50.0,
                min_price=your_price,
                max_price=your_price,
                median_price=your_price,
                avg_price=your_price,
                cheaper_count=0,
                more_expensive_count=0
            )
        
        # Find your position in listings (if product is in results)
        your_position = None
        for comp in competitors:
            if your_title.lower() in comp.title.lower() or comp.title.lower() in your_title.lower():
                your_position = comp.position
                break
        
        prices_sorted = sorted(prices)
        cheaper_count = sum(1 for p in prices if p < your_price)
        more_expensive_count = sum(1 for p in prices if p > your_price)
        
        # Calculate percentile
        percentile = (cheaper_count / len(prices) * 100) if prices else 50.0
        
        return MarketPosition(
            your_price=your_price,
            your_position=your_position,
            competitors_count=len(competitors),
            price_percentile=percentile,
            min_price=min(prices),
            max_price=max(prices),
            median_price=statistics.median(prices),
            avg_price=statistics.mean(prices),
            cheaper_count=cheaper_count,
            more_expensive_count=more_expensive_count
        )
    
    def _calculate_min_profitable_price(
        self,
        current_price: float,
        category_fee_pct: float,
        payment_fee_pct: float,
        target_margin: float
    ) -> int:
        """Calculate minimum price to maintain profitability.
        
        Formula: min_price = cost / (1 - fees - margin)
        Assuming cost is 0 (digital goods), we need: revenue * (1 - fees) >= target_margin
        """
        total_fees = (category_fee_pct + payment_fee_pct) / 100.0
        
        # For digital goods, we want net margin after fees
        # If current price is P, net = P * (1 - fees)
        # We want at least target_margin of revenue as net
        min_multiplier = 1.0 / max(0.01, (1.0 - total_fees - target_margin))
        
        # Use current price as baseline, apply multiplier
        min_price = current_price * min_multiplier
        
        # ponytail: Simplified profit calc assumes zero COGS for digital goods
        return max(1, int(min_price))
    
    def _recommend_price(
        self,
        metrics: ProductMetrics,
        market: MarketPosition,
        min_profitable: int,
        competitors: List[CompetitorProduct]
    ) -> Tuple[int, List[str], float]:
        """Generate pricing recommendation."""
        reasoning = []
        confidence = 0.7
        
        # Strategy: Position competitively while maintaining profitability
        
        # Get price range from top competitors
        top_20_pct = max(1, len(competitors) // 5)
        top_prices = [c.price_rub for c in sorted(competitors, key=lambda x: x.position)[:top_20_pct]]
        
        if top_prices:
            competitive_price = statistics.median(top_prices)
            reasoning.append(f"📊 Top {top_20_pct} competitors median: {competitive_price:.0f} RUB")
        else:
            competitive_price = market.median_price
            reasoning.append(f"📊 Market median: {competitive_price:.0f} RUB")
        
        # Check if you're in the market already
        if market.your_position:
            reasoning.append(f"📍 Your current position: #{market.your_position}")
            if market.your_position <= 5:
                reasoning.append("✅ You're in top 5 - maintain competitive pricing")
                confidence += 0.1
            elif market.your_position <= 10:
                reasoning.append("⚠️ You're in top 10 - consider slight price adjustment")
            else:
                reasoning.append("❗ Outside top 10 - price competitively or invest in ads")
                confidence -= 0.1
        
        # Analyze your price vs market
        if market.price_percentile < 30:
            reasoning.append(f"💵 Your price is in bottom 30% (cheaper than {market.cheaper_count} competitors)")
            # You're already cheap, maybe increase if profitable
            if metrics.total_sales > 10 and metrics.satisfaction_rate > 0.8:
                reasoning.append("📈 Good sales & reviews - you can afford to increase price")
                recommended = int(min(competitive_price, market.median_price))
            else:
                recommended = int(metrics.current_price_rub)
                reasoning.append("🔒 Maintain current price until more sales data")
        elif market.price_percentile > 70:
            reasoning.append(f"💰 Your price is in top 30% (more expensive than {market.more_expensive_count} competitors)")
            # You're expensive - lower unless quality justifies it
            if metrics.satisfaction_rate > 0.9 and metrics.good_reviews > 20:
                reasoning.append("⭐ High ratings justify premium pricing")
                recommended = int(metrics.current_price_rub)
                confidence += 0.1
            else:
                reasoning.append("📉 Consider lowering to median range for more sales")
                recommended = int(competitive_price)
        else:
            reasoning.append(f"🎯 Your price is mid-range ({market.price_percentile:.0f} percentile)")
            recommended = int(competitive_price)
        
        # Ensure profitability
        if recommended < min_profitable:
            reasoning.append(f"⚠️ Minimum profitable price: {min_profitable} RUB")
            reasoning.append(f"🔺 Adjusted recommendation to maintain {(metrics.net_margin_percent):.1f}% margin")
            recommended = min_profitable
            confidence -= 0.15
        
        # Confidence adjustments
        if market.competitors_count < 5:
            reasoning.append("⚠️ Limited competitor data - lower confidence")
            confidence -= 0.2
        elif market.competitors_count > 20:
            confidence += 0.1
        
        confidence = max(0.0, min(1.0, confidence))
        
        return recommended, reasoning, confidence
    
    def _recommend_ad_budget(
        self,
        market: MarketPosition,
        recommended_price: int,
        historical_sales: int
    ) -> Optional[float]:
        """Recommend daily ad budget based on market position."""
        
        # If already in top 5, minimal ad spend needed
        if market.your_position and market.your_position <= 5:
            return 50.0  # Minimal maintenance budget
        
        # If outside top 10 or not visible, need more aggressive bidding
        if not market.your_position or market.your_position > 10:
            # Budget scales with price and competition
            base_budget = recommended_price * 0.15  # 15% of item price
            competition_multiplier = min(2.0, market.competitors_count / 10)
            return base_budget * competition_multiplier
        
        # Mid-range position (6-10)
        return recommended_price * 0.10  # 10% of item price
    
    def format_recommendation(self, rec: PricingRecommendation) -> str:
        """Format recommendation for display."""
        lines = []
        lines.append(f"📊 **Competitive Analysis: {rec.product_name}**")
        lines.append(f"🏷️ Category: `{rec.category_slug}`")
        lines.append(f"⏰ {rec.timestamp.strftime('%Y-%m-%d %H:%M')}")
        lines.append("")
        
        lines.append("**💰 Pricing:**")
        lines.append(f"• Current: {rec.current_price_rub:.0f} RUB")
        lines.append(f"• Recommended: **{rec.recommended_price_rub} RUB**")
        
        if rec.price_change_percent != 0:
            change_emoji = "📈" if rec.price_change_percent > 0 else "📉"
            lines.append(f"• Change: {change_emoji} {rec.price_change_percent:+.1f}%")
        
        lines.append(f"• Min profitable: {rec.min_profitable_price_rub} RUB")
        lines.append("")
        
        lines.append("**🎯 Market Position:**")
        pos = rec.market_position
        if pos.your_position:
            lines.append(f"• Your position: **#{pos.your_position}**")
        lines.append(f"• Competitors: {pos.competitors_count}")
        lines.append(f"• Price percentile: {pos.price_percentile:.0f}%")
        lines.append(f"• Market range: {pos.min_price:.0f} - {pos.max_price:.0f} RUB")
        lines.append(f"• Market median: {pos.median_price:.0f} RUB")
        lines.append(f"• Cheaper than you: {pos.cheaper_count}")
        lines.append(f"• More expensive: {pos.more_expensive_count}")
        lines.append("")
        
        if rec.ad_bid_recommendation:
            lines.append("**📢 Advertising:**")
            lines.append(f"• Recommended daily budget: **{rec.ad_bid_recommendation:.0f} RUB**")
            lines.append("")
        
        lines.append("**💡 Analysis:**")
        for reason in rec.reasoning:
            lines.append(f"• {reason}")
        lines.append("")
        
        lines.append(f"**🎲 Confidence:** {rec.confidence*100:.0f}%")
        lines.append("")
        
        if rec.top_competitors:
            lines.append("**🏆 Top Competitors:**")
            for i, comp in enumerate(rec.top_competitors[:5], 1):
                lines.append(f"{i}. {comp.title[:40]}...")
                lines.append(f"   💵 {comp.price_rub:.0f} RUB | 👤 {comp.seller_name}")
            
        return "\n".join(lines)


if __name__ == "__main__":
    # Test competitive analyzer
    import sys
    from config import Config
    
    logging.basicConfig(level=logging.INFO)
    
    if len(sys.argv) < 3:
        print("Usage: python competitive_analysis.py <product_id> <category_slug>")
        print("Example: python competitive_analysis.py 12345 youtube-premium-activation")
        sys.exit(1)
    
    config = Config()
    api = GGSelAPI(config)
    analyzer = CompetitiveAnalyzer(api, config)
    
    product_id = int(sys.argv[1])
    category_slug = sys.argv[2]
    
    print(f"\nAnalyzing product {product_id} in category '{category_slug}'...\n")
    
    rec = analyzer.analyze_product_in_category(product_id, category_slug)
    
    if rec:
        print(analyzer.format_recommendation(rec))
    else:
        print("❌ Analysis failed")
