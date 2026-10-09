from __future__ import annotations

import pytest

from query_gates.errors import SpecError
from query_gates.registry import (
    ASSERT,
    CHOICE,
    FilterRegistry,
    FilterRule,
    always,
    builtin_registry,
    excluded_actors_rule,
    matches_all,
    registry_from_config,
    touches,
)

BUILTIN_KEYS = [
    "read_only_hint",
    "composite_agreement_key",
    "right_party_two_flag",
    "installment_dedup",
    "status_set",
    "date_window",
    "excluded_actors",
]


def rule(key="r", kind=CHOICE, **kwargs):
    applies = kwargs.pop("applies", always)
    detect = kwargs.pop("detect", always)
    return FilterRule(key, key, kind, "d", applies, detect, **kwargs)


class TestRegistry:
    def test_builtin_keys_and_kinds(self):
        registry = builtin_registry()
        assert registry.keys() == BUILTIN_KEYS
        kinds = {r.key: r.kind for r in registry}
        assert [k for k, v in kinds.items() if v == ASSERT] == [
            "read_only_hint",
            "composite_agreement_key",
            "right_party_two_flag",
        ]

    def test_duplicate_keys_need_replace(self):
        registry = FilterRegistry([rule("a")])
        with pytest.raises(SpecError, match="already registered"):
            registry.register(rule("a"))
        registry.register(rule("a", kind=ASSERT), replace=True)
        assert registry.get("a").kind == ASSERT

    def test_unregister_unknown_is_an_error(self):
        with pytest.raises(SpecError, match="unknown rule"):
            FilterRegistry().unregister("ghost")

    def test_copy_is_independent(self):
        original = builtin_registry()
        clone = original.copy()
        clone.unregister("status_set")
        assert "status_set" in original
        assert "status_set" not in clone
        assert len(original) == len(BUILTIN_KEYS)

    def test_invalid_kind_is_rejected(self):
        with pytest.raises(SpecError, match="kind must be one of"):
            rule("x", kind="maybe")

    def test_dialect_gating(self):
        gated = rule("g", dialects=frozenset({"sqlserver"}))
        assert gated.applies_to_dialect("sqlserver")
        assert not gated.applies_to_dialect("sqlite")
        assert rule("open").applies_to_dialect("anything")


class TestHelpers:
    def test_matches_all_requires_every_pattern(self):
        both = matches_all(r"\bfoo\b", r"\bbar\b")
        assert both("FOO and bar")
        assert not both("foo only")

    def test_touches_is_word_based(self):
        pred = touches("agreements")
        assert pred("SELECT 1 FROM Agreements")
        assert not pred("SELECT 1 FROM agreement_debts")

    def test_bad_regex_is_a_spec_error(self):
        with pytest.raises(SpecError, match="invalid regular expression"):
            matches_all("(unclosed")


class TestExcludedActorsRule:
    @pytest.mark.parametrize(
        "clause",
        [
            "login NOT LIKE 'system%' AND login NOT LIKE 'test%'",
            "LOWER(login) NOT LIKE 'system%' AND LOWER(login) NOT LIKE 'test%'",
            "login not  like 'system%' and login not like 'test%'",
        ],
    )
    def test_detects_each_spelling_of_a_wildcard_pattern(self, clause):
        assert excluded_actors_rule().detect(f"SELECT 1 FROM agents WHERE {clause}")

    @pytest.mark.parametrize(
        "clause",
        [
            "login <> 'system%' AND login <> 'test%'",
            "login != 'system%' AND login != 'test%'",
            "login NOT IN ('system%', 'test%')",
            "login NOT LIKE 'system%' AND login <> 'test%'",
        ],
    )
    def test_literal_comparison_with_a_wildcard_pattern_excludes_nothing(self, clause):
        assert not excluded_actors_rule().detect(f"SELECT 1 FROM agents WHERE {clause}")

    @pytest.mark.parametrize(
        "clause",
        [
            "login <> 'nightly' AND login <> 'robot'",
            "login != 'nightly' AND login != 'robot'",
            "login NOT IN ('nightly', 'robot')",
            "login NOT LIKE 'nightly' AND login NOT LIKE 'robot'",
        ],
    )
    def test_an_exact_login_may_use_a_literal_comparison(self, clause):
        rule = excluded_actors_rule(("nightly", "robot"))
        assert rule.detect(f"SELECT 1 FROM agents WHERE {clause}")

    def test_an_underscore_is_a_wildcard_too(self):
        rule = excluded_actors_rule(("batch_job",))
        assert rule.detect("... login NOT LIKE 'batch_job'")
        assert not rule.detect("... login <> 'batch_job'")

    def test_every_pattern_is_required(self):
        partial = "SELECT 1 FROM agents WHERE login NOT LIKE 'system%'"
        assert not excluded_actors_rule().detect(partial)

    def test_custom_patterns(self):
        custom = excluded_actors_rule(("batch%",), key="batch_actors")
        assert custom.key == "batch_actors"
        assert custom.detect("... login NOT LIKE 'batch%'")
        assert not custom.detect("... login <> 'batch%'")
        assert not custom.detect("... login NOT LIKE 'system%'")


class TestFromConfig:
    def test_default_is_the_builtin_set(self):
        assert registry_from_config(None).keys() == BUILTIN_KEYS
        assert registry_from_config({}).keys() == BUILTIN_KEYS

    def test_builtin_off_and_disable(self):
        assert registry_from_config({"builtin": False}).keys() == []
        assert "status_set" not in registry_from_config({"disable": ["status_set"]})
        with pytest.raises(SpecError, match="unknown rule"):
            registry_from_config({"disable": ["ghost"]})

    def test_excluded_actor_patterns_override(self):
        registry = registry_from_config({"excluded_actor_patterns": ["batch%"]})
        assert registry.get("excluded_actors").detect("x NOT LIKE 'batch%'")

    def test_custom_rule_from_regexes(self):
        registry = registry_from_config(
            {
                "builtin": False,
                "rules": [
                    {
                        "key": "tenant_scope",
                        "name": "Tenant scope",
                        "kind": "assert",
                        "applies_when": [r"\bFROM\s+invoices\b"],
                        "detected_when": [r"\btenant_id\s*="],
                        "dialects": ["sqlite"],
                    }
                ],
            }
        )
        custom = registry.get("tenant_scope")
        assert custom.kind == ASSERT
        assert custom.applies("SELECT 1 FROM invoices")
        assert not custom.applies("SELECT 1 FROM orders")
        assert custom.detect("... WHERE tenant_id = ?")
        assert custom.dialects == frozenset({"sqlite"})

    def test_custom_rule_replaces_a_builtin_only_when_asked(self):
        spec = {"key": "status_set", "detected_when": ["x"]}
        with pytest.raises(SpecError, match="already registered"):
            registry_from_config({"rules": [spec]})
        registry = registry_from_config({"rules": [{**spec, "replace": True}]})
        assert registry.get("status_set").detect("x")

    @pytest.mark.parametrize(
        ("config", "message"),
        [
            ({"nonsense": 1}, "unknown keys"),
            ({"rules": ["not an object"]}, "must be an object"),
            ({"rules": [{"detected_when": ["x"]}]}, "'key' is required"),
            ({"rules": [{"key": "k"}]}, "needs at least one regular expression"),
            ({"rules": [{"key": "k", "detected_when": ["x"], "color": "red"}]}, "unknown keys"),
            ({"rules": [{"key": "k", "detected_when": "x"}]}, "must be a list of strings"),
            ({"rules": [{"key": "k", "detected_when": ["x"], "kind": "maybe"}]}, "kind must be"),
            ({"rules": [{"key": "k", "detected_when": ["("]}]}, "invalid regular expression"),
        ],
    )
    def test_malformed_config_is_rejected(self, config, message):
        with pytest.raises(SpecError, match=message):
            registry_from_config(config)
