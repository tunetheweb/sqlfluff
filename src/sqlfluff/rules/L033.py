"""Implementation of Rule L033."""
from sqlfluff.core.parser import (
    WhitespaceSegment,
    KeywordSegment,
)

from sqlfluff.core.rules.base import BaseRule, LintFix, LintResult, RuleContext
from sqlfluff.core.rules.doc_decorators import document_groups


@document_groups
class Rule_L033(BaseRule):
    """``UNION [DISTINCT|ALL]`` is preferred over just ``UNION``.

    .. note::
       This rule is only enabled for dialects that support ``UNION`` and
       ``UNION DISTINCT`` (``ansi``, ``hive``, ``mysql``, and ``redshift``).

    **Anti-pattern**

    In this example, ``UNION DISTINCT`` should be preferred over ``UNION``, because
    explicit is better than implicit.

    .. code-block:: sql

        SELECT a, b FROM table_1
        UNION
        SELECT a, b FROM table_2

    **Best practice**

    Specify ``DISTINCT`` or ``ALL`` after ``UNION`` (note that ``DISTINCT`` is the
    default behavior).

    .. code-block:: sql

        SELECT a, b FROM table_1
        UNION DISTINCT
        SELECT a, b FROM table_2

    """

    groups = ("all", "core")

    def _eval(self, context: RuleContext) -> LintResult:
        """Look for UNION keyword not immediately followed by DISTINCT or ALL.

        Note that UNION DISTINCT is valid, rule only applies to bare UNION.
        The function does this by looking for a segment of type set_operator
        which has a UNION but no DISTINCT or ALL.

        Note only some dialects have concept of UNION DISTINCT, so rule is only
        applied to dialects that are known to support this syntax.
        """
        if context.dialect.name not in [
            "ansi",
            "hive",
            "mysql",
            "redshift",
        ]:
            return LintResult()

        if context.segment.is_type("set_operator"):
            if "union" in context.segment.raw and not (
                "ALL" in context.segment.raw.upper()
                or "DISTINCT" in context.segment.raw.upper()
            ):
                return LintResult(
                    anchor=context.segment,
                    fixes=[
                        LintFix.replace(
                            context.segment.segments[0],
                            [
                                KeywordSegment("union"),
                                WhitespaceSegment(),
                                KeywordSegment("distinct"),
                            ],
                        )
                    ],
                )
            elif "UNION" in context.segment.raw.upper() and not (
                "ALL" in context.segment.raw.upper()
                or "DISTINCT" in context.segment.raw.upper()
            ):
                return LintResult(
                    anchor=context.segment,
                    fixes=[
                        LintFix.replace(
                            context.segment.segments[0],
                            [
                                KeywordSegment("UNION"),
                                WhitespaceSegment(),
                                KeywordSegment("DISTINCT"),
                            ],
                        )
                    ],
                )
        return LintResult()
