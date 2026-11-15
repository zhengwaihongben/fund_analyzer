# -*- coding: utf-8 -*-
"""Exception hierarchy for the fund analyzer.

Data-layer failures (network down, upstream schema change, corrupt cache)
are the normal case for this tool, not the exceptional one: they are raised
as typed errors so callers can decide between "degrade gracefully" and
"abort the run", instead of every function inventing its own bare
``except Exception``.
"""
from __future__ import annotations


class FundAnalyzerError(Exception):
    """Base class for every error raised by this package."""


class ConfigurationError(FundAnalyzerError):
    """Invalid or contradictory configuration (bad fund code, bad window...)."""


class DataFetchError(FundAnalyzerError):
    """A remote data source could not be reached or returned unusable data."""


class CacheError(FundAnalyzerError):
    """A cached file exists but cannot be read / is not what we wrote."""


class AnalysisError(FundAnalyzerError):
    """An analysis step could not produce a meaningful result."""
