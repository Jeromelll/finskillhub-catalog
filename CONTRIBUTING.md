# Contributing

Issues and pull requests are welcome for corrections to skill descriptions, provenance, installation notes, or license references.

The catalog and generated index are synced daily from `https://finskillhub.com/data/catalog.json`. The sync checks the catalog structure and requires skill and repository links to use public `https://github.com/` URLs before replacing the files. Please correct catalog metadata at its source; do not hand-edit `data/catalog.json` or `docs/CATALOG.md` here.

For a new catalog entry, include:

- The upstream repository, immutable commit, and exact skill-file path.
- The applicable repository and subdirectory licenses, plus any required `NOTICE` file.
- Evidence that the description matches the fixed source version, with clear attribution.
- Inputs, outputs, dependencies, host requirements, and known limits.
- English and Chinese descriptions when available; they must express the same claims.

Do not submit credentials, personal data, unlicensed material, private research, paid product materials, or claims that imply endorsement or guaranteed returns. Do not copy third-party skill packages into this repository without confirming redistribution rights. Preserve each work's attribution and license.

For changes to the three included original skills, retain the package's `LICENSE`, use synthetic examples, and remove private paths and data. State what was changed and whether behavior was checked. Catalog inclusion is not a claim that a skill has been executed or independently validated.
