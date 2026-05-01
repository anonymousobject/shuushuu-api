"""Tests for app.core.logging configuration side-effects."""

import logging

import pytest

from app.core.logging import configure_logging


@pytest.mark.unit
def test_configure_logging_silences_arq_propagation():
    """
    arq emits its own progress lines via stdlib logging (e.g. the ``→`` job
    completion lines). With ``propagate=True`` those records bubble to the
    root logger, where structlog's stdlib handler re-emits them — producing
    duplicate worker-log lines (one prefixed with arq's timestamp, one bare).

    Stop the duplication by disabling propagation on the ``arq`` logger so
    only arq's own formatter writes the line.
    """
    # Re-enable propagation so we can prove configure_logging() turns it off.
    logging.getLogger("arq").propagate = True

    configure_logging()

    assert logging.getLogger("arq").propagate is False
