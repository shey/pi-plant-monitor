#!/usr/bin/env bash
set -euo pipefail

readonly VIDEO_DEVICE="/dev/video0"
readonly VIDEO_SIZE="2048x1536"

readonly BUCKET="${PLANT_CAM_BUCKET:?PLANT_CAM_BUCKET is required}"
readonly PREFIX="plant-cam"

configure_camera() {
  v4l2-ctl -d "${VIDEO_DEVICE}" \
    --set-ctrl=power_line_frequency=2 \
    --set-ctrl=white_balance_automatic=1 \
    --set-ctrl=brightness=75 \
    --set-ctrl=contrast=65 \
    --set-ctrl=saturation=90
}

build_timestamped_s3_uri() {
  local timestamp
  local object_key

  timestamp="$(date +%Y%m%d-%H%M%S)"
  object_key="${PREFIX}/plant-${timestamp}.jpg"

  echo "s3://${BUCKET}/${object_key}"
}

build_latest_s3_uri() {
  echo "s3://${BUCKET}/latest.jpg"
}

capture_image() {
  ffmpeg \
    -loglevel error \
    -f v4l2 \
    -input_format mjpeg \
    -video_size "${VIDEO_SIZE}" \
    -i "${VIDEO_DEVICE}" \
    -ss 1.5 \
    -frames:v 1 \
    -vf "unsharp=5:5:1.0:5:5:0.0, eq=gamma=0.85:contrast=1.15:saturation=1.25" \
    -f image2pipe \
    -vcodec mjpeg \
    -q:v 2 \
    -
}

main() {
  local timestamped_s3_uri
  local latest_s3_uri
  local ram_buffer

  configure_camera

  timestamped_s3_uri="$(build_timestamped_s3_uri)"
  latest_s3_uri="$(build_latest_s3_uri)"
  
  # Temporary path in RAM to safely handle image bytes without degrading the SD card
  ram_buffer="/dev/shm/plant_cam_tmp.jpg"

  # Capture raw binary stream directly to RAM disk
  capture_image > "${ram_buffer}"

  # Verify the file was captured successfully and isn't empty before uploading
  if [[ -s "${ram_buffer}" ]]; then
    /usr/bin/s3cmd put -q --mime-type="image/jpeg" "${ram_buffer}" "${latest_s3_uri}"
    /usr/bin/s3cmd put -q --mime-type="image/jpeg" "${ram_buffer}" "${timestamped_s3_uri}"
  else
    echo "Error: Captured image buffer is empty." >&2
    exit 1
  fi

  # Clean up the RAM allocation
  rm -f "${ram_buffer}"
}

main "$@"
