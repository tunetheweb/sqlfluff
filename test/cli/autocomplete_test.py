"""Test autocomplete commands."""
import pytest

from sqlfluff.cli.autocomplete import dialect_shell_complete


@pytest.mark.parametrize(
    "incomplete,expected",
    [
        ["an", ["ansi"]],
        ["s", ["snowflake", "soql", "sparksql", "sqlite"]],
        ["post", ["postgres"]],
    ],
)
def test_dialect_click_type_shell_complete(incomplete, expected):
    """Check that autocomplete returns dialects as expected."""
    completion_items = dialect_shell_complete(
        ctx="dummy_not_used", param="dummy_not_used", incomplete=incomplete
    )
    actual = [c.value for c in completion_items]
    assert expected == actual
