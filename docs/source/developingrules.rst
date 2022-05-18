.. _developingrulesref:

Developing Rules
================

`Rules` in `SQLFluff` are implemented as classes inheriting from ``BaseRule``.
SQLFluff crawls through the parse tree of a SQL file, calling the rule's
``_eval()`` function for each segment in the tree. For many rules, this allows
the rule code to be really streamlined and only contain the logic for the rule
itself, with all the other mechanics abstracted away.

Traversal Options
-----------------

``recurse_into``
^^^^^^^^^^^^^^^^
Some rules are a poor fit for the simple traversal pattern described above.
Typical reasons include:

* The rule only looks at a small portion of the file (e.g. the beginning or
  end).
* The rule needs to traverse the parse tree in a non-standard way.

These rules can override ``BaseRule``'s ``recurse_into`` field, setting it to
``False``. For these rules ``False``, ``_eval()`` is only called *once*, with
the root segment of the tree. This can be much more efficient, especially on
large files. For example, see rules ``L050`` and ``L009`` , which only look at
the beginning or end of the file, respectively.

``_works_on_unparsable``
^^^^^^^^^^^^^^^^^^^^^^^^
By default, `SQLFluff` calls ``_eval()`` for all segments, even "unparsable"
segments, i.e. segments that didn't match the parsing rules in the dialect.
This causes issues for some rules. If so, setting ``_works_on_unparsable``
to ``False`` tells SQLFluff not to call ``_eval()`` for unparsable segments and
their descendants.

Performance-related Options
---------------------------
These are other fields on ``BaseRule``. Rules can override them.

``needs_raw_stack``
^^^^^^^^^^^^^^^^^^^
``needs_raw_stack`` defaults to ``False``. Some rules use
``RuleContext.raw_stack`` property to access earlier segments in the traversal.
This can be useful, but it adds significant overhead to the linting process.
For this reason, it is disabled by default.

``lint_phase``
^^^^^^^^^^^^^^
There are two phases of rule running.

1. The ``main`` phase is appropriate for most rules. These rules are assumed to
interact and potentially cause a cascade of fixes requiring multiple passes.
These rules run the `runaway_limit` number of times (default 10).

2. The ``post`` phase is for post-processing rules, not expected to trigger
any downstream rules, e.g. capitalization fixes. They are run in a
post-processing loop at the end. This loop is identical to the ``main`` loop,
but is only run 2 times at the end (once to fix, and once again to confirm no
remaining issues).

The two phases add complexity, but they also improve performance by allowing
SQLFluff to run fewer rules during the ``main`` phase, which often runs several
times.

NOTE: ``post`` rules also run on the *first* pass of the ``main`` phase so that
any issues they find will be presented in the list of issues output by
``sqlfluff fix`` and ``sqlfluff lint``.

Base Rules
----------

`base_rules` Module
^^^^^^^^^^^^^^^^^^^

.. automodule:: sqlfluff.core.rules.base
   :members:

Functional API
--------------
These newer modules provide a higher-level API for rules working with segments
and slices. Rules that need to navigate or search the parse tree may benefit
from using these. Eventually, the plan is for **all** rules to use these
modules. As of December 30, 2021, 17+ rules use these modules.

The modules listed below are submodules of `sqlfluff.core.rules.functional`.

`segments` Module
^^^^^^^^^^^^^^^^^

.. automodule:: sqlfluff.core.rules.functional.segments
   :members:

`segment_predicates` Module
^^^^^^^^^^^^^^^^^^^^^^^^^^^

.. automodule:: sqlfluff.core.rules.functional.segment_predicates
   :members:

`raw_file_slices` Module
^^^^^^^^^^^^^^^^^^^^^^^^

.. automodule:: sqlfluff.core.rules.functional.raw_file_slices
   :members:

`raw_file_slice_predicates` Module
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

.. automodule:: sqlfluff.core.rules.functional.raw_file_slice_predicates
   :members:
