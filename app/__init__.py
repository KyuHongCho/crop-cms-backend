"""Crop CMS — the content layer for crop-climate-advisor.

Authors and publishes narrative crop documents (crop profiles, research
literature notes, cultivation practice, pests and disorders) together with
their provenance, so the advisor can retrieve and cite them.

The boundary that keeps this project honest: structured agronomic figures stay
in the advisor's ECOCROP data and its claims module, which look them up by
number. This package holds prose *about* those numbers, never the numbers
themselves. If a figure's only home is a document body, it is in the wrong
system.

Documents that contradict each other are both publishable and are never
ranked — mirroring the advisor's rule that nothing picks a winner among
disagreeing published sources.

This file is not a formality: it makes `app` a regular package rather than a
PEP 420 namespace package, so a same-named directory elsewhere on sys.path
cannot silently merge into it. Only the top-level package needs this.
"""

__version__ = "0.1.0"
