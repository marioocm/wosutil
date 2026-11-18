"""Contract shared by the UI bundle and the local server.

The manifest published by ``wosutil-site`` declares this API version. A bundle
with a different version is not served: the app falls back to its built-in
page instead of running a front-end that talks to an incompatible backend.
"""

API_VERSION = 1
