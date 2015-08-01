from __future__ import print_function
import requests

class EventSourceFollower:
    def __init__(self, url, timeout):
        self.resp = requests.get(url,
                                 headers={"accept": "text/event-stream"},
                                 stream=True,
                                 timeout=timeout)
        self.resp.raise_for_status()

    def close(self):
        self.resp.close()

    def _get_fields(self, lines):
        while True:
            first_line = next(lines) # raises StopIteration when closed
            fieldname, data = first_line.split(b": ", 1)
            data_lines = [data]
            while True:
                next_line = next(lines)
                if not next_line: # empty string, original was "\n"
                    yield (fieldname, b"\n".join(data_lines))
                    break
                data_lines.append(next_line)

    def iter_events(self):
        # I think Request.iter_lines and .iter_content use chunk_size= in a
        # funny way, and nothing happens until at least that much data has
        # arrived. So unless we set chunk_size=1, we won't hear about lines
        # for a long time. I'd prefer that chunk_size behaved like
        # read(size), and gave you 1<=x<=size bytes in response.
        eventtype = "message"
        lines_iter = self.resp.iter_lines(chunk_size=1)
        for (fieldname, data) in self._get_fields(lines_iter):
            if fieldname == b"data":
                yield (eventtype, data)
                eventtype = "message"
            elif fieldname == b"event":
                eventtype = data
            else:
                print("weird fieldname", fieldname, data)
