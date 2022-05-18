"""Implements the base rule class.

Rules crawl through the trees returned by the parser and evaluate particular
rules.

The intent is that it should be possible for the rules to be expressed
as simply as possible, with as much of the complexity abstracted away.

The evaluation function should take enough arguments that it can evaluate
the position of the given segment in relation to its neighbors, and that
the segment which finally "triggers" the error, should be the one that would
be corrected OR if the rule relates to something that is missing, then it
should flag on the segment FOLLOWING, the place that the desired element is
missing.
"""

import bdb
import copy
import fnmatch
import logging
import pathlib
import regex
from typing import (
    cast,
    Iterable,
    Iterator,
    Optional,
    List,
    Set,
    Tuple,
    Union,
    Any,
)
from collections import namedtuple
from dataclasses import dataclass, field

from sqlfluff.core.cached_property import cached_property

from sqlfluff.core.linter import LintedFile, NoQaDirective
from sqlfluff.core.parser import BaseSegment, PositionMarker, RawSegment
from sqlfluff.core.dialects import Dialect
from sqlfluff.core.errors import SQLLintError
from sqlfluff.core.rules.functional import Segments
from sqlfluff.core.templaters.base import RawFileSlice, TemplatedFile

# The ghost of a rule (mostly used for testing)
RuleGhost = namedtuple("RuleGhost", ["code", "description"])

# Instantiate the rules logger
rules_logger = logging.getLogger("sqlfluff.rules")

linter_logger: logging.Logger = logging.getLogger("sqlfluff.linter")


class RuleLoggingAdapter(logging.LoggerAdapter):
    """A LoggingAdapter for rules which adds the code of the rule to it."""

    def process(self, msg, kwargs):
        """Add the code element to the logging message before emit."""
        return "[{}] {}".format(self.extra["code"], msg), kwargs


class LintResult:
    """A class to hold the results of a rule evaluation.

    Args:
        anchor (:obj:`BaseSegment`, optional): A segment which represents
            the *position* of the a problem. NB: Each fix will also hold
            its own reference to position, so this position is mostly for
            alerting the user to where the *problem* is.
        fixes (:obj:`list` of :obj:`LintFix`, optional): An array of any
            fixes which would correct this issue. If not present then it's
            assumed that this issue will have to manually fixed.
        memory (:obj:`dict`, optional): An object which stores any working
            memory for the rule. The `memory` returned in any `LintResult`
            will be passed as an input to the next segment to be crawled.
        description (:obj:`str`, optional): A description of the problem
            identified as part of this result. This will override the
            description of the rule as what gets reported to the user
            with the problem if provided.

    """

    def __init__(
        self,
        anchor: Optional[BaseSegment] = None,
        fixes: Optional[List["LintFix"]] = None,
        memory=None,
        description=None,
    ):
        # An anchor of none, means no issue
        self.anchor = anchor
        # Fixes might be blank
        self.fixes = fixes or []
        # When instantiating the result, we filter any fixes which are "trivial".
        self.fixes = [f for f in self.fixes if not f.is_trivial()]
        # Memory is passed back in the linting result
        self.memory = memory
        # store a description_override for later
        self.description = description

    def to_linting_error(self, rule) -> Optional[SQLLintError]:
        """Convert a linting result to a :exc:`SQLLintError` if appropriate."""
        if self.anchor:
            # Allow description override from the LintResult
            description = self.description or rule.description
            return SQLLintError(
                rule=rule,
                segment=self.anchor,
                fixes=self.fixes,
                description=description,
            )
        else:
            return None


class LintFix:
    """A class to hold a potential fix to a linting violation.

    Args:
        edit_type (:obj:`str`): One of `create_before`, `create_after`,
            `replace`, `delete` to indicate the kind of fix this represents.
        anchor (:obj:`BaseSegment`): A segment which represents
            the *position* that this fix should be applied at. For deletions
            it represents the segment to delete, for creations it implies the
            position to create at (with the existing element at this position
            to be moved *after* the edit), for a `replace` it implies the
            segment to be replaced.
        edit (:obj:`BaseSegment`, optional): For `replace` and `create` fixes,
            this holds the iterable of segments to create or replace at the
            given `anchor` point.
        source (:obj:`BaseSegment`, optional): For `replace` and `create` fixes,
            this holds iterable of segments that provided code. IMPORTANT: The
            linter uses this to prevent copying material from templated areas.

    """

    def __init__(
        self,
        edit_type: str,
        anchor: BaseSegment,
        edit: Optional[Iterable[BaseSegment]] = None,
        source: Optional[Iterable[BaseSegment]] = None,
    ) -> None:
        if edit_type not in (
            "create_before",
            "create_after",
            "replace",
            "delete",
        ):  # pragma: no cover
            raise ValueError(f"Unexpected edit_type: {edit_type}")
        self.edit_type = edit_type
        if not anchor:  # pragma: no cover
            raise ValueError("Fixes must provide an anchor.")
        self.anchor = anchor
        self.edit: Optional[List[BaseSegment]] = None
        if edit is not None:
            # Coerce edit iterable to list
            edit = list(edit)
            # Copy all the elements of edit to stop contamination.
            # We're about to start stripping the position markers
            # off some of the elements and we don't want to end up
            # stripping the positions of the original elements of
            # the parsed structure.
            self.edit = copy.deepcopy(edit)
            # Check that any edits don't have a position marker set.
            # We should rely on realignment to make position markers.
            # Strip position markers of anything enriched, otherwise things can get
            # blurry
            for seg in self.edit:
                if seg.pos_marker:
                    # Developer warning.
                    rules_logger.debug(
                        "Developer Note: Edit segment found with preset position "
                        "marker. These should be unset and calculated later."
                    )
                    seg.pos_marker = None
            # Once stripped, we shouldn't replace any markers because
            # later code may rely on them being accurate, which we
            # can't guarantee with edits.
        self.source = [seg for seg in source if seg.pos_marker] if source else []

    def is_trivial(self):
        """Return true if the fix is trivial.

        Trivial edits are:
        - Anything of zero length.
        - Any edits which result in themselves.

        Removing these makes the routines which process fixes much faster.
        """
        if self.edit_type in ("create_before", "create_after"):
            if isinstance(self.edit, BaseSegment):
                if len(self.edit.raw) == 0:  # pragma: no cover TODO?
                    return True
            elif all(len(elem.raw) == 0 for elem in self.edit):
                return True
        elif self.edit_type == "replace" and self.edit == self.anchor:
            return True  # pragma: no cover TODO?
        return False

    def __repr__(self):
        if self.edit_type == "delete":
            detail = f"delete:{self.anchor.raw!r}"
        elif self.edit_type in ("replace", "create_before", "create_after"):
            if hasattr(self.edit, "raw"):
                new_detail = self.edit.raw  # pragma: no cover TODO?
            else:
                new_detail = "".join(s.raw for s in self.edit)

            if self.edit_type == "replace":
                detail = f"edt:{self.anchor.raw!r}->{new_detail!r}"
            else:
                detail = f"create:{new_detail!r}"
        else:
            detail = ""  # pragma: no cover TODO?
        return "<LintFix: {} @{} {}>".format(
            self.edit_type, self.anchor.pos_marker, detail
        )

    def __eq__(self, other):
        """Compare equality with another fix.

        A fix is equal to another if is in the same place (position), with the
        same type and (if appropriate) the same edit values.

        """
        if not self.edit_type == other.edit_type:
            return False
        if not self.anchor == other.anchor:
            return False
        if not self.edit == other.edit:
            return False
        return True  # pragma: no cover TODO?

    @classmethod
    def delete(cls, anchor_segment: BaseSegment) -> "LintFix":
        """Delete supplied anchor segment."""
        return cls("delete", anchor_segment)

    @classmethod
    def replace(
        cls,
        anchor_segment: BaseSegment,
        edit_segments: Iterable[BaseSegment],
        source: Optional[Iterable[BaseSegment]] = None,
    ) -> "LintFix":
        """Replace supplied anchor segment with the edit segments."""
        return cls("replace", anchor_segment, edit_segments, source)

    @classmethod
    def create_before(
        cls,
        anchor_segment: BaseSegment,
        edit_segments: Iterable[BaseSegment],
        source: Optional[Iterable[BaseSegment]] = None,
    ) -> "LintFix":
        """Create edit segments before the supplied anchor segment."""
        return cls("create_before", anchor_segment, edit_segments, source)

    @classmethod
    def create_after(
        cls,
        anchor_segment: BaseSegment,
        edit_segments: Iterable[BaseSegment],
        source: Optional[Iterable[BaseSegment]] = None,
    ) -> "LintFix":
        """Create edit segments after the supplied anchor segment."""
        return cls("create_after", anchor_segment, edit_segments, source)

    def get_fix_slices(
        self, templated_file: TemplatedFile, within_only: bool
    ) -> Set[RawFileSlice]:
        """Returns slices touched by the fix."""
        # Goal: Find the raw slices touched by the fix. Two cases, based on
        # edit type:
        # 1. "delete", "replace": Raw slices touching the anchor segment.
        # 2. "create_before", "create_after": Raw slices encompassing the two
        #    character positions surrounding the insertion point (**NOT** the
        #    whole anchor segment, because we're not *touching* the anchor
        #    segment, we're inserting **RELATIVE** to it.
        assert self.anchor.pos_marker
        anchor_slice = self.anchor.pos_marker.templated_slice
        templated_slices = [anchor_slice]

        # If "within_only" is set for a "create_*" fix, the slice should only
        # include the area of code "within" the area of insertion, not the other
        # side.
        adjust_boundary = 1 if not within_only else 0
        if self.edit_type == "create_before":
            # Consider the first position of the anchor segment and the
            # position just before it.
            templated_slices = [
                slice(anchor_slice.start - 1, anchor_slice.start + adjust_boundary),
            ]
        elif self.edit_type == "create_after":
            # Consider the last position of the anchor segment and the
            # character just after it.
            templated_slices = [
                slice(anchor_slice.stop - adjust_boundary, anchor_slice.stop + 1),
            ]
        # TRICKY: For creations at the end of the file, there won't be an
        # existing slice. In this case, the function adds file_end_slice to the
        # result, as a sort of placeholder or sentinel value. We pass a literal
        # slice for "file_end_slice" so that later in this function, the LintFix
        # is interpreted as literal code. Otherwise, it could be interpreted as
        # a fix to *templated* code and incorrectly discarded.
        return self._raw_slices_from_templated_slices(
            templated_file,
            templated_slices,
            file_end_slice=RawFileSlice("", "literal", -1),
        )

    def has_template_conflicts(self, templated_file: TemplatedFile) -> bool:
        """Based on the fix slices, should we discard the fix?"""
        # Given fix slices, check for conflicts.
        check_fn = all if self.edit_type in ("create_before", "create_after") else any
        fix_slices = self.get_fix_slices(templated_file, within_only=False)
        result = check_fn(fs.slice_type == "templated" for fs in fix_slices)
        if result or not self.source:
            return result

        # Fix slices were okay. Now check template safety of the "source" field.
        templated_slices = [
            cast(PositionMarker, source.pos_marker).templated_slice
            for source in self.source
        ]
        raw_slices = self._raw_slices_from_templated_slices(
            templated_file, templated_slices
        )
        return any(fs.slice_type == "templated" for fs in raw_slices)

    @staticmethod
    def _raw_slices_from_templated_slices(
        templated_file: TemplatedFile,
        templated_slices: List[slice],
        file_end_slice: Optional[RawFileSlice] = None,
    ) -> Set[RawFileSlice]:
        raw_slices: Set[RawFileSlice] = set()
        for templated_slice in templated_slices:
            try:
                raw_slices.update(
                    templated_file.raw_slices_spanning_source_slice(
                        templated_file.templated_slice_to_source_slice(templated_slice)
                    )
                )
            except (IndexError, ValueError):
                # These errors will happen with "create_before" at the beginning
                # of the file or "create_after" at the end of the file. By
                # default, we ignore this situation. If the caller passed
                # "file_end_slice", add that to the result. In effect,
                # file_end_slice serves as a placeholder or sentinel value.
                if file_end_slice is not None:
                    raw_slices.add(file_end_slice)
        return raw_slices


EvalResultType = Union[LintResult, List[LintResult], None]


@dataclass
class RuleContext:
    """Class for holding the context passed to rule eval functions."""

    # These don't change within a file.
    dialect: Dialect
    fix: bool
    templated_file: Optional[TemplatedFile]
    path: Optional[pathlib.Path]

    # These change within a file.
    segment: BaseSegment
    parent_stack: Tuple[BaseSegment, ...] = field(default=tuple())
    raw_segment_pre: Optional[RawSegment] = field(default=None)
    raw_stack: Tuple[RawSegment, ...] = field(default=tuple())
    memory: Any = field(default_factory=dict)
    segment_idx: int = field(default=0)

    @property
    def siblings_pre(self) -> Tuple[BaseSegment, ...]:  # pragma: no cover
        """Return sibling segments prior to self.segment."""
        if self.parent_stack:
            return self.parent_stack[-1].segments[: self.segment_idx]
        else:
            return tuple()

    @property
    def siblings_post(self) -> Tuple[BaseSegment, ...]:
        """Return sibling segments after self.segment."""
        if self.parent_stack:
            return self.parent_stack[-1].segments[self.segment_idx + 1 :]
        else:
            return tuple()

    @cached_property
    def final_segment(self) -> BaseSegment:
        """Returns rightmost & lowest descendant.

        Similar in spirit to BaseRule.is_final_segment(), but:
        - Much faster
        - Does not allow filtering out meta segments
        """
        last_segment: BaseSegment = (
            self.parent_stack[0] if self.parent_stack else self.segment
        )
        while True:
            try:
                last_segment = last_segment.segments[-1]
            except IndexError:
                return last_segment

    @property
    def functional(self):
        """Returns a Surrogates object that simplifies writing rules."""
        return FunctionalRuleContext(self)


class FunctionalRuleContext:
    """RuleContext written in a "functional" style; simplifies writing rules."""

    def __init__(self, context: RuleContext):
        self.context = context

    @property
    def segment(self) -> "Segments":
        """Returns a Segments object for context.segment."""
        return Segments(
            self.context.segment, templated_file=self.context.templated_file
        )

    @property
    def parent_stack(self) -> "Segments":  # pragma: no cover
        """Returns a Segments object for context.parent_stack."""
        return Segments(
            *self.context.parent_stack, templated_file=self.context.templated_file
        )

    @property
    def siblings_pre(self) -> "Segments":  # pragma: no cover
        """Returns a Segments object for context.siblings_pre."""
        return Segments(
            *self.context.siblings_pre, templated_file=self.context.templated_file
        )

    @property
    def siblings_post(self) -> "Segments":  # pragma: no cover
        """Returns a Segments object for context.siblings_post."""
        return Segments(
            *self.context.siblings_post, templated_file=self.context.templated_file
        )

    @property
    def raw_stack(self) -> "Segments":
        """Returns a Segments object for context.raw_stack."""
        return Segments(
            *self.context.raw_stack, templated_file=self.context.templated_file
        )

    @property
    def raw_segments(self):
        """Returns a Segments object for all the raw segments in the file."""
        file_segment = self.context.parent_stack[0]
        return Segments(
            *file_segment.get_raw_segments(), templated_file=self.context.templated_file
        )


class CrawlBehavior:
    """Implements how a lint rule traverses the parse tree."""

    def __init__(
        self, works_on_unparsable: bool, recurse_into: bool, needs_raw_stack: bool
    ):
        self.works_on_unparsable = works_on_unparsable
        self.recurse_into = recurse_into
        self.raw_stack: Optional[List[RawSegment]] = [] if needs_raw_stack else None

    def crawl(self, context: RuleContext) -> Iterator[RuleContext]:
        """Yields a RuleContext for each segment the rule should process."""
        # First, check whether we're looking at an unparsable and whether
        # this rule will still operate on that.
        if not self.works_on_unparsable and context.segment.is_type("unparsable"):
            # Abort here if it doesn't. Otherwise we'll get odd results.
            return

        assert (
            self.raw_stack is None
            or (len(context.raw_stack) == 0 and context.raw_segment_pre is None)
            or context.raw_stack[-1] is context.raw_segment_pre
        )
        # Rules should evaluate on segments FIRST, before evaluating on their
        # children.
        yield context

        if self.recurse_into:
            # The raw stack only keeps track of the previous *raw* segments.
            if len(context.segment.segments) == 0:
                context.raw_segment_pre = cast(RawSegment, context.segment)
                # Tracking raw_stack is expensive, so it's optional per rule.
                if self.raw_stack is not None:
                    self.raw_stack.append(context.raw_segment_pre)
                    context.raw_stack = tuple(self.raw_stack)
            base_parent_stack = context.parent_stack
            new_parent_stack = base_parent_stack + (context.segment,)
            for idx, child in enumerate(context.segment.segments):
                # For performance reasons, don't create a new RuleContext for
                # each segment; just modify the existing one in place. This
                # requires some careful bookkeeping, but it avoids creating a
                # HUGE number of short-lived RuleContext objects
                # (#linter loops x #rules x #segments).
                context.segment = child
                context.parent_stack = new_parent_stack
                context.segment_idx = idx
                yield from self.crawl(context)


class BaseRule:
    """The base class for a rule.

    Args:
        code (:obj:`str`): The identifier for this rule, used in inclusion
            or exclusion.
        description (:obj:`str`): A human readable description of what this
            rule does. It will be displayed when any violations are found.

    """

    _check_docstring = True
    _works_on_unparsable = True
    _adjust_anchors = False
    targets_templated = False

    # Lint loop / crawl behavior. When appropriate, rules can (and should)
    # override these values to make linting faster.
    recurse_into = True
    # "needs_raw_stack" defaults to False because rules run faster that way, and
    # most rules don't need it. Rules that use it are usually those that look
    # at the surroundings of a segment, e.g. "is there whitespace preceding this
    # segment?" In the long run, it would be good to review rules that use
    # raw_stack to try and eliminate its use. These rules will often be good
    # candidates for one of the following:
    # - Rewriting to use "RuleContext.raw_segment_pre", which is similar to
    #   "raw_stack", but it's only the ONE raw segment prior to the current
    #   one.
    # - Rewriting to use "BaseRule.recurse_into = False" and traversing the
    #   parse tree directly.
    # - Using "RuleContext.memory" to implement custom, lighter weight tracking
    #   of just the MINIMUM required state across calls to _eval().  Reason:
    #   "raw_stack" becomes very large for large files (thousands or more
    #   segments!). In practice, most rules only need to look at a few adjacent
    #   segments, e.g. others on the same line or in the same statement.
    needs_raw_stack = False
    # Rules can override this to specify "post". "Post" rules are those that are
    # not expected to trigger any downstream rules, e.g. capitalization fixes.
    # They run on two occasions:
    # - On the first pass of the main phase
    # - In a second linter pass after the main phase
    lint_phase = "main"

    def __init__(self, code, description, **kwargs):
        self.description = description
        self.code = code
        # kwargs represents the config passed to the rule. Add all kwargs as class
        # attributes so they can be accessed in rules which inherit from this class
        for key, value in kwargs.items():
            self.__dict__[key] = value

        # We also define a custom logger here, which also includes the code
        # of the rule in the logging.
        self.logger = RuleLoggingAdapter(rules_logger, {"code": code})
        # Validate that declared configuration options exist
        try:
            for keyword in self.config_keywords:
                if keyword not in kwargs.keys():
                    raise ValueError(
                        (
                            "Unrecognized config '{}' for Rule {}. If this "
                            "is a new option, please add it to "
                            "`default_config.cfg`"
                        ).format(keyword, code)
                    )
        except AttributeError:
            self.logger.info(f"No config_keywords defined for {code}")

    def _eval(self, context: RuleContext) -> EvalResultType:
        """Evaluate this rule against the current context.

        This should indicate whether a linting violation has occurred and/or
        whether there is something to remember from this evaluation.

        Note that an evaluate function should always accept `**kwargs`, but
        if it relies on any available kwargs, it should explicitly call
        them out at definition.

        Returns:
            :obj:`LintResult`, list of :obj:`LintResult` or :obj:`None`.

        The reason that this method is called :meth:`_eval` and not `eval` is
        a bit of a hack with sphinx autodoc, to make it so that the rule
        documentation auto-generates nicely.

        """
        raise NotImplementedError(
            (
                "{} has not had its `eval` function defined. This is a problem "
                "with the rule setup."
            ).format(self.__class__.__name__)
        )  # pragma: no cover

    def crawl(
        self,
        tree: BaseSegment,
        dialect: Dialect,
        fix: bool,
        templated_file: Optional["TemplatedFile"],
        ignore_mask: List[NoQaDirective],
        fname: Optional[str],
    ) -> Tuple[List[SQLLintError], Tuple[RawSegment, ...], List[LintFix], Any]:
        """Run the rule on a given tree.

        Returns:
            A tuple of (vs, raw_stack, fixes, memory)

        """
        root_context = RuleContext(
            dialect=dialect,
            fix=fix,
            templated_file=templated_file,
            path=pathlib.Path(fname) if fname else None,
            segment=tree,
        )
        vs: List[SQLLintError] = []
        fixes: List[LintFix] = []

        # Propagates memory from one rule _eval() to the next.
        memory: Any = root_context.memory
        context = None
        crawl_behavior = CrawlBehavior(
            self._works_on_unparsable, self.recurse_into, self.needs_raw_stack
        )
        for context in crawl_behavior.crawl(root_context):
            try:
                context.memory = memory
                res = self._eval(context=context)
            except (bdb.BdbQuit, KeyboardInterrupt):  # pragma: no cover
                raise
            # Any exception at this point would halt the linter and
            # cause the user to get no results
            except Exception as e:
                self.logger.critical(
                    f"Applying rule {self.code} threw an Exception: {e}", exc_info=True
                )
                assert context.segment.pos_marker
                exception_line, _ = context.segment.pos_marker.source_position()
                self._log_critical_errors(e)
                vs.append(
                    SQLLintError(
                        rule=self,
                        segment=context.segment,
                        fixes=[],
                        description=(
                            f"Unexpected exception: {str(e)};\n"
                            "Could you open an issue at "
                            "https://github.com/sqlfluff/sqlfluff/issues ?\n"
                            "You can ignore this exception for now, by adding "
                            f"'-- noqa: {self.code}' at the end\n"
                            f"of line {exception_line}\n"
                        ),
                    )
                )
                return vs, context.raw_stack, fixes, context.memory

            new_lerrs: List[SQLLintError] = []
            new_fixes: List[LintFix] = []

            if res is None or res == []:
                # Assume this means no problems (also means no memory)
                pass
            elif isinstance(res, LintResult):
                # Extract any memory
                memory = res.memory
                self._adjust_anchors_for_fixes(context, res)
                self._process_lint_result(
                    res, templated_file, ignore_mask, new_lerrs, new_fixes
                )
            elif isinstance(res, list) and all(
                isinstance(elem, LintResult) for elem in res
            ):
                # Extract any memory from the *last* one, assuming
                # it was the last to be added
                memory = res[-1].memory
                for elem in res:
                    self._adjust_anchors_for_fixes(context, elem)
                    self._process_lint_result(
                        elem, templated_file, ignore_mask, new_lerrs, new_fixes
                    )
            else:  # pragma: no cover
                raise TypeError(
                    "Got unexpected result [{!r}] back from linting rule: {!r}".format(
                        res, self.code
                    )
                )

            for lerr in new_lerrs:
                self.logger.debug("!! Violation Found: %r", lerr.description)
            for lfix in new_fixes:
                self.logger.debug("!! Fix Proposed: %r", lfix)

            # Consume the new results
            vs += new_lerrs
            fixes += new_fixes
        return vs, context.raw_stack if context else tuple(), fixes, context.memory

    # HELPER METHODS --------
    @staticmethod
    def _log_critical_errors(error: Exception):  # pragma: no cover
        """This method is monkey patched into a "raise" for certain tests."""
        pass

    def _process_lint_result(
        self, res, templated_file, ignore_mask, new_lerrs, new_fixes
    ):
        self.discard_unsafe_fixes(res, templated_file)
        lerr = res.to_linting_error(rule=self)
        ignored = False
        if lerr:
            if ignore_mask:
                filtered = LintedFile.ignore_masked_violations([lerr], ignore_mask)
                if not filtered:
                    lerr = None
                    ignored = True
        if lerr:
            new_lerrs.append(lerr)
        if not ignored:
            new_fixes.extend(res.fixes)

    @cached_property
    def indent(self) -> str:
        """String for a single indent, based on configuration."""
        self.tab_space_size: int
        self.indent_unit: str

        tab = "\t"
        space = " "
        return space * self.tab_space_size if self.indent_unit == "space" else tab

    def is_final_segment(self, context: RuleContext, filter_meta: bool = True) -> bool:
        """Is the current segment the final segment in the parse tree."""
        siblings_post = context.siblings_post
        if filter_meta:
            siblings_post = self.filter_meta(siblings_post)
        if len(siblings_post) > 0:
            # This can only fail on the last segment
            return False
        elif len(context.segment.segments) > 0:
            # This can only fail on the last base segment
            return False
        elif filter_meta and context.segment.is_meta:
            return False
        else:
            # We know we are at a leaf of the tree but not necessarily at the end of the
            # tree. Therefore we look backwards up the parent stack and ask if any of
            # the parent segments have another non-meta child segment after the current
            # one.
            child_segment = context.segment
            for parent_segment in context.parent_stack[::-1]:
                possible_children = parent_segment.segments
                if filter_meta:
                    possible_children = [s for s in possible_children if not s.is_meta]
                if len(possible_children) > possible_children.index(child_segment) + 1:
                    return False
                child_segment = parent_segment

        return True

    @staticmethod
    def filter_meta(segments, keep_meta=False):
        """Filter the segments to non-meta.

        Or optionally the opposite if keep_meta is True.
        """
        buff = []
        for elem in segments:
            if elem.is_meta is keep_meta:
                buff.append(elem)
        return tuple(buff)

    @classmethod
    def get_parent_of(cls, segment, root_segment):  # pragma: no cover TODO?
        """Return the segment immediately containing segment.

        NB: This is recursive.

        Args:
            segment: The segment to look for.
            root_segment: Some known parent of the segment
                we're looking for (although likely not the
                direct parent in question).

        """
        if segment in root_segment.segments:
            return root_segment
        elif root_segment.segments:
            # try each of the subsegments
            for sub in root_segment.segments:
                p = cls.get_parent_of(segment, sub)
                if p:
                    return p
        # Not directly in the segment and
        # no subsegments to check. Return None.
        return None

    @staticmethod
    def matches_target_tuples(
        seg: BaseSegment,
        target_tuples: List[Tuple[str, str]],
        parent: Optional[BaseSegment] = None,
    ):
        """Does the given segment match any of the given type tuples?"""
        if seg.name in [elem[1] for elem in target_tuples if elem[0] == "name"]:
            return True
        elif seg.is_type(*[elem[1] for elem in target_tuples if elem[0] == "type"]):
            return True
        # For parent type checks, there's a higher risk of getting an incorrect
        # segment, so we add some additional guards. We also only check keywords
        # as for other types we can check directly rather than using parent
        elif (
            not seg.is_meta
            and not seg.is_comment
            and not seg.is_templated
            and not seg.is_whitespace
            and isinstance(seg, RawSegment)
            and len(seg.raw) > 0
            and seg.is_type("keyword")
            and parent
            and parent.is_type(
                *[elem[1] for elem in target_tuples if elem[0] == "parenttype"]
            )
        ):
            return True
        return False

    @staticmethod
    def discard_unsafe_fixes(
        lint_result: LintResult, templated_file: Optional[TemplatedFile]
    ):
        """Remove (discard) LintResult fixes if they are "unsafe".

        By removing its fixes, a LintResult will still be reported, but it
        will be treated as _unfixable_.
        """
        if not lint_result.fixes or not templated_file:
            return

        # Check for fixes that touch templated code.
        for fix in lint_result.fixes:
            if fix.has_template_conflicts(templated_file):
                linter_logger.info(
                    "      * Discarding fixes that touch templated code: %s",
                    lint_result.fixes,
                )
                lint_result.fixes = []
                return

        # Issue 3079: Fixes that span multiple template blocks are bad. Don't
        # permit them.
        block_indices: Set[int] = set()
        for fix in lint_result.fixes:
            fix_slices = fix.get_fix_slices(templated_file, within_only=True)
            for fix_slice in fix_slices:
                # Ignore fix slices that exist only in the source. For purposes
                # of this check, it's not meaningful to say that a fix "touched"
                # one of these.
                if not fix_slice.is_source_only_slice():
                    block_indices.add(fix_slice.block_idx)
        if len(block_indices) > 1:
            linter_logger.info(
                "      * Discarding fixes that span multiple template blocks: %s",
                lint_result.fixes,
            )
            lint_result.fixes = []
            return

    @classmethod
    def _adjust_anchors_for_fixes(cls, context, lint_result):
        """Makes simple fixes to the anchor position for fixes.

        Some rules return fixes where the anchor is too low in the tree. These
        are most often rules like L003 and L016 that make whitespace changes
        without a "deep" understanding of the parse structure. This function
        attempts to correct those issues automatically. It may not be perfect,
        but it should be an improvement over the old behavior, where rules like
        L003 often corrupted the parse tree, placing spaces in weird places that
        caused issues with other rules. For more context, see issue #1304.
        """
        if not cls._adjust_anchors:
            return

        fix: LintFix
        for fix in lint_result.fixes:
            if fix.anchor:
                fix.anchor = cls._choose_anchor_segment(
                    context, fix.edit_type, fix.anchor
                )

    @staticmethod
    def _choose_anchor_segment(
        context: RuleContext,
        edit_type: str,
        segment: BaseSegment,
        filter_meta: bool = False,
    ):
        """Choose the anchor point for a lint fix, i.e. where to apply the fix.

        From a grammar perspective, segments near the leaf of the tree are
        generally less likely to allow general edits such as whitespace
        insertion.

        This function avoids such issues by taking a proposed anchor point
        (assumed to be near the leaf of the tree) and walking "up" the parse
        tree as long as the ancestor segments have the same start or end point
        (depending on the edit type) as "segment". This newly chosen anchor
        is more likely to be a valid anchor point for the fix.
        """
        if edit_type not in ("create_before", "create_after"):
            return segment

        anchor: BaseSegment = segment
        child: BaseSegment = segment
        parent: BaseSegment = context.parent_stack[0]
        path: Optional[List[BaseSegment]] = parent.path_to(segment) if parent else None
        inner_path: Optional[List[BaseSegment]] = path[1:-1] if path else None
        if inner_path:
            for seg in inner_path[::-1]:
                # Which lists of children to check against.
                children_lists: List[List[BaseSegment]] = []
                if filter_meta:
                    # Optionally check against filtered (non-meta only) children.
                    children_lists.append(
                        [child for child in seg.segments if not child.is_meta]
                    )
                # Always check against the full set of children.
                children_lists.append(seg.segments)
                children: List[BaseSegment]
                for children in children_lists:
                    if edit_type == "create_before" and children[0] is child:
                        anchor = seg
                        assert anchor.raw.startswith(segment.raw)
                        child = seg
                        break
                    elif edit_type == "create_after" and children[-1] is child:
                        anchor = seg
                        assert anchor.raw.endswith(segment.raw)
                        child = seg
                        break
        return anchor

    @staticmethod
    def split_comma_separated_string(raw_str: str) -> List[str]:
        """Converts comma separated string to List, stripping whitespace."""
        return [s.strip() for s in raw_str.split(",") if s.strip()]


class RuleSet:
    """Class to define a ruleset.

    A rule set is instantiated on module load, but the references
    to each of its classes are instantiated at runtime. This means
    that configuration values can be passed to those rules live
    and be responsive to any changes in configuration from the
    path that the file is in.

    Rules should be fetched using the :meth:`get_rulelist` command which
    also handles any filtering (i.e. allowlisting and denylisting).

    New rules should be added to the instance of this class using the
    :meth:`register` decorator. That decorator registers the class, but also
    performs basic type and name-convention checks.

    The code for the rule will be parsed from the name, the description
    from the docstring. The eval function is assumed that it will be
    overriden by the subclass, and the parent class raises an error on
    this function if not overriden.

    """

    def __init__(self, name, config_info):
        self.name = name
        self.config_info = config_info
        self._register = {}

    def _validate_config_options(self, config, rule=None):
        """Ensure that all config options are valid.

        Config options can also be checked for a specific rule e.g L010.
        """
        rule_config = config.get_section("rules")
        for config_name, info_dict in self.config_info.items():
            config_option = (
                rule_config.get(config_name)
                if not rule
                else rule_config.get(rule).get(config_name)
            )
            valid_options = info_dict.get("validation")
            if (
                valid_options
                and config_option not in valid_options
                and config_option is not None
            ):
                raise ValueError(
                    (
                        "Invalid option '{}' for {} configuration. Must be one of {}"
                    ).format(
                        config_option,
                        config_name,
                        valid_options,
                    )
                )

    @property
    def valid_rule_name_regex(self):
        """Defines the accepted pattern for rule names.

        The first group captures the plugin name (optional), which
        must be capitalized.
        The second group captures the rule code.

        Examples of valid rule names:

        * Rule_PluginName_L001
        * Rule_L001
        """
        return regex.compile(r"Rule_?([A-Z]{1}[a-zA-Z]+)?_([A-Z][0-9]{3})")

    def register(self, cls, plugin=None):
        """Decorate a class with this to add it to the ruleset.

        .. code-block:: python

           @myruleset.register
           class Rule_L001(BaseRule):
               "Description of rule."

               def eval(self, **kwargs):
                   return LintResult()

        We expect that rules are defined as classes with the name `Rule_XXXX`
        where `XXXX` is of the form `LNNN`, where L is a letter (literally L for
        *linting* by default) and N is a three digit number.

        If this receives classes by any other name, then it will raise a
        :exc:`ValueError`.

        """
        rule_name_match = self.valid_rule_name_regex.match(cls.__name__)
        # Validate the name
        if not rule_name_match:  # pragma: no cover
            raise ValueError(
                (
                    "Tried to register rule on set {!r} with unexpected "
                    "format: {}, format should be: Rule_PluginName_L123 (for plugins) "
                    "or Rule_L123 (for core rules)."
                ).format(self.name, cls.__name__)
            )

        plugin_name, code = rule_name_match.groups()
        # If the docstring is multiline, then we extract just summary.
        description = cls.__doc__.replace("``", "'").split("\n")[0]

        if plugin_name:
            code = f"{plugin_name}_{code}"

        # Keep track of the *class* in the register. Don't instantiate yet.
        if code in self._register:  # pragma: no cover
            raise ValueError(
                "Rule {!r} has already been registered on RuleSet {!r}!".format(
                    code, self.name
                )
            )

        try:
            assert (
                "all" in cls.groups
            ), "Rule {!r} must belong to the 'all' group".format(code)
            groups = cls.groups
        except AttributeError as attr_err:

            raise AttributeError(
                (
                    "Rule {!r} doesn't belong to any rule groups. "
                    "All rules must belong to at least one group"
                ).format(code)
            ) from attr_err

        self._register[code] = dict(
            code=code, description=description, groups=groups, cls=cls
        )

        # Make sure we actually return the original class
        return cls

    def _expand_config_rule_group_list(
        self, rule_list: List[str], valid_groups: Set[str]
    ) -> List[str]:
        expanded_rule_list: List[str] = []
        for r in rule_list:
            if r in valid_groups:
                rules_in_group = [
                    rule
                    for rule, rule_dict in self._register.items()
                    if r in rule_dict["groups"]
                ]
                expanded_rule_list.extend(rules_in_group)
            else:
                expanded_rule_list.extend(r)

        return expanded_rule_list

    def _expand_config_rule_glob_list(self, glob_list: List[str]) -> List[str]:
        """Expand a list of rule globs into a list of rule codes.

        Returns:
            :obj:`list` of :obj:`str` rule codes.

        """
        expanded_glob_list = []
        for r in glob_list:
            expanded_glob_list.extend(
                [
                    x
                    for x in fnmatch.filter(self._register, r)
                    if x not in expanded_glob_list
                ]
            )

        return expanded_glob_list

    def get_rulelist(self, config) -> List[BaseRule]:
        """Use the config to return the appropriate rules.

        We use the config both for allowlisting and denylisting, but also
        for configuring the rules given the given config.

        Returns:
            :obj:`list` of instantiated :obj:`BaseRule`.

        """
        # Validate all generic rule configs
        self._validate_config_options(config)
        # Find all valid groups for ruleset
        valid_groups: Set[str] = set(
            [group for attrs in self._register.values() for group in attrs["groups"]]
        )
        # default the allowlist to all the rules if not set
        allowlist = config.get("rule_allowlist") or list(self._register.keys())
        denylist = config.get("rule_denylist") or []
        valid_rules_and_groups = list(self._register) + list(valid_groups)

        allowlisted_unknown_rule_codes = [
            r
            for r in allowlist
            # Add valid groups to the register when searching for invalid rules _only_
            if not fnmatch.filter(valid_rules_and_groups, r)
        ]
        if any(allowlisted_unknown_rule_codes):
            rules_logger.warning(
                "Tried to allowlist unknown rules: {!r}".format(
                    allowlisted_unknown_rule_codes
                )
            )

        denylisted_unknown_rule_codes = [
            r
            for r in denylist
            if not fnmatch.filter({**self._register, **dict.fromkeys(valid_groups)}, r)
        ]
        if any(denylisted_unknown_rule_codes):  # pragma: no cover
            rules_logger.warning(
                "Tried to denylist unknown rules: {!r}".format(
                    denylisted_unknown_rule_codes
                )
            )

        keylist = sorted(self._register.keys())

        # First we expand the allowlist and denylist globs
        expanded_allowlist = self._expand_config_rule_glob_list(
            allowlist
        ) + self._expand_config_rule_group_list(allowlist, valid_groups)
        expanded_denylist = self._expand_config_rule_glob_list(
            denylist
        ) + self._expand_config_rule_group_list(denylist, valid_groups)

        # Then we filter the rules
        keylist = [
            r for r in keylist if r in expanded_allowlist and r not in expanded_denylist
        ]

        # Construct the kwargs for instantiation before we actually do it.
        rule_kwargs = {}
        for k in keylist:
            kwargs = {}
            generic_rule_config = config.get_section("rules")
            specific_rule_config = config.get_section(
                ("rules", self._register[k]["code"])
            )
            if generic_rule_config:
                kwargs.update(generic_rule_config)
            if specific_rule_config:
                # Validate specific rule config before adding
                self._validate_config_options(config, self._register[k]["code"])
                kwargs.update(specific_rule_config)
            kwargs["code"] = self._register[k]["code"]
            # Allow variable substitution in making the description
            kwargs["description"] = self._register[k]["description"].format(**kwargs)
            rule_kwargs[k] = kwargs

        # Instantiate in the final step
        return [self._register[k]["cls"](**rule_kwargs[k]) for k in keylist]

    def copy(self):
        """Return a copy of self with a separate register."""
        new_ruleset = copy.copy(self)
        new_ruleset._register = self._register.copy()
        return new_ruleset
