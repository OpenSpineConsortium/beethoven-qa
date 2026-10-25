# typeguard's import hook is the test-enclave layer of the typing rule (CLAUDE.md, definition of
# done, step 3): deeper and slower than beartype, so it runs only here. It must be installed before
# the package is first imported, which is why it lives in conftest rather than in a test module.
import pytest
from typeguard import install_import_hook

install_import_hook("beethoven_qa")


@pytest.fixture(autouse=True)
def _no_base_from_the_hook(monkeypatch: pytest.MonkeyPatch) -> None:
    # The kit's pre-push hook exports BEETHOVEN_QA_BASE=auto for the gate it runs, and the tests run
    # inside that gate, so a gate a test starts would take the hook's base; a test that means a base
    # passes it itself.
    monkeypatch.delenv("BEETHOVEN_QA_BASE", raising=False)
