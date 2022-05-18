"""Implementation of Rule L023."""

from typing import Optional, List

from sqlfluff.core.parser import BaseSegment, WhitespaceSegment

from sqlfluff.core.rules.base import BaseRule, LintFix, LintResult, RuleContext
from sqlfluff.core.rules.doc_decorators import document_fix_compatible, document_groups


@document_groups
@document_fix_compatible
class Rule_L023(BaseRule):
    """Single whitespace expected after ``AS`` in ``WITH`` clause.

    **Anti-pattern**

    .. code-block:: sql

        WITH plop AS(
            SELECT * FROM foo
        )

        SELECT a FROM plop


    **Best practice**

    Add a space after ``AS``, to avoid confusing it for a function.
    The ``•`` character represents a space.

    .. code-block:: sql
       :force:

        WITH plop AS•(
            SELECT * FROM foo
        )

        SELECT a FROM plop
    """

    groups = ("all", "core")
    expected_mother_segment_type = "with_compound_statement"
    pre_segment_identifier = ("name", "as")
    post_segment_identifier = ("type", "bracketed")
    allow_newline = False  # hard-coded, could be configurable
    expand_children: Optional[List[str]] = ["common_table_expression"]

    def _eval(self, context: RuleContext) -> Optional[List[LintResult]]:
        """Single whitespace expected in mother middle segment."""
        error_buffer: List[LintResult] = []
        if context.segment.is_type(self.expected_mother_segment_type):
            last_code = None
            mid_segs: List[BaseSegment] = []
            for seg in context.segment.iter_segments(expanding=self.expand_children):
                if seg.is_code:
                    if (
                        last_code
                        and self.matches_target_tuples(
                            last_code, [self.pre_segment_identifier]
                        )
                        and self.matches_target_tuples(
                            seg, [self.post_segment_identifier]
                        )
                    ):
                        # Do we actually have the right amount of whitespace?
                        raw_inner = "".join(s.raw for s in mid_segs)
                        if raw_inner != " " and not (
                            self.allow_newline
                            and any(s.name == "newline" for s in mid_segs)
                        ):
                            if not raw_inner.strip():
                                # There's some whitespace and/or newlines, or nothing
                                fixes = []
                                if raw_inner:
                                    # There's whitespace and/or newlines. Drop those.
                                    fixes += [
                                        LintFix.delete(mid_seg) for mid_seg in mid_segs
                                    ]
                                # Enforce a single space
                                fixes += [
                                    LintFix.create_before(
                                        seg,
                                        [WhitespaceSegment()],
                                    )
                                ]
                            else:
                                # Don't otherwise suggest a fix for now.
                                # Only whitespace & newlines are covered.
                                # At least a comment section between `AS` and `(` can
                                # result in an unfixable error.
                                # TODO: Enable more complex fixing here.
                                fixes = None  # pragma: no cover
                            error_buffer.append(
                                LintResult(anchor=last_code, fixes=fixes)
                            )
                    mid_segs = []
                    if not seg.is_meta:
                        last_code = seg
                else:
                    mid_segs.append(seg)
        return error_buffer or None
