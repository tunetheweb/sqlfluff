"""Implementation of Rule L050."""
from typing import Optional

from sqlfluff.core.rules.base import BaseRule, LintFix, LintResult, RuleContext
from sqlfluff.core.rules.functional import Segments
import sqlfluff.core.rules.functional.segment_predicates as sp
import sqlfluff.core.rules.functional.raw_file_slice_predicates as rsp
from sqlfluff.core.rules.doc_decorators import document_fix_compatible, document_groups


@document_groups
@document_fix_compatible
class Rule_L050(BaseRule):
    """Files must not begin with newlines or whitespace.

    **Anti-pattern**

    The file begins with newlines or whitespace. The ``^``
    represents the beginning of the file.

    .. code-block:: sql
       :force:

        ^

        SELECT
            a
        FROM foo

        -- Beginning on an indented line is also forbidden,
        -- (the • represents space).

        ••••SELECT
        ••••a
        FROM
        ••••foo

    **Best practice**

    Start file on either code or comment. (The ``^`` represents the beginning
    of the file.)

    .. code-block:: sql
       :force:


        ^SELECT
            a
        FROM foo

        -- Including an initial block comment.

        ^/*
        This is a description of my SQL code.
        */
        SELECT
            a
        FROM
            foo

        -- Including an initial inline comment.

        ^--This is a description of my SQL code.
        SELECT
            a
        FROM
            foo
    """

    groups = ("all",)
    targets_templated = True
    # TRICKY: Tells linter to only call _eval() ONCE, with the root segment
    recurse_into = False
    lint_phase = "post"

    def _eval(self, context: RuleContext) -> Optional[LintResult]:
        """Files must not begin with newlines or whitespace."""
        # Only check raw segments. This ensures we don't try and delete the same
        # whitespace multiple times (i.e. for non-raw segments higher in the
        # tree).
        raw_segments = []
        whitespace_types = {"newline", "whitespace", "indent", "dedent"}
        for seg in context.segment.recursive_crawl_all():
            if not seg.is_raw():
                continue

            if seg.is_type(*whitespace_types):
                raw_segments.append(seg)
                continue

            segment = Segments(seg)
            raw_stack = Segments(*raw_segments, templated_file=context.templated_file)
            # Non-whitespace segment.
            if (
                not raw_stack.all(sp.is_meta())
                # Found leaf of parse tree.
                and not segment.all(sp.is_expandable())
                # It is possible that a template segment (e.g.
                # {{ config(materialized='view') }}) renders to an empty string
                # and as such is omitted from the parsed tree. We therefore
                # should flag if a templated raw slice intersects with the
                # source slices in the raw stack and skip this rule to avoid
                # risking collisions with template objects.
                and not raw_stack.raw_slices.any(rsp.is_slice_type("templated"))
            ):
                return LintResult(
                    anchor=context.segment,
                    fixes=[LintFix.delete(d) for d in raw_stack],
                )
            else:
                break
        return None
