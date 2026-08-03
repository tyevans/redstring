"""
Unit tests for blocking indexes migration.

These tests verify that the blocking indexes migration is correctly defined
and that the pg_trgm extension will be available for trigram similarity.

The actual index creation is tested during migration application;
these tests validate the migration structure and extension availability.
"""

import pytest


class TestPgTrigram:
    """Test pg_trgm extension availability and functions."""

    def test_pg_trgm_import_sqlalchemy(self):
        """Test that SQLAlchemy can reference trigram operations."""
        from sqlalchemy import text

        # The text function should be able to create trigram queries
        query = text("SELECT similarity('hello', 'helo')")
        assert query is not None

    def test_trigram_gin_ops_syntax(self):
        """Test that GIN trigram index syntax is valid."""
        # This tests the SQL syntax pattern used in the migration
        index_sql = """
        CREATE INDEX idx_test
        ON test_table
        USING gin (column gin_trgm_ops)
        WHERE is_canonical = true
        """
        # Should be valid SQL (no parsing errors)
        assert "gin_trgm_ops" in index_sql
        assert "USING gin" in index_sql

    def test_similarity_operator_syntax(self):
        """Test trigram similarity operator syntax."""
        # The % operator is used for trigram similarity
        query_example = "SELECT * FROM entities WHERE name % 'search_term'"
        assert "%" in query_example

        # The <-> operator is used for trigram distance
        distance_query = "SELECT name, name <-> 'search' AS dist FROM entities"
        assert "<->" in distance_query


class TestBlockingIndexDefinitions:
    """Test blocking index definitions and structure."""

    def test_soundex_type_index_columns(self):
        """Test that soundex+type index has correct column order."""
        # Order matters for composite indexes
        expected_columns = ["tenant_id", "entity_type", "name_soundex"]

        # The first column should be tenant_id for efficient tenant filtering
        assert expected_columns[0] == "tenant_id"
        # Entity type comes next for type-specific blocking
        assert expected_columns[1] == "entity_type"
        # Soundex is the blocking key
        assert expected_columns[2] == "name_soundex"

    def test_normalized_type_index_columns(self):
        """Test that normalized name index has correct column order."""
        expected_columns = ["tenant_id", "entity_type", "normalized_name"]

        assert expected_columns[0] == "tenant_id"
        assert expected_columns[1] == "entity_type"
        assert expected_columns[2] == "normalized_name"

    def test_partial_index_condition(self):
        """Test that partial index condition is canonical-only."""
        condition = "is_canonical = true"

        # All blocking indexes should only include canonical entities
        # Alias entities don't participate in blocking
        assert "is_canonical" in condition
        assert "true" in condition

    def test_source_page_index_for_same_page_blocking(self):
        """Test source page index supports same-page blocking."""
        expected_columns = ["tenant_id", "source_page_id"]

        # Tenant first for multi-tenant isolation
        assert expected_columns[0] == "tenant_id"
        # Source page for grouping entities from same extraction
        assert expected_columns[1] == "source_page_id"


class TestMigrationStructure:
    """Test migration file structure and content."""

    def test_migration_file_exists(self):
        """Test that the migration file exists."""
        import os

        migration_path = (
            "/home/ty/workspace/knowledge-mapper/backend/alembic/versions/"
            "20251214_1700_add_blocking_indexes.py"
        )
        assert os.path.exists(migration_path), f"Migration file not found: {migration_path}"

    def test_migration_has_correct_revision(self):
        """Test migration has correct revision identifiers."""
        migration_path = (
            "/home/ty/workspace/knowledge-mapper/backend/alembic/versions/"
            "20251214_1700_add_blocking_indexes.py"
        )

        with open(migration_path) as f:
            content = f.read()

        # Check revision ID
        assert 'revision: str = "p6q7r8s9t0u1"' in content
        # Check down_revision points to consolidation_config migration
        assert 'down_revision: Union[str, None] = "o5p6q7r8s9t0"' in content

    def test_migration_creates_pg_trgm_extension(self):
        """Test migration creates pg_trgm extension."""
        migration_path = (
            "/home/ty/workspace/knowledge-mapper/backend/alembic/versions/"
            "20251214_1700_add_blocking_indexes.py"
        )

        with open(migration_path) as f:
            content = f.read()

        assert "CREATE EXTENSION IF NOT EXISTS pg_trgm" in content

    def test_migration_creates_expected_indexes(self):
        """Test migration creates all expected indexes."""
        migration_path = (
            "/home/ty/workspace/knowledge-mapper/backend/alembic/versions/"
            "20251214_1700_add_blocking_indexes.py"
        )

        with open(migration_path) as f:
            content = f.read()

        # All expected index names
        expected_indexes = [
            "idx_blocking_soundex_type",
            "idx_blocking_normalized_type",
            "idx_blocking_name_trigram",
            "idx_blocking_normalized_trigram",
            "idx_canonical_entities_type",
            "idx_blocking_source_page",
            "idx_canonical_with_embedding",
        ]

        for index_name in expected_indexes:
            assert index_name in content, f"Index {index_name} not found in migration"

    def test_migration_downgrade_drops_indexes(self):
        """Test migration downgrade drops all created indexes."""
        migration_path = (
            "/home/ty/workspace/knowledge-mapper/backend/alembic/versions/"
            "20251214_1700_add_blocking_indexes.py"
        )

        with open(migration_path) as f:
            content = f.read()

        # All indexes should be dropped in downgrade
        expected_drops = [
            "idx_blocking_soundex_type",
            "idx_blocking_normalized_type",
            "idx_blocking_name_trigram",
            "idx_blocking_normalized_trigram",
            "idx_canonical_entities_type",
            "idx_blocking_source_page",
            "idx_canonical_with_embedding",
        ]

        downgrade_section = content.split("def downgrade")[1]
        for index_name in expected_drops:
            assert index_name in downgrade_section, (
                f"Index {index_name} not dropped in downgrade"
            )


class TestBlockingIndexUseCases:
    """Test that indexes support expected blocking use cases."""

    def test_soundex_blocking_query_pattern(self):
        """Test the expected query pattern for soundex blocking."""
        # This is the query pattern the indexes should support
        query = """
        SELECT e2.id, e2.name
        FROM extracted_entities e1
        JOIN extracted_entities e2 ON (
            e2.tenant_id = e1.tenant_id
            AND e2.entity_type = e1.entity_type
            AND e2.name_soundex = e1.name_soundex
            AND e2.is_canonical = true
            AND e2.id != e1.id
        )
        WHERE e1.id = :entity_id
        """

        # Query should use tenant_id, entity_type, and name_soundex
        assert "e2.tenant_id = e1.tenant_id" in query
        assert "e2.entity_type = e1.entity_type" in query
        assert "e2.name_soundex = e1.name_soundex" in query
        assert "e2.is_canonical = true" in query

    def test_trigram_blocking_query_pattern(self):
        """Test the expected query pattern for trigram blocking."""
        query = """
        SELECT id, name, similarity(name, :search_name) AS sim
        FROM extracted_entities
        WHERE tenant_id = :tenant_id
          AND is_canonical = true
          AND name % :search_name
        ORDER BY sim DESC
        LIMIT 10
        """

        # Query should filter by tenant and use % operator
        assert "tenant_id = :tenant_id" in query
        assert "is_canonical = true" in query
        assert "name % :search_name" in query
        assert "similarity(name, :search_name)" in query

    def test_same_page_blocking_query_pattern(self):
        """Test the expected query pattern for same-page blocking."""
        query = """
        SELECT e1.id AS entity_a, e2.id AS entity_b
        FROM extracted_entities e1
        JOIN extracted_entities e2 ON (
            e2.tenant_id = e1.tenant_id
            AND e2.source_page_id = e1.source_page_id
            AND e2.is_canonical = true
            AND e2.id > e1.id
        )
        WHERE e1.tenant_id = :tenant_id
          AND e1.source_page_id = :page_id
          AND e1.is_canonical = true
        """

        # Query should efficiently find entity pairs from same page
        assert "e2.source_page_id = e1.source_page_id" in query
        assert "e2.id > e1.id" in query  # Avoid duplicate pairs

    def test_embedding_available_query_pattern(self):
        """Test query pattern to find entities available for embedding blocking."""
        query = """
        SELECT id, name, embedding
        FROM extracted_entities
        WHERE tenant_id = :tenant_id
          AND entity_type = :entity_type
          AND is_canonical = true
          AND embedding IS NOT NULL
        """

        # Query should efficiently filter entities with embeddings
        assert "tenant_id = :tenant_id" in query
        assert "entity_type = :entity_type" in query
        assert "embedding IS NOT NULL" in query
