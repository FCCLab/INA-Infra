#!/usr/bin/env python3
import argparse
import http.client
import sys
import time
import threading
from urllib.parse import urlparse

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
        self.duration = duration
        self.num_threads = num_threads
        self.total_bytes = 0
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
        while self.running:
            try:
                conn = self._get_connection()
                conn.request("GET", "/downloading")
                resp = conn.getresponse()
                if resp.status != 200:
                    print(f"\n[!] Download HTTP Error: {resp.status}")
                    time.sleep(1)
                    continue
                while self.running:
                    chunk = resp.read(65536)
                    if not chunk:
                        break
                    self.total_bytes += len(chunk)
                conn.close()
            except Exception as e:
                print(f"\n[!] Download Exception: {e}")
                time.sleep(1)

    def _upload_worker(self, data_block):
        # We pre-generate block and reuse it to avoid CPU overhead
        while self.running:
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
                print(f"\n[!] Upload Exception: {e}")
                time.sleep(1)

    def run_download(self):
        print(f"[*] Starting download test to {self.host}:{self.port} for {self.duration} seconds...", flush=True)
        self.total_bytes = 0
        self.running = True
        threads = []
        for _ in range(self.num_threads):
            t = threading.Thread(target=self._download_worker)
            t.daemon = True
            t.start()
            threads.append(t)

        start_time = time.time()
        last_time = start_time
        last_bytes = 0

        while True:
            now = time.time()
            elapsed = now - start_time
            if elapsed >= self.duration:
                break
            
            # Print intermediate speed every 1 second
            if now - last_time >= 1.0:
                interval_elapsed = now - last_time
                current_bytes = self.total_bytes
                interval_bytes = current_bytes - last_bytes
                speed = (interval_bytes * 8) / interval_elapsed / 1_000_000
                # Newline (not \r) so progress streams over SSH/kubectl without a TTY.
                print(
                    f"    Time remaining: {int(self.duration - elapsed):2d}s | Current Speed: {speed:6.2f} Mbps",
                    flush=True,
                )
                last_time = now
                last_bytes = current_bytes
            
            time.sleep(0.1)

        self.running = False
        # Wait for threads to terminate gracefully
        for t in threads:
            t.join(timeout=1)

        actual_elapsed = time.time() - start_time
        speed_mbps = (self.total_bytes * 8) / actual_elapsed / 1_000_000
        print(f"[+] Download test finished.", flush=True)
        print(f"    Total data: {self.total_bytes / 1024 / 1024:.2f} MB", flush=True)
        print(f"    Average Download Speed: {speed_mbps:.2f} Mbps\n", flush=True)
        return speed_mbps

    def run_upload(self):
        print(f"[*] Starting upload test to {self.host}:{self.port} for {self.duration} seconds...", flush=True)
        self.total_bytes = 0
        self.running = True
        
        # Pre-allocate 5MB payload to minimize CPU overhead in loop
        payload = b"x" * (5 * 1024 * 1024)
        
        threads = []
        for _ in range(self.num_threads):
            t = threading.Thread(target=self._upload_worker, args=(payload,))
            t.daemon = True
            t.start()
            threads.append(t)

        start_time = time.time()
        last_time = start_time
        last_bytes = 0

        while True:
            now = time.time()
            elapsed = now - start_time
            if elapsed >= self.duration:
                break
            
            # Print intermediate speed every 1 second
            if now - last_time >= 1.0:
                interval_elapsed = now - last_time
                current_bytes = self.total_bytes
                interval_bytes = current_bytes - last_bytes
                speed = (interval_bytes * 8) / interval_elapsed / 1_000_000
                print(
                    f"    Time remaining: {int(self.duration - elapsed):2d}s | Current Speed: {speed:6.2f} Mbps",
                    flush=True,
                )
                last_time = now
                last_bytes = current_bytes
            
            time.sleep(0.1)

        self.running = False
        for t in threads:
            t.join(timeout=1)

        actual_elapsed = time.time() - start_time
        speed_mbps = (self.total_bytes * 8) / actual_elapsed / 1_000_000
        print(f"[+] Upload test finished.", flush=True)
        print(f"    Total data: {self.total_bytes / 1024 / 1024:.2f} MB", flush=True)
        print(f"    Average Upload Speed: {speed_mbps:.2f} Mbps\n", flush=True)
        return speed_mbps

def main():
    parser = argparse.ArgumentParser(description="Python SpeedTest utility for OpenSpeedTest self-hosted servers.")
    parser.add_argument("--server", "-s", default="10.1.132.11", help="OpenSpeedTest server URL or IP (default: 10.1.132.11)")
    parser.add_argument("--bind", "-b", default=None, help="Local IP address to bind for the source of connections")
    parser.add_argument("--duration", "-d", type=int, default=10, help="Test duration in seconds per direction (default: 10)")
    parser.add_argument("--threads", "-t", type=int, default=6, help="Number of parallel worker connections (default: 6)")
    parser.add_argument("--direction", "--dir", choices=["download", "dl", "upload", "ul", "both"], default="both", 
                        help="Speedtest direction: dl (download only), ul (upload only), or both (default)")
    parser.add_argument("--skip-upload", action="store_true", help="Skip the upload test (deprecated, use --direction)")
    parser.add_argument("--skip-download", action="store_true", help="Skip the download test (deprecated, use --direction)")
    args = parser.parse_args()

    tester = SpeedTest(
        server_url=args.server,
        bind_ip=args.bind,
        duration=args.duration,
        num_threads=args.threads
    )

    print("==================================================", flush=True)
    print(f"  OpenSpeedTest CLI (Target: {tester.host}:{tester.port})", flush=True)
    if args.bind:
        print(f"  Bound to source interface IP: {args.bind}", flush=True)
    print("==================================================", flush=True)

    dl_speed = None
    ul_speed = None

    run_dl = args.direction in ["download", "dl", "both"] and not args.skip_download
    run_ul = args.direction in ["upload", "ul", "both"] and not args.skip_upload

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
