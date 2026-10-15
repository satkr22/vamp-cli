class Token_Tracker:
    def __init__(self):
        self.usage = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
        self.open = False

    def start(self):
        """Begin a new user turn."""
        self.usage = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
        self.open = True

    def add(self, delta):
        if not delta:
            return
        for k in self.usage:
            self.usage[k] += delta.get(k, 0)

    def close(self):
        """Turn ended (graph reached END with no pending resume)."""
        if not self.open:
            return
        print("Token Usage (this turn):")
        print(f"  Input : {self.usage['input_tokens']}")
        print(f"  Output: {self.usage['output_tokens']}")
        print(f"  Total : {self.usage['total_tokens']}")
        print()
        self.open = False

    def abort(self):
        """User cancelled — drop the accumulator without printing."""
        self.usage = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
        self.open = False