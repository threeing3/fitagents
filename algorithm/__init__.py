"""Algorithm experimentation layer for the AI Fitness Coach project.

The package is intentionally separated from the FastAPI runtime.  It can
export and validate datasets, run offline application-algorithm evaluations,
train optional adapters, and produce reproducible research artifacts without
changing the production request path.
"""

__all__ = ["data", "datasets", "app_algorithms", "business", "evaluation"]
