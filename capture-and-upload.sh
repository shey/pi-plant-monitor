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
    --set-ctrl=brightness=115 \
    --set-ctrl=contrast=50 \
    --set-ctrl=saturation=80
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
    -frames:v 1 \
    -vf "unsharp=5:5:1.0:5:5:0.0, eq=gamma=0.8:saturation=1.1" \
    -f image2pipe \
    -vcodec mjpeg \
    -q:v 2 \
    -
}

upload_image() {
  local s3_uri="$1"

  /usr/bin/s3cmd put \
    --mime-type="image/jpeg" \
    - \
    "${s3_uri}"
}

main() {
  local timestamped_s3_uri
  local latest_s3_uri

  configure_camera

  timestamped_s3_uri="$(build_timestamped_s3_uri)"
  latest_s3_uri="$(build_latest_s3_uri)"

  capture_image \
    | tee >(upload_image "${latest_s3_uri}") \
    | upload_image "${timestamped_s3_uri}"
}

main "$@"

