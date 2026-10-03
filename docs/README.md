# antcrew-docs

Documentation site for the [antcrew](https://antcrew.org) platform, built with [MkDocs Material](https://squidfunk.github.io/mkdocs-material/).

Published at **[docs.antcrew.org](https://docs.antcrew.org)**.

## Structure

```
docs/
  index.md          # Home
  platform/         # antcrew-platform (API, auth, workspaces...)
  engine/           # antcrew-engine (operators, capabilities...)
  proxy/            # keybridge (self-hosted LLM proxy)
  architecture/     # System design and concepts
  guides/           # End-to-end how-to guides
  releases/         # Changelog and release notes
```

## Running locally

```bash
pip install mkdocs-material
mkdocs serve
```

Open [http://localhost:8000](http://localhost:8000).

## Contributing

Documentation lives alongside the code it describes. For changes tied to a specific component, open a PR in the corresponding repo (, , ) — docs updates can be bundled in the same PR. For standalone doc improvements, open a PR here directly.
