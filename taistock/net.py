import json
import time
from urllib.error import HTTPError
from urllib.request import Request, urlopen


class DataSourceError(RuntimeError):
    pass


class HttpClient:
    def __init__(self, timeout=25, retries=3):
        self.timeout, self.retries = timeout, retries

    def json(self, url):
        last = None
        for attempt in range(self.retries):
            try:
                request = Request(url, headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json", "Accept-Encoding": "identity"})
                with urlopen(request, timeout=self.timeout) as response:
                    return json.loads(response.read().decode("utf-8-sig"))
            except Exception as exc:
                last = exc
                if attempt + 1 < self.retries:
                    delay = min(60.0, 1.5 * (2 ** attempt))
                    if isinstance(exc, HTTPError) and exc.code == 429:
                        try:
                            delay = max(delay, float(exc.headers.get("Retry-After", delay)))
                        except (TypeError, ValueError):
                            pass
                    time.sleep(delay)
        raise DataSourceError(f"資料來源連線失敗 {url}: {last}") from last
