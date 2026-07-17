from orchestrator.vm import VmLifecycleService


class Adapter:
    def __init__(self) -> None:
        self.destroyed: list[tuple[str, str]] = []

    def provision(self, run_id: str, guest_id: str, overlay_id: str) -> None:
        assert run_id == "run_example"

    def probe_ready(self, guest_id: str) -> bool:
        return guest_id == "guest-example"

    def destroy(self, guest_id: str, overlay_id: str) -> None:
        self.destroyed.append((guest_id, overlay_id))


def test_lifecycle_is_run_scoped_and_destroy_is_idempotent() -> None:
    adapter = Adapter()
    service = VmLifecycleService(adapter)
    assert service.create("run_example").status == "creating"
    assert service.probe("run_example").status == "ready"
    assert service.destroy("run_example").status == "destroyed"
    service.destroy("run_example")
    assert adapter.destroyed == [("guest-example", "overlay-example")]
