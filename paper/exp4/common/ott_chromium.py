"""linuxserver/chromium sidecar YAML for Exp4 slice-3 OTT UEs."""

CHROMIUM_IMAGE = "10.1.132.30:5000/linuxserver-chromium:latest"

CHROME_CLI = (
    "--proxy-server=socks5://127.0.0.1:1080 "
    "--remote-debugging-port=9222 "
    "--remote-allow-origins=* "
    "--no-first-run --no-default-browser-check "
    "--disable-features=TranslateUI "
    "--autoplay-policy=no-user-gesture-required "
    "--disable-backgrounding-occluded-windows "
    "--disable-renderer-backgrounding "
    "--disable-background-timer-throttling "
    "--disable-gpu --no-sandbox "
    "--window-size=1920,1080 "
    "https://www.youtube.com"
)


def extra_ott_env(console_ip: str) -> str:
    return f"""
        - name: OTT_PLAY_MODE
          value: "chromium_5g"
        - name: OTT_PLAY_QUALITY
          value: "4k"
        - name: OTT_MOSAIC
          value: "1"
        - name: OTT_MOSAIC_COUNT
          value: "4"
        - name: OTT_MOSAIC_WIDTH
          value: "1920"
        - name: OTT_MOSAIC_HEIGHT
          value: "1080"
        - name: PDU_SOCKS_PORT
          value: "1080"
        - name: CHROME_CDP_HOST
          value: "127.0.0.1"
        - name: CHROME_CDP_PORT
          value: "9222"
        - name: CHROME_CDP_WAIT
          value: "90"
        - name: CHROME_UPSTREAM
          value: "http://127.0.0.1:3000"
        - name: CHROME_HTTP_URL
          value: "https://{console_ip}/chrome/"
        - name: HTTPS_PORT
          value: "443"
"""


def chromium_sidecar_yaml(ue_name: str) -> str:
    return f"""      - name: chromium
        image: {CHROMIUM_IMAGE}
        imagePullPolicy: IfNotPresent
        securityContext:
          privileged: true
          capabilities:
            add:
            - NET_ADMIN
            - NET_RAW
          seccompProfile:
            type: Unconfined
        env:
        - name: PUID
          value: "1000"
        - name: PGID
          value: "1000"
        - name: TZ
          value: "UTC"
        - name: TITLE
          value: "OTT UE {ue_name}"
        - name: DISABLE_IPV6
          value: "true"
        - name: PIXELFLUX_WAYLAND
          value: "false"
        - name: CHROME_CLI
          value: "{CHROME_CLI}"
        - name: CUSTOM_PORT
          value: "3000"
        - name: CUSTOM_HTTPS_PORT
          value: "3001"
        - name: SUBFOLDER
          value: "/chrome/"
        ports:
        - name: chrome-http
          containerPort: 3000
        - name: cdp
          containerPort: 9222
        volumeMounts:
        - name: dshm
          mountPath: /dev/shm
        - name: chromium-config
          mountPath: /config
        resources:
          requests:
            cpu: 500m
            memory: 2Gi
          limits:
            cpu: "8"
            memory: 12Gi
"""


def chromium_volumes_yaml() -> str:
    return """      - name: dshm
        emptyDir:
          medium: Memory
          sizeLimit: 2Gi
      - name: chromium-config
        emptyDir: {}
"""
