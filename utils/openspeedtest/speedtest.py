#!/usr/bin/env python3
import argparse
import http.client
import signal
import sys
import time
import threading
from urllib.parse import urlparse


def parse_duration(value: str) -> int:
    """Seconds; 0 / forever / inf / unlimited / -1 = run until Ctrl+C."""
    s = str(value).strip().lower()
    if s in ("0", "forever", "inf", "infinite", "unlimited", "-1"):
        return 0
    n = int(s)
    if n < 0:
        return 0
    return n


class SpeedTest:
    def __init__(self, server_url, bind_ip=None, duration=10, num_threads=6):
        # Normalize URL
        if not server_url.startswith("http://") and not server_url.startswith("https://"):
            server_url = "http://" + server_url
        parsed = urlparse(server_url)
        self.host = parsed.hostname
        self.port = parsed.port or (443 if parsed.scheme == "https" else 80)
        self.scheme = parsed.scheme
        self.bind_ip = bind_ip
        self.duration = duration  # 0 = forever
        self.num_threads = num_threads
        self.total_bytes = 0
        self.running = False
        self._stop = threading.Event()

    @property
    def forever(self) -> bool:
        return self.duration <= 0

    def request_stop(self, *_args):
        self._stop.set()
        self.running = False

    def _get_connection(self):
        source_address = (self.bind_ip, 0) if self.bind_ip else None
        if self.scheme == "https":
            import ssl
            # Allow self-signed certs commonly used in OpenSpeedTest setups
            context = ssl._create_unverified_context()
            return http.client.HTTPSConnection(
                self.host, self.port,
                source_address=source_address,
                context=context,
                timeout=5
            )
        else:
            return http.client.HTTPConnection(
                self.host, self.port,
                source_address=source_address,
                timeout=5
            )

    def _download_worker(self):
        while self.running and not self._stop.is_set():
            try:
                conn = self._get_connection()
                conn.request("GET", "/downloading")
                resp = conn.getresponse()
                if resp.status != 200:
                    print(f"\n[!] Download HTTP Error: {resp.status}")
                    time.sleep(1)
                    continue
                while self.running and not self._stop.is_set():
                    chunk = resp.read(65536)
                    if not chunk:
                        break
                    self.total_bytes += len(chunk)
                conn.close()
            except Exception as e:
                if self.running and not self._stop.is_set():
                    print(f"\n[!] Download Exception: {e}")
                    time.sleep(1)

    def _upload_worker(self, data_block):
        # We pre-generate block and reuse it to avoid CPU overhead
        while self.running and not self._stop.is_set():
            try:
                conn = self._get_connection()
                # Send headers and body
                conn.request("POST", "/upload", body=data_block)
                resp = conn.getresponse()
                # Must read the response to complete the request cycle
                resp.read()
                if resp.status == 200:
                    self.total_bytes += len(data_block)
                else:
                    print(f"\n[!] Upload HTTP Error: {resp.status}")
                conn.close()
            except Exception as e:
                if self.running and not self._stop.is_set():
                    print(f"\n[!] Upload Exception: {e}")
                    time.sleep(1)

    def _run_timed(self, label: str, worker_factory):
        if self.forever:
            print(
                f"[*] Starting {label} test to {self.host}:{self.port} forever (Ctrl+C to stop)...",
                flush=True,
            )
        else:
            print(
                f"[*] Starting {label} test to {self.host}:{self.port} for {self.duration} seconds...",
                flush=True,
            )
        self.total_bytes = 0
        self.running = True
        self._stop.clear()
        threads = []
        for _ in range(self.num_threads):
            t = threading.Thread(target=worker_factory)
            t.daemon = True
            t.start()
            threads.append(t)

        start_time = time.time()
        last_time = start_time
        last_bytes = 0

        try:
            while not self._stop.is_set():
                now = time.time()
                elapsed = now - start_time
                if not self.forever and elapsed >= self.duration:
                    break

                if now - last_time >= 1.0:
                    interval_elapsed = now - last_time
                    current_bytes = self.total_bytes
                    interval_bytes = current_bytes - last_bytes
                    speed = (interval_bytes * 8) / interval_elapsed / 1_000_000
                    if self.forever:
                        print(
                            f"    Elapsed: {int(elapsed):5d}s | Current Speed: {speed:6.2f} Mbps",
                            flush=True,
                        )
                    else:
                        print(
                            f"    Time remaining: {int(self.duration - elapsed):2d}s | Current Speed: {speed:6.2f} Mbps",
                            flush=True,
                        )
                    last_time = now
                    last_bytes = current_bytes

                time.sleep(0.1)
        except KeyboardInterrupt:
            print(f"\n[!] Interrupted — stopping {label} test...", flush=True)
            self.request_stop()

        self.running = False
        for t in threads:
            t.join(timeout=1)

        actual_elapsed = max(time.time() - start_time, 1e-6)
        speed_mbps = (self.total_bytes * 8) / actual_elapsed / 1_000_000
        print(f"[+] {label.capitalize()} test finished.", flush=True)
        print(f"    Total data: {self.total_bytes / 1024 / 1024:.2f} MB", flush=True)
        print(f"    Elapsed: {actual_elapsed:.1f}s", flush=True)
        print(f"    Average {label.capitalize()} Speed: {speed_mbps:.2f} Mbps\n", flush=True)
        return speed_mbps

    def run_download(self):
        return self._run_timed("download", self._download_worker)

    def run_upload(self):
        payload = b"x" * (5 * 1024 * 1024)
        return self._run_timed("upload", lambda: self._upload_worker(payload))


def main():
    parser = argparse.ArgumentParser(description="Python SpeedTest utility for OpenSpeedTest self-hosted servers.")
    parser.add_argument("--server", "-s", default="10.1.132.11", help="OpenSpeedTest server URL or IP (default: 10.1.132.11)")
    parser.add_argument("--bind", "-b", default=None, help="Local IP address to bind for the source of connections")
    parser.add_argument(
        "-d", "--duration",
        type=parse_duration, default=10,
        help="Seconds per direction (default: 10). Use 0 or forever for continuous until Ctrl+C",
    )
    parser.add_argument("--threads", "-t", type=int, default=6, help="Number of parallel worker connections (default: 6)")
    parser.add_argument(
        "--dir", "--direction",
        dest="direction",
        choices=["download", "dl", "upload", "ul", "both"],
        default="both",
        help="Speedtest direction: download|dl|upload|ul|both (default: both)",
    )
    parser.add_argument("--skip-upload", action="store_true", help="Skip the upload test (deprecated, use --direction)")
    parser.add_argument("--skip-download", action="store_true", help="Skip the download test (deprecated, use --direction)")
    args = parser.parse_args()

    tester = SpeedTest(
        server_url=args.server,
        bind_ip=args.bind,
        duration=args.duration,
        num_threads=args.threads
    )
    signal.signal(signal.SIGINT, tester.request_stop)
    signal.signal(signal.SIGTERM, tester.request_stop)

    print("==================================================", flush=True)
    print(f"  OpenSpeedTest CLI (Target: {tester.host}:{tester.port})", flush=True)
    if args.bind:
        print(f"  Bound to source interface IP: {args.bind}", flush=True)
    if tester.forever:
        print("  Duration: forever (Ctrl+C to stop)", flush=True)
    print("==================================================", flush=True)

    dl_speed = None
    ul_speed = None

    run_dl = args.direction in ["download", "dl", "both"] and not args.skip_download
    run_ul = args.direction in ["upload", "ul", "both"] and not args.skip_upload
    if tester.forever and run_dl and run_ul:
        print(
            "[!] forever needs a single direction: use --direction download|upload",
            flush=True,
        )
        sys.exit(2)

    if run_dl:
        dl_speed = tester.run_download()

    if run_ul:
        ul_speed = tester.run_upload()

    print("==================================================", flush=True)
    print("  Summary Results:", flush=True)
    if dl_speed is not None:
        print(f"  - Download Speed: {dl_speed:.2f} Mbps", flush=True)
    if ul_speed is not None:
        print(f"  - Upload Speed:   {ul_speed:.2f} Mbps", flush=True)
    print("==================================================", flush=True)


if __name__ == "__main__":
    main()
