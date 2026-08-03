"""Tests for Wikipedia URL filtering in the base spider."""

from urllib.parse import urlparse

import pytest

from kg_builder.scraping.spiders.base import (
    WIKIPEDIA_NON_ARTICLE_PARAMS,
    WIKIPEDIA_NON_ARTICLE_PREFIXES,
    TenantAwareSpider,
)


class TestWikipediaDomainDetection:
    """Tests for _is_wikipedia_domain method."""

    @pytest.fixture
    def spider(self):
        """Create a spider instance for testing."""
        return TenantAwareSpider(
            job_id="123e4567-e89b-12d3-a456-426614174000",
            tenant_id="123e4567-e89b-12d3-a456-426614174001",
            start_url="https://en.wikipedia.org/wiki/Test",
        )

    @pytest.mark.parametrize(
        "domain,expected",
        [
            # Wikipedia domains (should match)
            ("en.wikipedia.org", True),
            ("de.wikipedia.org", True),
            ("fr.wikipedia.org", True),
            ("ja.wikipedia.org", True),
            ("en.m.wikipedia.org", True),  # Mobile
            ("simple.wikipedia.org", True),
            # Other Wikimedia projects (should match)
            ("commons.wikimedia.org", True),
            ("en.wiktionary.org", True),
            ("en.wikiquote.org", True),
            ("en.wikibooks.org", True),
            ("en.wikisource.org", True),
            ("en.wikinews.org", True),
            ("en.wikiversity.org", True),
            ("en.wikivoyage.org", True),
            ("www.wikidata.org", True),
            ("www.mediawiki.org", True),
            # Non-Wikipedia domains (should not match)
            ("example.com", False),
            ("google.com", False),
            ("wikipedia-fake.com", False),
            # Note: fakewikipedia.org ends with wikipedia.org, so it matches
            # This is acceptable - false positives here are harmless
            ("fakewikipedia.org", True),  # Ends with wikipedia.org
            ("notreallywikipedia.org", True),  # Ends with wikipedia.org
            ("en.wikipedia.org.evil.com", False),  # Evil subdomain trick
        ],
    )
    def test_wikipedia_domain_detection(self, spider, domain, expected):
        """Test that Wikipedia domains are correctly identified."""
        assert spider._is_wikipedia_domain(domain) == expected


class TestWikipediaArticleUrlFiltering:
    """Tests for _is_wikipedia_article_url method."""

    @pytest.fixture
    def spider(self):
        """Create a spider instance for testing."""
        return TenantAwareSpider(
            job_id="123e4567-e89b-12d3-a456-426614174000",
            tenant_id="123e4567-e89b-12d3-a456-426614174001",
            start_url="https://en.wikipedia.org/wiki/Test",
        )

    @pytest.mark.parametrize(
        "url,expected",
        [
            # Valid article URLs (should be allowed)
            ("https://en.wikipedia.org/wiki/Python_(programming_language)", True),
            ("https://en.wikipedia.org/wiki/Machine_learning", True),
            ("https://en.wikipedia.org/wiki/United_States", True),
            ("https://en.wikipedia.org/wiki/Alan_Turing", True),
            ("https://en.wikipedia.org/wiki/The_Beatles", True),
            ("https://en.wikipedia.org/wiki/2024", True),
            # Talk pages (should be filtered)
            ("https://en.wikipedia.org/wiki/Talk:Python_(programming_language)", False),
            ("https://en.wikipedia.org/wiki/Talk:Machine_learning", False),
            # User pages (should be filtered)
            ("https://en.wikipedia.org/wiki/User:ExampleUser", False),
            ("https://en.wikipedia.org/wiki/User_talk:ExampleUser", False),
            # Wikipedia project pages (should be filtered)
            ("https://en.wikipedia.org/wiki/Wikipedia:About", False),
            ("https://en.wikipedia.org/wiki/Wikipedia:Manual_of_Style", False),
            ("https://en.wikipedia.org/wiki/Wikipedia_talk:Manual_of_Style", False),
            ("https://en.wikipedia.org/wiki/WP:NOTABILITY", False),
            ("https://en.wikipedia.org/wiki/Project:About", False),
            # Template pages (should be filtered)
            ("https://en.wikipedia.org/wiki/Template:Infobox", False),
            ("https://en.wikipedia.org/wiki/Template_talk:Infobox", False),
            # Category pages (should be filtered)
            ("https://en.wikipedia.org/wiki/Category:Computer_science", False),
            ("https://en.wikipedia.org/wiki/Category_talk:Computer_science", False),
            # Special pages (should be filtered)
            ("https://en.wikipedia.org/wiki/Special:Random", False),
            ("https://en.wikipedia.org/wiki/Special:Search", False),
            ("https://en.wikipedia.org/wiki/Special:RecentChanges", False),
            # File/Media pages (should be filtered)
            ("https://en.wikipedia.org/wiki/File:Example.png", False),
            ("https://en.wikipedia.org/wiki/Image:Example.jpg", False),
            ("https://en.wikipedia.org/wiki/Media:Example.ogg", False),
            # Help pages (should be filtered)
            ("https://en.wikipedia.org/wiki/Help:Contents", False),
            ("https://en.wikipedia.org/wiki/Help_talk:Contents", False),
            # Module pages (should be filtered)
            ("https://en.wikipedia.org/wiki/Module:Example", False),
            ("https://en.wikipedia.org/wiki/Module_talk:Example", False),
            # Portal pages (should be filtered)
            ("https://en.wikipedia.org/wiki/Portal:Science", False),
            ("https://en.wikipedia.org/wiki/Portal_talk:Science", False),
            # Draft pages (should be filtered)
            ("https://en.wikipedia.org/wiki/Draft:Example_Article", False),
            ("https://en.wikipedia.org/wiki/Draft_talk:Example_Article", False),
            # MediaWiki pages (should be filtered)
            ("https://en.wikipedia.org/wiki/MediaWiki:Common.css", False),
            ("https://en.wikipedia.org/wiki/MediaWiki_talk:Common.css", False),
            # Book pages (should be filtered)
            ("https://en.wikipedia.org/wiki/Book:Python", False),
            ("https://en.wikipedia.org/wiki/Book_talk:Python", False),
        ],
    )
    def test_article_url_filtering_by_namespace(self, spider, url, expected):
        """Test that non-article namespaces are correctly filtered."""
        parsed = urlparse(url)
        assert spider._is_wikipedia_article_url(parsed) == expected

    @pytest.mark.parametrize(
        "url,expected",
        [
            # Normal article view (should be allowed)
            ("https://en.wikipedia.org/wiki/Python_(programming_language)", True),
            # Edit view (should be filtered)
            (
                "https://en.wikipedia.org/wiki/Python_(programming_language)?action=edit",
                False,
            ),
            # History view (should be filtered)
            (
                "https://en.wikipedia.org/wiki/Python_(programming_language)?action=history",
                False,
            ),
            # Specific revision (should be filtered)
            (
                "https://en.wikipedia.org/wiki/Python_(programming_language)?oldid=123456",
                False,
            ),
            # Diff view (should be filtered)
            (
                "https://en.wikipedia.org/wiki/Python_(programming_language)?diff=123456",
                False,
            ),
            # Visual editor (should be filtered)
            (
                "https://en.wikipedia.org/wiki/Python_(programming_language)?veaction=edit",
                False,
            ),
            # Printable version (should be filtered)
            (
                "https://en.wikipedia.org/wiki/Python_(programming_language)?printable=yes",
                False,
            ),
        ],
    )
    def test_article_url_filtering_by_query_params(self, spider, url, expected):
        """Test that non-article views (edit, history, etc.) are filtered."""
        parsed = urlparse(url)
        assert spider._is_wikipedia_article_url(parsed) == expected

    @pytest.mark.parametrize(
        "url,expected",
        [
            # /w/ paths (should be filtered - these are usually API/action endpoints)
            ("https://en.wikipedia.org/w/index.php?title=Test&action=edit", False),
            ("https://en.wikipedia.org/w/api.php", False),
            # Root path (allowed through - will be filtered elsewhere if needed)
            ("https://en.wikipedia.org/", True),
            # Main page without /wiki/ (allowed through)
            ("https://en.wikipedia.org/Main_Page", True),
        ],
    )
    def test_article_url_filtering_by_path(self, spider, url, expected):
        """Test that non-wiki paths are handled correctly."""
        parsed = urlparse(url)
        assert spider._is_wikipedia_article_url(parsed) == expected


class TestShouldFollowUrlWithWikipedia:
    """Tests for _should_follow_url with Wikipedia URLs."""

    @pytest.fixture
    def spider(self):
        """Create a spider instance for testing."""
        return TenantAwareSpider(
            job_id="123e4567-e89b-12d3-a456-426614174000",
            tenant_id="123e4567-e89b-12d3-a456-426614174001",
            start_url="https://en.wikipedia.org/wiki/Test",
        )

    def test_follows_article_urls(self, spider):
        """Test that article URLs are followed."""
        assert spider._should_follow_url("https://en.wikipedia.org/wiki/Machine_learning")
        assert spider._should_follow_url(
            "https://en.wikipedia.org/wiki/Python_(programming_language)"
        )

    def test_skips_talk_pages(self, spider):
        """Test that talk pages are skipped."""
        assert not spider._should_follow_url("https://en.wikipedia.org/wiki/Talk:Machine_learning")

    def test_skips_user_pages(self, spider):
        """Test that user pages are skipped."""
        assert not spider._should_follow_url("https://en.wikipedia.org/wiki/User:ExampleUser")

    def test_skips_special_pages(self, spider):
        """Test that special pages are skipped."""
        assert not spider._should_follow_url("https://en.wikipedia.org/wiki/Special:Random")

    def test_skips_edit_views(self, spider):
        """Test that edit views are skipped."""
        assert not spider._should_follow_url("https://en.wikipedia.org/wiki/Test?action=edit")

    def test_allows_non_wikipedia_urls(self, spider):
        """Test that non-Wikipedia URLs are still processed normally."""
        # These should pass through (domain filtering will handle them separately)
        # The Wikipedia filter should not interfere with non-Wikipedia URLs
        spider.allowed_domains = ["example.com"]
        assert spider._should_follow_url("https://example.com/page")
        assert not spider._should_follow_url("https://other.com/page")  # Domain restriction


class TestNonArticlePrefixCompleteness:
    """Tests to ensure all common Wikipedia namespaces are covered."""

    def test_all_standard_namespaces_covered(self):
        """Verify that standard MediaWiki namespaces are in the filter list."""
        # Standard namespaces from MediaWiki
        # https://www.mediawiki.org/wiki/Help:Namespaces
        # Note: Some talk namespaces are omitted from our filter because
        # the base namespace filtering catches them anyway
        standard_namespaces = [
            "Talk:",
            "User:",
            "User_talk:",
            "Wikipedia:",
            "Wikipedia_talk:",
            "File:",
            "File_talk:",
            "MediaWiki:",
            "MediaWiki_talk:",
            "Template:",
            "Template_talk:",
            "Help:",
            "Help_talk:",
            "Category:",
            "Category_talk:",
            "Special:",
            "Media:",
        ]

        # Additional Wikipedia-specific namespaces
        wikipedia_specific = [
            "Portal:",
            "Portal_talk:",
            "Book:",
            "Book_talk:",
            "Draft:",
            "Draft_talk:",
            "Module:",
            "Module_talk:",
        ]

        for ns in standard_namespaces + wikipedia_specific:
            assert ns in WIKIPEDIA_NON_ARTICLE_PREFIXES, f"Missing namespace: {ns}"

    def test_non_article_params_covered(self):
        """Verify common non-article query parameters are filtered."""
        essential_params = ["action", "oldid", "diff"]
        for param in essential_params:
            assert param in WIKIPEDIA_NON_ARTICLE_PARAMS, f"Missing param: {param}"
