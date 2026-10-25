# Dependency and licensing status

Cacheweft's code and associated project documentation are licensed under
[MIT](../LICENSE), copyright 2026 Andrew Yuan. Package metadata uses SPDX
`MIT`, and wheel/source distributions include the same LICENSE text.

## Runtime and external tools

The product's Python dependency list remains empty. It uses Python standard
libraries, including the platform's SQLite and TLS/OpenSSL integration. These
external components retain their own notices and terms; the project MIT license
does not relicense them:

- [Python licensing](https://docs.python.org/3.12/license.html)
- [OpenSSL licensing](https://openssl-library.org/source/license/index.html)
- [SQLite copyright/public-domain statement](https://www.sqlite.org/copyright.html)

Build tooling (setuptools/pip), Colima/Lima, guest OS packages, systemd, strace
and OpenSSL CLI are external tools, not vendored product sources. Generated
test certificates are ephemeral and ignored. Neither the product wheel nor
this repository bundles third-party source, weights or an image dataset.

## Optional real-model demonstration

[The trust lock](../examples/model.json) pins the SqueezeNet model revision,
size and SHA-256. Its [upstream model card](https://github.com/onnx/models/blob/c5cae0f1942c8d7727372c7b1f6b485aa39e4421/validated/vision/classification/squeezenet/README.md)
identifies Apache-2.0, with the corresponding
[upstream license](https://github.com/onnx/models/blob/c5cae0f1942c8d7727372c7b1f6b485aa39e4421/LICENSE).
The example downloads it separately; its license is independent of Cacheweft's
MIT license. Anyone redistributing those weights must preserve the upstream
license/notices. The repository contains only provenance and observed results.
Synthetic inference input is generated locally; no ImageNet images are included.

The separate inference environment installs pinned NumPy, ONNX Runtime,
flatbuffers, packaging and protobuf from [requirements.txt](../examples/requirements.txt).
Those components remain subject to their upstream licenses and third-party notices;
they are external optional tools, not bundled or product runtime requirements.
The demo's independent installation does not place them under the project MIT
license. See [MODEL_DEMO.md](MODEL_DEMO.md) for the exact provenance and limits.

## Historical evidence

Earlier records correctly describe their revisions as having no selected
project license. They remain unchanged. MIT adoption changes licensing/package
metadata, not runtime code or the recorded measurements. The final-review
qualification explicitly checks that only the two licensing metadata fields
were added to the measured packaging configuration.

## MIT package verification

[The verification record](../evidence/licensing.json) records a wheel and source
distribution built with SPDX `MIT` / `License-File: LICENSE`. Both contain the
exact root LICENSE text. A fresh local installation exposes the same metadata
and passes `cacheweft --help`. All five runtime modules match the previously
measured runtime byte for byte. The licensed archive hashes differ from the old
unlicensed archives because their metadata/license contents changed; this does
not constitute a new performance qualification. No package was published to PyPI.

Installed optional dependency metadata/notices were inspected. FlatBuffers
25.12.19 declares Apache 2.0 in metadata but its installed wheel has no standalone
license file. The matching upstream release was checked at
[commit 7e16302](https://github.com/google/flatbuffers/blob/7e163021e59cca4f8e1e35a7c828b5c6b7915953/LICENSE)
instead. The record preserves that distinction; Cacheweft redistributes neither
that wheel nor its source. NumPy/ONNX Runtime/protobuf notices and packaging's
`Apache-2.0 OR BSD-2-Clause` expression remain with their external installations.
