"""
News Sentiment Engine (Future Enhancement).

Uses FinBERT to analyze financial news and generate sentiment scores
that act as filters for trading signals.

Note: This requires additional dependencies:
    pip install transformers torch

This is an optional enhancement — the bot works without it.
"""

from __future__ import annotations

from loguru import logger


class NewsSentimentEngine:
    """
    Analyzes financial news headlines for sentiment.

    Uses ProsusAI/finbert (Hugging Face) for financial-specific NLP.
    Outputs:  -1 (very bearish) to +1 (very bullish)

    Integration:
        - Positive sentiment → allow BUY signals, suppress SELL
        - Negative sentiment → allow SELL signals, suppress BUY
        - Neutral → no filter
    """

    def __init__(self, enabled: bool = False):
        self.enabled = enabled
        self._model = None
        self._tokenizer = None

        if self.enabled:
            self._load_model()

    def _load_model(self):
        """Load FinBERT model (lazy, downloads on first use)."""
        try:
            from transformers import AutoTokenizer, AutoModelForSequenceClassification

            model_name = "ProsusAI/finbert"
            self._tokenizer = AutoTokenizer.from_pretrained(model_name)
            self._model = AutoModelForSequenceClassification.from_pretrained(model_name)
            self._model.eval()
            logger.info("FinBERT sentiment model loaded successfully")
        except ImportError:
            logger.warning(
                "transformers/torch not installed — news sentiment disabled. "
                "Install with: pip install transformers torch"
            )
            self.enabled = False
        except Exception as e:
            logger.error(f"Failed to load FinBERT model: {e}")
            self.enabled = False

    def analyze(self, headline: str) -> dict:
        """
        Analyze a single news headline.

        Returns:
            {"sentiment": "positive"|"negative"|"neutral", "score": float (-1 to 1)}
        """
        if not self.enabled or not self._model:
            return {"sentiment": "neutral", "score": 0.0}

        try:
            import torch

            inputs = self._tokenizer(
                headline, return_tensors="pt", padding=True, truncation=True, max_length=512
            )

            with torch.no_grad():
                outputs = self._model(**inputs)
                probs = torch.nn.functional.softmax(outputs.logits, dim=-1)

            # FinBERT labels: positive, negative, neutral
            positive = float(probs[0][0])
            negative = float(probs[0][1])

            # Convert to -1 to +1 score
            score = positive - negative

            if score > 0.2:
                sentiment = "positive"
            elif score < -0.2:
                sentiment = "negative"
            else:
                sentiment = "neutral"

            return {"sentiment": sentiment, "score": score}

        except Exception as e:
            logger.error(f"Sentiment analysis failed: {e}")
            return {"sentiment": "neutral", "score": 0.0}

    def analyze_batch(self, headlines: list[str]) -> dict:
        """
        Analyze multiple headlines and return aggregate sentiment.

        Returns:
            {"overall_sentiment": str, "overall_score": float, "details": list}
        """
        if not headlines:
            return {"overall_sentiment": "neutral", "overall_score": 0.0, "details": []}

        details = [self.analyze(h) for h in headlines]
        avg_score = sum(d["score"] for d in details) / len(details)

        if avg_score > 0.15:
            overall = "positive"
        elif avg_score < -0.15:
            overall = "negative"
        else:
            overall = "neutral"

        return {
            "overall_sentiment": overall,
            "overall_score": avg_score,
            "details": details,
        }

    def should_filter_signal(self, signal_side: str, sentiment_score: float) -> bool:
        """
        Determine if a signal should be filtered based on sentiment.

        Strong negative news → suppress BUY signals
        Strong positive news → suppress SELL signals
        """
        if not self.enabled:
            return False  # Don't filter if sentiment engine is off

        threshold = 0.4  # Only filter on strong sentiment

        if signal_side == "buy" and sentiment_score < -threshold:
            logger.info(f"📰 Suppressing BUY signal due to negative news (score: {sentiment_score:.2f})")
            return True

        if signal_side == "sell" and sentiment_score > threshold:
            logger.info(f"📰 Suppressing SELL signal due to positive news (score: {sentiment_score:.2f})")
            return True

        return False
