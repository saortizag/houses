"""Reusable Selenium browser configured to look like a real human visitor."""

import json
import random
import time

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service

from paths import CHROME_PROFILE_DIR

USER_AGENTS = [
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/126.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
]

WINDOW_SIZES = [(1920, 1080), (1366, 768), (1536, 864), (1440, 900)]

FINGERPRINT_PATCH_JS = """
Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
Object.defineProperty(navigator, 'languages', {get: () => ['en-US', 'en']});
Object.defineProperty(navigator, 'plugins', {get: () => [1, 2, 3, 4, 5]});
window.chrome = window.chrome || { runtime: {} };
const originalQuery = window.navigator.permissions.query;
window.navigator.permissions.query = (parameters) => (
    parameters.name === 'notifications'
        ? Promise.resolve({ state: Notification.permission })
        : originalQuery(parameters)
);
"""


class StealthBrowser:
    """Launches a Chromium instance configured to minimize automation tells."""

    def __init__(
        self,
        binary_location: str = "/snap/bin/chromium",
        user_data_dir: str | None = None,
        user_agent: str | None = None,
        language: str = "en-US,en;q=0.9",
        window_size: tuple[int, int] | None = None,
        extra_arguments: list[str] | None = None,
        driver_path: str | None = None,
        capture_network: bool = False,
        headless: bool = False,
    ):
        self.binary_location = binary_location
        self.user_data_dir = user_data_dir or str(CHROME_PROFILE_DIR)
        self.user_agent = user_agent
        self.language = language
        self.window_size = window_size
        self.extra_arguments = extra_arguments or []
        self.driver_path = driver_path
        self.capture_network = capture_network
        self.headless = headless
        self.driver: webdriver.Chrome | None = None

    def _build_options(self) -> Options:
        options = Options()
        options.binary_location = self.binary_location

        user_agent = self.user_agent or random.choice(USER_AGENTS)
        width, height = self.window_size or random.choice(WINDOW_SIZES)

        options.add_argument(f"--user-agent={user_agent}")
        options.add_argument(f"--window-size={width},{height}")
        options.add_argument(f"--lang={self.language.split(',')[0]}")
        options.add_argument("--disable-blink-features=AutomationControlled")
        options.add_argument(f"--user-data-dir={self.user_data_dir}")

        options.add_experimental_option("excludeSwitches", ["enable-automation"])
        options.add_experimental_option("useAutomationExtension", False)
        options.add_experimental_option(
            "prefs",
            {
                "intl.accept_languages": self.language,
                "credentials_enable_service": False,
                "profile.password_manager_enabled": False,
            },
        )

        for argument in self.extra_arguments:
            options.add_argument(argument)

        if self.capture_network:
            options.set_capability("goog:loggingPrefs", {"performance": "ALL"})

        if self.headless:
            # "new" headless mode renders closer to a real headed browser
            # than the legacy --headless flag, so it's less of a bot tell.
            options.add_argument("--headless=new")

        return options

    def _patch_fingerprint(self) -> None:
        self.driver.execute_cdp_cmd(
            "Page.addScriptToEvaluateOnNewDocument", {"source": FINGERPRINT_PATCH_JS}
        )
        self.driver.execute_cdp_cmd(
            "Network.setExtraHTTPHeaders", {"headers": {"Accept-Language": self.language}}
        )

    def start(self) -> webdriver.Chrome:
        options = self._build_options()
        service = Service(executable_path=self.driver_path) if self.driver_path else Service()
        self.driver = webdriver.Chrome(options=options, service=service)
        self._patch_fingerprint()
        return self.driver

    def get(self, url: str, min_delay: float = 0.5, max_delay: float = 1.5) -> None:
        self.driver.get(url)
        time.sleep(random.uniform(min_delay, max_delay))

    def find_network_response(
        self, url_substring: str, poll_timeout: float = 10.0, poll_interval: float = 0.5
    ) -> tuple[str | None, dict | None]:
        """Poll captured performance logs for a response whose URL contains
        `url_substring`, then fetch its body via CDP. Requires
        `capture_network=True`. Returns (request_url, response_json) or
        (None, None) if nothing matched before the timeout.
        """
        deadline = time.time() + poll_timeout
        while time.time() < deadline:
            for entry in self.driver.get_log("performance"):
                message = json.loads(entry["message"])["message"]
                if message.get("method") != "Network.responseReceived":
                    continue
                response = message["params"]["response"]
                if url_substring not in response["url"]:
                    continue
                request_id = message["params"]["requestId"]
                try:
                    body = self.driver.execute_cdp_cmd(
                        "Network.getResponseBody", {"requestId": request_id}
                    )
                except Exception:
                    continue
                return response["url"], json.loads(body["body"])
            time.sleep(poll_interval)
        return None, None

    def quit(self) -> None:
        if self.driver is not None:
            self.driver.quit()
            self.driver = None

    def __enter__(self) -> "StealthBrowser":
        self.start()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.quit()


if __name__ == "__main__":
    with StealthBrowser() as browser:
        browser.get("https://bot.sannysoft.com/")
        time.sleep(10)
