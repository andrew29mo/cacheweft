# Dependency and licensing status

Cacheweft has no selected license yet. No LICENSE file or licensing declaration
is included; repository access does not grant an open-source license.

The runtime uses Python standard libraries, Python's TLS/OpenSSL integration
and SQLite. Python, OpenSSL and SQLite retain their own notices/terms:

- https://docs.python.org/3.12/license.html
- https://openssl-library.org/source/license/index.html
- https://www.sqlite.org/copyright.html

Build tooling (setuptools/pip), Colima/Lima, guest OS packages and OpenSSL CLI
are external tools, not vendored product sources. Generated test certificates
are ephemeral and ignored. No model, dataset or third-party code is bundled.
Future actual model demonstration must separately record its license/provenance.
