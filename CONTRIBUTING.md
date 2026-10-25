# Contributing

Open an issue describing the workflow and a minimal synthetic reproduction before
proposing a feature. Do not attach private media, project names, credentials, or
unlicensed files. Keep format support explicit and reject cases we cannot verify.

```sh
python -m pip install -e ".[dev]"
python -m unittest discover -s tests -v
python -m ruff check .
python -m ruff format --check .
python -m build
```

Regression tests should demonstrate an independently constructed input and an
observable user outcome, including invalid or unsupported input. Runtime code
must remain offline and read-only. Dependencies and copied fixtures require
license review. Do not add workflows that commit to this repository.
