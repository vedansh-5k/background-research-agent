import datetime


class RunLogger:
    def __init__(self):
        self.lines: list[str] = []

    def log(self, stage: str, message: str) -> None:
        ts = datetime.datetime.now().strftime("%H:%M:%S")
        line = f"[{ts}] [{stage}] {message}"
        self.lines.append(line)
        print(line)

    def text(self) -> str:
        return "\n".join(self.lines)
