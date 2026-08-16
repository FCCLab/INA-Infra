/** Intel IoT sample clips — one distinct file per CCTV UE client. */

export type CctvClip = {
  id: string;
  file: string;
  label: string;
};

export const CCTV_SAMPLE_BASE =
  "https://github.com/intel-iot-devkit/sample-videos/raw/master";

export const CCTV_CLIPS: CctvClip[] = [
  { id: "classroom", file: "classroom.mp4", label: "Classroom" },
  { id: "car-detection", file: "car-detection.mp4", label: "Street / cars" },
  { id: "people-detection", file: "people-detection.mp4", label: "People" },
  { id: "store-aisle", file: "store-aisle-detection.mp4", label: "Store aisle" },
  { id: "person-bicycle-car", file: "person-bicycle-car-detection.mp4", label: "Bike / traffic" },
  { id: "worker-zone", file: "worker-zone-detection.mp4", label: "Worker zone" },
  { id: "one-by-one-person", file: "one-by-one-person-detection.mp4", label: "Lobby / people" },
  { id: "bottle-detection", file: "bottle-detection.mp4", label: "Bottles" },
  { id: "face-walking", file: "face-demographics-walking.mp4", label: "Walking faces" },
  { id: "head-pose-male", file: "head-pose-face-detection-male.mp4", label: "Head pose (male)" },
  { id: "head-pose-female", file: "head-pose-face-detection-female.mp4", label: "Head pose (female)" },
  { id: "face-walking-pause", file: "face-demographics-walking-and-pause.mp4", label: "Walking + pause" },
];

export function defaultCctvClipIds(clientCount: number): string[] {
  const n = Math.max(1, Math.min(10, Math.floor(clientCount) || 1));
  return Array.from({ length: n }, (_, i) => CCTV_CLIPS[i % CCTV_CLIPS.length].id);
}

export function clipById(id: string): CctvClip {
  return CCTV_CLIPS.find((c) => c.id === id) || CCTV_CLIPS[0];
}
