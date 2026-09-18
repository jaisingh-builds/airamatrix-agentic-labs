"""Tools for the gallery. Deliberately honest: they report their own failures."""


class ToyTools:
    def __init__(self) -> None:
        self.files = {"limits.txt": "max_queue_depth = 500"}
        self.calls: list[tuple[str, dict]] = []

    def dispatch(self, name: str, args: dict) -> tuple[str, bool]:
        self.calls.append((name, args))
        if name == "read_file":
            path = args.get("path", "")
            if path not in self.files:
                return (f"ERROR: no such file: {path}. "
                        f"Available files: {', '.join(self.files)}"), False
            return self.files[path], True
        if name == "save_report":
            return "report saved", True
        return f"unknown tool: {name}", False
