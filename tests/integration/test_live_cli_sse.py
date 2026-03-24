"""
Live Copilot CLI integration coverage for the SSE stream.

These tests are intentionally excluded by default and only run when the
environment is prepared for a real Copilot CLI session.
"""

import pytest

pytestmark = pytest.mark.integration


class TestSSEStream:
    """These tests require a real Copilot CLI. Skipped by default."""

    async def test_sse_stream_delivers_events(self, tmp_codebase):
        """Requires live Copilot CLI."""
        pytest.skip("Requires live Copilot CLI")
