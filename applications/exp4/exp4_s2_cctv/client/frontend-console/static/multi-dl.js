/* Multi-stream DeepStream YOLO downlink players (2x2). */
(function () {
  let playersWired = false;
  const tilePlayers = {};

  function waitIce(pc, ms) {
    if (pc.iceGatheringState === 'complete') return Promise.resolve();
    return new Promise((resolve) => {
      const t = setTimeout(resolve, ms);
      pc.addEventListener('icegatheringstatechange', () => {
        if (pc.iceGatheringState === 'complete') {
          clearTimeout(t);
          resolve();
        }
      });
    });
  }

  async function startTileWhep(tileId, video, url) {
    const prev = tilePlayers[tileId] || {};
    if (prev.pc) {
      try {
        prev.pc.close();
      } catch (e) {}
    }
    if (prev.hls) {
      try {
        prev.hls.destroy();
      } catch (e) {}
    }
    const pc = new RTCPeerConnection({ iceServers: [] });
    pc.addTransceiver('video', { direction: 'recvonly' });
    pc.ontrack = (ev) => {
      video.srcObject = ev.streams[0];
      video.play().catch(() => {});
    };
    const offer = await pc.createOffer();
    await pc.setLocalDescription(offer);
    await waitIce(pc, 2000);
    const resp = await fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/sdp' },
      body: pc.localDescription.sdp,
    });
    if (!resp.ok) throw new Error('WHEP ' + tileId + ' HTTP ' + resp.status);
    const answer = await resp.text();
    await pc.setRemoteDescription({ type: 'answer', sdp: answer });
    tilePlayers[tileId] = { pc, hls: null, video };
  }

  function startTileHls(tileId, video, url) {
    const prev = tilePlayers[tileId] || {};
    if (prev.pc) {
      try {
        prev.pc.close();
      } catch (e) {}
    }
    if (prev.hls) {
      try {
        prev.hls.destroy();
      } catch (e) {}
    }
    video.srcObject = null;
    if (window.Hls && Hls.isSupported()) {
      const h = new Hls({ enableWorker: true, lowLatencyMode: true });
      h.loadSource(url);
      h.attachMedia(video);
      h.on(Hls.Events.MANIFEST_PARSED, () => video.play().catch(() => {}));
      tilePlayers[tileId] = { pc: null, hls: h, video };
      return;
    }
    video.src = url;
    video.play().catch(() => {});
    tilePlayers[tileId] = { pc: null, hls: null, video };
  }

  window.wireMultiDlPlayers = async function wireMultiDlPlayers(state) {
    const streams = ((state && state.streams) || []).slice();
    const grid = document.getElementById('dl-grid');
    if (!grid) return;
    if (!streams.length) {
      for (let i = 1; i <= 4; i++) {
        streams.push({
          id: i,
          name: 'Camera ' + i,
          whep_url: '/whep/annotated' + i + '/whep',
          hls_url: '/live/annotated' + i + '/index.m3u8',
        });
      }
    }
    if (!playersWired) {
      grid.innerHTML = streams
        .map(
          (s) =>
            '<div class="player-tile" data-id="' +
            s.id +
            '">' +
            '<video id="dl-v-' +
            s.id +
            '" autoplay muted playsinline></video>' +
            '<span class="pill tile-label" id="dl-pill-' +
            s.id +
            '">' +
            (s.name || 'Cam ' + s.id) +
            ' · connecting…</span></div>'
        )
        .join('');
      playersWired = true;
    }
    const idPill = document.getElementById('dl-id-pill');
    if (idPill) idPill.textContent = 'streams: ' + streams.length;
    let okCount = 0;
    await Promise.all(
      streams.map(async (s) => {
        const video = document.getElementById('dl-v-' + s.id);
        const pill = document.getElementById('dl-pill-' + s.id);
        if (!video) return;
        try {
          await startTileWhep(s.id, video, s.whep_url);
          if (pill) {
            pill.textContent = s.name + ' · WHEP';
            pill.className = 'pill tile-label ok';
          }
          okCount += 1;
        } catch (err) {
          console.warn('WHEP failed', s.id, err);
          try {
            startTileHls(s.id, video, s.hls_url);
            if (pill) {
              pill.textContent = s.name + ' · HLS';
              pill.className = 'pill tile-label ok';
            }
            okCount += 1;
          } catch (e2) {
            if (pill) {
              pill.textContent = s.name + ' · waiting';
              pill.className = 'pill tile-label warn';
            }
          }
        }
      })
    );
    if (typeof setDlStatus === 'function') {
      setDlStatus(okCount > 0, 'DL: ' + okCount + '/' + streams.length);
    }
    const hint = document.getElementById('dl-hint');
    if (hint) {
      hint.textContent =
        'UE MediaMTX pulls ' + streams.length + ' annotated DeepStream streams (WHEP/HLS).';
    }
  };
})();
