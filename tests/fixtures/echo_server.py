"""A non-MCP test server that echoes every stdin line to stdout and records it.

What: reads bytes from stdin line by line, appends each line to the file named by
ECHO_RECORD_PATH and writes the same bytes to stdout.
Why: the proxy passthrough test must compare exact bytes in both directions, including lines
that are not valid JSON, which a real MCP server would reject.
How: the test starts the proxy with this script as the "server", sends bytes, then compares
what this script received (the record file) and what came back on the proxy's stdout.
"""

import os
import sys

record_path = os.environ["ECHO_RECORD_PATH"]
with open(record_path, "ab") as record:
    for line in sys.stdin.buffer:
        # Record exactly what arrived, then echo it unchanged.
        record.write(line)
        record.flush()
        sys.stdout.buffer.write(line)
        sys.stdout.buffer.flush()
