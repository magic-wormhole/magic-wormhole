from __future__ import print_function, unicode_literals
import requests

class EventSourceFollower:
    def __init__(self, url, timeout):
        self._resp = requests.get(url,
                                  headers={"accept": "text/event-stream"},
                                  stream=True,
                                  timeout=timeout)
        self._resp.raise_for_status()
        self._lines_iter = self._resp.iter_lines(chunk_size=1,
                                                 decode_unicode=True)

    def close(self):
        self._resp.close()

    def iter_events(self):
        # I think Request.iter_lines and .iter_content use chunk_size= in a
        # funny way, and nothing happens until at least that much data has
        # arrived. So unless we set chunk_size=1, we won't hear about lines
        # for a long time. I'd prefer that chunk_size behaved like
        # read(size), and gave you 1<=x<=size bytes in response.
        eventtype = "message"
        current_lines = []
        for line in self._lines_iter:
            assert isinstance(line, type(u"")), type(line)
            if not line:
                # blank line ends the field: deliver event, reset for next
                yield (eventtype, "\n".join(current_lines))
                eventtype = "message"
                current_lines[:] = []
                continue
            if ":" in line:
                fieldname, data = line.split(":", 1)
                if data.startswith(" "):
                    data = data[1:]
            else:
                fieldname = line
                data = ""
            if fieldname == "event":
                eventtype = data
            elif fieldname == "data":
                current_lines.append(data)
            elif fieldname in ("id", "retry"):
                # documented but unhandled
                pass
            else:
                #log.msg("weird fieldname", fieldname, data)
                pass
