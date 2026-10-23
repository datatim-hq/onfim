# Contributing to Onfim

Thanks for your interest. Issues and pull requests are welcome in English or Russian.

## Before you start

For anything larger than a bug fix, open an issue first so we can agree on the approach. The design specs in
`docs/design/` (English; Russian originals in `docs/ru/design/`) describe how memory, sleep and access are supposed
to work; a change in behavior usually needs a matching change there.

## Development

```bash
docker compose -f docker-compose.test.yml run --rm --build tests
```

Or run the tests against your own Postgres 16 with pgvector:

```bash
pip install --require-hashes -r requirements-dev.lock
export MEM_DATABASE_URL=postgresql://mem:mem@localhost:5432/mem MEM_EMBED_MODEL=fake MEM_EMBED_DIM=64 MEM_STAND=1
python -m pytest tests
```

## Rules for a pull request

- **Dependencies.** Versions are pinned in `requirements.lock` and `requirements-dev.lock` with hashes, and base images
  are pinned by digest. To add or move a library, change `requirements*.txt` and run `tools/lock.sh`; a test fails
  when a lock file no longer matches the requirements.
- **English in code.** Comments and docstrings are in English; `tools/check_english.py` runs in CI. Russian test
  data and the Russian MCP descriptions are data, not comments, and stay.
- **Documentation in two languages.** The README and the pages in `docs/`, design specs included, exist in English
  and in Russian (`README.ru.md`, `docs/ru/`). Change both in the same pull request; if you cannot write one of the
  languages, say so in the pull request and a maintainer will translate. Keep code blocks identical (comments inside
  them may be translated; a ```` ```text ```` block is translated as a whole) and keep the English anchor ids in the
  Russian pages with `<a id="...">`. `tools/check_docs.py` runs in CI and checks pairs, headings, code blocks and
  links.
- **Tests.** Every test has a docstring of the form `"<refs>: <what it checks>."`. After adding or changing tests,
  rebuild the registry with `python tools/test_catalog.py` and commit `docs/tests.md` and `docs/tests.json`;
  CI fails if they are stale.
- **No secrets.** CI runs gitleaks over the whole history. A deliberately fake key in a test gets a
  `# gitleaks:allow` comment.
- **No LLM on the write path.** The service never rewrites what a client sent; keep it that way.
- **Migrations are append-only.** Applied migrations are tracked by file name; add a new file instead of editing an
  old one.

## Sign-off (DCO)

By contributing you certify the [Developer Certificate of Origin](https://developercertificate.org/). Sign off each
commit with `git commit -s`, which adds a `Signed-off-by:` line with your name and email.

## License

Contributions are licensed under the Apache License 2.0, the same as the project.
