"""
Accuracy tests for entity and relationship extraction.

This package contains test suites that measure the precision, recall,
and F1 score of the extraction pipeline against known documentation samples.

Tests in this package:
- test_extraction_accuracy.py: Core accuracy metrics for entity extraction

Usage:
    pytest -m accuracy tests/accuracy/

Note:
    These tests require a running Ollama instance with the configured model.
    Tests will be skipped automatically if Ollama is not available.
"""
