# Releasing

A release is a GitHub release; publishing it uploads the package to PyPI. The
workflow in `.github/workflows/publish.yml` uses PyPI trusted publishing, so
there is no API token to create, store or leak.

## One-time setup

1. Create an account on [pypi.org](https://pypi.org) and, for rehearsals, on
   [test.pypi.org](https://test.pypi.org). Turn on two-factor authentication.
2. On each site open *Your account -> Publishing* and add a **pending
   publisher**:

   | Field | PyPI | TestPyPI |
   |---|---|---|
   | Project name | `napper-sync` | `napper-sync` |
   | Owner | `PW-CODING` | `PW-CODING` |
   | Repository | `napper-sync` | `napper-sync` |
   | Workflow file | `publish.yml` | `publish.yml` |
   | Environment | `pypi` | `testpypi` |

   The pending publisher creates the project on the first upload.
3. Optional but recommended: in the GitHub repository open *Settings ->
   Environments -> pypi* and add yourself as a required reviewer. Every upload
   then waits for one click.

## First upload

PyPI never accepts the same version twice, so rehearse first.

1. *Actions -> Publish to PyPI -> Run workflow*, target `testpypi`.
2. Look at `https://test.pypi.org/project/napper-sync/`: description, links,
   version. Install it with
   `pip install -i https://test.pypi.org/simple/ napper-sync`.
3. Run the workflow again with target `pypi`.

A manual run builds from the branch you pick. An upload to `pypi` only works
from `main`; `testpypi` can be rehearsed from any branch. Every run, by hand or
from a release, runs the test suite against the built wheel before anything is
uploaded.

## Every later release

1. Raise the version in `pyproject.toml` **and** `src/napper_sync/__init__.py`.
   The workflow refuses to upload when they disagree or differ from the tag.
2. Add a section to `CHANGELOG.md`.
3. Commit, push, wait for CI to pass.
4. Create the GitHub release `vX.Y.Z`. The tag must be the version with a
   leading `v`. Publishing the release builds, checks and uploads the package.
