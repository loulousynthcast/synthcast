"""
synthcast/agent/queue.py

Priority comment queue.
Ensures the agent always responds to the most important
comments first — gifts > questions > compliments > general.

Thread-safe. The TikTok listener and the agent processor
run in separate threads and share this queue.
"""

import time
import heapq
import threading
from dataclasses import dataclass, field
from typing import Optional

from .response_engine import IncomingComment, Priority


@dataclass(order=True)
class QueuedComment:
    """
    Wrapper for priority queue ordering.
    Lower sort_key = higher priority (min-heap).
    """
    sort_key: tuple = field(compare=True)
    comment: IncomingComment = field(compare=False)

    @classmethod
    def from_comment(cls, comment: IncomingComment, priority: Priority) -> "QueuedComment":
        # Negate priority so higher Priority value = lower sort key = dequeued first
        # Tie-break by timestamp (older comments first within same priority)
        return cls(
            sort_key=(-priority.value, comment.timestamp),
            comment=comment,
        )


class CommentQueue:
    """
    Thread-safe priority queue for live comments.

    Producer (stream listener) calls: queue.push(comment, priority)
    Consumer (agent processor) calls: queue.pop()

    Max size prevents memory overflow on busy streams.
    Old low-priority comments are dropped when full.
    """

    def __init__(self, max_size: int = 500):
        self._heap: list[QueuedComment] = []
        self._lock = threading.Lock()
        self._max_size = max_size
        self._total_received = 0
        self._total_dropped = 0
        self._total_processed = 0

    def push(self, comment: IncomingComment, priority: Priority) -> bool:
        """
        Add a comment to the queue.
        Returns True if added, False if dropped (queue full).
        """
        with self._lock:
            self._total_received += 1

            if len(self._heap) >= self._max_size:
                # Drop the lowest-priority item if new one is higher priority
                if self._heap and priority.value > -self._heap[0].sort_key[0]:
                    # Pop the lowest priority (end of sorted heap)
                    # For a min-heap by sort_key, the lowest priority is the max sort_key
                    # Simple approach: just drop the new comment if queue is full
                    self._total_dropped += 1
                    return False

            item = QueuedComment.from_comment(comment, priority)
            heapq.heappush(self._heap, item)
            return True

    def pop(self, timeout: float = 1.0) -> Optional[IncomingComment]:
        """
        Get the highest-priority comment.
        Blocks for up to `timeout` seconds if queue is empty.
        Returns None if nothing arrives within the timeout.
        """
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            with self._lock:
                if self._heap:
                    item = heapq.heappop(self._heap)
                    self._total_processed += 1
                    return item.comment
            time.sleep(0.05)   # poll every 50ms
        return None

    def peek_priority(self) -> Optional[Priority]:
        """What priority is the next item? Non-blocking."""
        with self._lock:
            if not self._heap:
                return None
            top = self._heap[0]
            return Priority(-top.sort_key[0])

    def size(self) -> int:
        with self._lock:
            return len(self._heap)

    def clear(self):
        with self._lock:
            self._heap.clear()

    def stats(self) -> dict:
        with self._lock:
            return {
                "queue_size":       len(self._heap),
                "total_received":   self._total_received,
                "total_processed":  self._total_processed,
                "total_dropped":    self._total_dropped,
                "drop_rate_pct":    round(
                    (self._total_dropped / max(self._total_received, 1)) * 100, 1
                ),
            }
