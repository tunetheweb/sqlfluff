"""Implementation of Rule L018."""

from typing import cast

from sqlfluff.core.parser import (
    IdentitySet,
    NewlineSegment,
    PositionMarker,
    WhitespaceSegment,
)

from sqlfluff.core.rules.base import BaseRule, LintFix, LintResult, RuleContext
from sqlfluff.core.rules.doc_decorators import (
    document_configuration,
    document_fix_compatible,
    document_groups,
)
from sqlfluff.core.rules.functional import sp


@document_groups
@document_fix_compatible
@document_configuration
class Rule_L018(BaseRule):
    """``WITH`` clause closing bracket should be aligned with ``WITH`` keyword.

    **Anti-pattern**

    The ``•`` character represents a space.
    In this example, the closing bracket is not aligned with ``WITH`` keyword.

    .. code-block:: sql
       :force:

        WITH zoo AS (
            SELECT a FROM foo
        ••••)

        SELECT * FROM zoo

    **Best practice**

    Remove the spaces to align the ``WITH`` keyword with the closing bracket.

    .. code-block:: sql

        WITH zoo AS (
            SELECT a FROM foo
        )

        SELECT * FROM zoo

    """

    groups = ("all", "core")
    _works_on_unparsable = False
    needs_raw_stack = True
    config_keywords = ["tab_space_size"]

    def _eval(self, context: RuleContext):
        """WITH clause closing bracket should be aligned with WITH keyword.

        Look for a with clause and evaluate the position of closing brackets.
        """
        # We only trigger on start_bracket (open parenthesis)
        if context.segment.is_type("with_compound_statement"):
            raw_stack_buff = list(context.raw_stack)
            # Look for the with keyword
            for seg in context.segment.segments:
                if seg.name.lower() == "with":
                    seg_line_no = seg.pos_marker.line_no
                    break
            else:  # pragma: no cover
                # This *could* happen if the with statement is unparsable,
                # in which case then the user will have to fix that first.
                if any(s.is_type("unparsable") for s in context.segment.segments):
                    return LintResult()
                # If it's parsable but we still didn't find a with, then
                # we should raise that.
                raise RuntimeError("Didn't find WITH keyword!")

            def indent_size_up_to(segs):
                seg_buff = []
                # Get any segments running up to the WITH
                for elem in reversed(segs):
                    if elem.is_type("newline"):
                        break
                    elif elem.is_meta:
                        continue
                    else:
                        seg_buff.append(elem)
                # reverse the indent if we have one
                if seg_buff:
                    seg_buff = list(reversed(seg_buff))
                indent_str = "".join(seg.raw for seg in seg_buff).replace(
                    "\t", " " * self.tab_space_size
                )
                indent_size = len(indent_str)
                return indent_size, indent_str

            with_indent, with_indent_str = indent_size_up_to(raw_stack_buff)
            # Find the end brackets for the CTE *query* (i.e. ignore optional
            # list of CTE columns).
            cte_end_brackets = IdentitySet()
            for cte in context.functional.segment.children(
                sp.is_type("common_table_expression")
            ).iterate_segments():
                cte_end_bracket = (
                    cte.children()
                    .last(sp.is_type("bracketed"))
                    .children()
                    .last(sp.is_name("end_bracket"))
                )
                if cte_end_bracket:
                    cte_end_brackets.add(cte_end_bracket[0])
            for seg in context.segment.iter_segments(
                expanding=["common_table_expression", "bracketed"], pass_through=True
            ):
                if seg not in cte_end_brackets:
                    if seg.name != "start_bracket":
                        raw_stack_buff.append(seg)
                    continue

                closing_bracket_indent, _ = indent_size_up_to(raw_stack_buff)
                indent_diff = closing_bracket_indent - with_indent
                # Is indent of closing bracket not the same as
                # indent of WITH keyword.
                if seg.pos_marker.line_no == seg_line_no:
                    # Skip if it's the one-line version. That's ok
                    pass
                elif indent_diff < 0:
                    return LintResult(
                        anchor=seg,
                        fixes=[
                            LintFix.create_before(
                                seg,
                                [WhitespaceSegment(" " * (-indent_diff))],
                            )
                        ],
                    )
                elif indent_diff > 0:
                    # Is it all whitespace before the bracket on this line?
                    assert seg.pos_marker
                    prev_segs_on_line = [
                        elem
                        for elem in context.segment.raw_segments
                        if cast(PositionMarker, elem.pos_marker).line_no
                        == seg.pos_marker.line_no
                        and cast(PositionMarker, elem.pos_marker).line_pos
                        < seg.pos_marker.line_pos
                    ]
                    if all(elem.is_type("whitespace") for elem in prev_segs_on_line):
                        # We can move it back, it's all whitespace
                        fixes = [
                            LintFix.create_before(
                                seg,
                                [WhitespaceSegment(with_indent_str)],
                            )
                        ] + [LintFix.delete(elem) for elem in prev_segs_on_line]
                    else:
                        # We have to move it to a newline
                        fixes = [
                            LintFix.create_before(
                                seg,
                                [
                                    NewlineSegment(),
                                    WhitespaceSegment(with_indent_str),
                                ],
                            )
                        ]
                    return LintResult(anchor=seg, fixes=fixes)
