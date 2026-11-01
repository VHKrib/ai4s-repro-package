# tools/ — AutoDock Vina binary

| item | value |
|---|---|
| file | `vina_1.2.5_win.exe` |
| version banner | `AutoDock Vina v1.2.5` |
| platform | Windows x86_64 |
| size | 1 203 712 bytes |
| sha256 | `b5a23b226ee5dc1b028df04082d13a7b0e5b9b12b988bbcf9498cb351e20b38f` |
| license | Apache-2.0 (see `../THIRD_PARTY_NOTICES`) |
| upstream | https://github.com/ccsb-scripps/AutoDock-Vina/releases/tag/v1.2.5 |

## Why a binary is bundled instead of a pip package

There is no `vina` wheel for Windows on PyPI (checked 1.2.0.dev1 … 1.2.7: no win
wheels; the sdist needs Boost + SWIG to compile). The stage-3 agent imports
`from vina import Vina`, so this package ships the official Vina CLI binary plus a
thin adapter, `scripts/shim/vina.py`, that maps the documented Python API onto the
real CLI. Every affinity still comes from the `REMARK VINA RESULT` lines the binary
wrote; the shim scores nothing itself.

## Verify the binary

```bash
tools/vina_1.2.5_win.exe --version
# AutoDock Vina v1.2.5

sha256sum tools/vina_1.2.5_win.exe
# b5a23b226ee5dc1b028df04082d13a7b0e5b9b12b988bbcf9498cb351e20b38f  tools/vina_1.2.5_win.exe
```

`tests/smoke_test.py` and `scripts/run_pipeline.py` both refuse to continue if the
hash does not match, so a swapped or corrupted binary cannot silently change results.

## Replacing it

Any official Vina 1.2.5 Windows build with the same hash is fine. If you must use a
different build, point `A4S_VINA_EXE` at it **and** update
`cases/hiv1_1t3r_darunavir/case.json → engine.binary_sha256`; otherwise the hash gate
stops the run (deliberately — a different binary means the expected numbers no longer
apply).

Source note: the binary was fetched from the upstream GitHub release and used
unmodified. Reproducing the download needs no credentials.
