"""Single-line terminal progress for the dictionary build stages."""

import sys
from typing import TextIO


class ProgressBar:
    def __init__(self, label: str, total: int, stream: TextIO = sys.stderr,
                 unit: str = "words"):
        self.label = label
        self.total = total
        self.stream = stream
        self.unit = unit
        self.last_width = 0

    def show(self, current: int, details: str = "") -> None:
        if not self.stream.isatty():
            return
        fraction = min(current / self.total, 1.0) if self.total else 1.0
        filled = round(30 * fraction)
        message = (
            f"\r{self.label} [{'#' * filled}{'-' * (30 - filled)}] "
            f"{fraction:6.2%} {current:,}/{self.total:,} {self.unit}"
        )
        if details:
            message += f" {details}"
        self.stream.write(message.ljust(self.last_width))
        self.stream.flush()
        self.last_width = max(self.last_width, len(message))

    def finish(self) -> None:
        if self.stream.isatty():
            self.stream.write("\n")
            self.stream.flush()
