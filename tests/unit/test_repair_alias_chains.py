"""Unit tests for scripts/repair_alias_chains.py's pure chain-walking logic.

resolve_terminal takes no DB dependency -- just a tag_id and a
{tag_id: alias_of} map -- so these run without a database.
"""

import pytest

from scripts.repair_alias_chains import resolve_terminal


@pytest.mark.unit
class TestResolveTerminal:
    def test_tag_pointing_at_canonical_returns_that_canonical(self):
        # P -> A (canonical, alias_of=None)
        alias_map = {1: 2, 2: None}
        assert resolve_terminal(1, alias_map) == 2

    def test_two_hop_chain_resolves_to_terminal(self):
        # P -> A -> B (canonical)
        alias_map = {1: 2, 2: 3, 3: None}
        assert resolve_terminal(1, alias_map) == 3

    def test_three_hop_chain_resolves_to_terminal(self):
        # W -> X -> A -> B (canonical)
        alias_map = {1: 2, 2: 3, 3: 4, 4: None}
        assert resolve_terminal(1, alias_map) == 4

    def test_cycle_returns_none(self):
        # A -> B -> A
        alias_map = {1: 2, 2: 1}
        assert resolve_terminal(1, alias_map) is None
