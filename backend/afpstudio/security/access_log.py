"""Keep request URLs and customer queries out of the built-in local log."""
from werkzeug.serving import WSGIRequestHandler


class LocalRequestHandler(WSGIRequestHandler):
    def log_request(self, code="-", size="-"):
        # Flask errors still surface, but successful and rejected request
        # URLs are not recorded in either desktop or browser mode.
        return None
