import React, { useState } from "react";
import type { OttChannel } from "../lib/api";

interface AddStreamModalProps {
  channels: OttChannel[];
  isOpen: boolean;
  onClose: () => void;
  onAddStream: (channelId: string, youtubeUrl: string, title?: string) => Promise<void>;
}

export default function AddStreamModal({
  channels,
  isOpen,
  onClose,
  onAddStream,
}: AddStreamModalProps) {
  const [selectedChannel, setSelectedChannel] = useState("channel_1");
  const [youtubeUrl, setYoutubeUrl] = useState("");
  const [title, setTitle] = useState("");
  const [loading, setLoading] = useState(false);

  if (!isOpen) return null;

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!youtubeUrl) return;
    setLoading(true);
    try {
      await onAddStream(selectedChannel, youtubeUrl, title || undefined);
      onClose();
      setYoutubeUrl("");
      setTitle("");
    } catch (err) {
      alert(`Failed to add stream: ${err}`);
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="modal-card" onClick={(e) => e.stopPropagation()}>
        <h3 className="modal-title">📺 Stream YouTube Video</h3>
        <form onSubmit={handleSubmit}>
          <div className="form-group">
            <label>Target Channel</label>
            <select
              className="form-input"
              value={selectedChannel}
              onChange={(e) => setSelectedChannel(e.target.value)}
            >
              {channels.map((ch) => (
                <option key={ch.id} value={ch.id}>
                  {ch.id.toUpperCase()}: {ch.name}
                </option>
              ))}
            </select>
          </div>

          <div className="form-group">
            <label>YouTube Video URL or Video ID</label>
            <input
              type="text"
              className="form-input"
              placeholder="e.g. https://www.youtube.com/watch?v=1La4QzGeaaQ"
              value={youtubeUrl}
              onChange={(e) => setYoutubeUrl(e.target.value)}
              required
            />
          </div>

          <div className="form-group">
            <label>Channel Title (Optional)</label>
            <input
              type="text"
              className="form-input"
              placeholder="e.g. 4K Drone City Tour"
              value={title}
              onChange={(e) => setTitle(e.target.value)}
            />
          </div>

          <div className="modal-actions">
            <button type="button" className="btn-secondary" onClick={onClose} disabled={loading}>
              Cancel
            </button>
            <button type="submit" className="btn-primary" disabled={loading}>
              {loading ? "Streaming..." : "Stream Video"}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
