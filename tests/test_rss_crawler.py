"""Tests for the RSS/Atom crawler.

All HTTP calls are mocked — no network access.
Tests verify entry hash stability, timestamp normalization, and error paths.
"""

from __future__ import annotations

import hashlib
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tools.rss_crawler import RSSCrawler, RSSEntry, RSSFeedResult


class TestRSSEntry:
    def test_entry_hash_is_deterministic(self):
        entry = RSSEntry(
            title="Acme launches AI feature",
            url="https://acme.example.com/blog/ai-feature",
            summary="Summary text",
            content="Full content",
            published_at="2026-06-14T09:00:00+00:00",
        )
        expected = hashlib.sha256(
            "https://acme.example.com/blog/ai-featureAcme launches AI feature".encode()
        ).hexdigest()[:16]
        assert entry.entry_hash == expected

    def test_same_url_same_title_produces_same_hash(self):
        entry_a = RSSEntry(
            title="Launch post",
            url="https://blog.example.com/post1",
            summary="s",
            content="c",
            published_at="2026-01-01",
        )
        entry_b = RSSEntry(
            title="Launch post",
            url="https://blog.example.com/post1",
            summary="different summary",
            content="different content",
            published_at="2026-06-01",
        )
        assert entry_a.entry_hash == entry_b.entry_hash

    def test_different_url_produces_different_hash(self):
        entry_a = RSSEntry(
            title="Post",
            url="https://blog.example.com/post1",
            summary="s",
            content="c",
            published_at="2026-01-01",
        )
        entry_b = RSSEntry(
            title="Post",
            url="https://blog.example.com/post2",
            summary="s",
            content="c",
            published_at="2026-01-01",
        )
        assert entry_a.entry_hash != entry_b.entry_hash

    def test_hash_length_is_16(self):
        entry = RSSEntry(
            title="T", url="https://x.com", summary="s", content="c", published_at="2026-01-01"
        )
        assert len(entry.entry_hash) == 16

    def test_tags_default_to_empty_list(self):
        entry = RSSEntry(
            title="T", url="https://x.com", summary="s", content="c", published_at="2026-01-01"
        )
        assert entry.tags == []

    def test_repr_includes_title_and_url(self):
        entry = RSSEntry(
            title="Test Post",
            url="https://example.com/test",
            summary="s",
            content="c",
            published_at="2026-06-14",
        )
        r = repr(entry)
        assert "Test Post" in r
        assert "example.com" in r


class TestRSSFeedResult:
    def test_entries_count_auto_computed(self):
        entries = [
            RSSEntry(title="A", url="https://x.com/a", summary="s", content="c", published_at="2026-01-01"),
            RSSEntry(title="B", url="https://x.com/b", summary="s", content="c", published_at="2026-01-02"),
        ]
        result = RSSFeedResult(
            feed_url="https://acme.example.com/rss",
            feed_title="Acme Blog",
            entries=entries,
            crawl_timestamp="2026-06-14T09:00:00+00:00",
        )
        assert result.feed_url == "https://acme.example.com/rss"
        assert result.entries_count == 2

    def test_empty_entries_gives_zero_count(self):
        result = RSSFeedResult(
            feed_url="https://acme.example.com/rss",
            feed_title="Acme Blog",
            entries=[],
            crawl_timestamp="2026-06-14T09:00:00+00:00",
        )
        assert result.entries_count == 0


class TestRSSCrawlerRequiresConnect:
    @pytest.mark.asyncio
    async def test_fetch_without_connect_raises_crawl_error(self):
        from tools.errors import CrawlError

        crawler = RSSCrawler()
        # Not connected — no await crawler.connect()
        with pytest.raises(CrawlError) as exc_info:
            await crawler.fetch_feed(feed_url="https://acme.example.com/rss", company="Acme")
        assert exc_info.value.code == "CRAWL_FAILED"


class TestRSSCrawlerNoFeedparser:
    @pytest.mark.asyncio
    async def test_raises_parse_error_when_feedparser_unavailable(self):
        from tools.errors import CrawlError

        crawler = RSSCrawler()
        await crawler.connect()

        # Mock the HTTP response so we get past the network layer
        resp = AsyncMock()
        resp.status_code = 200
        resp.text = "<rss/>"
        resp.headers = {}
        resp.raise_for_status = MagicMock()
        crawler._client = AsyncMock()
        crawler._client.get = AsyncMock(return_value=resp)

        with patch("tools.rss_crawler._HAS_FEEDPARSER", False):
            with pytest.raises(CrawlError) as exc_info:
                await crawler.fetch_feed(feed_url="https://acme.example.com/rss", company="Acme")
        assert exc_info.value.code == "CRAWL_PARSE_ERROR"
        assert "source_url" in exc_info.value.context

        await crawler.disconnect()


class TestRSSCrawlerFetchMultiple:
    @pytest.mark.asyncio
    async def test_returns_one_result_per_successful_feed(self):
        crawler = RSSCrawler()
        await crawler.connect()

        call_results = {
            "https://acme.example.com/rss": RSSFeedResult(
                feed_url="https://acme.example.com/rss",
                feed_title="Acme",
                entries=[],
                crawl_timestamp="2026-06-14T09:00:00+00:00",
            ),
            "https://rival.example.com/feed": RSSFeedResult(
                feed_url="https://rival.example.com/feed",
                feed_title="Rival",
                entries=[],
                crawl_timestamp="2026-06-14T09:00:00+00:00",
            ),
        }

        async def fake_fetch(feed_url, company, **kwargs):
            return call_results[feed_url]

        with patch.object(crawler, "fetch_feed", side_effect=fake_fetch):
            feeds = [
                {"url": "https://acme.example.com/rss", "company": "Acme"},
                {"url": "https://rival.example.com/feed", "company": "Rival"},
            ]
            results = await crawler.fetch_multiple(feeds)

        assert len(results) == 2
        urls = {r.feed_url for r in results}
        assert "https://acme.example.com/rss" in urls

        await crawler.disconnect()

    @pytest.mark.asyncio
    async def test_failed_feed_does_not_block_others(self):
        from tools.errors import CrawlError, ErrorCode

        crawler = RSSCrawler()
        await crawler.connect()

        async def fake_fetch(feed_url, company, **kwargs):
            if company == "Acme":
                raise CrawlError(
                    code=ErrorCode.CRAWL_FAILED,
                    message="Connection refused",
                    source_url=feed_url,
                )
            return RSSFeedResult(
                feed_url=feed_url,
                feed_title=company,
                entries=[],
                crawl_timestamp="2026-06-14",
            )

        with patch.object(crawler, "fetch_feed", side_effect=fake_fetch):
            feeds = [
                {"url": "https://acme.example.com/rss", "company": "Acme"},
                {"url": "https://rival.example.com/rss", "company": "Rival"},
            ]
            results = await crawler.fetch_multiple(feeds)

        # Only Rival succeeds
        assert len(results) == 1
        assert results[0].feed_title == "Rival"

        await crawler.disconnect()
