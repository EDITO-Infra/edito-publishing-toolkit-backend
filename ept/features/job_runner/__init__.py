"""EPT job runner: long-running feature operations executed in-process.

Runs are queued on the EPT API process itself, polled and cancelled here via
``/v1/runs`` with ``run-``-prefixed identifiers. They are **not** the
publish/remove jobs of the external EDITO publishing service, which are
queued there and polled via ``/v1/edito/publish/jobs/{job_id}``.

Operations are declared declaratively in ``operations.toml`` (this feature):
each entry maps an operation key to a feature-provided runner callable and
request model. Features do not import this feature; the dependency points
one way — runner → feature.
"""
