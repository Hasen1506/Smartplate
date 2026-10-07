# Epicure test fixture

A small subset of [Epicure-Core](https://huggingface.co/Kaikaku/epicure-core/tree/d31ebb5af8e92bbaf5cb67381d5006d4ea8368b7)
(revision `d31ebb5af8e92bbaf5cb67381d5006d4ea8368b7`) by Jakub Radzikowski and Josef Chen (KAIKAKU.AI),
© 2026, licensed under [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/).
Paper: [arXiv:2605.22391](https://arxiv.org/abs/2605.22391).

Changes made: only the vectors of SmartPlate's pantry ingredients, the ingredients its dish names imply and a
few test tokens are kept (207 of 1,790); `modes.json` keeps the modes behind the cuisine poles (poles rounded
to 6 decimals) and the factor modes' labels, with member lists trimmed to the kept ingredients;
`factor_poles.npy` and `cuisine_pole_provenance.json` are unchanged. `SHA256SUMS` lists this fixture's own hashes.

Rebuild with `python scripts/fetch_epicure.py && python scripts/build_epicure_fixture.py`.
